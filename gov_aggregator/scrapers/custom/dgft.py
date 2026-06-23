"""DGFT (Directorate General of Foreign Trade) crawler.

The homepage embeds the latest 5 notifications in a hidden input
``#plnNewsDtl`` as a double-JSON-encoded string — no JS execution needed.
A plain httpx GET to https://www.dgft.gov.in/CP/ returns this data instantly.

The full historical DataTable (700+ rows) requires a full browser session:
click "View More" → DataTables AJAX load → paginate. That path uses Playwright
in a thread executor as a fallback that adds more items when the table loads.
If Playwright fails or times out, the httpx path still returns the 5 embedded
items reliably.

Field mapping from the embedded JSON:
  title     → item title
  metadata1 → notice/policy number
  metadata2 → year
  metadata4 → date (DD/MM/YYYY)
  attachPath → relative PDF/link path
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dgft")

DGFT_BASE   = "https://www.dgft.gov.in"
DGFT_CP     = f"{DGFT_BASE}/CP/"
MAX_PAGES   = 5
ROW_WAIT_MS = 30_000
PAGE_NAV_MS = 15_000

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,*/*",
    "Referer": DGFT_CP,
}

_DATE_FMTS = ("%d/%m/%Y", "%Y-%m-%d")


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _build_link(attach_path: str) -> tuple[str, bool]:
    if not attach_path:
        return "", False
    if attach_path.startswith("http"):
        return attach_path, attach_path.lower().endswith(".pdf")
    link = urljoin(DGFT_CP, attach_path)
    is_pdf = "downloadPdf" in attach_path or "download" in attach_path.lower() or attach_path.lower().endswith(".pdf")
    return link, is_pdf


def _shape_embedded(record: dict) -> ScrapedItem | None:
    """Shape one record from the #plnNewsDtl embedded JSON."""
    title = (record.get("title") or "").strip()
    if not title:
        return None

    # Prepend the notice number when present to make titles descriptive
    number = (str(record.get("metadata1") or "")).strip()
    if number and number not in title:
        title = f"{title} {number}".strip()

    published_at = _parse_date(record.get("metadata4"))
    attach_path  = (record.get("attachPath") or record.get("path") or "").strip()
    link, is_pdf = _build_link(attach_path)

    if not link:
        return None

    return ScrapedItem(
        title=title,
        link=link,
        published_at=published_at,
        is_pdf=is_pdf,
        section_label="Latest Notifications",
    )


async def _fetch_embedded(client: httpx.AsyncClient) -> list[ScrapedItem]:
    """Extract items from the #plnNewsDtl hidden input in the homepage HTML."""
    try:
        resp = await client.get(DGFT_CP)
        html = resp.text
    except Exception as exc:
        logger.warning("[dgft] homepage fetch failed: %s", exc)
        return []

    m = re.search(
        r'id=["\']plnNewsDtl["\'][^>]*value=["\']([^"\']+)["\']', html
    ) or re.search(
        r'value=["\']([^"\']+)["\'][^>]*id=["\']plnNewsDtl["\']', html
    )
    if not m:
        logger.warning("[dgft] #plnNewsDtl not found in homepage")
        return []

    try:
        raw = unescape(m.group(1))
        data = json.loads(json.loads(raw))
        news_list = data.get("allNewsList") or []
    except Exception as exc:
        logger.warning("[dgft] failed to parse #plnNewsDtl: %s", exc)
        return []

    items: list[ScrapedItem] = []
    for record in news_list:
        if not isinstance(record, dict):
            continue
        item = _shape_embedded(record)
        if item:
            items.append(item)

    logger.info("[dgft] embedded: %d items", len(items))
    return items


# ── Playwright path (DataTables full history) ──────────────────────────────

def _parse_rows(html: str, seen_links: set[str], section_label: str) -> list[ScrapedItem]:
    """Parse one page of #metadataTable rows."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#metadataTable tbody tr")
    items: list[ScrapedItem] = []
    for row in rows:
        cells = row.select("td")
        if len(cells) < 5:
            continue
        number   = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        desc     = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        date_raw = cells[4].get_text(strip=True) if len(cells) > 4 else ""
        title = f"{number} - {desc}" if number and desc else (number or desc)
        if not title:
            continue

        published_at = _parse_date(date_raw)
        a_tag = cells[5].select_one("a[href]") if len(cells) > 5 else None
        if not a_tag:
            a_tag = row.select_one("a[href]")
        if not a_tag:
            continue
        href = (a_tag.get("href") or "").strip()
        if not href or "javascript" in href.lower():
            continue
        link = href if href.startswith("http") else urljoin(DGFT_BASE, href)
        if link in seen_links:
            continue
        seen_links.add(link)
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=href.lower().endswith(".pdf"),
            section_label=section_label,
        ))
    return items


def _run_playwright() -> list[ScrapedItem]:
    """Scrape the #metadataTable via Playwright (sync, runs in thread executor)."""
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []
    seen_links: set[str] = set()
    browser = None
    context = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=_HEADERS["User-Agent"],
                viewport={"width": 1400, "height": 900},
            )
            page = context.new_page()
            page.goto(DGFT_CP, wait_until="domcontentloaded", timeout=60_000)

            # Click "View More" to trigger the DataTables AJAX load
            try:
                page.get_by_role("button", name="View More").click(timeout=15_000)
            except Exception:
                logger.warning("[dgft] 'View More' button not found")

            # Wait for the table rows
            try:
                page.wait_for_selector("#metadataTable tbody tr", timeout=ROW_WAIT_MS)
            except Exception:
                logger.error("[dgft] #metadataTable rows never appeared — aborting Playwright path")
                return []

            # Discover section tabs
            tab_locators = page.locator(
                "ul.nav-tabs a, ul.nav li a[data-toggle='tab'], "
                ".tabs-container [role='tab'], .dgft-tabs a, .tab-links a"
            ).all()
            seen_labels: set[str] = set()
            section_tabs: list[tuple[str, object]] = []
            for loc in tab_locators:
                try:
                    label = (loc.inner_text(timeout=2_000) or "").strip()
                except Exception:
                    continue
                if not label or label in seen_labels:
                    continue
                seen_labels.add(label)
                section_tabs.append((label, loc))

            if not section_tabs:
                # No tabs — scrape current view
                for _ in range(MAX_PAGES):
                    items = _parse_rows(page.content(), seen_links, "All")
                    all_items.extend(items)
                    next_btn = page.query_selector("li#metadataTable_next:not(.disabled) a, #metadataTable_next:not(.disabled)")
                    if not next_btn or not items:
                        break
                    try:
                        next_btn.click()
                        page.wait_for_selector("#metadataTable tbody tr", timeout=PAGE_NAV_MS)
                    except Exception:
                        break
                return all_items

            for section_label, tab_loc in section_tabs:
                try:
                    tab_loc.click(timeout=8_000)
                    page.wait_for_selector("#metadataTable tbody tr", timeout=PAGE_NAV_MS)
                except Exception as exc:
                    logger.warning("[dgft] tab %r click failed: %s", section_label, exc)
                    continue

                for pg_num in range(MAX_PAGES):
                    items = _parse_rows(page.content(), seen_links, section_label)
                    all_items.extend(items)
                    logger.info("[dgft] tab=%r page=%d items=%d", section_label, pg_num + 1, len(items))
                    next_btn = page.query_selector("li#metadataTable_next:not(.disabled) a, #metadataTable_next:not(.disabled)")
                    if not next_btn or not items:
                        break
                    try:
                        next_btn.click()
                        page.wait_for_selector("#metadataTable tbody tr", timeout=PAGE_NAV_MS)
                    except Exception:
                        break

    except Exception as exc:
        logger.error("[dgft] Playwright scrape failed: %s", exc)
    finally:
        if context:
            try: context.close()
            except Exception: pass
        if browser:
            try: browser.close()
            except Exception: pass

    logger.info("[dgft] Playwright total: %d items", len(all_items))
    return all_items


async def crawl_dgft(_config: SiteConfig) -> list[ScrapedItem]:
    """
    Two-stage crawl:
      1. httpx — extract latest 5 items from #plnNewsDtl (always fast, always works)
      2. Playwright — scrape full #metadataTable (3-min cap, graceful fallback)
    Results are merged and deduplicated by link.
    """
    # Stage 1: fast embedded data
    async with httpx.AsyncClient(
        headers=_HEADERS, verify=False,
        follow_redirects=True, timeout=30.0,
    ) as client:
        embedded = await _fetch_embedded(client)

    # Stage 2: Playwright for full history (timeout 3 min)
    loop = asyncio.get_running_loop()
    try:
        pw_items: list[ScrapedItem] = await asyncio.wait_for(
            loop.run_in_executor(None, _run_playwright),
            timeout=180,
        )
    except asyncio.TimeoutError:
        logger.warning("[dgft] Playwright timed out after 3 minutes")
        pw_items = []

    # Merge: Playwright items first (richer), then embedded as fallback
    seen: set[str] = set()
    merged: list[ScrapedItem] = []
    for it in pw_items + embedded:
        if it.link not in seen:
            seen.add(it.link)
            merged.append(it)

    logger.info("[dgft] total after merge: %d items", len(merged))
    return merged

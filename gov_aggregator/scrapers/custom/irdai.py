from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://irdai.gov.in"
WHATS_NEW_URL = "https://irdai.gov.in/whats-new"

PRESS_RELEASES_BASE = (
    "https://irdai.gov.in/press-releases"
    "?p_p_id=com_irdai_document_media_IRDAIDocumentMediaPortlet_INSTANCE_4J7DnssD2EYn"
    "&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
    "&_com_irdai_document_media_IRDAIDocumentMediaPortlet_INSTANCE_4J7DnssD2EYn_delta=20"
    "&_com_irdai_document_media_IRDAIDocumentMediaPortlet_INSTANCE_4J7DnssD2EYn_resetCur=false"
    "&_com_irdai_document_media_IRDAIDocumentMediaPortlet_INSTANCE_4J7DnssD2EYn_cur={page}"
)

CUTOFF = datetime(2025, 10, 1, tzinfo=timezone.utc)
MAX_WHATS_NEW_PAGES = 5
MAX_PR_PAGES = 6

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": "https://irdai.gov.in/",
}

# Ordered list of selectors to try for the "Next page" button on What's New
_NEXT_SELECTORS = [
    ".lfr-pagination-buttons li.next:not(.disabled) a",
    ".lfr-pagination-buttons .next:not([class*='disabled']) a",
    ".pagination li.next:not(.disabled) a",
    ".pager li.next:not(.disabled) a",
    "ul.pager li.next a",
    "a[aria-label='Next Page']",
    "a[aria-label='Next']",
    ".pagination-next a",
    "li.next > a",
]


def _parse_date_dmy(raw: str | None) -> datetime | None:
    """Parse DD-MM-YYYY — the format used throughout irdai.gov.in."""
    if not raw:
        return None
    m = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", raw)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _parse_whats_new_html(
    html: str, seen_links: set[str]
) -> tuple[list[ScrapedItem], bool]:
    """
    Parse .whatsNew-content items from a rendered page.

    Returns (new_items, reached_cutoff).
    reached_cutoff=True means we hit an item older than CUTOFF — caller should stop paging.
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for container in soup.select(".whatsNew-content"):
        header = container.select_one(".timeline-header")
        if not header:
            continue

        link_tag = header.select_one("a")
        if not link_tag:
            continue

        title = link_tag.get_text(strip=True)
        href = (link_tag.get("href") or "").strip()
        if not href or not title:
            continue

        link = href if href.startswith("http") else f"{BASE_URL}{href}"
        if link in seen_links:
            continue

        header_text = header.get_text(" ", strip=True)
        published_at = _parse_date_dmy(header_text)

        if published_at and published_at < CUTOFF:
            return items, True  # items sorted newest-first; stop here

        body = container.select_one(".timeline-body")
        summary = body.get_text(strip=True) if body else None

        seen_links.add(link)
        items.append(
            ScrapedItem(
                title=title,
                link=link,
                summary=summary,
                published_at=published_at,
                is_pdf=False,
                section_label="What's New",
            )
        )

    return items, False


def _run_whats_new_playwright_sync() -> list[ScrapedItem]:
    """
    Runs in a thread-pool worker via run_in_executor to avoid clashing
    with the uvicorn event loop on Windows (same pattern as the engine).
    """

    async def _async() -> list[ScrapedItem]:
        from playwright.async_api import async_playwright

        items: list[ScrapedItem] = []
        seen_links: set[str] = set()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, channel="msedge")
            page = await browser.new_page()
            await page.set_extra_http_headers(
                {k: v for k, v in DEFAULT_HEADERS.items() if k != "User-Agent"}
            )
            await page.set_extra_http_headers({"User-Agent": DEFAULT_HEADERS["User-Agent"]})

            try:
                await page.goto(WHATS_NEW_URL, wait_until="domcontentloaded", timeout=30_000)

                # Wait for the JS to populate #itemsContainer
                try:
                    await page.wait_for_selector(
                        "#itemsContainer .whatsNew-content", timeout=15_000
                    )
                except Exception:
                    await page.wait_for_timeout(3_000)

                for _page_num in range(MAX_WHATS_NEW_PAGES):
                    html = await page.content()
                    new_items, reached_cutoff = _parse_whats_new_html(html, seen_links)
                    items.extend(new_items)

                    if reached_cutoff:
                        break

                    # Try every known "Next page" button selector
                    clicked = False
                    for selector in _NEXT_SELECTORS:
                        try:
                            btn = await page.query_selector(selector)
                            if btn and await btn.is_visible():
                                # Count items before click to detect DOM update
                                before = await page.eval_on_selector_all(
                                    "#itemsContainer .whatsNew-content",
                                    "els => els.length",
                                )
                                await btn.click()
                                # Wait until item count changes or a short timeout
                                try:
                                    await page.wait_for_function(
                                        f"document.querySelectorAll('#itemsContainer .whatsNew-content').length !== {before}",
                                        timeout=8_000,
                                    )
                                except Exception:
                                    await page.wait_for_timeout(2_000)
                                clicked = True
                                break
                        except Exception:
                            continue

                    if not clicked:
                        break  # No next button found — we have all available items

            finally:
                await browser.close()

        return items

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_async())
    finally:
        loop.close()


async def _scrape_whats_new() -> list[ScrapedItem]:
    """Async entry point — offloads Playwright to a thread pool worker."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_whats_new_playwright_sync)


async def _scrape_press_releases() -> list[ScrapedItem]:
    """
    Scrape IRDAI Press Releases via httpx using Liferay portlet URL pagination.
    The table IS server-rendered, so no Playwright needed here.
    """
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True, headers=DEFAULT_HEADERS, timeout=30.0
    ) as client:
        for page_num in range(1, MAX_PR_PAGES + 1):
            url = PRESS_RELEASES_BASE.format(page=page_num)
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
            except Exception as exc:
                print(f"[irdai] press-releases page {page_num} failed: {exc}")
                break

            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table.table tbody tr")
            if not rows:
                break

            reached_cutoff = False
            for row in rows:
                title_td = row.select_one(".table-col-shortDesc")
                date_td = row.select_one(".table-col-lastUpdated")
                doc_a = row.select_one(".table-col-documents a")

                if not title_td:
                    continue

                title = title_td.get_text(strip=True)
                if not title:
                    continue

                # Prefer direct PDF link; fall back to detail-page link
                href = ""
                if doc_a:
                    href = (doc_a.get("href") or "").strip()
                if not href:
                    sub_a = row.select_one(".table-col-subTitle a")
                    if sub_a:
                        href = (sub_a.get("href") or "").strip()
                if not href:
                    continue

                link = href if href.startswith("http") else f"{BASE_URL}{href}"
                date_text = date_td.get_text(strip=True) if date_td else ""
                published_at = _parse_date_dmy(date_text)

                if published_at and published_at < CUTOFF:
                    reached_cutoff = True
                    break

                is_pdf = "download=true" in href or href.lower().endswith(".pdf")
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        summary=None,
                        published_at=published_at,
                        is_pdf=is_pdf,
                        section_label="Press Releases",
                    )
                )

            if reached_cutoff:
                break

    return items


async def crawl_irdai(config: SiteConfig) -> list[ScrapedItem]:
    """
    Scrapes IRDAI in parallel:
      • What's New  — Playwright with AJAX click-through pagination
      • Press Releases — httpx with Liferay portlet URL pagination
    """
    whats_new, press_releases = await asyncio.gather(
        _scrape_whats_new(),
        _scrape_press_releases(),
    )

    all_items = whats_new + press_releases

    # Deduplicate by link
    seen: set[str] = set()
    deduped: list[ScrapedItem] = []
    for item in all_items:
        if item.link and item.link not in seen:
            seen.add(item.link)
            deduped.append(item)

    # Sort newest first
    deduped.sort(
        key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return deduped

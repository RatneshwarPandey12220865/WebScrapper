"""
Custom crawler for Department of Health Research (www.dhr.gov.in).

The site is a Next.js SPA — all pages are client-side rendered and return
an empty shell to plain HTTP requests. Requires Playwright.

Sections scraped:
  1. What's New announcements      (/whats-new)
  2. Orders & Notices              (/documents/orders-and-notices paginated)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dhr")

_BASE = "https://www.dhr.gov.in"
_GOTO_TIMEOUT = 30_000
_WAIT_TIMEOUT = 12_000


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", raw)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _abs(href: str) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else urljoin(_BASE, href)


def _parse_whats_new(soup: BeautifulSoup, section_label: str) -> list[ScrapedItem]:
    items = []
    
    for row in soup.select("div.announcementbox"):
        title_el = row.select_one("p.mb-0")
        title = _clean(title_el.get_text() if title_el else "")
        
        if not title:
            continue
        
        link = ""
        all_links = row.select("a[href]")
        for a in all_links:
            href = a.get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript"):
                link = _abs(href)
                break
        
        if not link or not link.startswith("http"):
            continue
        
        is_pdf = link.lower().endswith(".pdf")
        
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=_parse_date(title),
            is_pdf=is_pdf,
            section_label=section_label,
        ))

    return items


def _parse_orders_notices(soup: BeautifulSoup, section_label: str) -> list[ScrapedItem]:
    items = []
    
    for row in soup.select("div.announcementbox"):
        title_el = row.select_one("p.mb-0")
        link_el = row.select_one("a.download-btn[href]")
        
        title = _clean(title_el.get_text() if title_el else "")
        link = link_el.get("href", "") if link_el else ""
        
        title = title.replace("View All", "").strip()
        
        if not title:
            continue
        
        if not link.startswith("http"):
            continue
        
        date_el = row.select_one("small.ptype")
        date_text = date_el.get_text(strip=True) if date_el else ""
        published_at = _parse_date(date_text)
        
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label=section_label,
        ))

    return items


def _sync_crawl(_config: SiteConfig) -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto(f"{_BASE}/whats-new", wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
            page.wait_for_timeout(3000)

            soup = BeautifulSoup(page.content(), "html.parser")
            wn_items = _parse_whats_new(soup, "Whats New")
            logger.info("[dhr] Whats New: %d items", len(wn_items))
            all_items.extend(wn_items)

        except Exception as exc:
            logger.warning("[dhr] /whats-new failed: %s", exc)

        try:
            page.goto(f"{_BASE}/documents/orders-and-notices", wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
            page.wait_for_timeout(3000)

            soup = BeautifulSoup(page.content(), "html.parser")
            orders = _parse_orders_notices(soup, "Orders and Notices")
            logger.info("[dhr] Orders & Notices: %d items", len(orders))
            all_items.extend(orders)
        except Exception as exc:
            logger.warning("[dhr] /orders-and-notices failed: %s", exc)

        browser.close()

    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in all_items:
        if item.link and item.link not in seen:
            seen.add(item.link)
            unique.append(item)

    logger.info("[dhr] Returning: %d items", len(all_items))
    
    # Group by section_label and dedup within each group
    from collections import defaultdict
    by_section: defaultdict[str, list[ScrapedItem]] = defaultdict(list)
    for item in all_items:
        by_section[item.section_label].append(item)
    
    unique = []
    for section, items_list in by_section.items():
        seen = set()
        for item in items_list:
            link_key = item.link if item.link else ""
            if link_key not in seen:
                seen.add(link_key)
                unique.append(item)
    
    logger.info("[dhr] After section dedup: %d items", len(unique))
    return unique


# ─────────────────────────────────────────────────────────────────────────────
#  TARGETED ADDITION — View All resolution + Press Releases page
#  -----------------------------------------------------------------------------
#  Two narrow extras, both driven by the same Next.js UI the existing
#  _sync_crawl already renders:
#
#    1. View All resolution on /documents/orders-and-notices
#       The existing parser keeps rows whose download-btn href is an
#       absolute PDF URL, but drops rows whose href is a RELATIVE detail
#       path such as
#         /documents/orders-and-notices/<slug>-<hashid>?pageTitle=…
#       Those are the "View All" rows. We collect each such detail URL,
#       open the detail page (also a Next.js render), and parse its own
#       announcementbox rows — those carry the real PDF download links
#       (e.g. https://dhr.gov.in/static/uploads/2026/04/<hash>.pdf).
#
#    2. Press Releases listing at /documents/press-release?page=N
#       Same announcementbox shape as Orders & Notices, but most entries
#       are external "Visit" links (e.g. https://www.icmr.gov.in/...).
#       Parsed with the same logic and labelled "Press Releases".
#
#  Nothing above this divider is touched. crawl_dhr runs both passes
#  alongside the existing one and dedups by (section_label, link).
# ─────────────────────────────────────────────────────────────────────────────


def _parse_press_release_rows(soup: BeautifulSoup, section_label: str) -> list[ScrapedItem]:
    """Press release page rows. Same announcementbox shape, external links kept."""
    items: list[ScrapedItem] = []
    for row in soup.select("div.announcementbox"):
        title_el = row.select_one("p.mb-0")
        link_el = row.select_one("a.download-btn[href]")

        title = _clean(title_el.get_text() if title_el else "")
        link = (link_el.get("href") if link_el else "") or ""

        if not title:
            continue
        if not link.startswith("http"):
            continue

        date_el = row.select_one("small.ptype")
        date_text = ""
        if date_el is not None:
            date_text = date_el.get("aria-label", "") or date_el.get_text(strip=True)
        published_at = _parse_date(date_text)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label=section_label,
        ))
    return items


def _collect_view_all_detail_urls(soup: BeautifulSoup) -> list[str]:
    """Return URLs of View All detail pages from the orders-and-notices listing.

    A View All row's download-btn href is a relative path under
    /documents/orders-and-notices/  — distinct from regular rows whose href
    is an absolute PDF URL.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for row in soup.select("div.announcementbox"):
        link_el = row.select_one("a.download-btn[href]")
        if link_el is None:
            continue
        href = (link_el.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("http"):
            continue   # absolute PDF — already captured by the existing parser
        if "/documents/orders-and-notices/" not in href:
            continue
        full = _abs(href)
        if full and full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def _sync_crawl_additions(_config: SiteConfig) -> list[ScrapedItem]:
    """Targeted Playwright extras — View All detail pages + Press Releases listing.

    Mirrors the user's recorded codegen flow but talks directly to the
    public Next.js site at https://www.dhr.gov.in.
    """
    from playwright.sync_api import sync_playwright

    items: list[ScrapedItem] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # ── 1. Press Releases listing ─────────────────────────────────────
        try:
            for page_num in range(1, 6):  # walk pages until empty
                pr_url = f"{_BASE}/documents/press-release?page={page_num}"
                page.goto(pr_url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
                page.wait_for_timeout(3000)
                pr_soup = BeautifulSoup(page.content(), "html.parser")
                pr_rows = _parse_press_release_rows(pr_soup, "Press Releases")
                if not pr_rows:
                    logger.info("[dhr] Press Releases page %d empty — stop", page_num)
                    break
                items.extend(pr_rows)
                logger.info("[dhr] Press Releases page %d: +%d items", page_num, len(pr_rows))
        except Exception as exc:
            logger.warning("[dhr] /documents/press-release failed: %s", exc)

        # ── 2. View All resolution on Orders & Notices ────────────────────
        try:
            page.goto(
                f"{_BASE}/documents/orders-and-notices",
                wait_until="domcontentloaded",
                timeout=_GOTO_TIMEOUT,
            )
            page.wait_for_timeout(3000)
            list_soup = BeautifulSoup(page.content(), "html.parser")
            detail_urls = _collect_view_all_detail_urls(list_soup)
            logger.info("[dhr] Found %d View All detail pages to visit", len(detail_urls))

            for detail_url in detail_urls:
                try:
                    page.goto(detail_url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
                    page.wait_for_timeout(2500)
                    detail_soup = BeautifulSoup(page.content(), "html.parser")
                    sub_items = _parse_orders_notices(detail_soup, "Orders and Notices")
                    items.extend(sub_items)
                    logger.info("[dhr] View All %s → +%d items", detail_url.rsplit("/", 1)[-1][:40], len(sub_items))
                except Exception as exc:
                    logger.debug("[dhr] detail nav %s failed: %s", detail_url, exc)
                    continue
        except Exception as exc:
            logger.warning("[dhr] View All resolution failed: %s", exc)

        browser.close()

    return items


async def crawl_dhr(_config: SiteConfig) -> list[ScrapedItem]:
    """Async wrapper - runs the base Playwright crawl + targeted additions in parallel."""
    logger.info("[dhr] Starting async crawl...")
    loop = asyncio.get_running_loop()

    base_task = loop.run_in_executor(None, _sync_crawl, _config)
    additions_task = loop.run_in_executor(None, _sync_crawl_additions, _config)

    base_result, additions_result = await asyncio.gather(base_task, additions_task)

    combined = list(base_result) + list(additions_result)

    seen: set[tuple[str, str]] = set()
    unique: list[ScrapedItem] = []
    for item in combined:
        if not item.link:
            continue
        key = (item.section_label or "", item.link)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    logger.info(
        "[dhr] Combined: base=%d + additions=%d → unique=%d",
        len(base_result), len(additions_result), len(unique),
    )
    return unique



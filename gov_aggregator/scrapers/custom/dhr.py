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


async def crawl_dhr(_config: SiteConfig) -> list[ScrapedItem]:
    """Async wrapper - calls the sync version in a thread."""
    logger.info("[dhr] Starting async crawl...")
    result = await asyncio.get_running_loop().run_in_executor(
        None, _sync_crawl, _config
    )
    logger.info("[dhr] Async result: %d items", len(result))
    return result
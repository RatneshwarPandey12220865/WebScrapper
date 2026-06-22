from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urljoin

if TYPE_CHECKING:
    from playwright.sync_api import Page

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.mohfw_dohfw")

_BASE = "https://www.mohfw-dohfw.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_PAGES = 15

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_SECTIONS = [
    ("/whats-new",                    "What's New"),
    ("/documents/orders-and-notices", "Orders and Notices"),
    ("/documents/circulars",          "Circulars"),
]

def _parse_boxes(html: str, section_label: str) -> tuple[list[ScrapedItem], bool]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    for box in soup.select("div.announcementbox"):
        title_el = box.select_one("p.mb-0")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        a = box.select_one("a[href]")
        href = (a.get("href") or "").strip() if a else ""
        link = href if href.startswith("http") else urljoin(_BASE, href) if href else _BASE

        cells = box.find_all("div", role="cell")
        date_text = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        published_at = _parse_date(date_text)

        if published_at and published_at < _MIN_DATE:
            stop = True
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label=section_label,
        ))

    return items, stop


def _switch_language_to_english(page: "Page") -> None:
    """Try to switch the Bhashini language widget to English. Non-fatal if it fails."""
    try:
        btn = page.query_selector('button[aria-controls="bhashiniLanguageDropdown"]')
        if btn:
            btn.click()
            page.wait_for_timeout(600)
            en_option = page.query_selector('li[data-value="en"]')
            if en_option:
                en_option.click()
                page.wait_for_timeout(2000)
    except Exception as exc:
        logger.warning("[mohfw_dohfw] Language switch skipped: %s", exc)


def _run_playwright() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []
    seen: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=_UA, viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        for path, section_label in _SECTIONS:
            url = f"{_BASE}{path}"

            # Step 1: navigate — fatal if page can't load at all
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(1500)
            except Exception as exc:
                logger.error("[mohfw_dohfw] Navigation failed for %s: %s", path, exc)
                continue

            # Step 2: language switch — non-fatal
            _switch_language_to_english(page)

            # Step 3: wait for content — fatal for this section if content never appears
            try:
                page.wait_for_selector("div.announcementbox", timeout=25_000)
            except Exception as exc:
                logger.error("[mohfw_dohfw] Content selector timeout for %s: %s", path, exc)
                continue

            pages_fetched = 0
            while pages_fetched < _MAX_PAGES:
                items, stop = _parse_boxes(page.content(), section_label)

                for item in items:
                    if item.link not in seen:
                        seen.add(item.link)
                        all_items.append(item)

                pages_fetched += 1
                logger.info("[mohfw_dohfw] %s page %d: %d items", section_label, pages_fetched, len(items))

                if stop or not items:
                    break

                # Try next page button
                next_btn = page.query_selector('ul.pagination li.next a, a[aria-label*="next" i]')
                if not next_btn:
                    break
                try:
                    next_btn.click()
                    page.wait_for_selector("div.announcementbox", timeout=15_000)
                    page.wait_for_timeout(1000)
                except Exception:
                    break

        ctx.close()
        browser.close()

    return all_items


async def crawl_mohfw_dohfw(_config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _run_playwright)
    logger.info("[mohfw_dohfw] total: %d", len(items))
    return items

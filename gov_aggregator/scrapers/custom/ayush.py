"""
Custom crawler for Ministry of Ayush (ayush.gov.in).

The site is an Angular SPA. The sub-pages /whatsnew and /pressrelease are
client-side routes only — the server returns 404 for direct requests. We must
load the homepage first and then navigate via Angular's router (by clicking
the correct links) to reach each section.

Uses sync_playwright in a thread executor to avoid the Windows
SelectorEventLoop limitation (asyncio.create_subprocess_exec raises
NotImplementedError on Windows when called from SelectorEventLoop).
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.ayush")

_BASE = "https://ayush.gov.in"
_TIMEOUT = 20_000  # ms


def _parse_table(html: str, section_label: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    for row in soup.select("table.table tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        title = cells[1].get_text(" ", strip=True)
        if "\n" in title:
            title = title.split("\n")[0].strip()
        title = " ".join(title.split())
        if not title:
            continue
        link = ""
        if len(cells) >= 3:
            a = cells[2].find("a", href=True)
            if a:
                href = a["href"].strip()
                link = href if href.startswith("http") else urljoin(_BASE + "/", href)
        if not link:
            continue
        is_pdf = link.lower().endswith(".pdf")
        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=is_pdf,
            section_label=section_label,
        ))
    return items


def _sync_crawl() -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=DEFAULT_HEADERS["User-Agent"])
        page = context.new_page()

        # --- What's New ---
        try:
            page.goto(_BASE + "/", wait_until="domcontentloaded", timeout=_TIMEOUT)
            page.wait_for_timeout(2000)
            page.get_by_role("link", name="VIEW MORE", exact=True).click(timeout=10_000)
            page.wait_for_selector("table.table tbody tr", timeout=15_000)
            items = _parse_table(page.content(), "What's New")
            logger.info("[ayush] What's New: %d items", len(items))
            all_items.extend(items)
        except Exception as exc:
            logger.warning("[ayush] What's New failed: %s", exc)

        # --- Press Releases ---
        try:
            page.goto(_BASE + "/", wait_until="domcontentloaded", timeout=_TIMEOUT)
            page.wait_for_timeout(2000)
            page.get_by_text("Media", exact=True).click(timeout=10_000)
            page.get_by_role("link", name="Press Release", exact=True).click(timeout=10_000)
            page.wait_for_selector("table.table tbody tr", timeout=15_000)
            items = _parse_table(page.content(), "Press Releases")
            logger.info("[ayush] Press Releases: %d items", len(items))
            all_items.extend(items)
        except Exception as exc:
            logger.warning("[ayush] Press Releases failed: %s", exc)

        context.close()
        browser.close()

    # Deduplicate by link
    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in all_items:
        if item.link not in seen:
            seen.add(item.link)
            unique.append(item)

    return unique


async def crawl_ayush(config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_crawl)

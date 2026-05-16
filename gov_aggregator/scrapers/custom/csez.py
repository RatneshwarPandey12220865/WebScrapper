from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://csez.com"
_CIRCULARS_URL = "https://csez.com/rti/php/circulars.php"
_WHATSNEW_URL = "https://csez.com/php/whatsnew.php"
_MIN_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
# Date appears after "&" in "No. & Date" column: e.g. "CSEZ.../2025 & 21/10/2025"
_DATE_RE = re.compile(r"&\s*(\d{1,2}/\d{1,2}/\d{4})\s*$")
_EXECUTOR = ThreadPoolExecutor(max_workers=1)


def _parse_date(no_and_date: str) -> datetime | None:
    m = _DATE_RE.search(no_and_date.strip())
    if not m:
        return None
    day, month, year = m.group(1).split("/")
    try:
        return datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_circulars(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for div in soup.select("div[id^='div']"):
        for row in div.select("tr"):
            tds = row.find_all("td")
            if len(tds) < 4:
                continue
            if tds[0].find("th") or tds[1].find("strong"):
                continue

            no_date_raw = " ".join(tds[1].get_text().split())
            subject = " ".join(tds[2].get_text().split())
            if not subject:
                continue

            published_at = _parse_date(no_date_raw)
            if published_at and published_at < _MIN_DATE:
                continue

            link_tag = tds[3].select_one("a[href]")
            if not link_tag:
                continue

            href = link_tag.get("href", "")
            link = urljoin(_CIRCULARS_URL, href)
            is_pdf = href.lower().endswith(".pdf")

            items.append(ScrapedItem(
                title=subject,
                link=link,
                summary=no_date_raw,
                published_at=published_at,
                is_pdf=is_pdf,
                section_label="Circulars",
            ))

    return items


def _parse_whatsnew(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    seen: set[str] = set()

    for row in soup.select("table tr"):
        tds = row.find_all("td")
        if len(tds) < 2:
            continue
        a_tag = row.select_one("a[href]")
        if not a_tag:
            continue

        title_td = tds[-1]
        title = " ".join(title_td.get_text().split())
        # Strip inline link text like "Click here", "Click", "click here"
        title = re.sub(r"\s*(click here|click)\s*$", "", title, flags=re.IGNORECASE).strip()
        if not title or len(title) < 5:
            continue

        href = a_tag.get("href", "")
        if not href or href.startswith("#"):
            continue
        link = href if href.startswith("http") else urljoin(_BASE, href)
        if link in seen:
            continue
        seen.add(link)

        is_pdf = href.lower().endswith(".pdf")
        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=is_pdf,
            section_label="What's New",
        ))

    return items


def _crawl_sync() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(_CIRCULARS_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            circulars = _parse_circulars(page.content())

            page.goto(_WHATSNEW_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            whatsnew = _parse_whatsnew(page.content())
        finally:
            browser.close()

    return circulars + whatsnew


async def crawl_csez(_config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, _crawl_sync)

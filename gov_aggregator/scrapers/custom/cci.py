from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

_BASE = "https://cci.gov.in"
_WHATSNEW_URL = "https://cci.gov.in/whats-new"
_PRESS_URL = "https://cci.gov.in/media-gallery/press-release"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_WHATSNEW_PAGES = 4
_EXECUTOR = ThreadPoolExecutor(max_workers=1)

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date


def _parse_whatsnew_page(html: str) -> list[ScrapedItem]:
    """What's New table: cols = No | Title | Document (no date column)."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    for row in soup.select("table#datatable_ajax tbody tr"):
        tds = row.find_all("td")
        if len(tds) < 3:
            continue
        title = " ".join(tds[1].get_text().split())
        if not title:
            continue
        a_tag = tds[2].find("a", href=True)
        href = a_tag.get("href", "") if a_tag else ""
        link = urljoin(_BASE, href) if href and not href.startswith("http") else href
        is_pdf = href.lower().endswith(".pdf") if href else False
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=None,
            is_pdf=is_pdf,
            section_label="What's New",
        ))
    return items


def _resolve_press_pdf(detail_url: str) -> str:
    """Visit a press release detail page and return the direct PDF URL."""
    try:
        resp = requests.get(detail_url, headers=_HEADERS, timeout=15, verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")
        # <iframe src="https://cci.gov.in/images/pressrelease/en/...pdf">
        iframe = soup.select_one("iframe[src]")
        if iframe:
            src = iframe.get("src", "")
            if src.lower().endswith(".pdf"):
                return src
        # fallback: onclick="viewPdf('...')"
        for tag in soup.find_all(onclick=True):
            m = re.search(r"viewPdf\('([^']+\.pdf)'\)", tag.get("onclick", ""))
            if m:
                return m.group(1)
    except Exception:
        pass
    return detail_url


def _parse_press_page(html: str) -> tuple[list[ScrapedItem], bool]:
    """Press Release table: cols = No | Title | Date | Document.
    Returns (items, stop_flag) where stop_flag=True when a pre-2026 item is found."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False
    for row in soup.select("table#datatable_ajax tbody tr"):
        tds = row.find_all("td")
        if len(tds) < 4:
            continue
        title_el = tds[1].find("span") or tds[1]
        title = " ".join(title_el.get_text().split())
        if not title:
            continue
        published_at = _parse_date(tds[2].get_text(strip=True))
        if published_at and published_at < _MIN_DATE:
            stop = True
            continue
        a_tag = tds[3].find("a", href=True)
        detail_href = a_tag.get("href", "") if a_tag else ""
        detail_url = urljoin(_BASE, detail_href) if detail_href and not detail_href.startswith("http") else detail_href
        # Fetch detail page to get the real PDF link
        pdf_url = _resolve_press_pdf(detail_url)
        items.append(ScrapedItem(
            title=title,
            link=pdf_url,
            published_at=published_at,
            is_pdf=True,
            section_label="Press Releases",
        ))
    return items, stop


def _paginate(page, parse_fn, max_pages: int | None) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    current = 1
    while True:
        page.wait_for_selector("table#datatable_ajax tbody tr", timeout=15000)
        result = parse_fn(page.content())
        if isinstance(result, tuple):
            items, stop = result
        else:
            items, stop = result, False
        all_items.extend(items)
        if stop:
            break
        if max_pages and current >= max_pages:
            break
        next_btn = page.query_selector("#datatable_ajax_next")
        if not next_btn:
            break
        classes = next_btn.get_attribute("class") or ""
        if "disabled" in classes:
            break
        next_btn.click()
        page.wait_for_timeout(800)
        current += 1
    return all_items


def _crawl_sync() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()

            page.goto(_WHATSNEW_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            whatsnew = _paginate(page, _parse_whatsnew_page, max_pages=_WHATSNEW_PAGES)

            page.goto(_PRESS_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            press = _paginate(page, _parse_press_page, max_pages=None)

        finally:
            browser.close()

    return whatsnew + press


async def crawl_cci(_config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, _crawl_sync)

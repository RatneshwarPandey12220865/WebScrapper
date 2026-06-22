from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://agriwelfare.gov.in"
_MIN_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
_EXECUTOR = ThreadPoolExecutor(max_workers=1)


def _scrape_news_carousel(soup: BeautifulSoup) -> list[ScrapedItem]:
    """Parse the Agriculture News swiper carousel on the homepage."""
    items: list[ScrapedItem] = []
    seen: set[str] = set()

    for slide in soup.select(".swiper-slide:not(.swiper-slide-duplicate) .oc-item"):
        h3 = slide.select_one("h3")
        a_tag = slide.select_one("a[href]")
        if not h3 or not a_tag:
            continue

        title = " ".join(h3.get_text().split())
        href = a_tag.get("href", "")
        link = urljoin(_BASE, href) if href.startswith("/") else href
        if not link or link in seen:
            continue
        seen.add(link)

        # Date is in the link text: "Download (X KB) Publish Date: DD-MM-YYYY"
        link_text = a_tag.get_text(" ", strip=True)
        published_at = _parse_date(link_text)

        if published_at and published_at < _MIN_DATE:
            continue

        is_pdf = href.lower().endswith(".pdf")
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label="Agriculture News",
        ))

    return items


def _scrape_recent_initiatives(page) -> list[ScrapedItem]:
    """Paginate through the #tblRecruitment DataTable on /en/Recent."""
    items: list[ScrapedItem] = []
    seen: set[str] = set()

    while True:
        soup = BeautifulSoup(page.content(), "html.parser")
        for row in soup.select("#tblRecruitment tbody tr"):
            tds = row.find_all("td")
            if len(tds) < 4:
                continue

            title = " ".join(tds[1].get_text().split())
            published_at = _parse_date(tds[2].get_text(strip=True))

            if published_at and published_at < _MIN_DATE:
                continue

            pdf_links = [
                urljoin(_BASE, a["href"])
                for a in tds[3].find_all("a", href=True)
                if a["href"] and "/Documents/" in a["href"]
            ]
            if not pdf_links:
                continue

            for idx, pdf_url in enumerate(pdf_links):
                if pdf_url in seen:
                    continue
                seen.add(pdf_url)
                label = "What's New"
                if len(pdf_links) > 1:
                    label = f"What's New (PDF {idx + 1} of {len(pdf_links)})"
                items.append(ScrapedItem(
                    title=title,
                    link=pdf_url,
                    published_at=published_at,
                    is_pdf=True,
                    section_label=label,
                ))

        next_btn = page.query_selector("#tblRecruitment_next")
        if not next_btn or "disabled" in (next_btn.get_attribute("class") or ""):
            break
        next_btn.click()
        page.wait_for_timeout(1000)

    return items


def _crawl_sync() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            # Load homepage in English
            page.goto(_BASE, wait_until="networkidle", timeout=30000)
            with page.expect_navigation(wait_until="networkidle", timeout=20000):
                page.select_option("#ddlCulture", value="en")
            page.wait_for_timeout(1000)

            # Scrape News carousel from homepage
            home_soup = BeautifulSoup(page.content(), "html.parser")
            news_items = _scrape_news_carousel(home_soup)

            # Navigate to Recent Initiatives page
            page.goto(f"{_BASE}/en/Recent", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            recent_items = _scrape_recent_initiatives(page)

        finally:
            browser.close()

    return news_items + recent_items


async def crawl_agriculture(_config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, _crawl_sync)

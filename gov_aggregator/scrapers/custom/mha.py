from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.mha")

_BASE = "https://www.mha.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_PAGES = 20

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_SECTIONS = [
    ("/en/media/whats-new",                        "What's New"),
    ("/en/commoncontent/press-release-2026",        "Press Releases"),
]

def _parse_table(html: str, section_label: str, current_url: str) -> tuple[list[ScrapedItem], bool, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    wrapper = soup.select_one(".bg-wrapper.inner-body-wrapper")
    table = wrapper.find("table") if wrapper else soup.find("table")
    if not table:
        return items, stop, None

    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        title = cols[1].get_text(strip=True)
        if not title:
            continue

        link_tag = cols[2].find("a")
        href = (link_tag["href"] if link_tag else "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href) if href else _BASE

        date_text = cols[3].get_text(strip=True) if len(cols) > 3 else ""
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

    next_a = soup.select_one("li.pager__item--next a, ul.pagination li.next a")
    next_url = urljoin(current_url, next_a["href"].strip()) if next_a and next_a.get("href") else None

    return items, stop, next_url


async def crawl_mha(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        for path, section_label in _SECTIONS:
            url: str | None = f"{_BASE}{path}"
            pages = 0
            while url and pages < _MAX_PAGES:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.error("[mha] %s page %d failed: %s", section_label, pages + 1, exc)
                    break

                items, stop, next_url = _parse_table(resp.text, section_label, url)
                for item in items:
                    if item.link not in seen:
                        seen.add(item.link)
                        all_items.append(item)

                pages += 1
                logger.info("[mha] %s page %d: %d items", section_label, pages, len(items))

                if stop or not items:
                    break
                url = next_url

    logger.info("[mha] total: %d", len(all_items))
    return all_items

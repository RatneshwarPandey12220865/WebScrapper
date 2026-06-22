"""
Custom crawler for Ministry of External Affairs (MEA).

The site exposes a public AJAX API that returns HTML fragments directly:
  GET /FrontEnd/FetchPublicationListingData?publicationId=0&page=N&PageSize=10&...

Selectors (confirmed from Scrapy reference spider):
  - Items:  .pressRelesastBox
  - Title:  .pressTitle a  (text)
  - Date:   .date  (text)
  - Link:   .pressTitle a[href]
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.mea")

_BASE = "https://www.mea.gov.in"
_API = (
    "{base}/FrontEnd/FetchPublicationListingData"
    "?publicationId={pub_id}&KeywordName=&SortBy=new"
    "&page={page}&PageSize=10&DateRange=&IsInternalMEA=false&PLngId=1"
)
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_PAGES = 20

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,*/*;q=0.9",
    "Referer": _BASE,
}

# publicationId=0 returns all publications (only working ID on this endpoint)
_SECTION_LABEL = "Press Releases"
_PUB_ID = 0

_DATE_RE = re.compile(r"(\d{1,2})\s+(\w+),?\s*(\d{4})", re.I)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw.strip())
    if not m:
        return None
    month = _MONTHS.get(m.group(2).lower()[:3])
    if not month:
        return None
    try:
        return datetime(int(m.group(3)), month, int(m.group(1)), tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_boxes(html: str, section_label: str) -> tuple[list[ScrapedItem], bool]:
    """Returns (items, stop) where stop=True means we've hit items older than _MIN_DATE."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    for box in soup.select(".pressRelesastBox"):
        a = box.select_one(".pressTitle a") or box.select_one("a.whatsnew-title")
        if not a:
            continue
        title = a.get("title") or a.get_text(strip=True)
        if not title:
            continue

        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)

        date_el = box.select_one(".date") or box.select_one(".whatsnew-date")
        date_text = date_el.get_text(strip=True) if date_el else ""
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


async def crawl_mea(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=30,
    ) as client:
        for page_num in range(1, _MAX_PAGES + 1):
            url = _API.format(base=_BASE, pub_id=_PUB_ID, page=page_num)
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error("[mea] page %d failed: %s", page_num, exc)
                break

            items, stop = _parse_boxes(resp.text, _SECTION_LABEL)
            if not items:
                break

            for item in items:
                if item.link not in seen:
                    seen.add(item.link)
                    all_items.append(item)

            logger.info("[mea] page %d: %d items", page_num, len(items))

            if stop:
                break

    logger.info("[mea] total: %d", len(all_items))
    return all_items

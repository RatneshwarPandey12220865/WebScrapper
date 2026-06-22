from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.coal")

_BASE = "https://www.coal.nic.in"
_WHATS_NEW_URL = f"{_BASE}/whats-new"
_PRESS_URL = f"{_BASE}/media/press-release"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _next_page(soup: BeautifulSoup, page_url: str) -> str | None:
    a = soup.select_one("li.pager__item--next a")
    return urljoin(page_url, a["href"]) if a else None


def _parse_whats_new(html: str, page_url: str) -> tuple[list[ScrapedItem], str | None]:
    """
    Cols: SNo | Title | Download (link + file info) | Date
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(".layout-content table tbody tr"):
        cells = row.select("td")
        if len(cells) < 4:
            continue

        title = " ".join(cells[1].get_text().split())
        if not title:
            continue

        date_raw = " ".join(cells[3].get_text().split())
        published_at = _parse_date(date_raw)
        if published_at and published_at < _MIN_DATE:
            continue

        a = cells[2].find("a", href=True)
        link = urljoin(_BASE, a["href"]) if a else page_url
        is_pdf = link.lower().endswith(".pdf")

        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=is_pdf,
            published_at=published_at,
            section_label="What's New",
        ))

    return items, _next_page(soup, page_url)


def _parse_press(html: str, page_url: str) -> tuple[list[ScrapedItem], str | None]:
    """
    Cols: SNo | Title (with link) | Date | Download
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select("div#block-moc-content table tbody tr"):
        cells = row.select("td")
        if len(cells) < 3:
            continue

        title_cell = cells[1]
        a = title_cell.find("a", href=True)
        title = " ".join((a or title_cell).get_text().split())
        if not title:
            continue

        date_raw = " ".join(cells[2].get_text().split()) if len(cells) > 2 else ""
        published_at = _parse_date(date_raw)
        if published_at and published_at < _MIN_DATE:
            continue

        # Prefer a download link in the last column, fall back to title link
        dl_a = cells[-1].find("a", href=True) if len(cells) > 3 else None
        link_tag = dl_a or a
        link = urljoin(_BASE, link_tag["href"]) if link_tag else page_url
        is_pdf = link.lower().endswith(".pdf")

        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=is_pdf,
            published_at=published_at,
            section_label="Press Releases",
        ))

    return items, _next_page(soup, page_url)


async def _paginate(client: httpx.AsyncClient, start_url: str, parser) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    url: str | None = start_url

    while url:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("[coal] Failed %s: %s", url, exc)
            break

        page_items, url = parser(resp.text, str(resp.url))
        all_items.extend(page_items)

        # stop paginating early if all items were filtered (past min date)
        if not page_items:
            break

    return all_items


async def crawl_coal(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=30,
    ) as client:
        whats_new = await _paginate(client, _WHATS_NEW_URL, _parse_whats_new)
        press = await _paginate(client, _PRESS_URL, _parse_press)

    logger.info("[coal] What's New: %d, Press Releases: %d", len(whats_new), len(press))
    return whats_new + press

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.arunachal_pradesh")

_BASE = "https://arunachalpradesh.gov.in"
_NOTICES_URL = "https://arunachalpradesh.gov.in/advertiesment-notice.php"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Referer": "https://arunachalpradesh.gov.in/",
}


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%b-%Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _row_title(td) -> str:
    # Title may be inside a <div> child or directly as text
    div = td.select_one("div")
    if div:
        return " ".join(div.get_text().split())
    return " ".join(td.get_text().split())


def _row_link(td) -> str:
    a = td.select_one("a[href]")
    if not a:
        return ""
    href = (a.get("href") or "").strip()
    if not href:
        return ""
    return href if href.startswith("http") else urljoin(_BASE, href)


def _parse_notices(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(".table-container table tbody tr"):
        cells = row.select("td")
        if len(cells) < 4:
            continue

        title = _row_title(cells[1])
        if not title:
            continue

        link = _row_link(cells[3])
        if not link:
            continue

        date_raw = " ".join(cells[2].get_text().split())
        published_at = _parse_date(date_raw)

        if published_at and published_at < _MIN_DATE:
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Advertisements & Notices",
        ))

    return items


async def crawl_arunachal_pradesh(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=30,
        verify=False,
    ) as client:
        try:
            resp = await client.get(_NOTICES_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[arunachal-pradesh] fetch failed: %s", exc)
            return []

    items = _parse_notices(resp.text)
    logger.info("[arunachal-pradesh] %d items after %s filter", len(items), _MIN_DATE.date())
    return items

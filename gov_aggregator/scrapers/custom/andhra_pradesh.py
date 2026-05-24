from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.andhra_pradesh")

_BASE = "https://www.ap.gov.in"
_LATEST_NEWS_URL = "https://www.ap.gov.in/api/api/ApNewsLatestAnnouncements"
_ANNOUNCEMENTS_URL = "https://www.ap.gov.in/api/api/ApNewsAnnouncements"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "authorization": "Bearer null",
    "referer": "https://www.ap.gov.in/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:26], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _parse_latest_news(data: dict) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    for entry in data.get("dataList") or []:
        if not entry.get("isActive"):
            continue
        title = _clean(entry.get("title"))
        if not title:
            continue
        link = (entry.get("url") or "").strip()
        if not link:
            continue
        published_at = _parse_dt(entry.get("from"))
        if published_at and published_at < _MIN_DATE:
            continue
        items.append(ScrapedItem(
            title=title,
            link=link,
            summary=_clean(entry.get("description")) or None,
            published_at=published_at,
            is_pdf=False,
            section_label="Latest News",
        ))
    return items


def _parse_announcements(data: dict) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    for entry in data.get("dataList") or []:
        if not entry.get("isActive"):
            continue
        title = _clean(entry.get("title"))
        if not title:
            continue
        # PDF/document link is stored in imageBase64 field
        link = (entry.get("imageBase64") or "").strip()
        if not link:
            continue
        published_at = _parse_dt(entry.get("from"))
        if published_at and published_at < _MIN_DATE:
            continue
        items.append(ScrapedItem(
            title=title,
            link=link,
            summary=_clean(entry.get("description")) or None,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Announcements",
        ))
    return items


async def crawl_andhra_pradesh(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=30,
    ) as client:
        results: list[ScrapedItem] = []

        for url, parser, label in [
            (_LATEST_NEWS_URL, _parse_latest_news, "Latest News"),
            (_ANNOUNCEMENTS_URL, _parse_announcements, "Announcements"),
        ]:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("isSuccess"):
                    logger.warning("[andhra_pradesh] API returned isSuccess=false for %s", url)
                    continue
                items = parser(data)
                logger.info("[andhra_pradesh] %s: %d items", label, len(items))
                results.extend(items)
            except Exception as exc:
                logger.warning("[andhra_pradesh] Failed to fetch %s: %s", url, exc)

    return results

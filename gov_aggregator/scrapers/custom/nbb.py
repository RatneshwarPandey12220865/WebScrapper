from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.nbb")

_BASE = "https://nbb.gov.in"
_API_URL = "https://api.nbb.gov.in/api/Master/GetAllNewsList"


async def crawl_nbb(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30,
    ) as client:
        try:
            resp = await client.get(_API_URL)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("[nbb] Failed to fetch API: %s", exc)
            return []

    if not data.get("Success"):
        logger.error("[nbb] API returned Success=false")
        return []

    items: list[ScrapedItem] = []
    for category in data.get("Data", []):
        for entry in category.get("contentSubmission", []):
            title = (entry.get("caption") or "").strip()
            if not title:
                continue

            raw_url = entry.get("dmsLocationPathtbl") or ""
            link = urljoin(_BASE, raw_url) if raw_url else _BASE
            is_pdf = link.lower().endswith(".pdf")

            items.append(ScrapedItem(
                title=title,
                link=link,
                is_pdf=is_pdf,
                section_label="News & Events",
            ))

    logger.info("[nbb] %d items fetched", len(items))
    return items

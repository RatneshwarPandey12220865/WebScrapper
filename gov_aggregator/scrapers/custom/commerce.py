from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.commerce")

_BASE = "https://www.commerce.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Authorization": "Basic YXBpYWRtaW46QVBJZGNAMjAyNQ==",
    "Origin": "https://www.commerce.gov.in",
    "Referer": "https://www.commerce.gov.in/",
}

# (endpoint, date_field, doc_field, section_label)
_ENDPOINTS = [
    ("/ministryofcommerce/api/v1/whats_new",        "field_date",  "field_document",    "What's New"),
    ("/ministryofcommerce/api/v1/press-release-pib", "field_dates", "field_upload_file", "Press Releases"),
]


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%d/%b/%Y", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def crawl_commerce(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        for path, date_field, doc_field, section_label in _ENDPOINTS:
            url = f"{_BASE}{path}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("[commerce] fetch failed %s: %s", path, exc)
                continue

            records = data.get("data") if isinstance(data, dict) else data
            if not isinstance(records, list):
                records = []
            logger.info("[commerce] %s: %d records", path, len(records))

            for rec in records:
                title = (rec.get("title") or "").strip()
                if not title:
                    continue

                doc = (rec.get(doc_field) or "").strip()
                if not doc:
                    continue
                link = doc if doc.startswith("http") else f"{_BASE}{doc}"

                published_at = _parse_date(rec.get(date_field))
                if published_at and published_at < _MIN_DATE:
                    continue

                category = (rec.get("field_select_press_release") or section_label).strip()

                all_items.append(ScrapedItem(
                    title=title,
                    link=link,
                    published_at=published_at,
                    is_pdf=link.lower().endswith(".pdf"),
                    section_label=category,
                ))

    logger.info("[commerce] total: %d items", len(all_items))
    return all_items

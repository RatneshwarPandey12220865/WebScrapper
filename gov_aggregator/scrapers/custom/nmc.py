from __future__ import annotations

from datetime import datetime, timezone

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

API_URL = "https://www.nmc.org.in/MCIRest/open/getDataFromService?service=getLatestNewsNmc"
DOC_BASE = "https://www.nmc.org.in/MCIRest/open/getDocument?path="
CUTOFF = datetime(2025, 10, 1, tzinfo=timezone.utc)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nmc.org.in/all-news/",
}


def _parse_date(raw: str | None) -> datetime | None:
    """Parse NMC date format: 'DD/MM/YYYY'"""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _build_link(record: dict) -> tuple[str, bool]:
    """
    Return (link_url, is_pdf).
    Priority: docUpload (PDF on NMC server) → link (external URL).
    """
    doc_path = (record.get("docUpload") or "").strip()
    if doc_path:
        return f"{DOC_BASE}{doc_path}", True

    external = (record.get("link") or "").strip()
    if external:
        return external, external.lower().endswith(".pdf")

    return "", False


def _shape_record(record: dict) -> ScrapedItem | None:
    title = (record.get("pageName") or "").strip()
    if not title:
        return None

    published_at = _parse_date(record.get("updatedDate"))

    # Apply cutoff — skip items before July 2025
    if published_at and published_at < CUTOFF:
        return None

    link, is_pdf = _build_link(record)
    if not link:
        return None

    return ScrapedItem(
        title=title,
        link=link,
        summary=None,
        published_at=published_at,
        is_pdf=is_pdf,
        section_label="All News",
    )


async def crawl_nmc(config: SiteConfig) -> list[ScrapedItem]:
    """
    Fetches all NMC news items directly from the internal JSON API:
    GET https://www.nmc.org.in/MCIRest/open/getDataFromService?service=getLatestNewsNmc
    Returns all items (currently ~967) without pagination.
    """
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30.0,
        verify=False,
    ) as client:
        try:
            response = await client.get(API_URL)
            response.raise_for_status()
            records = response.json()
        except Exception as exc:
            print(f"[nmc] Failed to fetch API: {exc}")
            return []

    for record in records:
        item = _shape_record(record)
        if item is not None:
            items.append(item)

    # Sort newest first by published_at
    items.sort(
        key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items

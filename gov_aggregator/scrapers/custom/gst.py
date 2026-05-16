from __future__ import annotations

from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

API_BASE      = "https://services.gst.gov.in/master/advisories/updated"
DETAIL_BASE   = "https://services.gst.gov.in/services/advisoryandreleases/read"
TARGET_YEARS  = [2025, 2026]
CUTOFF        = datetime(2025, 10, 1, tzinfo=timezone.utc)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://services.gst.gov.in/services/advisory/advisoryandreleases",
    "Origin": "https://services.gst.gov.in",
}


def _parse_date(raw: str) -> datetime | None:
    """Parse MM/DD/YYYY date format from API response."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _extract_pdf_from_content(html_content: str) -> str | None:
    """Extract first PDF link from HTML content field."""
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().endswith(".pdf"):
            return href
    return None


def _build_link(record: dict) -> tuple[str, bool]:
    """
    Return (link_url, is_pdf).
    Priority: linkURl (external) → PDF in content → detail page fallback.
    """
    # 1. External link
    if record.get("IsExternal") == "Y":
        url = (record.get("linkURl") or "").strip()
        if url:
            return url, url.lower().endswith(".pdf")

    # 2. PDF embedded in content HTML
    pdf = _extract_pdf_from_content(record.get("content") or "")
    if pdf:
        return pdf, True

    # 3. Detail page fallback
    record_id = record.get("id", "")
    return f"{DETAIL_BASE}/{record_id}", False


def _get_category_from_module(module: str) -> str:
    """Map module to category."""
    module_lower = (module or "").lower()
    if module_lower == "registration":
        return "notification"
    if module_lower == "returns":
        return "circular"
    if module_lower == "refunds":
        return "circular"
    if module_lower == "payments":
        return "notification"
    if module_lower == "e-invoice":
        return "notification"
    if module_lower == "others":
        return "news"
    return "circular"


def _shape_record(record: dict) -> ScrapedItem | None:
    title = (record.get("title") or "").strip()
    if not title:
        return None

    published_at = _parse_date(record.get("date") or "")

    # Apply cutoff — skip items before October 2025
    if published_at and published_at < CUTOFF:
        return None

    link, is_pdf = _build_link(record)
    if not link:
        return None

    module = (record.get("module") or "").strip()
    section_label = f"GST — {module}" if module else "GST News & Updates"
    category = _get_category_from_module(module)

    # Brief summary from content (strip HTML tags)
    content_html = record.get("content") or ""
    summary = BeautifulSoup(content_html, "html.parser").get_text(" ", strip=True)[:400] if content_html else None

    return ScrapedItem(
        title=title,
        link=link,
        summary=summary,
        published_at=published_at,
        is_pdf=is_pdf,
        section_label=section_label,
    )


async def crawl_gst(config: SiteConfig) -> list[ScrapedItem]:
    """
    Fetches GST portal advisories for 2025 and 2026 from the API:
    GET https://services.gst.gov.in/master/advisories/updated/{year}
    """
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30.0,
    ) as client:
        for year in TARGET_YEARS:
            url = f"{API_BASE}/{year}"
            try:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                print(f"[gst] Failed to fetch year {year}: {exc}")
                continue

            records = payload.get("data", [])
            for record in records:
                item = _shape_record(record)
                if item is not None:
                    items.append(item)

    # Sort newest first
    items.sort(
        key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items

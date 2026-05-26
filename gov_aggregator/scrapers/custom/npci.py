from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.npci")

_BASE = "https://www.npci.org.in"
_API = "/api/press-release-details"

# Press Releases: 2026+; Media Coverage: 2025+
_MIN_DATE_PR = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MIN_DATE_MC = datetime(2025, 1, 1, tzinfo=timezone.utc)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.npci.org.in/",
}

_SECTIONS = [
    # (tabSlug, section_label, min_date, years_to_fetch)
    ("press-releases", "Press Releases", _MIN_DATE_PR, [2026, 2025]),
    ("media-coverage", "Media Coverage", _MIN_DATE_MC, [2026, 2025, 2024]),
]


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _fetch_section(
    client: httpx.AsyncClient,
    tab_slug: str,
    section_label: str,
    min_date: datetime,
    years: list[int],
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    for year in years:
        page = 1
        while True:
            params = {
                "tabSlug": tab_slug,
                "year": str(year),
                "sortOrder": "desc",
                "page": str(page),
                "pageSize": "20",
                "slug": "press-release",
                "locale": "en",
            }
            try:
                resp = await client.get(_BASE + _API, params=params)
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("[npci] fetch failed tab=%s year=%d page=%d: %s", tab_slug, year, page, exc)
                break

            data = resp.json().get("data", {})
            results = data.get("results", [])
            total_pages = data.get("totalPages", 1)

            for r in results:
                published_at = _parse_date(r.get("createdAt"))
                if published_at and published_at < min_date:
                    continue

                title = (r.get("title") or "").strip()
                if not title:
                    continue

                media = r.get("media") or {}
                rel_url = (media.get("url") or "").strip()
                if not rel_url:
                    continue
                link = rel_url if rel_url.startswith("http") else urljoin(_BASE, rel_url)

                is_pdf = link.lower().endswith(".pdf") or r.get("mediaType") == "pdf"

                items.append(ScrapedItem(
                    title=title,
                    link=link,
                    published_at=published_at,
                    is_pdf=is_pdf,
                    section_label=section_label,
                ))

            logger.info("[npci] tab=%s year=%d page=%d: %d results", tab_slug, year, page, len(results))

            if page >= total_pages:
                break
            page += 1

    return items


async def crawl_npci(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        for tab_slug, section_label, min_date, years in _SECTIONS:
            section_items = await _fetch_section(client, tab_slug, section_label, min_date, years)
            all_items.extend(section_items)

    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in all_items:
        if item.link not in seen:
            seen.add(item.link)
            unique.append(item)

    logger.info("[npci] total: %d items", len(unique))
    return unique

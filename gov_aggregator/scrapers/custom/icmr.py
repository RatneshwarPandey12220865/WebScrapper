from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.icmr")

_BASE = "https://www.icmr.gov.in"
_WHATS_NEW_URL = f"{_BASE}/whats-new"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_PAGES = 20

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _parse_page(html: str, current_url: str) -> tuple[list[ScrapedItem], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for item in soup.select(".inner__body-content .colorBox__list"):
        category = item.select_one(".title")
        details = item.select_one(".details")

        title = (details.get_text(strip=True) if details else "") or (category.get_text(strip=True) if category else "")
        if not title:
            continue

        href = (item.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=None,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="What's New",
            summary=category.get_text(strip=True) if category else None,
        ))

    next_a = soup.select_one('a[aria-label="Go to Next page"]')
    next_url = urljoin(current_url, next_a["href"].strip()) if next_a and next_a.get("href") else None

    return items, next_url


async def crawl_icmr(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        url: str | None = _WHATS_NEW_URL
        pages = 0
        while url and pages < _MAX_PAGES:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error("[icmr] page %d failed: %s", pages + 1, exc)
                break

            items, next_url = _parse_page(resp.text, url)
            if not items:
                break

            for item in items:
                if item.link not in seen:
                    seen.add(item.link)
                    all_items.append(item)

            pages += 1
            logger.info("[icmr] page %d: %d items", pages, len(items))
            url = next_url

    logger.info("[icmr] total: %d", len(all_items))
    return all_items

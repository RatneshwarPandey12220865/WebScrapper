from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.chhattisgarh")

_BASE_URL = "https://cgstate.gov.in/en/all-notification"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://cgstate.gov.in/en/all-notification",
}


def _parse_date(raw: str | None) -> datetime | None:
    # Format: "14 May, 2026"
    for fmt in ("%d %b, %Y", "%d %B, %Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_page(html: str) -> tuple[list[ScrapedItem], bool]:
    """Returns (items, stop_early). stop_early=True when an item is before _MIN_DATE."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop_early = False

    for card in soup.select(".card-notification"):
        title_el = card.select_one(".notification-heading p")
        date_el = card.select_one(".notification-date-box .date p")
        cat_el = card.select_one(".category-name")
        link_el = card.select_one('a[href*="file-download"]')

        title = " ".join(title_el.get_text().split()) if title_el else ""
        if not title:
            continue

        link = (link_el.get("href") or "").strip() if link_el else ""
        if not link:
            continue  # no document attached — skip

        raw_date = date_el.get_text(strip=True) if date_el else None
        published_at = _parse_date(raw_date)

        if published_at and published_at < _MIN_DATE:
            stop_early = True
            continue

        section = " ".join(cat_el.get_text().split()) if cat_el else "Notifications"

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf") or "file-download" in link,
            section_label=section,
        ))

    return items, stop_early


async def crawl_chhattisgarh(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        page = 1
        while True:
            url = f"{_BASE_URL}?page={page}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("[chhattisgarh] fetch failed page %d: %s", page, exc)
                break

            items, stop_early = _parse_page(resp.text)
            all_items.extend(items)
            logger.info("[chhattisgarh] page %d: %d items", page, len(items))

            if stop_early or not items:
                break

            page += 1

    logger.info("[chhattisgarh] total: %d items", len(all_items))
    return all_items

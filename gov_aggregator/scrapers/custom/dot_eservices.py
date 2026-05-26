from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dot_eservices")

_BASE = "https://eservices.dot.gov.in"
_URL = f"{_BASE}/circular-notifications-others"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://eservices.dot.gov.in/",
}


def _parse_date(raw: str | None) -> datetime | None:
    # Format: "15/05/2026"
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_table(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("div#block-telcomeservices-content table tbody tr")
    logger.info("[dot_eservices] %d rows found", len(rows))

    items: list[ScrapedItem] = []
    for row in rows:
        tds = row.select("td")
        if len(tds) < 6:
            continue

        category = tds[1].get_text(strip=True) or "Circular"
        title = " ".join(tds[4].get_text().split())
        if not title:
            continue

        a = tds[5].select_one("a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)

        raw_date = tds[6].get_text(strip=True) if len(tds) > 6 else None
        published_at = _parse_date(raw_date)
        if published_at and published_at < _MIN_DATE:
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label=category,
        ))

    return items


async def crawl_dot_eservices(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[dot_eservices] fetch failed: %s", exc)
            return []

    items = _parse_table(resp.text)
    logger.info("[dot_eservices] total: %d items", len(items))
    return items

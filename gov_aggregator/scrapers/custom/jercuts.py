from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.jercuts")

_URL = "https://jercuts.gov.in/whats-new/"
_BASE = "https://jercuts.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://jercuts.gov.in/",
}


def _parse_date(raw: str | None) -> datetime | None:
    raw = (raw or "").strip()
    if not raw or raw == "-":
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def crawl_jercuts(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[jercuts] fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("#whats_new_table tbody tr")
    logger.info("[jercuts] %d rows found", len(rows))

    items: list[ScrapedItem] = []
    for row in rows:
        tds = row.select("td")
        if len(tds) < 4:
            continue

        raw_date = tds[1].get_text(strip=True)
        published_at = _parse_date(raw_date)
        if published_at and published_at < _MIN_DATE:
            continue

        title = " ".join(tds[2].get_text().split())
        if not title:
            continue

        a = tds[3].select_one("a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="What's New",
        ))

    logger.info("[jercuts] total: %d items", len(items))
    return items

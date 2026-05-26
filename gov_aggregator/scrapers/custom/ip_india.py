from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.ip_india")

_BASE = "https://ipindia.gov.in"
_URL = "https://ipindia.gov.in/dynamic/news-updates"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://ipindia.gov.in/",
}


def _parse_date(raw: str | None) -> datetime | None:
    # Format: "23-05-2026" (DD-MM-YYYY)
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%B %d, %Y"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def crawl_ip_india(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[ip_india] fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select(".aboutSectionArea table tbody tr")
    logger.info("[ip_india] %d rows found", len(rows))

    items: list[ScrapedItem] = []
    for row in rows:
        tds = row.select("td")
        if len(tds) < 4:
            continue

        raw_date = tds[1].get_text(strip=True)
        published_at = _parse_date(raw_date)
        if published_at and published_at < _MIN_DATE:
            continue

        category = " ".join(tds[2].get_text().split()) or "IP News"

        title_td = tds[3]
        title = " ".join(title_td.get_text().split())
        if not title:
            continue

        # Prefer PDF link (td[5]) over detail page link (td[4])
        pdf_a = tds[4].select_one("a") if len(tds) > 4 else None
        detail_a = title_td.select_one("a")

        if pdf_a and pdf_a.get("href"):
            href = pdf_a["href"].strip()
            link = href if href.startswith("http") else urljoin(_BASE, href)
            is_pdf = link.lower().endswith(".pdf") or "download" in link.lower()
        elif detail_a and detail_a.get("href"):
            href = detail_a["href"].strip()
            link = href if href.startswith("http") else urljoin(_BASE, href)
            is_pdf = link.lower().endswith(".pdf")
        else:
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label=category,
        ))

    logger.info("[ip_india] total: %d items", len(items))
    return items

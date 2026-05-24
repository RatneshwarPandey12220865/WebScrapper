from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dopt")

_BASE = "https://dopt.gov.in"
_START = "https://dopt.gov.in/whats-new"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%B %d, %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_page(html: str, page_url: str) -> tuple[list[ScrapedItem], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for row in soup.select(".view-content table tr"):
        if row.select_one("th"):
            continue
        cols = row.select("td")
        if len(cols) < 2:
            continue
        a = cols[1].select_one("a")
        if not a:
            continue
        title = " ".join(a.get_text().split())
        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)
        raw_date = cols[2].get_text().strip() if len(cols) > 2 else None
        published_at = _parse_date(raw_date)
        if published_at and published_at < _MIN_DATE:
            continue
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="What's New",
        ))

    next_a = soup.select_one("li.pager-next a") or soup.select_one("li.pager__item--next a")
    next_url = urljoin(page_url, next_a["href"]) if next_a else None
    return items, next_url


async def crawl_dopt(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    url: str | None = _START

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        while url:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                items, url = _parse_page(resp.text, url)
                all_items.extend(items)
                if not items:
                    break
            except Exception as exc:
                logger.warning("[dopt] fetch failed %s: %s", url, exc)
                break

    logger.info("[dopt] total: %d items", len(all_items))
    return all_items

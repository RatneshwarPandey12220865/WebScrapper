from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.darpg")

_BASE = "https://darpg.gov.in"
_LIST_URL = "https://darpg.gov.in/en/whats-new"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CONCURRENCY = 6
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%B %d, %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_pdf(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("a[href$='.pdf']") or soup.select_one("span.file a")
    if not a:
        return None
    href = (a.get("href") or "").strip()
    return href if href.startswith("http") else urljoin(_BASE, href)


async def _fetch_detail(client: httpx.AsyncClient, sem: asyncio.Semaphore, detail_url: str, title: str, date_raw: str | None) -> ScrapedItem | None:
    async with sem:
        try:
            resp = await client.get(detail_url, timeout=20)
            resp.raise_for_status()
            pdf_url = _extract_pdf(resp.text, detail_url)
            link = pdf_url or detail_url
            published_at = _parse_date(date_raw)
            if published_at and published_at < _MIN_DATE:
                return None
            return ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                is_pdf=bool(pdf_url),
                section_label="What's New",
            )
        except Exception as exc:
            logger.warning("[darpg] detail fetch failed %s: %s", detail_url, exc)
            return None


def _parse_listing(html: str, page_url: str) -> tuple[list[tuple[str, str, str | None]], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[tuple[str, str, str | None]] = []

    for row in soup.select(".view-home-tabs table.views-table tbody tr, .view-content table.views-table tbody tr"):
        a = row.select_one(".views-field-title a")
        if not a:
            continue
        title = " ".join(a.get_text().split())
        href = (a.get("href") or "").strip()
        detail_url = href if href.startswith("http") else urljoin(_BASE, href)
        date_td = row.select_one(".views-field-created, .views-field-field-date")
        date_raw = date_td.get_text().strip() if date_td else None
        entries.append((title, detail_url, date_raw))

    next_a = soup.select_one("li.pager-next a") or soup.select_one("li.pager__item--next a")
    next_url = urljoin(page_url, next_a["href"]) if next_a else None
    return entries, next_url


async def crawl_darpg(_config: SiteConfig) -> list[ScrapedItem]:
    all_entries: list[tuple[str, str, str | None]] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        # Collect all listing entries across pages
        url: str | None = _LIST_URL
        while url:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                entries, url = _parse_listing(resp.text, url)
                all_entries.extend(entries)
                if not entries:
                    break
            except Exception as exc:
                logger.warning("[darpg] listing fetch failed %s: %s", url, exc)
                break

        logger.info("[darpg] %d listing entries found", len(all_entries))

        # Fetch detail pages concurrently
        sem = asyncio.Semaphore(_CONCURRENCY)
        results = await asyncio.gather(*[
            _fetch_detail(client, sem, detail_url, title, date_raw)
            for title, detail_url, date_raw in all_entries
        ])

    items = [r for r in results if r is not None]
    logger.info("[darpg] total: %d items", len(items))
    return items

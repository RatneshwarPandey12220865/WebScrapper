from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.power")

_BASE = "https://powermin.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://powermin.gov.in/",
}

_CIRCULAR_URL = "https://powermin.gov.in/en/circular"


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_page(html: str, section_label: str, page_url: str) -> tuple[list[ScrapedItem], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for row in soup.select(".view-id-circular .views-table tbody tr"):
        subject_td = row.select_one("td.views-field-title-1")
        title = " ".join(subject_td.get_text().split()) if subject_td else ""
        if not title:
            continue

        date_span = row.select_one("td.views-field-field-date span[content]")
        raw_date = (date_span.get("content") or date_span.get_text()).strip() if date_span else None
        published_at = _parse_date(raw_date)
        if published_at and published_at < _MIN_DATE:
            continue

        division_td = row.select_one("td.views-field-field-division")
        label = " ".join(division_td.get_text().split()) if division_td else section_label
        label = label or section_label

        a = row.select("td")[-1].select_one("a") if row.select("td") else None
        if not a:
            continue
        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label=label,
        ))

    next_a = soup.select_one(".pager-next a") or soup.select_one("li.pager__item--next a")
    next_url = urljoin(page_url, next_a["href"]) if next_a else None
    return items, next_url


async def crawl_power(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        # warm up session
        try:
            await client.get(_BASE)
        except Exception:
            pass

        url: str | None = _CIRCULAR_URL
        while url:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                items, url = _parse_page(resp.text, "Circulars", url)
                all_items.extend(items)
                logger.info("[power] Circulars: %d items, next=%s", len(items), url)
                if not items:
                    break
            except Exception as exc:
                logger.warning("[power] fetch failed %s: %s", url, exc)
                break

    logger.info("[power] total: %d items", len(all_items))
    return all_items

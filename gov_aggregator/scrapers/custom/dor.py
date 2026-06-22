from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dor")

_BASE = "https://dor.gov.in"
_WHATS_NEW_URL = f"{_BASE}/whats-new"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_PAGES = 20

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_DATE_RE = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})|(\d{1,2})\s+(\w+)\s+(\d{4})", re.I)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    m = _DATE_RE.search(raw)
    if not m:
        return None
    if m.group(1):  # DD/MM/YYYY or DD-MM-YYYY
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
    else:  # DD Month YYYY
        d = int(m.group(4))
        mo = _MONTHS.get(m.group(5).lower()[:3], 0)
        y = int(m.group(6))
    if not mo:
        return None
    try:
        return datetime(y, mo, d, tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_whats_new(html: str, current_url: str) -> tuple[list[ScrapedItem], bool, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    for row in soup.select(".view-whats-new-management tbody tr"):
        title_el = row.select_one(".views-field-title a")
        title_td = row.select_one(".views-field-title")
        title = title_el.get_text(strip=True) if title_el else (title_td.get_text(strip=True) if title_td else "")
        if not title:
            continue

        href = (title_el.get("href") or "") if title_el else ""
        link = href if href.startswith("http") else urljoin(_BASE, href) if href else _WHATS_NEW_URL

        # Date: <time datetime="2026-04-17T12:00:00Z"> inside .views-field-whats-new-date
        time_el = row.select_one(".views-field-field-whats-new-date time")
        dt_attr = time_el.get("datetime") if time_el else None
        if dt_attr:
            try:
                published_at: datetime | None = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
            except ValueError:
                published_at = _parse_date(time_el.get_text(strip=True))
        else:
            date_el = row.select_one(".views-field-field-whats-new-date")
            published_at = _parse_date(date_el.get_text(strip=True) if date_el else "")

        if published_at and published_at < _MIN_DATE:
            stop = True
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="What's New",
        ))

    # Pagination: href="?page=1" — must join with current page URL
    next_a = soup.select_one("li.pager__item--next a")
    next_url = None
    if next_a and next_a.get("href"):
        next_url = urljoin(current_url, next_a["href"].strip())

    return items, stop, next_url


async def crawl_dor(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=30,
    ) as client:
        url: str | None = _WHATS_NEW_URL
        pages = 0
        while url and pages < _MAX_PAGES:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error("[dor] Failed to fetch %s: %s", url, exc)
                break

            items, stop, next_url = _parse_whats_new(resp.text, url)
            all_items.extend(items)
            pages += 1
            logger.info("[dor] What's New page %d: %d items", pages, len(items))

            if stop or not items:
                break
            url = next_url

    logger.info("[dor] total: %d", len(all_items))
    return all_items

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.pharmexcil")

_BASE = "https://pharmexcil.com"
_HOME_URL = f"{_BASE}/"
_NEWS_URL = f"{_BASE}/news_article"
_GOV_NOTIF_URL = f"{_BASE}/government-notifications-circulars"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Omit Accept-Encoding — this site returns garbled content when brotli is requested
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Matches "04 06 2026" or "17 Oct 2024" style dates
_DATE_RE = re.compile(r"\b(\d{1,2})\s+(\w+)\s+(\d{4})\b")
_MONTHS = {
    "01": 1, "02": 2, "03": 3, "04": 4, "05": 5, "06": 6,
    "07": 7, "08": 8, "09": 9, "10": 10, "11": 11, "12": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw.strip())
    if not m:
        return None
    day, mon_raw, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    month = _MONTHS.get(mon_raw[:3])
    if not month:
        return None
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _clean(s: str) -> str:
    return " ".join(s.split())


def _parse_home(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    # Latest Circulars tab (#curriculam)
    for row in soup.select("#curriculam table tr"):
        a = row.select_one("a.scrolingtext")
        if not a:
            continue
        title = _clean(a.get_text())
        if not title:
            continue
        link = urljoin(_BASE, a.get("href", ""))
        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Latest Circulars",
        ))

    # Trade Enquiries tab (#semester)
    for row in soup.select("#semester table tr"):
        a = row.select_one("a.preview") or row.select_one("a")
        if not a:
            continue
        title = _clean(a.get_text())
        if not title:
            continue
        link = urljoin(_BASE, a.get("href", ""))
        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Trade Enquiries",
        ))

    return items


def _parse_news(html: str) -> list[ScrapedItem]:
    """Parse /news_article — cols: SNo | Date | Title | Source | Region"""
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select("tr.griderow1, tr.griderow2"):
        cells = row.select("td")
        if len(cells) < 3:
            continue

        date_raw = _clean(cells[1].get_text())
        published_at = _parse_date(date_raw)
        if published_at and published_at < _MIN_DATE:
            continue

        title_cell = cells[2]
        a = title_cell.find("a", href=True)
        title = _clean(a.get_text() if a else title_cell.get_text())
        if not title:
            continue

        link = urljoin(_BASE, a["href"]) if a else _NEWS_URL
        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=link.lower().endswith(".pdf"),
            published_at=published_at,
            section_label="News Articles",
        ))

    return items


def _parse_gov_notif(html: str) -> list[ScrapedItem]:
    """Parse /government-notifications-circulars — cols: SNo | Date | Ministry | Category | Ref No | Subject"""
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select("tr.griderow1, tr.griderow2"):
        cells = row.select("td")
        if len(cells) < 6:
            continue

        date_raw = _clean(cells[1].get_text())
        published_at = _parse_date(date_raw)
        if published_at and published_at < _MIN_DATE:
            continue

        subject_cell = cells[5]
        a = subject_cell.find("a", href=True)
        title = _clean(a.get_text() if a else subject_cell.get_text())
        if not title:
            continue

        link = urljoin(_BASE, a["href"]) if a else _GOV_NOTIF_URL
        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=link.lower().endswith(".pdf"),
            published_at=published_at,
            section_label="Government Notifications",
        ))

    return items


async def crawl_pharmexcil(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=30,
    ) as client:
        all_items: list[ScrapedItem] = []

        for url, parser in (
            (_HOME_URL, _parse_home),
            (_NEWS_URL, _parse_news),
            (_GOV_NOTIF_URL, _parse_gov_notif),
        ):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                items = parser(resp.text)
                all_items.extend(items)
                logger.info("[pharmexcil] %s → %d items", url, len(items))
            except Exception as exc:
                logger.error("[pharmexcil] Failed %s: %s", url, exc)

    return all_items

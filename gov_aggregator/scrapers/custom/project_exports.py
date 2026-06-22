from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.project_exports")

_BASE = "https://projectexports.com"
_HOME_URL = f"{_BASE}/"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Matches "3rd Dec 2026", "11th Aug 2023", "04 Jun 2026"
_DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(\w{3,9})\s+(\d{4})\b", re.I)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Tab pane ID → section label
_PANES = {
    "menu2": "Research Articles",
    "menu6": "News",
    "menu1": "Members",
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw.strip())
    if not m:
        return None
    day, mon_raw, year = int(m.group(1)), m.group(2).lower()[:3], int(m.group(3))
    month = _MONTHS.get(mon_raw)
    if not month:
        return None
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_home(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for pane_id, section_label in _PANES.items():
        pane = soup.find(id=pane_id)
        if not pane:
            continue

        for news_div in pane.select(".news"):
            a = news_div.select_one("a[href]")
            if not a:
                continue

            link = urljoin(_BASE, a["href"])

            # Title: first p in col-md-9 that is not "Read More"
            paras = news_div.select(".col-md-9 p")
            title = ""
            date_text = ""
            for p in paras:
                text = p.get_text(strip=True)
                if not text or text.lower() == "read more":
                    continue
                if _DATE_RE.search(text):
                    date_text = text
                elif not title:
                    title = text

            if not title:
                continue

            published_at = _parse_date(date_text)
            if published_at and published_at < _MIN_DATE:
                continue

            items.append(ScrapedItem(
                title=title,
                link=link,
                is_pdf=link.lower().endswith(".pdf"),
                published_at=published_at,
                section_label=section_label,
            ))

    logger.info("[project_exports] %d items parsed", len(items))
    return items


async def crawl_project_exports(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=30,
    ) as client:
        try:
            resp = await client.get(_HOME_URL)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("[project_exports] Failed to fetch homepage: %s", exc)
            return []

    return _parse_home(resp.text)

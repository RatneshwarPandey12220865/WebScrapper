from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.tea_board")

_URL = "https://www.teaboard.gov.in/LATEST-NEWS"
_BASE = "https://www.teaboard.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.teaboard.gov.in/",
}

_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")


def _parse_date(raw: str | None) -> datetime | None:
    m = _DATE_RE.search(raw or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def crawl_tea_board(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[tea_board] fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    container = soup.select_one("#contn_contnaar")
    if not container:
        logger.warning("[tea_board] #contn_contnaar not found")
        return []

    rows = container.select("table tr.data_table_row_light, table tr.data_table_row_dark")
    logger.info("[tea_board] %d rows found", len(rows))

    items: list[ScrapedItem] = []
    for row in rows:
        td = row.select_one("td:first-child")
        if not td:
            continue

        # Date is in a span inside .divCss
        div = td.select_one(".divCss")
        raw_date = div.get_text() if div else ""
        published_at = _parse_date(raw_date)
        if published_at and published_at < _MIN_DATE:
            continue

        # Remove the .divCss from text to get clean title
        if div:
            div.decompose()
        raw_title = td.get_text(separator=" ")
        # Title may start with "Category:- " prefix
        if ":-" in raw_title:
            section_part, _, title_part = raw_title.partition(":-")
            section = section_part.strip()
            title = title_part.strip()
        else:
            section = "Latest News"
            title = raw_title.strip()
        title = " ".join(title.split())

        if not title:
            continue

        a = row.select_one("a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf") or "/pdf/" in link.lower(),
            section_label=section,
        ))

    logger.info("[tea_board] total: %d items", len(items))
    return items

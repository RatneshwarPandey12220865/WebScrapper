from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_NEWS_URL = "https://coffeeboard.gov.in/News.aspx"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")


def _clean(v: str | None) -> str:
    return " ".join((v or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_datalist(soup: BeautifulSoup, list_id: str, section_label: str) -> list[ScrapedItem]:
    """
    Parse DataList1 or DataList2 from coffeeboard.gov.in/News.aspx.

    Each row has:
      - span[id^="DataList_Label1_"] → date  (DD/MM/YYYY)
      - a.arch[href="javascript:__doPostBack(...)"] → title  (no real URL)

    Since items have no individual URLs, the News page itself is used as the
    link so the button in the UI opens the correct page.
    """
    container = soup.select_one(f"#{list_id}")
    if not container:
        return []

    items: list[ScrapedItem] = []

    for row in container.select("tr"):
        # Date span: id like "DataList1_Label1_0"
        date_span = row.select_one(f'span[id^="{list_id}_Label1_"]')
        date_text = _clean(date_span.get_text()) if date_span else ""
        published_at = _parse_date(date_text)

        # Skip items older than MIN_DATE
        if published_at and published_at < _MIN_DATE:
            continue

        # Title from the anchor
        link_tag = row.select_one("a.arch")
        if not link_tag:
            continue
        title = _clean(link_tag.get_text())
        if not title:
            continue

        items.append(ScrapedItem(
            title=title,
            link="",              # Site has no per-item URLs (ASP.NET postbacks only)
            published_at=published_at,
            is_pdf=False,
            section_label=section_label,
        ))

    return items


async def crawl_coffee_board(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
    ) as client:
        resp = await client.get(_NEWS_URL)
        if resp.status_code != 200:
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = _parse_datalist(soup, "DataList1", "News")
    items += _parse_datalist(soup, "DataList2", "Coffee News")
    return items

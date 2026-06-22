from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://cercind.gov.in"
_WHATS_NEW_URL = f"{_BASE}/viewall.html"
_ORDERS_URL = f"{_BASE}/recent_orders.html"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clean(value: str) -> str:
    return " ".join(value.split())


def _parse_whats_new(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for li in soup.select(".container.coc-mid ul.list-group li.list-group-item"):
        a = li.find("a", href=True)
        title = _clean("".join(li.stripped_strings))
        if not title:
            continue

        link = urljoin(_BASE, a["href"]) if a else ""
        is_pdf = link.lower().endswith(".pdf")

        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=is_pdf,
            section_label="What's New",
        ))

    return items


def _parse_orders(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    rows = soup.select(".container.coc-mid table tr")[1:]  # skip header
    for row in rows:
        cells = row.select("td")
        if len(cells) < 6:
            continue

        subject = _clean(cells[2].get_text())
        if not subject:
            continue

        a = cells[2].find("a", href=True)
        link = urljoin(_BASE, a["href"]) if a else ""
        is_pdf = link.lower().endswith(".pdf")

        date_of_order = _clean(cells[3].get_text())
        published_at = _parse_date(date_of_order)

        if published_at and published_at < _MIN_DATE:
            continue

        items.append(ScrapedItem(
            title=subject,
            link=link,
            is_pdf=is_pdf,
            published_at=published_at,
            section_label="Orders",
        ))

    return items


async def crawl_cerc(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30,
        verify=False,
    ) as client:
        all_items: list[ScrapedItem] = []

        for url, parser in (
            (_WHATS_NEW_URL, _parse_whats_new),
            (_ORDERS_URL, _parse_orders),
        ):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                all_items.extend(parser(resp.text))
            except httpx.HTTPError:
                continue

    return all_items

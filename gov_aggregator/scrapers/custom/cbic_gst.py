from __future__ import annotations

from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://cbic-gst.gov.in"
_HOME = f"{_BASE}/"
_TICKERS = f"{_BASE}/tickers.html"


def _clean(value: str) -> str:
    return " ".join(value.split())


def _parse_news(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for li in soup.select("#vmarquee ul li"):
        full_text = _clean(li.get_text())
        if not full_text:
            continue

        a = li.find("a", href=True)
        link = urljoin(_BASE, a["href"]) if a else ""
        is_pdf = link.lower().endswith(".pdf") if link else False

        items.append(ScrapedItem(
            title=full_text,
            link=link,
            is_pdf=is_pdf,
            section_label="What's New",
        ))

    return items


def _parse_tickers(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(".innerpage-tab-content table tbody tr"):
        td = row.find("td")
        if not td:
            continue

        full_text = _clean(td.get_text())
        if not full_text:
            continue

        a = td.find("a", href=True)
        link = urljoin(_BASE, a["href"]) if a else ""
        is_pdf = link.lower().endswith(".pdf") if link else False

        items.append(ScrapedItem(
            title=full_text,
            link=link,
            is_pdf=is_pdf,
            section_label="Tickers",
        ))

    return items


async def crawl_cbic_gst(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30,
    ) as client:
        items: list[ScrapedItem] = []

        try:
            resp = await client.get(_HOME)
            resp.raise_for_status()
            items.extend(_parse_news(resp.text))
        except httpx.HTTPError:
            pass

        try:
            resp = await client.get(_TICKERS)
            resp.raise_for_status()
            items.extend(_parse_tickers(resp.text))
        except httpx.HTTPError:
            pass

    return items

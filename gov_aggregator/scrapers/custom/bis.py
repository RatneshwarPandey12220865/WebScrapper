from __future__ import annotations

from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://www.bis.gov.in"
_URL = f"{_BASE}/"


def _clean(value: str) -> str:
    return " ".join(value.split())


def _parse_whats_new(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    about_row = soup.select_one(".row.about_row")
    if not about_row:
        return items

    columns = about_row.select(".who_we_area")
    if len(columns) < 2:
        return items

    for h2 in columns[1].find_all("h2"):
        a = h2.find("a", href=True)
        if not a:
            continue

        title = _clean(a.get_text())
        if not title:
            continue

        link = urljoin(_BASE, a["href"])
        is_pdf = link.lower().endswith(".pdf")

        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=is_pdf,
            section_label="What's New",
        ))

    return items


async def crawl_bis(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30,
    ) as client:
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except httpx.HTTPError:
            return []

    return _parse_whats_new(resp.text)

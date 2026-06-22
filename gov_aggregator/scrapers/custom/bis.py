from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://www.bis.gov.in"
_START_URL = f"{_BASE}/whats-new/?lang=en"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clean(value: str) -> str:
    return " ".join(value.split())


def _h3_text(item: BeautifulSoup, keyword: str) -> str | None:
    tag = item.find(lambda t: t.name == "h3" and keyword in t.get_text())
    if tag:
        return _clean(tag.get_text().replace(keyword, "").strip())
    return None


def _parse_page(html: str, page_url: str) -> tuple[list[ScrapedItem], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for item in soup.select(".staffcat"):
        a = item.select_one("h2 a")
        if not a:
            continue

        title = _clean(a.get_text())
        if not title:
            continue

        href = a.get("href")
        link = urljoin(page_url, href) if href else ""

        published_on_text = _h3_text(item, "Published On:")
        published_at = _parse_date(published_on_text)

        if published_at and published_at < _MIN_DATE:
            continue

        file_type = _h3_text(item, "Type:")
        is_pdf = (file_type or "").lower() == "pdf" or link.lower().endswith(".pdf")

        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=is_pdf,
            published_at=published_at,
            section_label="What's New",
        ))

    next_tag = soup.select_one("a.next.page-numbers")
    next_url = urljoin(page_url, next_tag["href"]) if next_tag else None

    return items, next_url


async def crawl_bis(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30,
    ) as client:
        url: str | None = _START_URL
        all_items: list[ScrapedItem] = []

        while url:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError:
                break

            page_items, url = _parse_page(resp.text, str(resp.url))
            all_items.extend(page_items)

    return all_items

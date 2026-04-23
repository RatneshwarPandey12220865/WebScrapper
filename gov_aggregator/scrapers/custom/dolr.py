from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", cleaned)
    if match:
        try:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


async def crawl_dolr(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=DEFAULT_HEADERS, timeout=60) as client:
        # Section 1: What's New
        whats_new_url = "https://dolr.gov.in/"
        resp = await client.get(whats_new_url)
        if resp.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("#whats-new-content ul li")
            for row in rows:
                link_tag = row.select_one("a")
                if not link_tag:
                    continue
                title = _clean_text(link_tag.get_text())
                if not title:
                    continue
                href = link_tag.get("href", "")
                link = urljoin("https://dolr.gov.in", href) if href else ""
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        is_pdf=False,
                        section_label="What's New",
                    )
                )

        # Section 2: Orders & Notices
        orders_url = "https://dolr.gov.in/document-category/orders-notices/"
        for page in range(10):
            url = orders_url if page == 0 else f"{orders_url}page/{page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.data-table-1 tbody tr")
            if not rows:
                break
            for row in rows:
                title_elem = row.select_one("td:nth-child(1)")
                link_elem = row.select_one("td:nth-child(3) a")
                date_elem = row.select_one("td:nth-child(2)")
                if not title_elem or not link_elem:
                    continue
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                date_text = _clean_text(date_elem.get_text()) if date_elem else ""
                parsed_date = _parse_date(date_text) if date_text else None
                if not parsed_date:
                    continue
                href = link_elem.get("href", "")
                link = urljoin("https://dolr.gov.in", href) if href else ""
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        published_at=parsed_date,
                        is_pdf=href.lower().endswith(".pdf") if href else False,
                        section_label="Orders & Notices",
                    )
                )

        # Section 3: Notifications
        notifications_url = "https://dolr.gov.in/document-category/notification/"
        for page in range(10):
            url = notifications_url if page == 0 else f"{notifications_url}page/{page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.data-table-1 tbody tr")
            if not rows:
                break
            for row in rows:
                title_elem = row.select_one("td:nth-child(1)")
                link_elem = row.select_one("td:nth-child(3) a")
                date_elem = row.select_one("td:nth-child(2)")
                if not title_elem or not link_elem:
                    continue
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                date_text = _clean_text(date_elem.get_text()) if date_elem else ""
                parsed_date = _parse_date(date_text) if date_text else None
                if not parsed_date:
                    continue
                href = link_elem.get("href", "")
                link = urljoin("https://dolr.gov.in", href) if href else ""
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        published_at=parsed_date,
                        is_pdf=href.lower().endswith(".pdf") if href else False,
                        section_label="Notifications",
                    )
                )

        # Section 4: Circulars (NEW)
        circular_url = "https://dolr.gov.in/document-category/circular/"
        for page in range(10):
            url = circular_url if page == 0 else f"{circular_url}page/{page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.data-table-1 tbody tr")
            if not rows:
                break
            for row in rows:
                title_elem = row.select_one("td:nth-child(1)")
                link_elem = row.select_one("td:nth-child(3) a")
                date_elem = row.select_one("td:nth-child(2)")
                if not title_elem or not link_elem:
                    continue
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                date_text = _clean_text(date_elem.get_text()) if date_elem else ""
                parsed_date = _parse_date(date_text) if date_text else None
                if not parsed_date:
                    continue
                href = link_elem.get("href", "")
                link = urljoin("https://dolr.gov.in", href) if href else ""
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        published_at=parsed_date,
                        is_pdf=href.lower().endswith(".pdf") if href else False,
                        section_label="Circulars",
                    )
                )

    return items

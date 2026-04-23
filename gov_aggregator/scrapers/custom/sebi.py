from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

CUTOFF = datetime(2025, 10, 1, tzinfo=timezone.utc)
ITEMS_PER_PAGE = 25

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.sebi.gov.in/",
}

AJAX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.sebi.gov.in/",
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    patterns = [
        ("%b %d, %Y", raw),
        ("%B %d, %Y", raw),
        ("%d-%b-%Y", raw),
        ("%d %b %Y", raw),
        ("%d/%m/%Y", raw),
        ("%d-%m-%Y", raw),
    ]
    for fmt, text in patterns:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _clean_title(title: str) -> str:
    title = _strip_html(title)
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"^[-–—\s]+", "", title)
    title = re.sub(r"[-–—\s]+$", "", title)
    return title


async def _fetch_page(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> str:
    response = await client.get(url, params=params, headers=headers or DEFAULT_HEADERS)
    response.raise_for_status()
    return response.text


async def _fetch_ajax_page(
    client: httpx.AsyncClient,
    page_index: int,
    sid: str = "",
    ssid: str = "",
    section_type: str = "all",  # "all" for All Updates, "list" for other sections
) -> str:
    if section_type == "all":
        url = "https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistallinfo.jsp"
    else:
        url = "https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp"
    
    data = {
        "direction": "n",
        "nextValue": page_index,
    }
    if sid:
        data["sid"] = sid
    if ssid:
        data["ssid"] = ssid

    response = await client.post(
        url,
        data=data,
        headers=AJAX_HEADERS,
        timeout=30.0,
    )
    response.raise_for_status()
    return response.text


def _parse_items_from_html(html: str, section_label: str) -> list[ScrapedItem]:
    items = []
    soup = BeautifulSoup(html, "html.parser")

    rows = soup.find_all("tr")
    for row in rows:
        try:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            date_cell = cells[0]
            
            # All Updates page has 3 columns: Date, Type, Title
            # Other pages have 2 columns: Date, Title
            if len(cells) >= 3:
                title_cell = cells[2]
            else:
                title_cell = cells[1] if len(cells) > 1 else cells[-1]

            date_text = date_cell.get_text(strip=True)
            published_at = _parse_date(date_text)

            if published_at and published_at < CUTOFF:
                continue

            link_tag = title_cell.find("a", href=True)
            if not link_tag:
                title_text = _clean_title(title_cell.get_text())
                if not title_text:
                    continue
                items.append(
                    ScrapedItem(
                        title=title_text,
                        link="",
                        published_at=published_at,
                        section_label=section_label,
                    )
                )
                continue

            title = _clean_title(link_tag.get_text())
            if not title:
                continue

            href = link_tag.get("href", "").strip()

            if href.startswith("/"):
                href = f"https://www.sebi.gov.in{href}"
            elif not href.startswith("http"):
                href = f"https://www.sebi.gov.in/{href}"

            is_pdf = href.lower().endswith(".pdf")

            items.append(
                ScrapedItem(
                    title=title,
                    link=href,
                    published_at=published_at,
                    is_pdf=is_pdf,
                    section_label=section_label,
                )
            )
        except Exception:
            continue

    return items


async def _scrape_section(
    client: httpx.AsyncClient,
    section_label: str,
    sid: str = "",
    ssid: str = "",
    section_type: str = "list",  # "all" for All Updates, "list" for other sections
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    page_index = 0
    seen_titles: set[str] = set()
    max_pages = 50

    while page_index < max_pages:
        try:
            html = await _fetch_ajax_page(client, page_index, sid, ssid, section_type)
            page_items = _parse_items_from_html(html, section_label)

            if not page_items:
                break

            new_items = 0
            for item in page_items:
                if item.title not in seen_titles:
                    seen_titles.add(item.title)
                    items.append(item)
                    new_items += 1

            if new_items == 0:
                break

            page_index += 1
            await asyncio.sleep(0.3)

        except Exception as e:
            print(f"[sebi] Error fetching {section_label} page {page_index}: {e}")
            break

    return items


async def _scrape_all_updates(client: httpx.AsyncClient) -> list[ScrapedItem]:
    return await _scrape_section(client, "SEBI — All Updates", sid="", ssid="", section_type="all")


async def _scrape_public_notices(client: httpx.AsyncClient) -> list[ScrapedItem]:
    return await _scrape_section(client, "SEBI — Public Notices", sid="6", ssid="25", section_type="list")


async def _scrape_circulars(client: httpx.AsyncClient) -> list[ScrapedItem]:
    return await _scrape_section(client, "SEBI — Circulars", sid="1", ssid="1", section_type="list")


async def _scrape_master_circulars(client: httpx.AsyncClient) -> list[ScrapedItem]:
    return await _scrape_section(client, "SEBI — Master Circulars", sid="1", ssid="45", section_type="list")


async def _scrape_press_releases(client: httpx.AsyncClient) -> list[ScrapedItem]:
    return await _scrape_section(client, "SEBI — Press Releases", sid="6", ssid="23", section_type="list")


async def _scrape_orders(client: httpx.AsyncClient) -> list[ScrapedItem]:
    return await _scrape_section(client, "SEBI — Orders", sid="5", ssid="1", section_type="list")


async def _scrape_notifications(client: httpx.AsyncClient) -> list[ScrapedItem]:
    return await _scrape_section(client, "SEBI — Notifications", sid="2", ssid="1", section_type="list")


async def crawl_sebi(config: SiteConfig) -> list[ScrapedItem]:
    """
    Scrapes SEBI website for regulatory updates.
    Handles AJAX POST pagination for multiple section types.
    """
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30.0,
    ) as client:
        tasks = [
            _scrape_public_notices(client),
            _scrape_circulars(client),
            _scrape_master_circulars(client),
            _scrape_press_releases(client),
            _scrape_orders(client),
            _scrape_notifications(client),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                print(f"[sebi] Section scrape failed: {result}")
            else:
                items.extend(result)

    items.sort(
        key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    return items


async def crawl_sebi_stream(config: SiteConfig) -> AsyncIterator[ScrapedItem]:
    """Streaming version that yields items as they are scraped."""
    items = await crawl_sebi(config)
    for item in items:
        yield item

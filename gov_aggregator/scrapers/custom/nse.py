from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

CIRCULAR_CUTOFF_MONTHS = 1
PRESS_RELEASE_CUTOFF_MONTHS = 3
PRESS_CUTOFF_DATE = datetime.now(timezone.utc) - timedelta(days=PRESS_RELEASE_CUTOFF_MONTHS * 30)
CIRCULAR_CUTOFF_DATE = datetime.now(timezone.utc) - timedelta(days=CIRCULAR_CUTOFF_MONTHS * 30)


async def crawl_nse(config: SiteConfig) -> list[ScrapedItem]:
    """Scrapes NSE India website.
    
    NOTE: The NSE website loads data via JavaScript/AJAX and has anti-bot protection.
    This scraper works by hitting the page and hoping the JS executes, but may
    get blocked. The site returns full HTML but with empty data containers.
    """
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(60.0, connect=30.0),
    ) as client:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6455.137 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

        try:
            await client.get("https://www.nseindia.com/", headers=headers)
            await asyncio.sleep(2)

            headers["Referer"] = "https://www.nseindia.com/"
            resp = await client.get(
                "https://www.nseindia.com/resources/exchange-communication-press-releases",
                headers=headers,
            )

            if "table-PressRelease" in resp.text:
                items = _parse_nse_html(resp.text, "Press Releases")

            if not items:
                await asyncio.sleep(2)
                headers["Referer"] = "https://www.nseindia.com/resources/exchange-communication-press-releases"
                resp = await client.get(
                    "https://www.nseindia.com/resources/exchange-communication-circulars",
                    headers=headers,
                )
                if "table-" in resp.text or "Circular" in resp.text:
                    items.extend(_parse_nse_html(resp.text, "Circulars"))

        except Exception as e:
            print(f"[nse] Error: {e}")

    items.sort(
        key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    return items


def _parse_nse_html(html: str, section_label: str) -> list[ScrapedItem]:
    items = []
    soup = BeautifulSoup(html, "html.parser")

    div = soup.find("div", {"id": "table-PressRelease"})
    if not div:
        div = soup.find("div", {"id": "table-Circular"})
    
    if not div:
        return items

    table = div.find("table")
    if not table:
        return items

    tbody = table.find("tbody")
    if not tbody:
        return items

    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        date_cell = cells[0]
        title_cell = cells[1] if len(cells) > 1 else cells[0]

        date_text = date_cell.get_text(strip=True)
        published_at = _parse_date_str(date_text)

        if section_label == "Circulars" and published_at and published_at < CIRCULAR_CUTOFF_DATE:
            continue
        if section_label == "Press Releases" and published_at and published_at < PRESS_CUTOFF_DATE:
            continue

        link_tag = title_cell.find("a", href=True)
        if link_tag:
            title = link_tag.get_text(strip=True)
            href = link_tag.get("href", "").strip()
        else:
            title = title_cell.get_text(strip=True)
            href = ""

        if not title:
            continue

        if href and not href.startswith("http"):
            href = "https://www.nseindia.com" + href

        items.append(
            ScrapedItem(
                title=title,
                link=href,
                published_at=published_at,
                is_pdf=(href.lower().endswith(".pdf") if href else False),
                section_label=section_label,
            )
        )

    return items


def _parse_date_str(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    formats = ["%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
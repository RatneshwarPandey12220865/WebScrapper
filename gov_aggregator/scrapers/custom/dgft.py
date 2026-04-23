from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig, SiteSection
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS

DGFT_BASE_URL = "https://www.dgft.gov.in/CP"
DGFT_CONTENT_URL = "https://content.dgft.gov.in"
MAX_ITEMS = 30

SECTION_URLS = {
    "Notifications": "index.jsp?opt=notification",
    "Public Notices": "index.jsp?opt=publicNotice",
    "Circulars": "index.jsp?opt=circular",
    "Trade Notices": "index.jsp?opt=tradeNotice",
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ["%d/%m/%Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


import httpx


async def _scrape_section(
    client: httpx.AsyncClient,
    section_path: str,
    section_label: str,
    current_year: int,
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    seen_links: set[str] = set()
    
    url = f"{DGFT_BASE_URL}/{section_path}"
    
    try:
        response = await client.get(url)
    except Exception:  # noqa: BLE001
        return items

    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.select_one("table.table")
    if not table:
        return items

    for row in table.select("tbody tr"):
        cells = row.select("td")
        if len(cells) < 6:
            continue

        number = cells[1].get_text(strip=True)
        year = cells[2].get_text(strip=True)
        
        desc = cells[3].get_text(strip=True)
        if number.startswith("Corrigendum"):
            title = f"{number} - {desc}"
        elif desc:
            title = f"{number}/{year} - {desc}" if year else desc
        else:
            title = number

        if not title:
            continue

        date_text = cells[4].get_text(strip=True)
        published_at = _parse_date(date_text)
        if not published_at or published_at.year != current_year:
            continue

        link_tag = cells[6].select_one("a[href]") if len(cells) > 6 else None
        if not link_tag:
            continue

        href = (link_tag.get("href") or "").strip()
        if not href or "javascript" in href.lower():
            continue

        if not href.startswith("http"):
            if href.startswith("/"):
                link = DGFT_CONTENT_URL + href
            else:
                link = urljoin(DGFT_BASE_URL, href)
        else:
            link = href

        if link in seen_links:
            continue
        seen_links.add(link)

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                is_pdf=link.lower().endswith(".pdf"),
                section_label=section_label,
            )
        )

        if len(items) >= MAX_ITEMS:
            break

    return items


async def crawl_dgft(config: SiteConfig) -> list[ScrapedItem]:
    import httpx  # noqa: F811

    current_year = datetime.now(timezone.utc).year
    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30.0,
    ) as client:
        sections = config.sections or [
            SiteSection(
                source_url="index.jsp?opt=notification",
                section_label="Notifications",
            )
        ]
        
        for section in sections:
            section_label = section.section_label or "Notifications"
            section_path = SECTION_URLS.get(section_label, SECTION_URLS["Notifications"])
            
            items = await _scrape_section(
                client, section_path, section_label, current_year
            )
            all_items.extend(items)

    return all_items
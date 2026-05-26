from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://www.rbi.org.in"

HOME_URL = "https://www.rbi.org.in/"
WHATS_NEW_URL = "https://www.rbi.org.in/scripts/NewLinkDetails.aspx"
PRESS_RELEASE_URL = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
# NotificationUser.aspx returns 403/Unauthorised without a valid browser session — skipped

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.rbi.org.in/",
}

CONCURRENCY = 3


def _parse_rbi_date(raw: str | None) -> datetime | None:
    """Parse RBI date format: 'Mar 16, 2026' or 'Jan 05, 2026'"""
    if not raw:
        return None
    cleaned = raw.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_title_date(title: str) -> datetime | None:
    """Extract date from press release title like 'Money Market Operations as on March 26, 2026'"""
    match = re.search(r"on\s+(\w+\s+\d{1,2},\s+\d{4})", title, re.IGNORECASE)
    if match:
        return _parse_rbi_date(match.group(1))
    match = re.search(r"as\s+on\s+(\w+\s+\d{1,2},\s+\d{4})", title, re.IGNORECASE)
    if match:
        return _parse_rbi_date(match.group(1))
    return None


def _extract_homepage_recent(html: str) -> list[ScrapedItem]:
    """Parse ul#Recent from the RBI homepage — 25 latest items, no dates."""
    soup = BeautifulSoup(html, "html.parser")
    ul = soup.find("ul", id="Recent")
    if not ul:
        return []

    items: list[ScrapedItem] = []
    for li in ul.find_all("li"):
        anchor = li.find("a")
        if not anchor:
            continue
        title = " ".join(anchor.get_text().split())
        href = (anchor.get("href") or "").strip()
        if not title or not href:
            continue
        link = href if href.startswith("http") else urljoin(BASE_URL, href)
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=None,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Recent",
        ))
    return items


def _extract_whats_new(html: str) -> list[ScrapedItem]:
    """
    Parse the What's New page (NewLinkDetails.aspx).
    Simple flat table: each row has a single <a class="link2"> with absolute URL.
    No dates available.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="tablebg")
    if not table:
        return []

    items: list[ScrapedItem] = []
    for anchor in table.find_all("a", class_="link2"):
        title = anchor.get_text(" ", strip=True)
        href = (anchor.get("href") or "").strip()
        if not title or not href:
            continue
        link = href if href.startswith("http") else urljoin(BASE_URL, href)
        is_pdf = link.lower().endswith(".pdf")
        items.append(
            ScrapedItem(
                title=title,
                link=link,
                summary=None,
                published_at=None,
                is_pdf=is_pdf,
                section_label="What's New",
            )
        )

    return items


def _extract_press_releases(html: str) -> list[ScrapedItem]:
    """
    Parse the Press Releases page (BS_PressReleaseDisplay.aspx).
    Date headers: td.tableheader h2 rows set current_date for following item rows.
    PDF links extracted from a[href*=".PDF"] when present.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="tablebg")
    if not table:
        return []

    items: list[ScrapedItem] = []
    current_date: datetime | None = None

    for row in table.find_all("tr"):
        header_td = row.find("td", class_="tableheader")
        if header_td:
            current_date = _parse_rbi_date(header_td.get_text(strip=True))
            continue

        title_anchor = row.find("a", class_="link2")
        if not title_anchor:
            continue

        title = title_anchor.get_text(" ", strip=True)
        href = (title_anchor.get("href") or "").strip()
        if not title or not href:
            continue

        detail_url = urljoin(BASE_URL + "/Scripts/", href)

        pdf_anchor = row.find("a", href=re.compile(r"\.PDF$", re.IGNORECASE))
        pdf_url: str | None = None
        if pdf_anchor:
            pdf_href = (pdf_anchor.get("href") or "").strip()
            if pdf_href:
                pdf_url = urljoin(BASE_URL, pdf_href)

        link = pdf_url or detail_url

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                summary=None,
                published_at=current_date,
                is_pdf=bool(pdf_url),
                section_label="Press Releases",
            )
        )

    return items


def _extract_notifications(html: str, section_label: str) -> list[ScrapedItem]:
    """
    Parse the grouped table structure of NotificationUser.aspx.
    """
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table", class_="tablebg")
    if not table:
        return []

    items: list[ScrapedItem] = []
    current_date: datetime | None = None

    for row in table.find_all("tr"):
        header_td = row.find("td", class_="tableheader")
        if header_td:
            date_tag = header_td.find("h2", class_="dop_header")
            if date_tag:
                current_date = _parse_rbi_date(date_tag.get_text(strip=True))
            continue

        title_td = row.find("td", style=lambda s: s and "word-wrap" in s)
        if not title_td:
            continue

        title_anchor = title_td.find("a", class_="link2")
        if not title_anchor:
            continue

        title = title_anchor.get_text(" ", strip=True)
        if not title:
            continue

        detail_href = (title_anchor.get("href") or "").strip()
        detail_url = urljoin(BASE_URL + "/Scripts/", detail_href) if detail_href else ""

        pdf_anchor = row.find("a", href=re.compile(r"\.PDF$", re.IGNORECASE))
        pdf_url: str | None = None
        if pdf_anchor:
            pdf_href = (pdf_anchor.get("href") or "").strip()
            if pdf_href:
                pdf_url = urljoin(BASE_URL, pdf_href)

        link = pdf_url or detail_url
        if not link:
            continue

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                summary=None,
                published_at=current_date,
                is_pdf=bool(pdf_url),
                section_label=section_label,
            )
        )

    return items


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            return resp.text
        return ""
    except Exception:
        return ""


async def crawl_rbi(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60.0,
    ) as client:
        # Fetch all three sources in parallel
        home_html, whats_new_html, press_html = await asyncio.gather(
            _fetch(client, HOME_URL),
            _fetch(client, WHATS_NEW_URL),
            _fetch(client, PRESS_RELEASE_URL),
        )

        if home_html:
            items.extend(_extract_homepage_recent(home_html))
        if whats_new_html:
            items.extend(_extract_whats_new(whats_new_html))
        if press_html:
            items.extend(_extract_press_releases(press_html))

    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in items:
        if item.link not in seen:
            seen.add(item.link)
            unique.append(item)

    return unique

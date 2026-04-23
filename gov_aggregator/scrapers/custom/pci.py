from __future__ import annotations

import asyncio
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://pci.gov.in"

LIST_SECTIONS = [
    ("Circulars - Page 1",     "circular",     "/en/blog/?category=Circulars"),
    ("Circulars - Page 2",     "circular",     "/en/blog/?category=Circulars&page=2"),
    ("Circulars - Page 3",     "circular",     "/en/blog/?category=Circulars&page=3"),
    ("Circulars - Page 4",     "circular",     "/en/blog/?category=Circulars&page=4"),
    ("Announcements",          "notification", "/en/blog/?category=Announcement"),
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

CONCURRENCY = 5  # max parallel detail page fetches


def _extract_list_items(html: str) -> list[tuple[str, str]]:
    """
    Parse a list page and return [(title, detail_url), ...].
    detail_url is absolute.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for article in soup.select("div.divide-y article.py-6"):
        anchor = article.select_one("h2 a")
        if not anchor:
            continue
        title = anchor.get_text(" ", strip=True)
        href = (anchor.get("href") or "").strip()
        if not href or not title:
            continue
        results.append((title, urljoin(BASE_URL, href)))
    return results


def _extract_pdf_from_detail(html: str, detail_url: str) -> str | None:
    """
    From a detail page, extract the first PDF link from the
    document downloads list. Falls back to the detail URL itself.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Primary: document download list
    doc_list = soup.select_one('ul[aria-label="Document downloads"]')
    if doc_list:
        anchor = doc_list.select_one("a[href]")
        if anchor:
            href = anchor.get("href", "").strip()
            if href:
                return urljoin(BASE_URL, href)

    # Fallback: any direct .pdf link on the page
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if href.lower().endswith(".pdf"):
            return urljoin(BASE_URL, href)

    # Last resort: return the detail page URL itself
    return detail_url


def _extract_date_from_detail(html: str):
    """Extract published date from <time datetime='...'> on detail page."""
    from datetime import datetime, timezone
    soup = BeautifulSoup(html, "html.parser")
    time_tag = soup.select_one("time[datetime]")
    if not time_tag:
        return None
    dt_str = time_tag.get("datetime", "").strip()
    if not dt_str:
        return None
    try:
        from dateutil import parser as date_parser
        return date_parser.parse(dt_str)
    except Exception:
        return None


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""


async def _process_detail(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    title: str,
    detail_url: str,
    section_label: str,
    default_category: str,
) -> ScrapedItem | None:
    async with semaphore:
        html = await _fetch(client, detail_url)
        if not html:
            return None

        pdf_url = _extract_pdf_from_detail(html, detail_url)
        published_at = _extract_date_from_detail(html)
        is_pdf = pdf_url.lower().endswith(".pdf") if pdf_url else False

        return ScrapedItem(
            title=title,
            link=pdf_url or detail_url,
            summary=None,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label=section_label,
        )


async def crawl_pci(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30.0,
    ) as client:
        for section_label, default_category, path in LIST_SECTIONS:
            list_url = urljoin(BASE_URL, path)
            list_html = await _fetch(client, list_url)
            if not list_html:
                continue

            list_items = _extract_list_items(list_html)
            if not list_items:
                continue

            # Fetch all detail pages concurrently (bounded by semaphore)
            tasks = [
                _process_detail(
                    client, semaphore,
                    title, detail_url,
                    section_label, default_category,
                )
                for title, detail_url in list_items
            ]
            results = await asyncio.gather(*tasks)

            for item in results:
                if item is not None:
                    items.append(item)

    return items

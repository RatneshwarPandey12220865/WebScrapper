"""Custom scraper for Ministry of Cooperation.

Two sections scraped:
  1. Notices & Circulars  — https://www.cooperation.gov.in/en/notices-circulars
     Table: table.table tbody tr
     Cols:  views-field-title | views-field-field-date | views-field-field-upload-file
     Pagination: li.next a[href]

  2. Announcements        — https://www.cooperation.gov.in/en/announcement
     Table: .view-content table tbody tr
     Cols:  views-field-field-date-1 | views-field-title a
     Pagination: li.pager__item--next a[href]

Both sections are SSR (no JS rendering needed); plain HTTPX fetch suffices.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.cooperation")

_BASE = "https://www.cooperation.gov.in"
_NOTICES_URL = "https://www.cooperation.gov.in/en/notices-circulars"
_ANNOUNCEMENTS_URL = "https://www.cooperation.gov.in/en/announcement"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_PAGES = 10


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


# ── Section 1: Notices & Circulars ───────────────────────────────────────────

def _parse_notices_page(html: str) -> tuple[list[ScrapedItem], bool]:
    """
    Returns (items, stop) where stop=True when a pre-MIN_DATE item is found.

    Table structure:
      table.table tbody tr
        td.views-field-title           → title text
        td.views-field-field-date      → date text (e.g. "15/01/2026")
        td.views-field-field-upload-file a → PDF href + display text (file size)
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    for row in soup.select("table.table tbody tr"):
        title_td = row.select_one("td.views-field-title")
        date_td = row.select_one("td.views-field-field-date")
        file_td = row.select_one("td.views-field-field-upload-file")

        title = _clean(title_td.get_text() if title_td else "")
        if not title:
            continue

        raw_date = _clean(date_td.get_text() if date_td else "")
        published_at = _parse_date(raw_date) if raw_date else None

        if published_at and published_at < _MIN_DATE:
            stop = True
            continue

        pdf_a = file_td.find("a", href=True) if file_td else None
        href = (pdf_a.get("href") or "") if pdf_a else ""
        link = urljoin(_BASE, href) if href and not href.startswith("http") else href
        is_pdf = href.lower().endswith(".pdf") if href else False

        items.append(ScrapedItem(
            title=title,
            link=link or _NOTICES_URL,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label="Notices & Circulars",
        ))

    return items, stop


def _next_notices_url(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("li.next a[href]")
    if not a:
        return None
    href = a.get("href", "")
    return urljoin(current_url, href) if href else None


# ── Section 2: Announcements ──────────────────────────────────────────────────

def _parse_announcements_page(html: str) -> tuple[list[ScrapedItem], bool]:
    """
    Table structure:
      .view-content table tbody tr
        td.views-field-field-date-1    → date text
        td.views-field-title a         → title text + href (PDF or page)
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    for row in soup.select(".view-content table tbody tr"):
        date_td = row.select_one("td.views-field-field-date-1")
        title_td = row.select_one("td.views-field-title")

        raw_date = _clean(date_td.get_text() if date_td else "")
        published_at = _parse_date(raw_date) if raw_date else None

        if published_at and published_at < _MIN_DATE:
            stop = True
            continue

        a_tag = title_td.find("a", href=True) if title_td else None
        title = _clean(a_tag.get_text() if a_tag else (title_td.get_text() if title_td else ""))
        if not title:
            continue

        href = (a_tag.get("href") or "") if a_tag else ""
        link = urljoin(_BASE, href) if href and not href.startswith("http") else href
        is_pdf = href.lower().endswith(".pdf") if href else False

        items.append(ScrapedItem(
            title=title,
            link=link or _ANNOUNCEMENTS_URL,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label="Announcements",
        ))

    return items, stop


def _next_announcements_url(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("li.pager__item--next a[href]")
    if not a:
        return None
    href = a.get("href", "")
    return urljoin(current_url, href) if href else None


# ── Shared paginator ──────────────────────────────────────────────────────────

async def _paginate(
    client: httpx.AsyncClient,
    start_url: str,
    parse_fn,
    next_fn,
    section: str,
) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    url: str | None = start_url
    page = 0

    while url and page < _MAX_PAGES:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[cooperation] %s page %d fetch failed: %s", section, page, exc)
            if page == 0:
                raise
            break

        items, stop = parse_fn(resp.text)
        all_items.extend(items)
        logger.info("[cooperation] %s page %d: %d items", section, page, len(items))

        if stop:
            logger.info("[cooperation] %s: hit pre-MIN_DATE item, stopping", section)
            break

        url = next_fn(resp.text, url)
        page += 1

    return all_items


# ── Entry point ───────────────────────────────────────────────────────────────

async def crawl_cooperation(config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
        verify=getattr(config, "verify_ssl", True),
    ) as client:

        notices_task = _paginate(
            client, _NOTICES_URL, _parse_notices_page, _next_notices_url, "Notices & Circulars"
        )
        announcements_task = _paginate(
            client, _ANNOUNCEMENTS_URL, _parse_announcements_page, _next_announcements_url, "Announcements"
        )

        notices, announcements = await asyncio.gather(
            notices_task, announcements_task, return_exceptions=True
        )

    all_items: list[ScrapedItem] = []

    if isinstance(notices, Exception):
        logger.error("[cooperation] Notices & Circulars failed: %s", notices)
    else:
        all_items.extend(notices)

    if isinstance(announcements, Exception):
        logger.error("[cooperation] Announcements failed: %s", announcements)
    else:
        all_items.extend(announcements)

    # Deduplicate by link
    seen: set[str] = set()
    deduped: list[ScrapedItem] = []
    for item in all_items:
        if item.link not in seen:
            seen.add(item.link)
            deduped.append(item)

    logger.info("[cooperation] Total items after dedup: %d", len(deduped))
    return deduped

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://asi.nic.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
# Matches DD-MM-YYYY or DD/MM/YYYY anywhere in text (used for title-stripping)
_STRIP_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _parse_table(html: str, section_label: str) -> tuple[list[ScrapedItem], bool]:
    """
    Parse one page of a 3-column ASI table (Sr No | Title | Publish Date).

    The server sometimes emits the date td as text inside the title td when
    parsed with strict parsers, so we use a two-step date extraction:
      1. Check tds[2] (ideal case)
      2. Fall back to searching the full row text for DD-MM-YYYY

    Returns (items, hit_cutoff) — hit_cutoff=True stops pagination.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.tabel-border")
    if not table:
        return [], True

    rows = table.select("tbody tr")
    if not rows:
        return [], True

    items: list[ScrapedItem] = []
    hit_cutoff = False

    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 2:
            continue

        # Find the anchor link — search all tds in case structure shifts
        link_tag = None
        for td in tds:
            link_tag = td.find("a", href=True)
            if link_tag:
                break
        if not link_tag:
            continue

        # Title: anchor text only (never includes surrounding td text)
        title = _clean(link_tag.get_text())
        if not title:
            continue

        # ── Date extraction (three-strategy fallback) ──────────────────────
        date_text = ""

        # Strategy 1: dedicated 3rd column
        if len(tds) >= 3:
            date_text = _clean(tds[-1].get_text())   # last td = Publish Date

        # Strategy 2: date leaked into the last part of the title string
        if not _parse_date(date_text):
            m = _STRIP_DATE_RE.search(title)
            if m:
                date_text = m.group(0)

        # Strategy 3: scan the full row text
        if not _parse_date(date_text):
            row_text = _clean(row.get_text())
            m = _STRIP_DATE_RE.search(row_text)
            if m:
                date_text = m.group(0)

        # Strip any leaked date from the end of the title
        title = _STRIP_DATE_RE.sub("", title).strip(" -–—")
        title = _clean(title)

        published_at = _parse_date(date_text)

        if published_at and published_at < _MIN_DATE:
            hit_cutoff = True
            break

        href = link_tag.get("href", "")
        link = urljoin(_BASE, href)
        is_pdf = "/download" in href or "/downloadPdf" in href or href.lower().endswith(".pdf")

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label=section_label,
        ))

    return items, hit_cutoff


async def _scrape_section(
    client: httpx.AsyncClient,
    base_url: str,
    section_label: str,
    max_pages: int,
) -> list[ScrapedItem]:
    """
    Fetch all pages of a section. First page = base_url (no ?p=),
    subsequent pages = base_url?p=1, base_url?p=2, …
    """
    items: list[ScrapedItem] = []

    for page in range(max_pages):
        url = base_url if page == 0 else f"{base_url}?p={page}"
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            break
        if resp.status_code != 200:
            break

        page_items, hit_cutoff = _parse_table(resp.text, section_label)
        items.extend(page_items)
        if hit_cutoff:
            break

    return items


def _parse_tenders_table(html: str, section_label: str) -> tuple[list[ScrapedItem], bool]:
    """
    Parse one page of a 4-column ASI tenders table
    (Sr No | Title | Publish Date | Last Date).

    Returns (items, hit_cutoff) — hit_cutoff=True stops pagination.
    """
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("section.exp-sec1")
    if not section:
        return [], True
    table = section.select_one("table.tabel")
    if not table:
        return [], True

    rows = table.select("tbody tr")
    if not rows:
        return [], True

    items: list[ScrapedItem] = []
    hit_cutoff = False

    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 3:
            continue

        # Title + link from 2nd column
        link_tag = tds[1].find("a", href=True) if len(tds) > 1 else None
        if not link_tag:
            continue

        title = _clean(link_tag.get_text())
        if not title:
            continue

        # Publish date from 3rd column
        publish_date_text = _clean(tds[2].get_text()) if len(tds) > 2 else ""

        # Last date from 4th column
        last_date_text = _clean(tds[3].get_text()) if len(tds) > 3 else ""

        published_at = _parse_date(publish_date_text)
        end_date = _parse_date(last_date_text)

        if published_at and published_at < _MIN_DATE:
            hit_cutoff = True
            break

        href = link_tag.get("href", "")
        link = urljoin(_BASE, href)
        is_pdf = "/download" in href or "/downloadPdf" in href or href.lower().endswith(".pdf")

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            end_date=end_date,
            is_pdf=is_pdf,
            section_label=section_label,
        ))

    return items, hit_cutoff


async def _scrape_tenders(
    client: httpx.AsyncClient,
    base_url: str,
    section_label: str,
    max_pages: int,
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    for page in range(max_pages):
        url = base_url if page == 0 else f"{base_url}?p={page}"
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            break
        if resp.status_code != 200:
            break

        page_items, hit_cutoff = _parse_tenders_table(resp.text, section_label)
        items.extend(page_items)
        if hit_cutoff:
            break

    return items


async def crawl_asi(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
    ) as client:
        whats_new = await _scrape_section(
            client,
            "https://asi.nic.in/HQ/whatsnew/",
            "What's New",
            max_pages=30,
        )
        circulars = await _scrape_section(
            client,
            "https://asi.nic.in/HQ/circulars/",
            "Circulars",
            max_pages=14,
        )
        tenders = await _scrape_tenders(
            client,
            "https://asi.nic.in/HQ/tenders/",
            "Tenders",
            max_pages=55,
        )

    return whats_new + circulars + tenders

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.civil_aviation")

_BASE = "https://www.civilaviation.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.civilaviation.gov.in/",
    "Connection": "keep-alive",
}

# (label, path, apply_date_filter)
_SECTIONS = [
    ("Circulars",        "/ministry-documents/circulars",        False),
    ("Orders",           "/ministry-documents/orders-documents", False),
    ("Rules",            "/ministry-documents/rules",            True),
]


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_doc_table_page(html: str, section_label: str, apply_date_filter: bool, page_url: str) -> tuple[list[ScrapedItem], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one("div#block-views-block-ministry-documents-block-1")
    if not block:
        return [], None
    items = []
    for row in block.select("table tbody tr"):
        title_td = row.select_one("td.views-field-title")
        title = " ".join((title_td.get_text() if title_td else "").split())
        if not title:
            continue
        a = row.select_one("td.views-field-nothing a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)
        date_td = row.select_one("td.views-field-field-date")
        published_at = _parse_date(date_td.get_text().strip() if date_td else None)
        end_td = row.select_one("td.views-field-field-end-date")
        end_date = _parse_date(end_td.get_text().strip() if end_td else None)
        if apply_date_filter:
            ref = end_date or published_at
            if ref and ref < _MIN_DATE:
                continue
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            end_date=end_date,
            is_pdf=link.lower().endswith(".pdf"),
            section_label=section_label,
        ))
    next_a = soup.select_one("li.pager__item--next a")
    next_url = urljoin(page_url, next_a["href"]) if next_a else None
    return items, next_url


def _parse_infocus(html: str, page_url: str) -> tuple[list[ScrapedItem], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for row in soup.select(".scroll-table1 tbody tr"):
        title_td = row.select_one(".views-field-title")
        title = " ".join((title_td.get_text() if title_td else "").split())
        if not title:
            continue
        a = row.select_one(".views-field-nothing a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)
        date_td = row.select_one(".views-field-field-date-1")
        published_at = _parse_date(date_td.get_text().strip() if date_td else None)
        if published_at and published_at < _MIN_DATE:
            continue
        type_td = row.select_one(".views-field-type")
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="In Focus",
            summary=" ".join((type_td.get_text() if type_td else "").split()) or None,
        ))
    next_a = soup.select_one("li.pager__item--next a")
    next_url = urljoin(page_url, next_a["href"]) if next_a else None
    return items, next_url


async def crawl_civil_aviation(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        for label, path, date_filter in _SECTIONS:
            url: str | None = _BASE + path
            while url:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    items, url = _parse_doc_table_page(resp.text, label, date_filter, url)
                    logger.info("[civil_aviation] %s page: %d items, next=%s", label, len(items), url)
                    all_items.extend(items)
                    if not items:
                        break
                except Exception as exc:
                    logger.warning("[civil_aviation] %s fetch failed: %s", label, exc)
                    break

        # In Focus (paginated, date filtered)
        url: str | None = _BASE + "/In-focus"
        while url:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                items, url = _parse_infocus(resp.text, url)
                logger.info("[civil_aviation] In Focus page: %d items, next=%s", len(items), url)
                all_items.extend(items)
                if not items:
                    break
            except Exception as exc:
                logger.warning("[civil_aviation] In Focus fetch failed: %s", exc)
                break

    logger.info("[civil_aviation] total: %d items", len(all_items))
    return all_items

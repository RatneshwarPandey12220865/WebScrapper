"""Custom scraper for Department of Financial Services (DFS).

Three sections scraped in parallel:

  1. What's New        — https://financialservices.gov.in/what-s-new
     Table: table.views-table.cols-4 tbody tr
     Cols:  td:1 title | td:2 time (date) | td:3 or td:4 doc link

  2. Orders & Notices  — https://financialservices.gov.in/orders-and-notices
     Table: table tbody tr
     Cols:  td:1 title | td:2 date text | td:3 type/size | td:4 doc link

  3. Gazette Notifications — https://financialservices.gov.in/gazettes-notification
     Table: table tbody tr (skip header via thead)
     Cols:  td:1 title | td:2 time (date) | td:3 doc link

All three share the same Drupal pager: li.pager__item--next a[href]
Dates are in ISO format (YYYY-MM-DD) from <time> tags or plain text.
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

logger = logging.getLogger("gov_aggregator.custom.dfs")

_BASE = "https://financialservices.gov.in"
_WHATS_NEW_URL = f"{_BASE}/what-s-new"
_ORDERS_URL = f"{_BASE}/orders-and-notices"
_GAZETTE_URL = f"{_BASE}/gazettes-notification"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_PAGES = 15


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _parse_date_cell(td) -> datetime | None:
    """Extract date from a <td> — prefers <time> tag, falls back to text."""
    if td is None:
        return None
    time_tag = td.find("time")
    raw = (time_tag.get_text() if time_tag else td.get_text()) or ""
    return _parse_date(_clean(raw))


def _doc_link(td, base: str) -> tuple[str, bool]:
    """Return (absolute_url, is_pdf) from a table cell containing a link."""
    if td is None:
        return "", False
    a = td.find("a", href=True)
    if not a:
        return "", False
    href = (a.get("href") or "").strip()
    link = urljoin(base, href) if href and not href.startswith("http") else href
    is_pdf = href.lower().endswith(".pdf")
    return link, is_pdf


def _next_url(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("li.pager__item--next a[href]")
    if not a:
        return None
    href = a.get("href", "")
    return urljoin(current_url, href) if href else None


# ── Section parsers ───────────────────────────────────────────────────────────

def _parse_whats_new(html: str) -> tuple[list[ScrapedItem], bool]:
    """
    table.views-table.cols-4 tbody tr
      td[0] → title text
      td[1] → <time> date
      td[2] or td[3] → doc link
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    table = soup.select_one("table.views-table.cols-4 tbody") or soup.select_one("table tbody")
    if not table:
        return items, stop

    for row in table.select("tr"):
        tds = row.find_all("td")
        if len(tds) < 1:
            continue

        title = _clean(tds[0].get_text())
        if not title:
            continue

        published_at = _parse_date_cell(tds[1] if len(tds) > 1 else None)
        if published_at and published_at < _MIN_DATE:
            stop = True
            continue

        # Try col 3 first, then col 4
        link, is_pdf = _doc_link(tds[2] if len(tds) > 2 else None, _BASE)
        if not link and len(tds) > 3:
            link, is_pdf = _doc_link(tds[3], _BASE)

        items.append(ScrapedItem(
            title=title,
            link=link or _WHATS_NEW_URL,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label="What's New",
        ))

    return items, stop


def _parse_orders(html: str) -> tuple[list[ScrapedItem], bool]:
    """
    table tbody tr
      td[0] → title
      td[1] → date text (YYYY-MM-DD or similar)
      td[2] → type/size (ignored)
      td[3] → doc link
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    for row in soup.select("table tbody tr"):
        tds = row.find_all("td")
        if len(tds) < 1:
            continue

        title = _clean(tds[0].get_text())
        if not title:
            continue

        published_at = _parse_date_cell(tds[1] if len(tds) > 1 else None)
        if published_at and published_at < _MIN_DATE:
            stop = True
            continue

        link, is_pdf = _doc_link(tds[3] if len(tds) > 3 else None, _BASE)

        items.append(ScrapedItem(
            title=title,
            link=link or _ORDERS_URL,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label="Orders & Notices",
        ))

    return items, stop


def _parse_gazette(html: str) -> tuple[list[ScrapedItem], bool]:
    """
    table tbody tr (thead skipped by BeautifulSoup tbody selection)
      td[0] → title
      td[1] → <time> date
      td[2] → doc link
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    # Skip thead rows by selecting tbody explicitly; fall back to all tr
    tbody = soup.select_one("table tbody")
    rows = tbody.select("tr") if tbody else soup.select("table tr")[1:]

    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 1:
            continue

        title = _clean(tds[0].get_text())
        if not title:
            continue

        published_at = _parse_date_cell(tds[1] if len(tds) > 1 else None)
        if published_at and published_at < _MIN_DATE:
            stop = True
            continue

        link, is_pdf = _doc_link(tds[2] if len(tds) > 2 else None, _BASE)

        items.append(ScrapedItem(
            title=title,
            link=link or _GAZETTE_URL,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label="Gazette Notifications",
        ))

    return items, stop


# ── Shared paginator ──────────────────────────────────────────────────────────

async def _paginate(
    client: httpx.AsyncClient,
    start_url: str,
    parse_fn,
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
            logger.warning("[dfs] %s page %d failed: %s", section, page, exc)
            if page == 0:
                raise
            break

        items, stop = parse_fn(resp.text)
        all_items.extend(items)
        logger.info("[dfs] %s page %d: %d items", section, page, len(items))

        if stop:
            logger.info("[dfs] %s: hit pre-MIN_DATE item, stopping", section)
            break

        url = _next_url(resp.text, url)
        page += 1

    return all_items


# ── Entry point ───────────────────────────────────────────────────────────────

async def crawl_dfs(config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
        verify=getattr(config, "verify_ssl", True),
    ) as client:

        results = await asyncio.gather(
            _paginate(client, _WHATS_NEW_URL, _parse_whats_new, "What's New"),
            _paginate(client, _ORDERS_URL, _parse_orders, "Orders & Notices"),
            _paginate(client, _GAZETTE_URL, _parse_gazette, "Gazette Notifications"),
            return_exceptions=True,
        )

    all_items: list[ScrapedItem] = []
    labels = ["What's New", "Orders & Notices", "Gazette Notifications"]
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            logger.error("[dfs] %s failed: %s", label, result)
        else:
            all_items.extend(result)

    # Deduplicate by link
    seen: set[str] = set()
    deduped: list[ScrapedItem] = []
    for item in all_items:
        if item.link not in seen:
            seen.add(item.link)
            deduped.append(item)

    logger.info("[dfs] Total items after dedup: %d", len(deduped))
    return deduped

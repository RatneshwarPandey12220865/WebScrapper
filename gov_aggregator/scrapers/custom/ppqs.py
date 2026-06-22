"""Custom scraper for Directorate of Plant Protection Quarantine & Storage (PPQS).

Source: https://ppqs.gov.in/news-archive
Structure: Drupal table — tbody tr rows with:
  - td.views-field-title       → title text (date often embedded in title)
  - td.views-field-field-attached a → PDF/document href
Pagination: li.pager__item--next a[href] (?page=N)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.ppqs")

_BASE = "https://ppqs.gov.in"
_NEWS_URL = "https://ppqs.gov.in/news-archive"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_PAGES = 15

# Common date patterns embedded in titles:
#   "dated 21.05.2026", "dated 21/05/2026", "dated 21-05-2026"
#   "dt. 21.05.2026", "w.e.f. 01.01.2026"
_DATE_IN_TITLE_RE = re.compile(
    r"(?:dated?\.?\s*|dt\.?\s*|w\.e\.f\.?\s*)(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4})",
    re.IGNORECASE,
)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _extract_date_from_title(title: str) -> datetime | None:
    """Try to pull a date from embedded date text in the title."""
    m = _DATE_IN_TITLE_RE.search(title)
    if m:
        return _parse_date(m.group(1))
    # Fallback: let parse_date try the whole title
    return _parse_date(title)


def _parse_page(html: str) -> tuple[list[ScrapedItem], bool]:
    """
    Parse one news-archive page.
    Returns (items, stop) — stop=True when a pre-MIN_DATE item is found.
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    stop = False

    for row in soup.select("table tbody tr"):
        title_td = row.select_one("td.views-field-title")
        attach_td = row.select_one("td.views-field-field-attached")

        title = _clean(title_td.get_text() if title_td else "")
        if not title:
            continue

        # Date embedded in title text
        published_at = _extract_date_from_title(title)
        if published_at and published_at < _MIN_DATE:
            stop = True
            continue

        # Document / PDF link
        a_tag = attach_td.find("a", href=True) if attach_td else None
        href = (a_tag.get("href") or "").strip() if a_tag else ""
        link = urljoin(_BASE, href) if href and not href.startswith("http") else href
        is_pdf = href.lower().endswith(".pdf") if href else False

        # If no attachment, fall back to the page URL itself
        if not link:
            link = _NEWS_URL

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label="News Archive",
        ))

    return items, stop


def _next_url(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("li.pager__item--next a[href]")
    if not a:
        return None
    href = a.get("href", "")
    return urljoin(current_url, href) if href else None


async def crawl_ppqs(config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
        verify=getattr(config, "verify_ssl", True),
    ) as client:

        all_items: list[ScrapedItem] = []
        url: str | None = _NEWS_URL
        page = 0

        while url and page < _MAX_PAGES:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("[ppqs] Page %d fetch failed: %s", page, exc)
                if page == 0:
                    raise
                break

            items, stop = _parse_page(resp.text)
            all_items.extend(items)
            logger.info("[ppqs] Page %d: %d items (total: %d)", page, len(items), len(all_items))

            if stop:
                logger.info("[ppqs] Hit pre-MIN_DATE item — stopping pagination")
                break

            url = _next_url(resp.text, url)
            page += 1

        # Deduplicate by link
        seen: set[str] = set()
        deduped: list[ScrapedItem] = []
        for item in all_items:
            if item.link not in seen:
                seen.add(item.link)
                deduped.append(item)

        logger.info("[ppqs] Final items after dedup: %d", len(deduped))
        return deduped

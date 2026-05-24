"""Custom scraper for Department of Fertilizers (https://fert.gov.in).

Sections scraped
────────────────
1. What's New  (https://fert.gov.in/what-s-new)
   ├── Drupal Views table  →  table.cols-8
   ├── Direct PDF links in td.views-field-nothing a.table_view_btn
   ├── Paginated via ?page=N
   └── Filter: published_at >= 2026-01-01

2. Notifications  (https://fert.gov.in/en/documents/notification)
   ├── Drupal Views listing table  →  table.cols-8
   ├── Each row links to a notification category sub-page
   ├── Paginated via ?page=N
   └── Sub-pages (e.g. /documents/notification/shipping-1)
       ├── DataTables table  →  table.dataTable / table#example2
       ├── Columns: Title | Start Date | End Date | Format | Language | Type/Size | Action
       ├── Language filter: English only
       └── Filter: end_date >= 2026-01-01
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.fertilizers")

_BASE             = "https://fert.gov.in"
_WHATS_NEW_URL    = "https://fert.gov.in/what-s-new"
_NOTIF_LIST_URL   = "https://fert.gov.in/en/documents/notification"
_MIN_DATE         = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CONCURRENCY      = 6
_MAX_PAGES        = 5   # max listing pages for both What's New and Notifications

# DD-MM-YYYY  (sub-page date cells)
_DMY_RE  = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})")
# ISO or YYYY-MM-DD  (Drupal <time datetime="…"> attribute)
_ISO_RE  = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# MM/DD/YYYY  (What's New start/end date cells: "Mon, 04/27/2026 - 12:00")
_MDY_RE  = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


# ── Date helpers ──────────────────────────────────────────────────────────────

def _clean(v: str | None) -> str:
    return " ".join((v or "").split())


def _parse_iso(raw: str | None) -> datetime | None:
    """Parse ISO/Drupal datetime attribute: '2026-05-12T12:00:00Z'."""
    if not raw:
        return None
    m = _ISO_RE.search(raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_dmy(raw: str | None) -> datetime | None:
    """Parse DD-MM-YYYY date text from DataTables cells: '15-10-2024'."""
    if not raw:
        return None
    m = _DMY_RE.search(raw.strip())
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_mdy(raw: str | None) -> datetime | None:
    """Parse MM/DD/YYYY date text from What's New cells: 'Mon, 04/27/2026 - 12:00'."""
    if not raw:
        return None
    m = _MDY_RE.search(raw.strip())
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


# ── What's New parser ─────────────────────────────────────────────────────────

def _parse_whatsnew_page(html: str) -> list[ScrapedItem]:
    """
    Parse one page of the What's New listing.

    Drupal Views table layout (table.cols-8):
      td.views-field-title          → title text
      td.views-field-field-language → language (skip non-English)
      td.views-field-field-date     → <time datetime="ISO"> → published_at
      td.views-field-nothing        → <a.table_view_btn href="PDF or node">
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.cols-8")
    if not table:
        return []

    items: list[ScrapedItem] = []
    for row in table.select("tbody tr"):
        lang_td = row.select_one("td.views-field-field-language")
        if lang_td:
            if _clean(lang_td.get_text()).lower() not in ("", "english"):
                continue

        title_td = row.select_one("td.views-field-title")
        if not title_td:
            continue
        title = _clean(title_td.get_text())
        if not title:
            continue

        link_tag = row.select_one("td.views-field-nothing a.table_view_btn")
        if not link_tag:
            continue
        href = (link_tag.get("href") or "").strip()
        if not href:
            continue
        link = href if href.startswith("http") else urljoin(_BASE, href)

        time_tag = row.select_one("td.views-field-field-date time")
        raw_dt   = (time_tag.get("datetime") or _clean(time_tag.get_text())) if time_tag else ""
        published_at = _parse_iso(raw_dt)

        # Fallback: parse start date from views-field-field-start-date
        if not published_at:
            start_td = row.select_one("td.views-field-field-start-date")
            if start_td:
                published_at = _parse_mdy(_clean(start_td.get_text()))

        end_td = row.select_one("td.views-field-field-end-date")
        end_date = _parse_mdy(_clean(end_td.get_text())) if end_td else None

        if published_at and published_at < _MIN_DATE:
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            end_date=end_date,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="What's New",
        ))

    return items


# ── Notifications listing parser ──────────────────────────────────────────────

def _extract_subpage_urls(html: str) -> list[str]:
    """
    Extract notification category sub-page URLs from the listing page.

    Tries two strategies:
    1. Drupal Views table.cols-8 → td.views-field-nothing a.table_view_btn
    2. Any <a href> whose path contains '/documents/notification/' (fallback)
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    # Strategy 1: standard Drupal Views action column
    table = soup.select_one("table.cols-8")
    if table:
        for row in table.select("tbody tr"):
            a = row.select_one("td.views-field-nothing a.table_view_btn")
            if a:
                href = (a.get("href") or "").strip()
                if href:
                    urls.append(href if href.startswith("http") else urljoin(_BASE, href))

    # Strategy 2: fallback scan for notification sub-page hrefs
    if not urls:
        for a in soup.select("a[href*='/documents/notification/'], a[href*='/notification/']"):
            href = (a.get("href") or "").strip()
            if href and "/notification/" in href:
                full = href if href.startswith("http") else urljoin(_BASE, href)
                if full not in urls:
                    urls.append(full)

    return urls


# ── Notification sub-page parser (DataTables) ─────────────────────────────────

def _parse_notification_subpage(html: str, page_url: str) -> list[ScrapedItem]:
    """
    Parse a notification category sub-page.

    DataTables layout (table#example2 / table.dataTable):
      Column 0 → Title
      Column 1 → Start Date  (DD-MM-YYYY)  → published_at
      Column 2 → End Date    (DD-MM-YYYY)  → end_date  ← used for filtering
      Column 3 → Format      (PDF / Excel)
      Column 4 → Language
      Column 5 → Type / Size
      Column 6 → Action      (<a.table_view_btn href="…pdf">)

    Filter: Language == English  AND  end_date >= 2026-01-01
    """
    soup = BeautifulSoup(html, "html.parser")

    # Locate the DataTables table — try multiple selectors for robustness
    table = (
        soup.select_one("table#example2")
        or soup.select_one("table.dataTable")
        or soup.select_one(".field--name-body table")
    )
    if not table:
        logger.warning("[fertilizers] No DataTables table found on: %s", page_url)
        return []

    items: list[ScrapedItem] = []
    for row in table.select("tbody tr"):
        cells = row.select("td")
        if len(cells) < 7:
            continue

        # ── Language filter (col 4) ────────────────────────────────────────
        lang = _clean(cells[4].get_text()).lower()
        if lang and lang != "english":
            continue

        # ── Title (col 0) ─────────────────────────────────────────────────
        title = _clean(cells[0].get_text())
        if not title:
            continue

        # ── Dates (col 1 = start/published, col 2 = end) ──────────────────
        published_at = _parse_dmy(_clean(cells[1].get_text()))
        end_date     = _parse_dmy(_clean(cells[2].get_text()))

        # Filter: end_date must be on or after Jan 2026
        # If end_date is unparseable, fall back to start date for the check
        reference_date = end_date or published_at
        if reference_date and reference_date < _MIN_DATE:
            continue

        # ── PDF link (col 6) ──────────────────────────────────────────────
        link_tag = cells[6].select_one("a.table_view_btn")
        if not link_tag:
            # Fallback: any <a href> in the action cell
            link_tag = cells[6].select_one("a[href]")
        if not link_tag:
            continue
        href = (link_tag.get("href") or "").strip()
        if not href:
            continue
        link = href if href.startswith("http") else urljoin(_BASE, href)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            end_date=end_date,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Notifications",
        ))

    return items


async def _fetch_subpage(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
) -> list[ScrapedItem]:
    async with semaphore:
        try:
            resp = await client.get(url, timeout=30)
            resp.raise_for_status()
            items = _parse_notification_subpage(resp.text, url)
            logger.info("[fertilizers] Sub-page %s → %d items", url.split("/")[-1], len(items))
            return items
        except Exception as exc:
            logger.warning("[fertilizers] Sub-page fetch failed %s: %s", url, exc)
            return []


# ── Main entry point ──────────────────────────────────────────────────────────

async def crawl_fertilizers(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    seen_links: set[str] = set()

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
    ) as client:

        # ── 1. What's New  (paginated) ────────────────────────────────────
        for page_num in range(_MAX_PAGES):
            url = f"{_WHATS_NEW_URL}?page={page_num}" if page_num else _WHATS_NEW_URL
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("[fertilizers] What's New page %d failed: %s", page_num, exc)
                break

            page_items = _parse_whatsnew_page(resp.text)
            if not page_items:
                logger.info("[fertilizers] What's New page %d: 0 items — stopping pagination", page_num)
                break

            for item in page_items:
                if item.link not in seen_links:
                    seen_links.add(item.link)
                    all_items.append(item)
            logger.info("[fertilizers] What's New page %d: %d items", page_num, len(page_items))

        # ── 2. Notifications listing  →  collect sub-page URLs ────────────
        subpage_urls: list[str] = []
        for page_num in range(_MAX_PAGES):
            url = f"{_NOTIF_LIST_URL}?page={page_num}" if page_num else _NOTIF_LIST_URL
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("[fertilizers] Notifications listing page %d failed: %s", page_num, exc)
                break

            urls = _extract_subpage_urls(resp.text)
            if not urls:
                logger.info("[fertilizers] Notifications listing page %d: no sub-page URLs — stopping", page_num)
                break

            subpage_urls.extend(urls)
            logger.info("[fertilizers] Notifications listing page %d: %d sub-pages found", page_num, len(urls))

        subpage_urls = list(dict.fromkeys(subpage_urls))  # deduplicate, preserve order
        logger.info("[fertilizers] Total notification sub-pages to fetch: %d", len(subpage_urls))

        # ── 3. Fetch each notification sub-page concurrently ──────────────
        if subpage_urls:
            semaphore = asyncio.Semaphore(_CONCURRENCY)
            results = await asyncio.gather(*[
                _fetch_subpage(client, semaphore, url)
                for url in subpage_urls
            ])
            for page_items in results:
                for item in page_items:
                    if item.link not in seen_links:
                        seen_links.add(item.link)
                        all_items.append(item)

    logger.info("[fertilizers] Grand total items: %d", len(all_items))
    return all_items

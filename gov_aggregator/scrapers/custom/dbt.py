from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://dbt.gov.in"

WHATS_NEW_URL = "https://dbt.gov.in/offerings/whats-new-list-page"
ORDERS_URL = "https://dbt.gov.in/documents/order-and-notices"
PRESS_URL = "https://dbt.gov.in/offerings/dbt-press"

TABLE_SELECTOR = "table.mantine-Table-table tbody tr td"   # used for Playwright wait_for_selector
TABLE_ROW_SELECTOR = "table.mantine-Table-table tbody tr"  # used for BeautifulSoup row iteration
BADGE_SELECTOR = ".m_5add502a"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_ARIA_PREFIX_RE = re.compile(r"^Document title:\s*", re.IGNORECASE)


def _parse_dbt_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    for pattern in (
        r"(\d{2})-(\d{2})-(\d{4})",
        r"(\d{2})/(\d{2})/(\d{4})",
    ):
        m = re.search(pattern, raw)
        if m:
            try:
                return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _extract_whats_new_items(html: str, section_label: str) -> list[ScrapedItem]:
    """
    Parse What's New table (offerings/whats-new-list-page).
    Cols: S.No | Title | Start Date | End Date | Details (one or more PDF links)
    Emits one ScrapedItem per PDF link found in the Details cell.
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(TABLE_ROW_SELECTOR):
        tds = row.find_all("td")
        if len(tds) < 5:
            continue

        title_td = tds[1]
        title_div = title_td.find("div", attrs={"aria-label": True})
        if title_div:
            raw_label = title_div["aria-label"].strip()
            title = _ARIA_PREFIX_RE.sub("", raw_label).strip()
        else:
            title = title_td.get_text(strip=True)
        if not title:
            continue

        start_date_raw = tds[2].get_text(strip=True)
        published_at = _parse_dbt_date(start_date_raw)

        # Collect all PDF/document links from the Details cell
        anchors = [a for a in tds[4].find_all("a", href=True) if a["href"].strip() and a["href"].strip() != "#"]
        if not anchors:
            continue

        total = len(anchors)
        for idx, anchor in enumerate(anchors):
            href = anchor["href"].strip()
            link = href if href.startswith("http") else urljoin(BASE_URL, href)
            is_pdf = link.lower().endswith(".pdf") or "/storage/media" in link.lower()
            label = section_label if total == 1 else f"{section_label} (PDF {idx + 1} of {total})"
            items.append(ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                end_date=None,   # What's New only shows published date
                is_pdf=is_pdf,
                section_label=label,
            ))

    return items


def _extract_orders_items(html: str, section_label: str) -> list[ScrapedItem]:
    """
    Parse Orders & Notices table (documents/order-and-notices).
    Table columns (1-based index):
      1: S.No
      2: Title
      3: Category
      4: Start Date
      5: End Date
      6: Extension
      7: Size
      8: Details (PDF link)
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(TABLE_ROW_SELECTOR):
        tds = row.find_all("td")
        if len(tds) < 8:
            continue

        title_td = tds[1]
        title_div = title_td.find("div", attrs={"aria-label": True})
        if title_div:
            raw_label = title_div["aria-label"].strip()
            title = _ARIA_PREFIX_RE.sub("", raw_label).strip()
        else:
            title = title_td.get_text(strip=True)
        if not title:
            continue

        anchor = tds[7].find("a", href=True)
        if not anchor:
            continue
        href = anchor["href"].strip()
        if not href or href == "#":
            continue
        link = href if href.startswith("http") else urljoin(BASE_URL, href)

        start_date_raw = tds[3].get_text(strip=True)
        published_at = _parse_dbt_date(start_date_raw)

        is_pdf = link.lower().endswith(".pdf") or link.lower().endswith(".jpg") or "/storage/media" in link.lower()

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                is_pdf=is_pdf,
                section_label=section_label,
            )
        )

    return items


def _extract_press_items(html: str, section_label: str) -> list[ScrapedItem]:
    """
    Parse DBT Press table (offerings/dbt-press).
    Table columns (1-based index):
      1: S.No
      2: Title
      3: Start Date
      4: End Date
      5: Extension
      6: Size
      7: Details (PDF link)
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(TABLE_ROW_SELECTOR):
        tds = row.find_all("td")
        if len(tds) < 7:
            continue

        title_td = tds[1]
        title_div = title_td.find("div", attrs={"aria-label": True})
        if title_div:
            raw_label = title_div["aria-label"].strip()
            title = _ARIA_PREFIX_RE.sub("", raw_label).strip()
        else:
            title = title_td.get_text(strip=True)
        if not title:
            continue

        anchor = tds[6].find("a", href=True)
        if not anchor:
            continue
        href = anchor["href"].strip()
        if not href or href == "#":
            continue
        link = href if href.startswith("http") else urljoin(BASE_URL, href)

        start_date_raw = tds[2].get_text(strip=True)
        published_at = _parse_dbt_date(start_date_raw)

        end_date_raw = tds[3].get_text(strip=True)
        end_date = _parse_dbt_date(end_date_raw)

        is_pdf = link.lower().endswith(".pdf") or "/storage/media" in link.lower()

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                end_date=end_date,
                is_pdf=is_pdf,
                section_label=section_label,
            )
        )

    return items


def _run_whats_new_sync() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []
    seen_links: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(WHATS_NEW_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5000)
            while True:
                try:
                    page.wait_for_selector(TABLE_SELECTOR, timeout=30000)
                except Exception:
                    page.wait_for_timeout(3000)
                for item in _extract_whats_new_items(page.content(), "What's New"):
                    if item.link not in seen_links:
                        seen_links.add(item.link)
                        all_items.append(item)
                if not _click_next(page):
                    break

        finally:
            browser.close()

    return all_items


def _run_orders_sync() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []
    seen_links: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(ORDERS_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5000)
            while True:
                try:
                    page.wait_for_selector(TABLE_SELECTOR, timeout=30000)
                except Exception:
                    page.wait_for_timeout(3000)
                for item in _extract_orders_items(page.content(), "Orders and Notices"):
                    if item.link not in seen_links:
                        seen_links.add(item.link)
                        all_items.append(item)
                if not _click_next(page):
                    break

        finally:
            browser.close()

    return all_items


def _click_next(page) -> bool:
    """Click the Mantine 'Next page' arrow button.

    Mantine pagination renders 4 icon-only nav buttons (first/prev/next/last)
    that have no data-with-padding attribute, while numbered page buttons do.
    Index 2 (0-based) among those nav buttons is always 'next'.
    """
    try:
        all_controls = page.query_selector_all(".mantine-Pagination-control")
        nav_btns = [b for b in all_controls if b.get_attribute("data-with-padding") is None]
        if len(nav_btns) < 3:
            return False
        next_btn = nav_btns[2]
        if (next_btn.get_attribute("data-disabled") == "true"
                or next_btn.get_attribute("disabled") is not None):
            return False
        next_btn.click()
        page.wait_for_timeout(2500)
        return True
    except Exception:
        return False


def _run_press_sync() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []
    seen_links: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(PRESS_URL, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(5000)
            while True:
                try:
                    page.wait_for_selector(TABLE_SELECTOR, timeout=30000)
                except Exception:
                    page.wait_for_timeout(3000)
                for item in _extract_press_items(page.content(), "DBT Press"):
                    if item.link not in seen_links:
                        seen_links.add(item.link)
                        all_items.append(item)
                if not _click_next(page):
                    break

        finally:
            browser.close()

    return all_items


async def crawl_dbt(_config: SiteConfig) -> list[ScrapedItem]:
    """
    Crawl DBT sections:
      • What's New — /offerings/whats-new-list-page
      • Orders and Notices — /documents/order-and-notices
      • DBT Press — /offerings/dbt-press
    """
    import asyncio
    loop = asyncio.get_running_loop()
    
    whats_new = await loop.run_in_executor(None, _run_whats_new_sync)
    orders = await loop.run_in_executor(None, _run_orders_sync)
    press = await loop.run_in_executor(None, _run_press_sync)

    all_items = whats_new + orders + press

    seen: set[str] = set()
    deduped: list[ScrapedItem] = []
    for item in all_items:
        if item.link and item.link not in seen:
            seen.add(item.link)
            deduped.append(item)

    deduped.sort(
        key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return deduped
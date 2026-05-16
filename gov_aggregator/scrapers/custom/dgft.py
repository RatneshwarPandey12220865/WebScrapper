from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

DGFT_BASE_URL = "https://www.dgft.gov.in"
DGFT_CONTENT_URL = "https://content.dgft.gov.in"
MAX_PAGES = 5
ROW_WAIT_TIMEOUT = 20_000   # ms
PAGE_NAV_TIMEOUT = 12_000   # ms

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_rows(html: str, seen_links: set[str]) -> list[ScrapedItem]:
    """
    Parse one DataTable page from page.content().

    Column order (from live DGFT HTML):
      0 = Sl.No.
      1 = Number
      2 = Year
      3 = Description   (may be display:none but still in DOM)
      4 = Date          (may be display:none but still in DOM)
      5 = Attachment    (may be display:none but still in DOM)
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#metadataTable tbody tr.odd, #metadataTable tbody tr.even")
    items: list[ScrapedItem] = []

    for row in rows:
        cells = row.select("td")
        if len(cells) < 3:
            continue

        number = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        # column 2 is Year — already embedded in the Number field, skip it
        desc   = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        date_raw = cells[4].get_text(strip=True) if len(cells) > 4 else ""

        # Build a readable title — Number already contains the year on DGFT (e.g. "PN 12/2026-27")
        if desc:
            title = f"{number} - {desc}" if number else desc
        elif number:
            title = number
        else:
            continue

        published_at = _parse_date(date_raw)

        # Link: prefer the attachment anchor in column 5
        href = ""
        if len(cells) > 5:
            a_tag = cells[5].select_one("a[href]")
            if a_tag:
                href = (a_tag.get("href") or "").strip()

        if not href:
            # Fallback to any anchor in the row
            a_tag = row.select_one("a[href]")
            if a_tag:
                href = (a_tag.get("href") or "").strip()

        if not href or "javascript" in href.lower():
            continue

        if not href.startswith("http"):
            href = urljoin(DGFT_CONTENT_URL if href.startswith("/") else DGFT_BASE_URL, href)

        if href in seen_links:
            continue
        seen_links.add(href)

        items.append(ScrapedItem(
            title=title,
            link=href,
            published_at=published_at,
            is_pdf=href.lower().endswith(".pdf"),
        ))

    return items


def _run_playwright_scrape() -> list[ScrapedItem]:
    """
    Runs synchronous Playwright in a fresh event loop (called via run_in_executor
    to keep it off the uvicorn loop on Windows).
    """
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []
    seen_links: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()

        try:
            page.goto("https://www.dgft.gov.in/CP/", wait_until="domcontentloaded", timeout=60_000)

            # Click the "All" tab to show all document types in one table
            try:
                page.get_by_role("link", name="All").click(timeout=10_000)
            except Exception:
                pass  # may already be on "All" view

            # Click "View More" to trigger the DataTable AJAX load
            try:
                page.get_by_role("button", name="View More").click(timeout=10_000)
            except Exception:
                pass

            # Wait for actual data rows
            try:
                page.wait_for_selector(
                    "#metadataTable tbody tr.odd",
                    timeout=ROW_WAIT_TIMEOUT,
                )
            except Exception:
                # Table did not load — return empty
                return []

            for page_num in range(1, MAX_PAGES + 1):
                html = page.content()
                items = _parse_rows(html, seen_links)
                all_items.extend(items)

                if page_num >= MAX_PAGES:
                    break

                # Navigate to next DataTable page
                next_label = str(page_num + 1)
                next_btn = page.get_by_role("link", name=next_label, exact=True)
                try:
                    if not next_btn.is_visible(timeout=3_000):
                        break
                    next_btn.click(timeout=5_000)
                    page.wait_for_selector(
                        "#metadataTable tbody tr.odd",
                        timeout=PAGE_NAV_TIMEOUT,
                    )
                except Exception:
                    break

        finally:
            context.close()
            browser.close()

    return all_items


async def crawl_dgft(_config: SiteConfig) -> list[ScrapedItem]:
    """
    DGFT is a JS SPA. Data only appears after:
      1. Navigating to https://www.dgft.gov.in/CP/
      2. Clicking the "All" tab
      3. Clicking the "View More" button (triggers DataTables AJAX load)

    Playwright is run in a thread executor to keep it off the uvicorn event loop.
    """
    loop = asyncio.get_event_loop()
    items: list[ScrapedItem] = await loop.run_in_executor(None, _run_playwright_scrape)
    return items

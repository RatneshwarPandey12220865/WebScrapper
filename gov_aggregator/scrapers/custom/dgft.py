from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dgft")

DGFT_BASE_URL    = "https://www.dgft.gov.in"
DGFT_CONTENT_URL = "https://content.dgft.gov.in"
MAX_PAGES        = 5
ROW_WAIT_MS      = 45_000   # wait for first data row
PAGE_NAV_MS      = 20_000   # wait after pagination click

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


def _parse_rows(html: str, seen_links: set[str], section_label: str) -> list[ScrapedItem]:
    """
    Parse one DataTable page from page.content().

    Column order (from live DGFT HTML):
      0 = Sl.No.
      1 = Number
      2 = Year
      3 = Description
      4 = Date
      5 = Attachment  (<a href> → PDF link)
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#metadataTable tbody tr")
    items: list[ScrapedItem] = []

    for row in rows:
        cells = row.select("td")
        if len(cells) < 3:
            continue

        number   = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        desc     = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        date_raw = cells[4].get_text(strip=True) if len(cells) > 4 else ""

        title = f"{number} - {desc}" if number and desc else (number or desc)
        if not title:
            continue

        published_at = _parse_date(date_raw)

        # Primary: attachment link in col 5
        href = ""
        if len(cells) > 5:
            a_tag = cells[5].select_one("a[href]")
            if a_tag:
                href = (a_tag.get("href") or "").strip()

        # Fallback: any anchor in row
        if not href:
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
            section_label=section_label,
        ))

    return items


def _scrape_section(page, section_label: str, seen_links: set[str]) -> list[ScrapedItem]:
    """
    Extract up to MAX_PAGES from #metadataTable for the currently active section tab.
    Table must already be visible before calling this.
    """
    all_items: list[ScrapedItem] = []

    for page_num in range(1, MAX_PAGES + 1):
        html  = page.content()
        items = _parse_rows(html, seen_links, section_label)
        all_items.extend(items)
        logger.info("[dgft] Section=%r page=%d → %d items", section_label, page_num, len(items))

        if page_num >= MAX_PAGES:
            break

        # Navigate to next DataTable page
        next_btn = page.get_by_role("link", name=str(page_num + 1), exact=True)
        try:
            if not next_btn.is_visible(timeout=3_000):
                break
            next_btn.click(timeout=5_000)
            page.wait_for_selector(
                "#metadataTable tbody tr",
                timeout=PAGE_NAV_MS,
            )
        except Exception:
            break

    return all_items


def _run_playwright_scrape() -> list[ScrapedItem]:
    """
    Synchronous Playwright scraper — runs in a thread executor.

    Flow (mirrors browser codegen recording):
      1. Navigate to https://www.dgft.gov.in/CP/
      2. Click "View More" to trigger DataTables AJAX load
      3. Discover all section tabs dynamically
      4. For each tab: click it → wait for rows → extract up to MAX_PAGES
    """
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []
    seen_links: set[str] = set()
    browser = None
    context = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                viewport={"width": 1400, "height": 900},
            )
            page = context.new_page()

            # ── Step 1: load the page ──────────────────────────────────────
            page.goto("https://www.dgft.gov.in/CP/", wait_until="domcontentloaded", timeout=60_000)

            # ── Step 2: click "View More" → triggers DataTables AJAX ───────
            try:
                page.get_by_role("button", name="View More").click(timeout=15_000)
            except Exception:
                logger.warning("[dgft] 'View More' button not found — trying to proceed")

            # Wait for at least one data row to appear
            try:
                page.wait_for_selector(
                    "#metadataTable tbody tr",
                    timeout=ROW_WAIT_MS,
                )
            except Exception:
                logger.error("[dgft] #metadataTable rows never appeared — aborting")
                return []

            # ── Step 3: discover section tabs ──────────────────────────────
            # DGFT renders section tabs as <li> or <a> inside a nav/tab strip.
            # We collect their text labels and locators for iteration.
            #
            # Selector covers both Bootstrap tabs and the custom DGFT tab strip:
            #   ul.nav-tabs a[data-toggle], div.tabs-container a[role="tab"]
            tab_locators = page.locator(
                "ul.nav-tabs a, ul.nav li a[data-toggle='tab'], "
                ".tabs-container [role='tab'], "
                ".dgft-tabs a, .tab-links a"
            ).all()

            # Deduplicate by visible label; skip empty / icon-only tabs
            seen_labels: set[str] = set()
            section_tabs: list[tuple[str, object]] = []
            for loc in tab_locators:
                try:
                    label = (loc.inner_text(timeout=2_000) or "").strip()
                except Exception:
                    continue
                if not label or label in seen_labels:
                    continue
                seen_labels.add(label)
                section_tabs.append((label, loc))

            logger.info("[dgft] Found %d section tabs: %s", len(section_tabs), [l for l, _ in section_tabs])

            # If no tabs found, just scrape current view as "All"
            if not section_tabs:
                logger.info("[dgft] No tabs found — scraping current view as 'All'")
                all_items.extend(_scrape_section(page, "All", seen_links))
                return all_items

            # ── Step 4: iterate each section tab ──────────────────────────
            for section_label, tab_loc in section_tabs:
                try:
                    tab_loc.click(timeout=8_000)
                    # Wait for table to refresh with new section's data
                    page.wait_for_selector(
                        "#metadataTable tbody tr",
                        timeout=PAGE_NAV_MS,
                    )
                except Exception as exc:
                    logger.warning("[dgft] Could not activate tab %r: %s", section_label, exc)
                    continue

                section_items = _scrape_section(page, section_label, seen_links)
                all_items.extend(section_items)
                logger.info("[dgft] Section=%r total=%d", section_label, len(section_items))

    except Exception as exc:
        logger.error("[dgft] Playwright scrape failed: %s", exc)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    logger.info("[dgft] Grand total: %d items", len(all_items))
    return all_items


async def crawl_dgft(_config: SiteConfig) -> list[ScrapedItem]:
    """
    DGFT is a JS SPA. Data only appears after:
      1. Navigating to https://www.dgft.gov.in/CP/
      2. Clicking "View More" (triggers DataTables AJAX load)
      3. Iterating each section tab and extracting #metadataTable

    Playwright runs in a thread executor (Windows ProactorEventLoop workaround).
    Hard 3-minute cap prevents a crashed browser from blocking bulk crawl.
    """
    loop = asyncio.get_running_loop()
    try:
        items: list[ScrapedItem] = await asyncio.wait_for(
            loop.run_in_executor(None, _run_playwright_scrape),
            timeout=180,
        )
    except asyncio.TimeoutError:
        logger.warning("[dgft] Playwright scrape timed out after 3 minutes")
        items = []
    return items

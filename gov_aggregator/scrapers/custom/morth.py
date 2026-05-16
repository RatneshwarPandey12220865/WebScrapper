"""
Custom crawler for Ministry of Road Transport and Highways (morth.gov.in).

The site is an Angular SPA with hash routing — plain HTTP returns an empty
shell. All rendering requires Playwright.

Actual HTML structure (verified from inspect):

  What's New  (/whats-new):
    Container : div.list_det_bx.tender_list > div.row
    Title col : div.col-md-8 > div.det_cont > p   (img + text inside p)
    Date col  : div.col-md-2 > div.det_cont > small
    Link col  : div.col-md-2 > div.det_cont.last_vw > div.viewdiv > a.viw[href]

  Gazette Notifications + Orders & Notices (/documents/…):
    Container : div.list_det_bx.tender_list > div.row
    Title col : div.col-md-7 > div.det_cont > span  (may also have .counter-box)
    Date col  : div.col-md-2 > div.det_cont > small
    Link col  : div.col-md-3 > div.det_cont.last_vw > div.viewdiv > a.viw[href]
    SKIP rows : those with .counter-box (category groups, no real href)

  Pagination : <button class="button-item next"> — click to advance;
               disabled="" attribute means last page.

Sections scraped:
  1. What's New               — date-filtered via config.min_date
  2. Gazette Notifications    — all pages, no date filter
  3. Orders / Circulars       — all pages, no date filter
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.morth")

_BASE         = "https://morth.gov.in"
_GOTO_TIMEOUT = 35_000   # ms
_WAIT_TIMEOUT = 15_000   # ms
_MAX_PAGES    = 100      # safety ceiling


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    """Parse DD.MM.YYYY / DD-MM-YYYY / YYYY-MM-DD date strings."""
    if not raw:
        return None
    # YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            pass
    # DD.MM.YYYY or DD-MM-YYYY or DD/MM/YYYY
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", raw)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _abs(href: str | None) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else urljoin(_BASE, href)


def _select_english(page) -> None:
    try:
        combo = page.get_by_role("combobox", name="भाषा अनुवादक")
        combo.wait_for(timeout=5_000)
        combo.select_option("en")
        page.wait_for_timeout(1_500)
    except Exception:
        pass


def _wait_for_rows(page) -> None:
    try:
        page.wait_for_selector("div.list_det_bx", timeout=_WAIT_TIMEOUT)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------

def _parse_whats_new(soup: BeautifulSoup) -> list[ScrapedItem]:
    """
    What's New page layout:
      col-md-8  → p (title, contains img + text)
      col-md-2  → small (date)
      col-md-2  → a.viw[href] (document link)
    """
    items: list[ScrapedItem] = []

    for row in soup.select("div.list_det_bx.tender_list div.row"):
        # Skip category-group rows (they have no real href, just cursor:pointer)
        a_el = row.select_one("a.viw[href]")
        if not a_el:
            continue

        # Title: get text from the p tag, stripping img alt text
        p_el = row.select_one("div.col-md-8 p, div.col-md-7 p")
        title = ""
        if p_el:
            # Remove img tags then get text
            for img in p_el.find_all("img"):
                img.decompose()
            title = _clean(p_el.get_text())

        if not title:
            # fallback: use span if p is missing
            span_el = row.select_one("span")
            if span_el:
                title = _clean(span_el.get_text())

        link = _abs(a_el["href"])
        if not title or not link:
            continue

        date_el = row.select_one("div.col-md-2 small")
        date_str = _clean(date_el.get_text() if date_el else "")

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=_parse_date(date_str),
            is_pdf=bool(re.search(r"\.(pdf|PDF)$", link)),
            section_label="What's New",
        ))

    return items


def _parse_doc_section(soup: BeautifulSoup, section_label: str) -> list[ScrapedItem]:
    """
    Document section layout (gazette / orders):
      col-md-7  → span (title) + optional .counter-box for category groups
      col-md-2  → small (date)
      col-md-3  → a.viw[href] (document link)

    Rows with .counter-box have no href — they are category headers. Skip them.
    """
    items: list[ScrapedItem] = []

    for row in soup.select("div.list_det_bx.tender_list div.row"):
        # Skip category-group rows (no real document href)
        if row.select_one("div.counter-box"):
            continue

        a_el = row.select_one("a.viw[href]")
        if not a_el:
            continue

        span_el = row.select_one("div.col-md-7 span, div.col-md-8 span")
        title = ""
        if span_el:
            title = _clean(span_el.get_text())
        if not title:
            p_el = row.select_one("p")
            if p_el:
                for img in p_el.find_all("img"):
                    img.decompose()
                title = _clean(p_el.get_text())

        link = _abs(a_el["href"])
        if not title or not link:
            continue

        date_el = row.select_one("div.col-md-2 small")
        date_str = _clean(date_el.get_text() if date_el else "")

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=_parse_date(date_str),
            is_pdf=bool(re.search(r"\.(pdf|PDF)$", link)),
            section_label=section_label,
        ))

    return items


def _next_btn_enabled(page) -> bool:
    """Return True if the Next pagination button exists and is not disabled."""
    try:
        btn = page.query_selector("button.button-item.next")
        if btn is None:
            return False
        disabled = btn.get_attribute("disabled")
        return disabled is None   # None means attribute absent = enabled
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Section fetchers
# ---------------------------------------------------------------------------

def _fetch_whats_new(page, min_date: datetime | None) -> list[ScrapedItem]:
    """
    Navigate to /whats-new, scrape page by page clicking Next.
    Stop early when all dated items on a page are older than min_date.
    """
    all_items: list[ScrapedItem] = []

    # Initial navigation
    try:
        page.goto(f"{_BASE}/#/whats-new", wait_until="domcontentloaded",
                  timeout=_GOTO_TIMEOUT)
        _select_english(page)
        _wait_for_rows(page)
    except Exception as exc:
        logger.warning("[morth] failed to load whats-new: %s", exc)
        return all_items

    for page_num in range(1, _MAX_PAGES + 1):
        soup  = BeautifulSoup(page.content(), "html.parser")
        items = _parse_whats_new(soup)
        logger.debug("[morth] What's New page %d: %d rows", page_num, len(items))

        if not items:
            break

        if min_date:
            fresh = [i for i in items
                     if i.published_at is None or i.published_at >= min_date]
            all_items.extend(fresh)
            dated = [i for i in items if i.published_at is not None]
            if dated and all(i.published_at < min_date for i in dated):
                logger.info("[morth] What's New: min_date cutoff reached at page %d", page_num)
                break
        else:
            all_items.extend(items)

        if not _next_btn_enabled(page):
            break

        # Click Next and wait for content to re-render
        try:
            page.click("button.button-item.next")
            page.wait_for_timeout(2_000)
            _wait_for_rows(page)
        except Exception as exc:
            logger.warning("[morth] What's New next-page click failed: %s", exc)
            break

    logger.info("[morth] What's New total: %d items", len(all_items))
    return all_items


def _fetch_paginated_section(page, path: str, section_label: str) -> list[ScrapedItem]:
    """Navigate to a document section and scrape ALL pages by clicking Next."""
    all_items: list[ScrapedItem] = []

    try:
        page.goto(f"{_BASE}/#/{path}", wait_until="domcontentloaded",
                  timeout=_GOTO_TIMEOUT)
        _select_english(page)
        _wait_for_rows(page)
    except Exception as exc:
        logger.warning("[morth] failed to load %s: %s", path, exc)
        return all_items

    for page_num in range(1, _MAX_PAGES + 1):
        soup  = BeautifulSoup(page.content(), "html.parser")
        items = _parse_doc_section(soup, section_label)
        logger.debug("[morth] %s page %d: %d rows", section_label, page_num, len(items))

        if not items:
            break

        all_items.extend(items)

        if not _next_btn_enabled(page):
            break

        try:
            page.click("button.button-item.next")
            page.wait_for_timeout(2_000)
            _wait_for_rows(page)
        except Exception as exc:
            logger.warning("[morth] %s next-page click failed: %s", section_label, exc)
            break

    logger.info("[morth] %s total: %d items", section_label, len(all_items))
    return all_items


# ---------------------------------------------------------------------------
# Main sync crawl
# ---------------------------------------------------------------------------

def _sync_crawl(config: SiteConfig) -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    min_date: datetime | None = None
    if config.min_date:
        try:
            min_date = datetime.strptime(config.min_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
        except ValueError:
            pass

    all_items: list[ScrapedItem] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            locale="en-US",
        )
        page = ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())

        # 1. What's New (date-filtered)
        all_items.extend(_fetch_whats_new(page, min_date))

        # 2. Gazette Notifications (all pages)
        all_items.extend(_fetch_paginated_section(
            page, "documents/gazettes-notifications", "Gazette Notifications"))

        # 3. Orders / Circulars / Notices (all pages)
        all_items.extend(_fetch_paginated_section(
            page, "documents/orders-and-notices", "Orders & Circulars / Notices"))

        ctx.close()
        browser.close()

    # Deduplicate by link
    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in all_items:
        if item.link and item.link not in seen:
            seen.add(item.link)
            unique.append(item)

    logger.info("[morth] Total unique items: %d", len(unique))
    return unique


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------

async def crawl_morth(config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_crawl, config)

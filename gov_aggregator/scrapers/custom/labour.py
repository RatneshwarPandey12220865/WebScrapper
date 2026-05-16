"""
Custom crawler for Ministry of Labour and Employment (www.labour.gov.in).

The site is a Next.js SPA — all pages are client-side rendered and return
an empty shell (~7 KB) to plain HTTP requests. Every section requires
Playwright. A single browser session handles all sections to minimise
overhead.

Sections scraped:
  1. What's New announcements      (/whats-new)
  2. Schemes and Services          (/whats-new  +  detail pages for internal links)
  3. Press Release                 (/whats-new  +  /documents/press-release paginated)
  4. Orders & Notices              (/documents/orders-and-notices paginated)
  5. Gazette Notifications         (/documents/gazettes-notifications paginated)
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

logger = logging.getLogger("gov_aggregator.custom.labour")

_BASE = "https://www.labour.gov.in"
_GOTO_TIMEOUT = 30_000   # ms
_WAIT_TIMEOUT  = 12_000  # ms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", raw or "")
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _abs(href: str) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else urljoin(_BASE, href)


def _select_english(page) -> None:
    try:
        page.get_by_role("combobox", name="भाषा अनुवादक").select_option("en")
        page.wait_for_timeout(1500)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTML parsers (operate on BeautifulSoup objects)
# ---------------------------------------------------------------------------

def _parse_announcement_rows(soup: BeautifulSoup, section_label: str) -> list[ScrapedItem]:
    items = []
    container = soup.select_one('[aria-label="What\'s new announcements"]') or soup
    for row in container.select("div.announcementbox"):
        t_el = row.select_one("p.mb-0")
        a_el = row.select_one("a.download-btn[href]")
        title = _clean(t_el.get_text() if t_el else "")
        link  = _abs(a_el["href"]) if a_el else ""
        if not title or not link:
            continue
        items.append(ScrapedItem(
            title=title, link=link,
            published_at=_parse_date(title),
            is_pdf=link.lower().endswith(".pdf"),
            section_label=section_label,
        ))
    return items


def _parse_doc_table(soup: BeautifulSoup, aria_label: str, section_label: str) -> list[ScrapedItem]:
    items = []
    table = soup.select_one(f'[role="table"][aria-label="{aria_label}"]')
    if not table:
        return items
    for row in table.select("div.announcementbox"):
        t_el   = row.select_one("p.mb-0")
        date_el = row.select_one("small[aria-label]")
        a_el   = row.select_one("a.download-btn[href]")
        title  = _clean(t_el.get_text() if t_el else "")
        link   = _abs(a_el["href"]) if a_el else ""
        if not title or not link:
            continue
        date_str = (date_el.get("aria-label", "") if date_el else "") or title
        items.append(ScrapedItem(
            title=title, link=link,
            published_at=_parse_date(date_str),
            is_pdf=link.lower().endswith(".pdf"),
            section_label=section_label,
        ))
    return items


def _parse_schemes_section(soup: BeautifulSoup) -> tuple[list[ScrapedItem], list[tuple[str, str]]]:
    """
    Returns:
        direct_items  – items whose link is already a direct PDF / external URL
        detail_needed – [(title, detail_url)] for internal /offerings/... pages
    """
    direct: list[ScrapedItem] = []
    detail_needed: list[tuple[str, str]] = []

    table = soup.select_one('[role="table"][aria-label="schemes_and_services data"]')
    if not table:
        return direct, detail_needed

    for row in table.select("div.announcementbox"):
        t_el = row.select_one("div.text-break, p.mb-0")
        a_el = row.select_one("a.link-btn[href]")
        if not t_el or not a_el:
            continue
        title = _clean(t_el.get_text())
        href  = a_el["href"]
        if not title or not href:
            continue

        if href.startswith("http"):
            direct.append(ScrapedItem(
                title=title, link=href,
                is_pdf=href.lower().endswith(".pdf"),
                section_label="Schemes and Services",
            ))
        else:
            detail_needed.append((title, _abs(href)))

    return direct, detail_needed


def _parse_press_release_whats_new(soup: BeautifulSoup) -> list[ScrapedItem]:
    """Parse the Press Release sub-table embedded in the /whats-new page."""
    return _parse_doc_table(soup, "press-release data", "Press Release")


# ---------------------------------------------------------------------------
# Paginated document page fetcher
# ---------------------------------------------------------------------------

def _fetch_paginated(page, url_base: str, aria_label: str, section_label: str,
                     max_pages: int = 10) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    for page_num in range(max_pages):
        url = url_base if page_num == 0 else f"{url_base}?page={page_num}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
            page.wait_for_selector("div.announcementbox", timeout=_WAIT_TIMEOUT)
        except Exception:
            break
        soup = BeautifulSoup(page.content(), "html.parser")
        rows = _parse_doc_table(soup, aria_label, section_label)
        if not rows:
            break
        items.extend(rows)
    return items


# ---------------------------------------------------------------------------
# Detail page fetcher (Schemes and Services internal links)
# ---------------------------------------------------------------------------

def _fetch_scheme_detail(page, scheme_title: str, detail_url: str) -> list[ScrapedItem]:
    results: list[ScrapedItem] = []
    try:
        page.goto(detail_url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
        try:
            page.wait_for_selector("a.download-btn", timeout=_WAIT_TIMEOUT)
        except Exception:
            pass
        _select_english(page)

        soup = BeautifulSoup(page.content(), "html.parser")

        # Primary: docsCard layout
        for card in soup.select("div.docsCard"):
            t_el = card.select_one("p")
            a_el = card.select_one("a.download-btn[href]")
            title = _clean(t_el.get_text() if t_el else scheme_title)
            link  = _abs(a_el["href"]) if a_el else ""
            if link:
                results.append(ScrapedItem(
                    title=title or scheme_title, link=link,
                    is_pdf=link.lower().endswith(".pdf"),
                    section_label="Schemes and Services",
                ))

        # Fallback: any PDF anchor
        if not results:
            for a in soup.select("a[href$='.pdf'], a[href$='.PDF']"):
                link = _abs(a["href"])
                if not link:
                    continue
                row = a.find_parent(attrs={"role": "row"})
                t_el = row.select_one("p, div.text-break") if row else None
                title = _clean(t_el.get_text() if t_el else a.get_text()) or scheme_title
                results.append(ScrapedItem(
                    title=title, link=link, is_pdf=True,
                    section_label="Schemes and Services",
                ))

    except Exception as exc:
        logger.warning("[labour] detail page failed for %s: %s", scheme_title, exc)

    return results


# ---------------------------------------------------------------------------
# Main sync crawl (runs in thread executor)
# ---------------------------------------------------------------------------

def _sync_crawl() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=DEFAULT_HEADERS["User-Agent"])
        page = ctx.new_page()

        # ── 1 & 2 & 3a: /whats-new page ──────────────────────────────────
        try:
            page.goto(f"{_BASE}/whats-new", wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
            _select_english(page)
            try:
                page.wait_for_selector("div.announcementbox", timeout=_WAIT_TIMEOUT)
            except Exception:
                pass

            soup = BeautifulSoup(page.content(), "html.parser")

            # 1. What's New announcements
            wn_items = _parse_announcement_rows(soup, "What's New")
            logger.info("[labour] What's New: %d items", len(wn_items))
            all_items.extend(wn_items)

            # 2. Schemes and Services
            direct_schemes, detail_needed = _parse_schemes_section(soup)
            logger.info("[labour] Schemes direct: %d, need detail: %d",
                        len(direct_schemes), len(detail_needed))
            all_items.extend(direct_schemes)

            for scheme_title, detail_url in detail_needed:
                detail_items = _fetch_scheme_detail(page, scheme_title, detail_url)
                logger.info("[labour] Scheme '%s': %d PDF(s)", scheme_title, len(detail_items))
                all_items.extend(detail_items)

            # 3a. Press Release block on /whats-new
            pr_items = _parse_press_release_whats_new(soup)
            logger.info("[labour] Press Release (whats-new): %d items", len(pr_items))
            all_items.extend(pr_items)

        except Exception as exc:
            logger.warning("[labour] /whats-new failed: %s", exc)

        # ── 3b. /documents/press-release (paginated) ──────────────────────
        pr_doc = _fetch_paginated(
            page, f"{_BASE}/documents/press-release",
            "press-release data", "Press Release",
        )
        logger.info("[labour] Press Release (documents): %d items", len(pr_doc))
        all_items.extend(pr_doc)

        # ── 4. Orders & Notices (paginated) ───────────────────────────────
        orders = _fetch_paginated(
            page, f"{_BASE}/documents/orders-and-notices",
            "Orders and Notices data", "Orders & Notices",
        )
        logger.info("[labour] Orders & Notices: %d items", len(orders))
        all_items.extend(orders)

        # ── 5. Gazette Notifications (paginated) ──────────────────────────
        gazette = _fetch_paginated(
            page, f"{_BASE}/documents/gazettes-notifications",
            "Gazettes Notifications data", "Gazette Notifications",
        )
        logger.info("[labour] Gazette Notifications: %d items", len(gazette))
        all_items.extend(gazette)

        ctx.close()
        browser.close()

    # Deduplicate by link
    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in all_items:
        if item.link and item.link not in seen:
            seen.add(item.link)
            unique.append(item)

    return unique


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------

async def crawl_labour(_config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_crawl)

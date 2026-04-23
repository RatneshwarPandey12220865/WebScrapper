from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://dbt.gov.in"

# What's New: /archive?category=whats-new  — shows all 10 current items, no pagination
# Orders:     /archive?category=order-and-notices — full history, possibly paginated
# DBT Press:  /offerings/dbt-press — 136 items across 14 pages
WHATS_NEW_URL = "https://dbt.gov.in/archive?category=whats-new"
ORDERS_URL = "https://dbt.gov.in/archive?category=order-and-notices"
PRESS_URL = "https://dbt.gov.in/offerings/dbt-press"

TABLE_SELECTOR = "table.m_b23fa0ef tbody tr"
# Mantine badge label class — multiple badges may exist per page
BADGE_SELECTOR = ".m_5add502a"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Prefix that the site injects into every aria-label: "Document title: XYZ"
_ARIA_PREFIX_RE = re.compile(r"^Document title:\s*", re.IGNORECASE)


def _parse_dbt_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    for pattern in (
        r"(\d{2})-(\d{2})-(\d{4})",  # DD-MM-YYYY
        r"(\d{2})/(\d{2})/(\d{4})",  # DD/MM/YYYY
    ):
        m = re.search(pattern, raw)
        if m:
            try:
                return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _extract_table_items(
    html: str,
    section_label: str,
    link_col: int,
    date_col: int,
) -> list[ScrapedItem]:
    """
    Parse Mantine React table rows into ScrapedItems.

    Column indices (1-based):
      What's New        — link_col=8, date_col=3
      Orders & Notices  — link_col=8, date_col=4
      DBT Press         — link_col=7, date_col=3
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(TABLE_SELECTOR):
        tds = row.find_all("td")
        if len(tds) < max(link_col, date_col):
            continue

        # Title: the site puts aria-label="Document title: XYZ" on the div
        title_td = tds[1]
        title_div = title_td.find("div", attrs={"aria-label": True})
        if title_div:
            raw_label = title_div["aria-label"].strip()
            title = _ARIA_PREFIX_RE.sub("", raw_label).strip()
        else:
            title = title_td.get_text(strip=True)
        if not title:
            continue

        # Link: anchor in the designated column
        anchor = tds[link_col - 1].find("a", href=True)
        if not anchor:
            continue
        href = anchor["href"].strip()
        if not href or href == "#":
            continue
        link = href if href.startswith("http") else urljoin(BASE_URL, href)

        # Date
        date_raw = tds[date_col - 1].get_text(strip=True) if len(tds) >= date_col else None
        published_at = _parse_dbt_date(date_raw)

        is_pdf = link.lower().endswith(".pdf") or "/storage/media" in link.lower()

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


# ---------------------------------------------------------------------------
# Generic paginated section crawler (handles both single-page and multi-page)
# ---------------------------------------------------------------------------

def _run_section_sync(
    url: str,
    section_label: str,
    link_col: int,
    date_col: int,
) -> list[ScrapedItem]:
    """
    Launch Playwright in a fresh event loop (safe for run_in_executor).

    Automatically detects pagination via the "Page X of Y" badge.
    If no such badge exists the page is treated as single-page.
    """

    async def _async() -> list[ScrapedItem]:
        from playwright.async_api import async_playwright

        all_items: list[ScrapedItem] = []
        seen_links: set[str] = set()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, channel="msedge")
            page = await browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
            await page.set_extra_http_headers(
                {k: v for k, v in DEFAULT_HEADERS.items() if k != "User-Agent"}
            )
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                try:
                    await page.wait_for_selector(TABLE_SELECTOR, timeout=20_000)
                except Exception:
                    await page.wait_for_timeout(4_000)

                # ── Detect total pages ──────────────────────────────────────
                # There may be two badges: "Total: N" and "Page X of Y".
                # We iterate all .m_5add502a spans to find the "Page X of Y" one.
                total_pages = 1
                try:
                    badge_texts = await page.locator(BADGE_SELECTOR).all_inner_texts()
                    for text in badge_texts:
                        m = re.search(r"Page\s+\d+\s+of\s+(\d+)", text, re.IGNORECASE)
                        if m:
                            total_pages = int(m.group(1))
                            break
                except Exception:
                    pass

                for current_page in range(1, total_pages + 1):
                    html = await page.content()
                    for item in _extract_table_items(html, section_label, link_col, date_col):
                        if item.link not in seen_links:
                            seen_links.add(item.link)
                            all_items.append(item)

                    if current_page >= total_pages:
                        break

                    # ── Click Next ──────────────────────────────────────────
                    clicked = False

                    # Strategy 1: explicit aria-label on the Next button
                    for label in ("Next page", "Next", "next"):
                        try:
                            btn = page.locator(f'button[aria-label="{label}"]')
                            if await btn.count() > 0 and await btn.is_enabled():
                                await btn.click()
                                clicked = True
                                break
                        except Exception:
                            continue

                    # Strategy 2: second-to-last enabled button in the Mantine
                    #             pagination container (layout: First Prev … Next Last)
                    if not clicked:
                        try:
                            btns = page.locator("div.m_4addd315 button:not([disabled])")
                            count = await btns.count()
                            if count >= 2:
                                await btns.nth(count - 2).click()
                                clicked = True
                        except Exception:
                            pass

                    if not clicked:
                        break

                    # ── Wait for badge to confirm page advance ──────────────
                    # Check all badges for "Page {next_page} of ..."
                    next_page = current_page + 1
                    try:
                        await page.wait_for_function(
                            f"""() => {{
                                const badges = document.querySelectorAll('{BADGE_SELECTOR}');
                                for (const b of badges) {{
                                    if (/Page\\s+{next_page}\\s+of/i.test(b.textContent)) return true;
                                }}
                                return false;
                            }}""",
                            timeout=10_000,
                        )
                    except Exception:
                        # Fallback: just wait for the table to be non-empty
                        try:
                            await page.wait_for_function(
                                f"document.querySelectorAll('{TABLE_SELECTOR}').length > 0",
                                timeout=8_000,
                            )
                        except Exception:
                            await page.wait_for_timeout(2_500)

            finally:
                await browser.close()

        return all_items

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_async())
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Async wrappers (offload Playwright to thread pool)
# ---------------------------------------------------------------------------

async def _scrape_whats_new() -> list[ScrapedItem]:
    """
    /archive?category=whats-new — 8 columns
    Cols: S.No | Title | Start Date(3) | End Date | Category | Ext | Size | Details(8)
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _run_section_sync, WHATS_NEW_URL, "What's New", 8, 3
    )


async def _scrape_orders() -> list[ScrapedItem]:
    """
    /archive?category=order-and-notices — 8 columns
    Cols: S.No | Title(2) | Title/Category(3) | Start Date(4) | End Date | Ext | Size | Details(8)
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _run_section_sync, ORDERS_URL, "Orders and Notices", 8, 4
    )


async def _scrape_press() -> list[ScrapedItem]:
    """
    /offerings/dbt-press — 7 columns, 14 pages
    Cols: S.No | Title(2) | Start Date(3) | End Date | Ext | Size | Details(7)
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _run_section_sync, PRESS_URL, "DBT Press", 7, 3
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def crawl_dbt(config: SiteConfig) -> list[ScrapedItem]:
    """
    Crawl all three DBT sections in parallel:
      • What's New         — /archive?category=whats-new (10 items, single page)
      • Orders and Notices — /archive?category=order-and-notices (paginated)
      • DBT Press          — /offerings/dbt-press (136 items across 14 pages)
    """
    whats_new, orders, press = await asyncio.gather(
        _scrape_whats_new(),
        _scrape_orders(),
        _scrape_press(),
    )

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

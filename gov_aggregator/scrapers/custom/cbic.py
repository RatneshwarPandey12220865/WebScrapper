"""Custom scraper for CBIC - Central Board of Indirect Taxes & Customs.

PRIMARY STRATEGY — API interception (fast, no HTML parsing):
  Navigate to https://www.cbic.gov.in/entities/view-sticker using Playwright.
  Intercept the XHR response from:
    GET /api/Filepath/1/{comma_separated_content_ids}
  which returns JSON with full title, file path, publish date, doc type.

  Response fields used:
    docTitleEn  → title (strip Hindi part after "||")
    filePathEn  → relative path → joined with CBIC_BASE to get full URL
    publishDt   → ISO datetime with +05:30 offset
    docType     → "pdf" / other

FALLBACK STRATEGY — Playwright HTML parsing:
  Navigate to https://www.cbic.gov.in/entities/citizen-corner,
  click "New Releases" tab, set page size to 500, parse the table.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.cbic")

CBIC_BASE       = "https://www.cbic.gov.in"
VIEW_STICKER_URL   = f"{CBIC_BASE}/entities/view-sticker"
CITIZEN_CORNER_URL = f"{CBIC_BASE}/entities/citizen-corner"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_IST = timezone(timedelta(hours=5, minutes=30))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_iso_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # "2026-05-22T00:01:49+05:30"
        raw = raw.strip()
        # Python < 3.11 doesn't parse "+05:30" directly in all cases
        dt = datetime.fromisoformat(raw)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _clean_title(raw: str) -> str:
    """Keep only the English part (before '||' Hindi separator)."""
    return (raw.split("||")[0]).strip()


def _item_from_api_entry(entry: dict) -> ScrapedItem | None:
    """Build a ScrapedItem from one /api/Filepath response object."""
    title = _clean_title(entry.get("docTitleEn") or "")
    if not title:
        return None

    file_path = (entry.get("filePathEn") or "").strip()
    if not file_path:
        return None

    # filePathEn is relative: "CONTENTREPO/tickers/GE-TN-21052026.pdf"
    link = urljoin(CBIC_BASE + "/", file_path)

    doc_type = (entry.get("docType") or "").lower()
    is_pdf = doc_type == "pdf" or link.lower().endswith(".pdf")

    published_at = _parse_iso_date(entry.get("publishDt"))

    return ScrapedItem(
        title=title[:300],
        link=link,
        is_pdf=is_pdf,
        published_at=published_at,
        section_label="What's New",
    )


# ── Fallback HTML parser (Citizen Corner → New Releases tab) ──────────────────

def _items_from_html(html: str, seen_links: set[str]) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    container = soup.find("mat-tab-body", class_=lambda c: c and "mat-tab-body-active" in c)
    table = container.find("table") if container else None
    if table is None:
        table = soup.find("table", class_=lambda c: c and "table-hover" in c)
    if table is None:
        logger.warning("[cbic] fallback: no table found")
        return items

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        span = cells[1].find("span", class_="chapter-detail-text")
        title = (span.get_text(" ", strip=True) if span else cells[1].get_text(" ", strip=True))
        title = re.sub(r"\s*Read More\.\.\s*$", "", title).strip()
        if not title:
            continue

        a_tag = cells[2].find("a", href=True)
        if not a_tag:
            continue
        href = a_tag.get("href", "").strip()
        if not href:
            continue

        link = href if href.startswith("http") else urljoin(CBIC_BASE + "/", href)
        if link in seen_links:
            continue
        seen_links.add(link)

        aria = (a_tag.get("aria-label") or "").lower()
        ext = href.rsplit(".", 1)[-1].lower() if "." in href else ""
        is_pdf = ext == "pdf" or "pdf file" in aria

        items.append(ScrapedItem(
            title=title[:300],
            link=link,
            is_pdf=is_pdf,
            published_at=None,
            section_label="What's New",
        ))
    return items


# ── Playwright scrape ─────────────────────────────────────────────────────────

def _run_playwright() -> list[ScrapedItem]:

    async def _scrape() -> list[ScrapedItem]:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=_UA)
            page = await ctx.new_page()

            # ── PRIMARY: intercept /api/Filepath/ JSON response ───────────
            captured: list[dict] = []

            async def on_response(response):
                if "/api/Filepath/" in response.url:
                    try:
                        data = await response.json()
                        if isinstance(data, list):
                            captured.extend(data)
                            logger.info(
                                "[cbic] Intercepted %d entries from %s",
                                len(data), response.url,
                            )
                    except Exception as exc:
                        logger.warning("[cbic] Could not parse Filepath response: %s", exc)

            page.on("response", on_response)

            try:
                logger.info("[cbic] Loading %s", VIEW_STICKER_URL)
                await page.goto(VIEW_STICKER_URL, wait_until="domcontentloaded", timeout=60_000)
                # Give Angular time to fire the XHR
                await page.wait_for_timeout(6_000)
            finally:
                page.remove_listener("response", on_response)

            if captured:
                logger.info("[cbic] API interception succeeded — %d raw entries", len(captured))
                seen_links: set[str] = set()
                items: list[ScrapedItem] = []
                for entry in captured:
                    item = _item_from_api_entry(entry)
                    if item and item.link not in seen_links:
                        seen_links.add(item.link)
                        items.append(item)
                logger.info("[cbic] API approach: %d unique items", len(items))
                await ctx.close()
                await browser.close()
                return items

            # ── FALLBACK: Citizen Corner → New Releases tab ───────────────
            logger.warning("[cbic] API interception got 0 entries — falling back to Citizen Corner HTML")
            await page.goto(CITIZEN_CORNER_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3_000)

            try:
                tab = page.get_by_role("tab", name=re.compile(r"New Releases", re.I))
                await tab.first.click(timeout=12_000)
                await page.wait_for_timeout(2_000)
            except Exception as exc:
                logger.warning("[cbic] New Releases tab click failed: %s", exc)

            try:
                await page.wait_for_selector("table.table-hover", timeout=15_000)
            except Exception as exc:
                logger.warning("[cbic] Table not visible: %s", exc)

            seen_links2: set[str] = set()
            fallback_items: list[ScrapedItem] = []

            try:
                sel = page.locator("select.show_entries").first
                await sel.select_option("500")
                await page.wait_for_timeout(3_000)
                html = await page.content()
                fallback_items = _items_from_html(html, seen_links2)
            except Exception as exc:
                logger.warning("[cbic] Could not set page size: %s — paging manually", exc)
                page_num = 1
                while page_num <= 20:
                    html = await page.content()
                    batch = _items_from_html(html, seen_links2)
                    fallback_items.extend(batch)
                    logger.info("[cbic] Page %d: %d items", page_num, len(batch))
                    next_a = page.locator("li.pagination-next a")
                    if await next_a.count() == 0:
                        break
                    await next_a.first.click(timeout=5_000)
                    await page.wait_for_timeout(2_500)
                    page_num += 1

            logger.info("[cbic] Fallback: %d items", len(fallback_items))
            await ctx.close()
            await browser.close()
            return fallback_items

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_scrape())
    finally:
        loop.close()


# ── Public entry-point ────────────────────────────────────────────────────────

async def crawl_cbic_customs(config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_running_loop()
    items: list[ScrapedItem] = await loop.run_in_executor(None, _run_playwright)
    logger.info("[cbic] Total items: %d", len(items))
    return items

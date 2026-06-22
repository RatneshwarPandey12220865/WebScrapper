"""
Custom crawler for Ministry of Ayush (ayush.gov.in).

Angular SPA — /whatsnew is a client-side route that only works after the
homepage bootstraps. Direct URL navigation returns an empty shell.

Navigation path (from Playwright recording):
  1. Load https://ayush.gov.in/
  2. Click "VIEW MORE" → What's New table appears
  3. Click "Press Release" nav link → Press Release table appears
  (Add more sections below when ready.)
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.ayush")

_BASE    = "https://ayush.gov.in"
_ROW_SEL = "table tbody tr"


def _extract_rows(page, section_label: str) -> list[ScrapedItem]:
    """Wait for the table to appear then extract all rows via JS eval."""
    try:
        page.wait_for_selector(_ROW_SEL, timeout=20_000)
    except Exception as exc:
        logger.warning("[ayush] %s — table did not appear: %s", section_label, exc)
        return []

    raw = page.eval_on_selector_all(
        _ROW_SEL,
        """rows => rows.map(row => {
            const cols = row.querySelectorAll('td');
            const a    = row.querySelector('a');
            return {
                subject:  cols[1] ? cols[1].innerText.trim() : '',
                fileLink: a ? a.href : '',
            };
        })""",
    )

    items: list[ScrapedItem] = []
    for entry in raw:
        title = entry.get("subject", "").strip()
        link  = entry.get("fileLink", "").strip()
        if not title or not link:
            continue
        if not link.startswith("http"):
            link = urljoin(_BASE, link)
        items.append(ScrapedItem(
            title=title,
            link=link,
            is_pdf=link.lower().endswith(".pdf"),
            section_label=section_label,
        ))

    logger.info("[ayush] %s: %d items", section_label, len(items))
    return items


def _sync_crawl() -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    seen: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
        )
        page = context.new_page()

        # ── Bootstrap Angular SPA ──────────────────────────────────────────
        try:
            page.goto(_BASE + "/", wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            logger.error("[ayush] homepage failed to load: %s", exc)
            browser.close()
            return []

        # Wait for Angular to finish bootstrapping
        try:
            page.wait_for_selector("a", timeout=20_000)
        except Exception:
            pass
        page.wait_for_timeout(3_000)

        # ── Section 1: What's New ──────────────────────────────────────────
        try:
            page.get_by_role("link", name="VIEW MORE", exact=True).click(timeout=10_000)
            page.wait_for_timeout(1_500)
            for item in _extract_rows(page, "What's New"):
                if item.link not in seen:
                    seen.add(item.link)
                    all_items.append(item)
        except Exception as exc:
            logger.warning("[ayush] What's New navigation failed: %s", exc)

        # ── Section 2: Press Release ───────────────────────────────────────
        # The link is inside a collapsed Bootstrap dropdown — use JS .click()
        # so Angular's router handles the transition without a full page reload.
        try:
            navigated = page.evaluate(
                """() => {
                    const a = document.querySelector('a[href="/pressrelease"]')
                           || document.querySelector('a[href="https://ayush.gov.in/pressrelease"]');
                    if (a) { a.click(); return true; }
                    return false;
                }"""
            )
            if not navigated:
                raise RuntimeError("Press Release link not found in DOM")
            page.wait_for_timeout(2_500)
            for item in _extract_rows(page, "Press Release"):
                if item.link not in seen:
                    seen.add(item.link)
                    all_items.append(item)
        except Exception as exc:
            logger.warning("[ayush] Press Release navigation failed: %s", exc)

        context.close()
        browser.close()

    return all_items


async def crawl_ayush(_config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_crawl)

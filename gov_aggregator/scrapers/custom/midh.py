"""Custom scraper for Mission for Integrated Development of Horticulture (MIDH).

Angular SPA at https://www.midh.gov.in/Letters&Circulars.

How it works:
  - Angular renders a table: .tabContent table tbody tr
      td[0] → date text (e.g. "5th May, 2026")
      td[1] → <a target="_blank">title text</a>  (no href in DOM)
  - Each <a> click fires POST api.midh.gov.in/api/Common/GetDocumentPathLink
      body:     {"locationPath": "pdf/circulars/filename.pdf"}
      response: {"Data": {"dmsLocationPath": "https://storage.googleapis.com/..."}}

Strategy:
  1. Load page; wait for Angular to render the table.
  2. For each row, read date + title from DOM.
  3. Click the <a>; intercept the POST response to get the signed GCS URL.
  4. Use the signed URL as the item link.

Playwright runs in a worker thread (new event loop) to avoid ProactorEventLoop
conflicts on Windows under uvicorn — same pattern as nccd.py / rajasthan.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.midh")

_URL = "https://www.midh.gov.in/Letters&Circulars"
_API_ENDPOINT = "GetDocumentPathLink"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _run_in_thread() -> list[dict]:
    """
    Launch Playwright in a fresh event loop (worker thread).
    Returns list of {date, title, link, is_pdf} dicts.
    """

    async def _fetch() -> list[dict]:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                await page.goto(_URL, wait_until="networkidle", timeout=60_000)

                # Wait for Angular to hydrate the table
                try:
                    await page.wait_for_selector(
                        ".tabContent table tbody tr", timeout=25_000
                    )
                except Exception:
                    await page.wait_for_timeout(8_000)

                # Extract static row data (date + title) from the rendered DOM
                rows_meta: list[dict] = await page.evaluate(
                    """
                    () => {
                        const rows = document.querySelectorAll('.tabContent table tbody tr');
                        return Array.from(rows).map(row => {
                            const tds = row.querySelectorAll('td');
                            const date = tds[0] ? tds[0].innerText.trim() : '';
                            const a = tds[1] ? tds[1].querySelector('a') : null;
                            const title = a
                                ? a.innerText.replace(/[\\u{1F000}-\\u{1FFFF}]/gu, '').trim()
                                : (tds[1] ? tds[1].innerText.trim() : '');
                            return { date, title };
                        });
                    }
                    """
                )

                # For each row, click its <a> and capture the API response
                a_handles = await page.query_selector_all(
                    ".tabContent table tbody tr td:nth-child(2) a"
                )

                results: list[dict] = []
                for i, (meta, a_handle) in enumerate(
                    zip(rows_meta, a_handles)
                ):
                    signed_url: str | None = None

                    async def capture_response(resp, _idx=i):
                        nonlocal signed_url
                        if _API_ENDPOINT in resp.url:
                            try:
                                body = await resp.json()
                                signed_url = (
                                    body.get("Data", {}) or {}
                                ).get("dmsLocationPath") or None
                            except Exception:
                                pass

                    page.on("response", capture_response)
                    try:
                        await a_handle.click()
                        # Give the XHR time to complete
                        await page.wait_for_timeout(2_000)
                    except Exception as exc:
                        logger.debug("[midh] Click %d failed: %s", i, exc)
                    finally:
                        page.remove_listener("response", capture_response)

                    results.append(
                        {
                            "date": meta.get("date", ""),
                            "title": meta.get("title", ""),
                            "link": signed_url or _URL,
                            "is_pdf": bool(
                                signed_url
                                and (
                                    ".pdf" in signed_url.lower()
                                    or "storage.googleapis" in signed_url
                                )
                            ),
                        }
                    )

                return results

            finally:
                await page.close()
                await context.close()
                await browser.close()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()


async def crawl_midh(_config: SiteConfig) -> list[ScrapedItem]:
    raw: list[dict] = await asyncio.get_running_loop().run_in_executor(
        None, _run_in_thread
    )

    items: list[ScrapedItem] = []
    seen: set[str] = set()

    for entry in raw:
        title = _clean(entry.get("title") or "")
        if not title:
            continue

        raw_date = _clean(entry.get("date") or "")
        published_at = _parse_date(raw_date) if raw_date else None

        if published_at and published_at < _MIN_DATE:
            continue

        link = entry.get("link") or _URL
        if link in seen:
            continue
        seen.add(link)

        items.append(
            ScrapedItem(
                title=title[:500],
                link=link,
                published_at=published_at,
                is_pdf=bool(entry.get("is_pdf")),
                section_label="Letters & Circulars",
            )
        )

    logger.info("[midh] Total items: %d", len(items))
    return items

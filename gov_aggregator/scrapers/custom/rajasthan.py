from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.rajasthan")

_TIMEOUT_MS = 90_000
_ROWS_SELECTOR = "table tbody tr"
_PAGE_SIZE_SELECTOR = 'select[name="example_length "]'
_NEXT_PAGE_SELECTOR = 'a[aria-label="Next page"]'


def _parse_rajasthan_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    cleaned = " ".join(raw.split())
    try:
        return datetime.strptime(cleaned, "%d %b %Y, %I:%M %p").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _extract_press_release_items(html: str, base_url: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(_ROWS_SELECTOR):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        title = " ".join(cells[2].stripped_strings)
        if not title:
            continue

        link_tag = cells[-1].find("a", href=True) or row.find("a", href=True)
        if not link_tag:
            continue

        href = (link_tag.get("href") or "").strip()
        if not href:
            continue

        link = urljoin(base_url, href)
        published_at = _parse_rajasthan_date(cells[1].get_text(" ", strip=True))

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                is_pdf=link.lower().endswith(".pdf"),
            )
        )

    return items


def _collect_page_items(page, *, base_url: str, seen_links: set[str], items: list[ScrapedItem]) -> int:
    added = 0
    for item in _extract_press_release_items(page.content(), base_url):
        if item.link in seen_links:
            continue
        seen_links.add(item.link)
        items.append(item)
        added += 1
    return added


def _try_expand_to_all(page) -> bool:
    page_size = page.locator(_PAGE_SIZE_SELECTOR).first
    if page_size.count() == 0:
        return False

    options = page_size.locator("option").evaluate_all(
        "(opts) => opts.map((opt) => ({ text: opt.textContent || '', value: opt.value || '' }))"
    )

    all_value = next(
        (option["value"] for option in options if option["text"].strip().lower() == "all" and option["value"]),
        None,
    )
    if not all_value:
        return False

    rows_before = page.locator(_ROWS_SELECTOR).count()
    page_size.select_option(value=all_value, timeout=30_000)
    try:
        page.wait_for_function(
            """([selector, previousCount]) => {
                return document.querySelectorAll(selector).length > previousCount;
            }""",
            arg=[_ROWS_SELECTOR, rows_before],
            timeout=20_000,
        )
    except PlaywrightTimeoutError:
        page.wait_for_timeout(5_000)

    rows_after = page.locator(_ROWS_SELECTOR).count()
    return rows_after > rows_before


def _paginate_if_needed(page, *, base_url: str, seen_links: set[str], items: list[ScrapedItem]) -> None:
    visited_signatures: set[tuple[str, ...]] = set()

    while True:
        signature = tuple(item.link for item in _extract_press_release_items(page.content(), base_url))
        if not signature or signature in visited_signatures:
            break
        visited_signatures.add(signature)
        _collect_page_items(page, base_url=base_url, seen_links=seen_links, items=items)

        next_link = page.locator(_NEXT_PAGE_SELECTOR).first
        if next_link.count() == 0:
            break

        parent_classes = (next_link.locator("xpath=ancestor::li[1]").get_attribute("class") or "").lower()
        if "disabled" in parent_classes:
            break

        next_link.click(timeout=15_000)
        try:
            page.wait_for_function(
                """([selector, previousSignature]) => {
                    const rows = Array.from(document.querySelectorAll(selector));
                    const currentSignature = rows.map((row) => {
                        const link = row.querySelector('a[href]');
                        return link ? link.getAttribute('href') || '' : row.textContent || '';
                    });
                    return JSON.stringify(currentSignature) !== previousSignature;
                }""",
                arg=[_ROWS_SELECTOR, json.dumps(list(signature))],
                timeout=20_000,
            )
        except PlaywrightTimeoutError:
            page.wait_for_timeout(2_500)


def _sync_crawl(config: SiteConfig) -> list[ScrapedItem]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=DEFAULT_HEADERS["User-Agent"])
        page = context.new_page()
        page.set_extra_http_headers({k: v for k, v in DEFAULT_HEADERS.items() if k != "User-Agent"})

        items: list[ScrapedItem] = []
        seen_links: set[str] = set()

        try:
            page.goto(config.source_url, wait_until="networkidle", timeout=_TIMEOUT_MS)
            try:
                page.wait_for_selector(_ROWS_SELECTOR, timeout=20_000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(5_000)

            expanded = False
            try:
                expanded = _try_expand_to_all(page)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[rajasthan] Failed to expand page size to All: %s", exc)

            if expanded:
                _collect_page_items(page, base_url=config.base_url, seen_links=seen_links, items=items)
            else:
                _paginate_if_needed(page, base_url=config.base_url, seen_links=seen_links, items=items)

            return items
        finally:
            context.close()
            browser.close()


async def crawl_rajasthan(config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_crawl, config)

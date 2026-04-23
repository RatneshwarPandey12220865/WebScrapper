"""
Custom crawler for Ministry of Electronics and Information Technology (MeitY).

The current MeitY site uses the same announcementbox-heavy NIC layout seen on
several other ministry sites. This crawler keeps the parsing generic enough to
reuse for similar sites while using Playwright to fetch the fully rendered DOM.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig, SiteSection

_TIMEOUT_MS = 45_000
_WAIT_MS = 20_000


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None

    cleaned = raw.strip()
    if not cleaned:
        return None

    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _section_scope_selector(section: SiteSection) -> str | None:
    wait_selector = section.selectors.get("wait_for_selector")
    # Only use a scope selector if it's a non-announcementbox container
    # (e.g. a wrapping div that limits which rows we parse).
    # Do NOT scope to "div.whats-new-announcements" — the /whats-new page
    # renders announcementbox rows directly without that wrapper.
    if wait_selector and "announcementbox" not in wait_selector:
        return str(wait_selector)
    return None


def _page_url(section: SiteSection, page_number: int) -> str:
    if not section.pagination_param:
        return section.source_url

    parts = urlsplit(section.source_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[section.pagination_param] = str(page_number)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _extract_nic_announcementbox_items(
    html: str,
    *,
    base_url: str,
    section_label: str,
    scope_selector: str | None = None,
) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    scope = soup.select_one(scope_selector) if scope_selector else None
    root = scope or soup

    items: list[ScrapedItem] = []
    for row in root.select("div[role='row'].announcementbox"):
        title_tag = row.select_one("p.mb-0") or row.select_one("div.mb-0.text-break")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        if not title:
            continue

        link_tag = row.select_one("a.download-btn[href]") or row.select_one("a.link-btn[href]") or row.select_one("a[href]")
        if not link_tag:
            continue

        href = (link_tag.get("href") or "").strip()
        if not href or href == "#":
            continue

        link = href if href.startswith("http") else urljoin(base_url, href)

        published_at: datetime | None = None
        for candidate in (
            row.select_one("small.ptype.mb-0[aria-label]"),
            row.select_one("small.ptype.mb-0"),
            row.select_one("small.ptype"),
        ):
            if not candidate:
                continue
            raw_date = (candidate.get("aria-label") or "").strip() or candidate.get_text(strip=True)
            published_at = _parse_date(raw_date)
            if published_at:
                break

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                summary=None,
                published_at=published_at,
                is_pdf=link.lower().endswith(".pdf") or link_tag.get("type", "").lower() == "pdf",
                section_label=section_label,
            )
        )

    return items


async def _launch_browser(playwright):
    try:
        return await playwright.chromium.launch(headless=True)
    except Exception:  # noqa: BLE001
        return await playwright.chromium.launch(headless=True, channel="msedge")


async def _wait_for_rows(page, section: SiteSection) -> None:
    wait_selector = section.selectors.get("wait_for_selector")
    if wait_selector:
        try:
            await page.wait_for_selector(str(wait_selector), timeout=_WAIT_MS)
        except Exception:  # noqa: BLE001
            pass

    try:
        await page.locator("div[role='row'].announcementbox").first.wait_for(timeout=8_000)
    except Exception:  # noqa: BLE001
        await page.wait_for_timeout(2_000)


async def _fetch_section_items(page, section: SiteSection, base_url: str) -> list[ScrapedItem]:
    scope_selector = _section_scope_selector(section)
    items: list[ScrapedItem] = []
    seen_links: set[str] = set()

    start_page = section.start_page or 1
    max_pages = section.max_pages or 1

    for offset in range(max_pages):
        page_number = start_page + offset
        url = _page_url(section, page_number)

        await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)
        await _wait_for_rows(page, section)

        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:  # noqa: BLE001
            await page.wait_for_timeout(1_500)

        html = await page.content()
        page_items = _extract_nic_announcementbox_items(
            html,
            base_url=base_url,
            section_label=section.section_label,
            scope_selector=scope_selector,
        )

        new_found = 0
        for item in page_items:
            if item.link in seen_links:
                continue
            seen_links.add(item.link)
            items.append(item)
            new_found += 1

        if new_found == 0 and offset > 0:
            break

    return items


def _configured_sections(config: SiteConfig) -> list[SiteSection]:
    if config.sections:
        return list(config.sections)

    # Fallback defaults — mirrors sites_config.json entry for meity
    _wait = {"wait_for_selector": "div[role='row'].announcementbox"}
    return [
        SiteSection(
            source_url="https://www.meity.gov.in/whats-new",
            parser="list",
            parser_backend="bs4",
            render_js=True,
            section_label="What's New",
            pagination_param="page",
            start_page=1,
            max_pages=5,
            selectors=_wait,
        ),
        SiteSection(
            source_url="https://www.meity.gov.in/documents/orders-and-notices",
            parser="list",
            parser_backend="bs4",
            render_js=True,
            section_label="Orders & Notices",
            pagination_param="page",
            start_page=1,
            max_pages=5,
            selectors=_wait,
        ),
        SiteSection(
            source_url="https://www.meity.gov.in/documents/press-release",
            parser="list",
            parser_backend="bs4",
            render_js=True,
            section_label="Press Releases",
            pagination_param="page",
            start_page=1,
            max_pages=3,
            selectors=_wait,
        ),
        SiteSection(
            source_url="https://www.meity.gov.in/documents/publications",
            parser="list",
            parser_backend="bs4",
            render_js=True,
            section_label="Publications",
            pagination_param="page",
            start_page=1,
            max_pages=3,
            selectors=_wait,
        ),
        SiteSection(
            source_url="https://www.meity.gov.in/documents",
            parser="list",
            parser_backend="bs4",
            render_js=True,
            section_label="Documents",
            pagination_param="page",
            start_page=1,
            max_pages=5,
            selectors=_wait,
        ),
    ]


async def _crawl_meity_async(config: SiteConfig) -> list[ScrapedItem]:
    from playwright.async_api import async_playwright

    all_items: list[ScrapedItem] = []
    base_url = config.base_url or "https://www.meity.gov.in"

    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright)
        context = await browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            extra_http_headers={key: value for key, value in DEFAULT_HEADERS.items() if key != "User-Agent"},
            ignore_https_errors=not config.verify_ssl,
        )
        page = None
        try:
            page = await context.new_page()
            for section in _configured_sections(config):
                section_items = await _fetch_section_items(page, section, base_url)
                all_items.extend(section_items)
        finally:
            if page is not None:
                with suppress(Exception):
                    await page.close()
            with suppress(Exception):
                await context.close()
            with suppress(Exception):
                await browser.close()

    unique: list[ScrapedItem] = []
    seen_links: set[str] = set()
    for item in all_items:
        if item.link in seen_links:
            continue
        seen_links.add(item.link)
        unique.append(item)

    return unique


def _run_meity_in_worker(config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_crawl_meity_async(config))
    finally:
        loop.close()


async def crawl_meity(config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_meity_in_worker, config)

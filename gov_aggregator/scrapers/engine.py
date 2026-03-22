from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import Browser, Page, async_playwright

from gov_aggregator.scrapers.parsers import extract_items
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig, SiteSection


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _paginate_url(url: str, param: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[param] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _pagination_urls(source_url: str, pagination_param: str | None, start_page: int, max_pages: int) -> list[str]:
    if not pagination_param or max_pages <= 1:
        return [source_url]
    return [_paginate_url(source_url, pagination_param, page) for page in range(start_page, start_page + max_pages)]


def _run_playwright_in_subprocess(
    site_configs: list[SiteConfig],
    concurrency: int,
    timeout_seconds: float,
) -> list["ScrapeResult"]:
    """
    Run Playwright-backed scraping on an isolated event loop.

    Despite the helper name, this currently runs in a worker thread via
    run_in_executor(), which is sufficient to isolate Playwright from the
    uvicorn-owned asyncio loop on Windows.
    """

    async def _run() -> list[ScrapeResult]:
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
            timeout=timeout_seconds,
        ) as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, channel="msedge")
                semaphore = asyncio.Semaphore(concurrency)
                engine = ScraperEngine(
                    site_configs=site_configs,
                    concurrency=concurrency,
                    timeout_seconds=timeout_seconds,
                )
                try:
                    return await asyncio.gather(
                        *[engine._scrape_site(site, semaphore, client, browser) for site in site_configs]
                    )
                finally:
                    await browser.close()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


@dataclass(slots=True)
class ScrapeResult:
    site_key: str
    ministry: str
    found: int
    error: str | None = None
    items: list[ScrapedItem] = field(default_factory=list, repr=False)
    site_config: SiteConfig | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "site_key": self.site_key,
            "ministry": self.ministry,
            "found": self.found,
            "error": self.error,
        }


class ScraperEngine:
    def __init__(
        self,
        *,
        site_configs: list[SiteConfig],
        concurrency: int = 10,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.site_configs = site_configs
        self.concurrency = concurrency
        self.timeout_seconds = timeout_seconds

    async def scrape_all(self, site_keys: set[str] | None = None) -> list[ScrapeResult]:
        selected = [site for site in self.site_configs if not site_keys or site.site_key in site_keys]
        semaphore = asyncio.Semaphore(self.concurrency)

        # Determine if playwright is needed for any site or any section
        def needs_playwright(site: SiteConfig) -> bool:
            if site.render_js:
                return True
            return any(s.render_js for s in site.sections)

        uses_playwright = any(needs_playwright(site) for site in selected)

        async with httpx.AsyncClient(
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
            timeout=self.timeout_seconds,
        ) as client:
            if not uses_playwright:
                return await asyncio.gather(*[self._scrape_site(site, semaphore, client, None) for site in selected])

            js_sites = [site for site in selected if needs_playwright(site)]
            static_sites = [site for site in selected if not needs_playwright(site)]

            static_results = (
                await asyncio.gather(*[self._scrape_site(site, semaphore, client, None) for site in static_sites])
                if static_sites
                else []
            )

            loop = asyncio.get_running_loop()
            js_results = await loop.run_in_executor(
                None,
                _run_playwright_in_subprocess,
                js_sites,
                self.concurrency,
                self.timeout_seconds,
            )

            return list(static_results) + js_results

    async def _scrape_config_pages(
        self,
        config: SiteConfig,
        client: httpx.AsyncClient,
        browser: Browser | None,
    ) -> list[ScrapedItem]:
        urls = _pagination_urls(config.source_url, config.pagination_param, config.start_page, config.max_pages)
        items: list[ScrapedItem] = []
        seen_links: set[str] = set()

        for url in urls:
            html = await self._fetch_html_for(url, config.render_js, client, browser, config.selectors)
            page_items = extract_items(config, html)

            if config.pagination_param and not page_items:
                break

            for item in page_items:
                if item.link in seen_links:
                    continue
                seen_links.add(item.link)
                items.append(item)

        return items

    async def _scrape_site(
        self,
        site: SiteConfig,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
        browser: Browser | None,
    ) -> ScrapeResult:
        async with semaphore:
            try:
                # --- Multi-section mode ---
                if site.sections:
                    all_items: list[ScrapedItem] = []
                    for section in site.sections:
                        section_items = await self._scrape_section(site, section, client, browser)
                        # Tag each item with its section label
                        for item in section_items:
                            item.section_label = section.section_label
                        all_items.extend(section_items)
                    return ScrapeResult(
                        site_key=site.site_key,
                        ministry=site.ministry,
                        found=len(all_items),
                        items=all_items,
                        site_config=site,
                    )

                # --- Single-section mode (default) ---
                items = await self._scrape_config_pages(site, client, browser)
                return ScrapeResult(
                    site_key=site.site_key,
                    ministry=site.ministry,
                    found=len(items),
                    items=items,
                    site_config=site,
                )
            except Exception as exc:  # noqa: BLE001
                return ScrapeResult(
                    site_key=site.site_key,
                    ministry=site.ministry,
                    found=0,
                    error=str(exc),
                    site_config=site,
                )

    async def _scrape_section(
        self,
        parent: SiteConfig,
        section: SiteSection,
        client: httpx.AsyncClient,
        browser: Browser | None,
    ) -> list[ScrapedItem]:
        """Scrape a single section URL using its own parser settings, but the parent's base_url and category_mapping."""
        # Build a temporary SiteConfig that mimics the section but inherits parent identity
        section_config = SiteConfig(
            site_key=parent.site_key,
            ministry=parent.ministry,
            name=parent.name,
            source_url=section.source_url,
            base_url=parent.base_url,
            parser=section.parser,
            parser_backend=section.parser_backend,
            render_js=section.render_js,
            selectors=section.selectors,
            category_mapping=parent.category_mapping,
            default_category=section.default_category,
            pagination_param=section.pagination_param,
            start_page=section.start_page,
            max_pages=section.max_pages,
        )
        return await self._scrape_config_pages(section_config, client, browser)

    async def _fetch_html_for(
        self,
        url: str,
        render_js: bool,
        client: httpx.AsyncClient,
        browser: Browser | None,
        selectors: dict,
    ) -> str:
        if render_js:
            if browser is None:
                raise RuntimeError("Playwright browser was not initialized for a JS-enabled site/section")
            return await self._fetch_with_playwright(url, browser, selectors.get("wait_for_selector"))
        return await self._fetch_with_httpx(url, client)

    # Keep old helpers for backward compatibility
    async def _fetch_html(self, site: SiteConfig, client: httpx.AsyncClient, browser: Browser | None) -> str:
        return await self._fetch_html_for(site.source_url, site.render_js, client, browser, site.selectors)

    async def _fetch_with_httpx(self, url: str, client: httpx.AsyncClient) -> str:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    async def _fetch_with_playwright(self, url: str, browser: Browser, wait_for_selector: str | None = None) -> str:
        page: Page = await browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
        try:
            await page.set_extra_http_headers(
                {key: value for key, value in DEFAULT_HEADERS.items() if key != "User-Agent"}
            )
            await page.goto(url, wait_until="networkidle", timeout=int(self.timeout_seconds * 1000))
            if wait_for_selector:
                await page.wait_for_selector(wait_for_selector, timeout=int(self.timeout_seconds * 1000))
            return await page.content()
        finally:
            await page.close()

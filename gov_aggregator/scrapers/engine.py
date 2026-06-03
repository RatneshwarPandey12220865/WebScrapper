from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import Browser, Page, async_playwright

from gov_aggregator.scrapers.config import is_ssl_error
from gov_aggregator.scrapers.parsers import extract_items
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig, SiteSection

logger = logging.getLogger("gov_aggregator.engine")


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
        async with (
            httpx.AsyncClient(
                follow_redirects=True,
                headers=DEFAULT_HEADERS,
                timeout=timeout_seconds,
            ) as client,
            httpx.AsyncClient(
                follow_redirects=True,
                headers=DEFAULT_HEADERS,
                timeout=timeout_seconds,
                verify=False,
            ) as insecure_client,
        ):
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                semaphore = asyncio.Semaphore(concurrency)
                engine = ScraperEngine(
                    site_configs=site_configs,
                    concurrency=concurrency,
                    timeout_seconds=timeout_seconds,
                )
                try:
                    return await asyncio.gather(
                        *[
                            engine._scrape_site(site, semaphore, client, insecure_client, browser)
                            for site in site_configs
                        ]
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
    ssl_bypassed: bool = False

    def to_dict(self) -> dict:
        return {
            "site_key": self.site_key,
            "ministry": self.ministry,
            "found": self.found,
            "error": self.error,
            "ssl_bypassed": self.ssl_bypassed,
        }


def _with_ssl_disabled(site: SiteConfig) -> SiteConfig:
    """Return a shallow copy of SiteConfig with verify_ssl=False on the site and all sections."""
    from copy import copy
    from gov_aggregator.scrapers.schemas import SiteSection

    new_sections = []
    for sec in site.sections:
        s = copy(sec)
        s.verify_ssl = False
        new_sections.append(s)

    new_site = copy(site)
    new_site.verify_ssl = False
    new_site.sections = new_sections
    return new_site


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

        async with (
            httpx.AsyncClient(
                follow_redirects=True,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout_seconds,
            ) as client,
            httpx.AsyncClient(
                follow_redirects=True,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout_seconds,
                verify=False,
            ) as insecure_client,
        ):
            if not uses_playwright:
                return await asyncio.gather(
                    *[self._scrape_site(site, semaphore, client, insecure_client, None) for site in selected]
                )

            js_sites = [site for site in selected if needs_playwright(site)]
            static_sites = [site for site in selected if not needs_playwright(site)]

            static_results = (
                await asyncio.gather(
                    *[
                        self._scrape_site(site, semaphore, client, insecure_client, None)
                        for site in static_sites
                    ]
                )
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
        insecure_client: httpx.AsyncClient | None,
        browser: Browser | None,
    ) -> list[ScrapedItem]:
        urls = _pagination_urls(config.source_url, config.pagination_param, config.start_page, config.max_pages)
        items: list[ScrapedItem] = []
        seen_links: set[str] = set()

        logger.info("[%s] Scraping %d page(s) starting from %s", config.site_key, len(urls), urls[0] if urls else "(no URL)")

        for url in urls:
            logger.debug("[%s] Fetching: %s (render_js=%s)", config.site_key, url, config.render_js)
            html = await self._fetch_html_for(
                url,
                config.render_js,
                client,
                insecure_client,
                browser,
                config.selectors,
                verify_ssl=config.verify_ssl,
            )
            logger.debug("[%s] Got %d bytes of HTML from %s", config.site_key, len(html), url)
            page_items = extract_items(config, html)

            if config.pagination_param and not page_items:
                logger.info("[%s] Page %s returned 0 items, stopping pagination", config.site_key, url)
                break

            for item in page_items:
                if item.link in seen_links:
                    continue
                seen_links.add(item.link)
                items.append(item)
                if config.max_items is not None and len(items) >= config.max_items:
                    return items[: config.max_items]

        logger.info("[%s] Total items extracted: %d", config.site_key, len(items))
        return items

    async def _scrape_site(
        self,
        site: SiteConfig,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient | None,
        browser: Browser | None,
    ) -> ScrapeResult:
        async with semaphore:
            try:
                return await self._scrape_site_attempt(site, client, insecure_client, browser)
            except Exception as exc:  # noqa: BLE001
                if is_ssl_error(exc):
                    logger.warning(
                        "[%s] SSL error on first attempt — retrying entire site with SSL verification disabled",
                        site.site_key,
                    )
                    ssl_free_site = _with_ssl_disabled(site)
                    try:
                        result = await self._scrape_site_attempt(ssl_free_site, client, insecure_client, browser)
                        # Preserve original site_config reference so callers can still persist verify_ssl=False
                        result.ssl_bypassed = True
                        logger.info(
                            "[%s] SSL bypass succeeded — found %d items",
                            site.site_key, result.found,
                        )
                        return result
                    except Exception as retry_exc:  # noqa: BLE001
                        error_msg = f"[SSL ERROR] {retry_exc}"
                        logger.error("[%s] SSL bypass also failed: %s", site.site_key, retry_exc)
                        return ScrapeResult(
                            site_key=site.site_key,
                            ministry=site.ministry,
                            found=0,
                            error=error_msg,
                            site_config=site,
                        )
                error_msg = str(exc)
                return ScrapeResult(
                    site_key=site.site_key,
                    ministry=site.ministry,
                    found=0,
                    error=error_msg,
                    site_config=site,
                )

    async def _scrape_site_attempt(
        self,
        site: SiteConfig,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient | None,
        browser: Browser | None,
    ) -> "ScrapeResult":
        """Inner scrape logic — called once normally, then again with SSL disabled on SSL error."""
        # --- Multi-section mode ---
        if site.sections:
            all_items: list[ScrapedItem] = []
            for section in site.sections:
                section_items = await self._scrape_section(site, section, client, insecure_client, browser)
                for item in section_items:
                    item.section_label = section.section_label
                all_items.extend(section_items)

            seen_links: set[str] = set()
            deduped_items: list[ScrapedItem] = []
            for item in all_items:
                if item.link and item.link not in seen_links:
                    seen_links.add(item.link)
                    deduped_items.append(item)

            return ScrapeResult(
                site_key=site.site_key,
                ministry=site.ministry,
                found=len(deduped_items),
                items=deduped_items,
                site_config=site,
            )

        # --- Single-section mode ---
        items = await self._scrape_config_pages(site, client, insecure_client, browser)
        return ScrapeResult(
            site_key=site.site_key,
            ministry=site.ministry,
            found=len(items),
            items=items,
            site_config=site,
        )

    async def _scrape_section(
        self,
        parent: SiteConfig,
        section: SiteSection,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient | None,
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
            max_items=parent.max_items if section.max_items is None else section.max_items,
            verify_ssl=parent.verify_ssl if section.verify_ssl is None else section.verify_ssl,
            min_date=section.min_date,
        )
        return await self._scrape_config_pages(section_config, client, insecure_client, browser)

    async def _fetch_html_for(
        self,
        url: str,
        render_js: bool,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient | None,
        browser: Browser | None,
        selectors: dict,
        verify_ssl: bool = True,
    ) -> str:
        if render_js:
            if browser is None:
                raise RuntimeError("Playwright browser was not initialized for a JS-enabled site/section")
            return await self._fetch_with_playwright(
                url,
                browser,
                selectors.get("wait_for_selector"),
                selectors.get("pre_capture_js"),
                selectors.get("pre_capture_click"),
                verify_ssl=verify_ssl,
            )
        return await self._fetch_with_httpx(url, client, insecure_client=insecure_client, verify_ssl=verify_ssl)

    # Keep old helpers for backward compatibility
    async def _fetch_html(self, site: SiteConfig, client: httpx.AsyncClient, browser: Browser | None) -> str:
        return await self._fetch_html_for(
            site.source_url,
            site.render_js,
            client,
            None,
            browser,
            site.selectors,
            verify_ssl=site.verify_ssl,
        )

    async def _fetch_with_httpx(
        self,
        url: str,
        client: httpx.AsyncClient,
        *,
        insecure_client: httpx.AsyncClient | None = None,
        verify_ssl: bool = True,
    ) -> str:
        async def _one_off_insecure_get() -> str:
            async with httpx.AsyncClient(
                follow_redirects=True,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout_seconds,
                verify=False,
            ) as one_off_insecure_client:
                response = await one_off_insecure_client.get(url)
                response.raise_for_status()
                return response.text

        if not verify_ssl:
            if insecure_client is not None:
                response = await insecure_client.get(url)
                response.raise_for_status()
                return response.text
            return await _one_off_insecure_get()

        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001
            # If the caller wanted SSL verification but the site has broken chains,
            # retry once with verification disabled (and optionally persist config
            # changes via the upper-level exception handlers).
            if is_ssl_error(exc):
                logger.warning("SSL error fetching %s; retrying once with verify=False", url)
                if insecure_client is not None:
                    response = await insecure_client.get(url)
                    response.raise_for_status()
                    return response.text
                return await _one_off_insecure_get()
            raise

    async def _fetch_with_playwright(
        self,
        url: str,
        browser: Browser,
        wait_for_selector: str | None = None,
        pre_capture_js: str | None = None,
        pre_capture_click: str | None = None,
        *,
        verify_ssl: bool = True,
    ) -> str:
        import asyncio
        last_error: Exception | None = None
        for attempt in range(2):  # retry once on browser target errors
            context = None
            page: Page | None = None
            try:
                extra_headers = {k: v for k, v in DEFAULT_HEADERS.items() if k != "User-Agent"}
                context = await browser.new_context(
                    user_agent=DEFAULT_HEADERS["User-Agent"],
                    extra_http_headers=extra_headers,
                    ignore_https_errors=not verify_ssl,
                )
                page = await context.new_page()
                await page.goto(
                    url,
                    wait_until="domcontentloaded",  # faster than "load"
                    timeout=int(self.timeout_seconds * 1000),
                )
                if wait_for_selector:
                    try:
                        await page.wait_for_selector(
                            wait_for_selector, timeout=12000
                        )
                    except Exception as wait_exc:
                        logger.warning(
                            "wait_for_selector('%s') timed out for %s: %s — "
                            "falling back to 3s sleep. Page content may be incomplete.",
                            wait_for_selector, url, wait_exc,
                        )
                        await page.wait_for_timeout(3000)
                else:
                    await page.wait_for_timeout(2000)

                if pre_capture_click:
                    try:
                        await page.click(pre_capture_click, timeout=5000)
                        await page.wait_for_timeout(1500)
                    except Exception:
                        pass

                if pre_capture_js:
                    try:
                        await page.evaluate(pre_capture_js)
                        await page.wait_for_timeout(1000)
                    except Exception:
                        pass

                return await page.content()

            except Exception as exc:
                last_error = exc
                error_str = str(exc)
                # Only retry on browser/target closed errors
                if "closed" in error_str.lower() or "target" in error_str.lower():
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise  # re-raise non-retryable errors immediately
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass  # page may already be closed
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass

        raise last_error  # exhausted retries

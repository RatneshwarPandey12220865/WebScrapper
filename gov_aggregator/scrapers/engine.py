from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

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


# ── Playwright Browser Pool ────────────────────────────────────────────────────
class PlaywrightPool:
    """
    Pool of N independent Chromium browser instances.

    Each browser handles one page at a time. Callers acquire a slot via the
    semaphore, then get a browser assigned round-robin. This gives N-times the
    JS-rendering throughput of a single browser while keeping memory bounded.
    """

    def __init__(self, browser_count: int = 4) -> None:
        self._count = browser_count
        self._browsers: list[Browser] = []
        self._sem: asyncio.Semaphore | None = None
        self._idx = 0

    async def start(self, playwright_ctx: object) -> None:
        self._browsers = [
            await playwright_ctx.chromium.launch(headless=True)
            for _ in range(self._count)
        ]
        self._sem = asyncio.Semaphore(self._count)

    async def close(self) -> None:
        for b in self._browsers:
            try:
                await b.close()
            except Exception:
                pass
        self._browsers.clear()

    def _next_browser(self) -> Browser:
        b = self._browsers[self._idx % len(self._browsers)]
        self._idx += 1
        return b

    async def fetch(
        self,
        url: str,
        wait_for_selector: str | None = None,
        pre_capture_js: str | None = None,
        pre_capture_click: str | None = None,
        verify_ssl: bool = True,
    ) -> str:
        assert self._sem is not None, "PlaywrightPool.start() not called"
        async with self._sem:
            browser = self._next_browser()
            return await _fetch_page(
                browser, url,
                wait_for_selector=wait_for_selector,
                pre_capture_js=pre_capture_js,
                pre_capture_click=pre_capture_click,
                verify_ssl=verify_ssl,
            )


async def _fetch_page(
    browser: Browser,
    url: str,
    *,
    wait_for_selector: str | None = None,
    pre_capture_js: str | None = None,
    pre_capture_click: str | None = None,
    verify_ssl: bool = True,
    timeout_ms: int = 90_000,
) -> str:
    last_error: Exception | None = None
    for attempt in range(2):
        context: BrowserContext | None = None
        page: Page | None = None
        try:
            extra_headers = {k: v for k, v in DEFAULT_HEADERS.items() if k != "User-Agent"}
            context = await browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                extra_http_headers=extra_headers,
                ignore_https_errors=not verify_ssl,
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            if wait_for_selector:
                try:
                    await page.wait_for_selector(wait_for_selector, timeout=12_000)
                except Exception as wait_exc:
                    logger.warning(
                        "wait_for_selector('%s') timed out for %s — falling back to 3s sleep: %s",
                        wait_for_selector, url, wait_exc,
                    )
                    await page.wait_for_timeout(3_000)
            else:
                await page.wait_for_timeout(2_000)

            if pre_capture_click:
                try:
                    await page.click(pre_capture_click, timeout=5_000)
                    await page.wait_for_timeout(1_500)
                except Exception:
                    pass

            if pre_capture_js:
                try:
                    await page.evaluate(pre_capture_js)
                    await page.wait_for_timeout(1_000)
                except Exception:
                    pass

            return await page.content()

        except Exception as exc:
            last_error = exc
            if "closed" in str(exc).lower() or "target" in str(exc).lower():
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    raise last_error  # type: ignore[misc]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _with_ssl_disabled(site: SiteConfig) -> SiteConfig:
    from copy import copy

    new_sections = []
    for sec in site.sections:
        s = copy(sec)
        s.verify_ssl = False
        new_sections.append(s)

    new_site = copy(site)
    new_site.verify_ssl = False
    new_site.sections = new_sections
    return new_site


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


# ── ScraperEngine ──────────────────────────────────────────────────────────────

class ScraperEngine:
    def __init__(
        self,
        *,
        site_configs: list[SiteConfig],
        concurrency: int = 20,
        timeout_seconds: float = 90.0,
        playwright_browsers: int = 4,
    ) -> None:
        self.site_configs = site_configs
        self.concurrency = concurrency
        self.timeout_seconds = timeout_seconds
        self.playwright_browsers = playwright_browsers

    async def scrape_all(self, site_keys: set[str] | None = None) -> list[ScrapeResult]:
        selected = [s for s in self.site_configs if not site_keys or s.site_key in site_keys]
        if not selected:
            return []

        def needs_playwright(site: SiteConfig) -> bool:
            return site.render_js or any(s.render_js for s in site.sections)

        js_sites    = [s for s in selected if needs_playwright(s)]
        http_sites  = [s for s in selected if not needs_playwright(s)]

        http_sem    = asyncio.Semaphore(self.concurrency)

        async with (
            httpx.AsyncClient(
                follow_redirects=True, headers=DEFAULT_HEADERS, timeout=self.timeout_seconds,
            ) as client,
            httpx.AsyncClient(
                follow_redirects=True, headers=DEFAULT_HEADERS, timeout=self.timeout_seconds, verify=False,
            ) as insecure_client,
        ):
            # ── HTTP-only sites — full concurrency ──────────────────────────
            http_task = asyncio.gather(*[
                self._scrape_site(s, http_sem, client, insecure_client, None)
                for s in http_sites
            ]) if http_sites else asyncio.sleep(0, result=[])

            # ── JS sites — run inside a shared Playwright pool ──────────────
            if js_sites:
                js_results = await self._scrape_js_sites(js_sites, client, insecure_client)
            else:
                js_results = []

            http_results = await http_task

        return list(http_results) + js_results

    async def _scrape_js_sites(
        self,
        js_sites: list[SiteConfig],
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient,
    ) -> list[ScrapeResult]:
        """Run JS sites using a pool of Playwright browsers in a worker thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            _run_playwright_pool,
            js_sites,
            min(self.playwright_browsers, len(js_sites)),
            self.concurrency,
            self.timeout_seconds,
        )

    async def _scrape_site(
        self,
        site: SiteConfig,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient,
        pw_pool: PlaywrightPool | None,
    ) -> ScrapeResult:
        async with semaphore:
            try:
                return await self._scrape_site_attempt(site, client, insecure_client, pw_pool)
            except Exception as exc:  # noqa: BLE001
                if is_ssl_error(exc):
                    logger.warning("[%s] SSL error — retrying with SSL disabled", site.site_key)
                    ssl_free = _with_ssl_disabled(site)
                    try:
                        result = await self._scrape_site_attempt(ssl_free, client, insecure_client, pw_pool)
                        result.ssl_bypassed = True
                        return result
                    except Exception as retry_exc:  # noqa: BLE001
                        return ScrapeResult(
                            site_key=site.site_key, ministry=site.ministry, found=0,
                            error=f"[SSL ERROR] {retry_exc}", site_config=site,
                        )
                return ScrapeResult(
                    site_key=site.site_key, ministry=site.ministry, found=0,
                    error=str(exc), site_config=site,
                )

    async def _scrape_site_attempt(
        self,
        site: SiteConfig,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient,
        pw_pool: PlaywrightPool | None,
    ) -> ScrapeResult:
        if site.sections:
            # All sections fetched in parallel
            section_results = await asyncio.gather(*[
                self._scrape_section(site, sec, client, insecure_client, pw_pool)
                for sec in site.sections
            ])
            all_items: list[ScrapedItem] = []
            for sec, sec_items in zip(site.sections, section_results):
                for item in sec_items:
                    item.section_label = sec.section_label
                all_items.extend(sec_items)

            seen: set[str] = set()
            deduped = [it for it in all_items if it.link not in seen and not seen.add(it.link)]  # type: ignore[func-returns-value]
            return ScrapeResult(
                site_key=site.site_key, ministry=site.ministry,
                found=len(deduped), items=deduped, site_config=site,
            )

        items = await self._scrape_config_pages(site, client, insecure_client, pw_pool)
        return ScrapeResult(
            site_key=site.site_key, ministry=site.ministry,
            found=len(items), items=items, site_config=site,
        )

    async def _scrape_section(
        self,
        parent: SiteConfig,
        section: SiteSection,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient,
        pw_pool: PlaywrightPool | None,
    ) -> list[ScrapedItem]:
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
            date_format=section.date_format,
        )
        return await self._scrape_config_pages(section_config, client, insecure_client, pw_pool)

    async def _scrape_config_pages(
        self,
        config: SiteConfig,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient,
        pw_pool: PlaywrightPool | None,
    ) -> list[ScrapedItem]:
        urls = _pagination_urls(config.source_url, config.pagination_param, config.start_page, config.max_pages)
        logger.info("[%s] Fetching %d page(s) from %s", config.site_key, len(urls), urls[0] if urls else "(none)")

        if len(urls) == 1:
            # Single page — no gather overhead
            html = await self._fetch_html_for(
                urls[0], config.render_js, client, insecure_client, pw_pool,
                config.selectors, verify_ssl=config.verify_ssl,
            )
            items = extract_items(config, html)
            logger.info("[%s] Extracted %d items", config.site_key, len(items))
            if config.max_items is not None:
                items = items[: config.max_items]
            return items

        # Multiple pages — fetch all in parallel then deduplicate
        htmls = await asyncio.gather(*[
            self._fetch_html_for(
                url, config.render_js, client, insecure_client, pw_pool,
                config.selectors, verify_ssl=config.verify_ssl,
            )
            for url in urls
        ], return_exceptions=True)

        seen_links: set[str] = set()
        items: list[ScrapedItem] = []
        for url, html_or_exc in zip(urls, htmls):
            if isinstance(html_or_exc, Exception):
                logger.warning("[%s] Page %s failed: %s", config.site_key, url, html_or_exc)
                continue
            page_items = extract_items(config, html_or_exc)
            if config.pagination_param and not page_items:
                break  # stop early on first empty page (same as before)
            for it in page_items:
                if it.link not in seen_links:
                    seen_links.add(it.link)
                    items.append(it)
                    if config.max_items is not None and len(items) >= config.max_items:
                        logger.info("[%s] Extracted %d items (max_items cap)", config.site_key, len(items))
                        return items

        logger.info("[%s] Extracted %d items across %d pages", config.site_key, len(items), len(urls))
        return items

    async def _fetch_html_for(
        self,
        url: str,
        render_js: bool,
        client: httpx.AsyncClient,
        insecure_client: httpx.AsyncClient,
        pw_pool: PlaywrightPool | None,
        selectors: dict,
        verify_ssl: bool = True,
    ) -> str:
        if render_js:
            if pw_pool is None:
                raise RuntimeError("Playwright pool not initialised for a JS-enabled site")
            return await pw_pool.fetch(
                url,
                wait_for_selector=selectors.get("wait_for_selector"),
                pre_capture_js=selectors.get("pre_capture_js"),
                pre_capture_click=selectors.get("pre_capture_click"),
                verify_ssl=verify_ssl,
            )
        return await self._fetch_with_httpx(url, client, insecure_client=insecure_client, verify_ssl=verify_ssl)

    async def _fetch_with_httpx(
        self,
        url: str,
        client: httpx.AsyncClient,
        *,
        insecure_client: httpx.AsyncClient | None = None,
        verify_ssl: bool = True,
    ) -> str:
        async def _insecure_get() -> str:
            if insecure_client is not None:
                r = await insecure_client.get(url)
                r.raise_for_status()
                return r.text
            async with httpx.AsyncClient(
                follow_redirects=True, headers=DEFAULT_HEADERS,
                timeout=self.timeout_seconds, verify=False,
            ) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.text

        if not verify_ssl:
            return await _insecure_get()

        try:
            r = await client.get(url)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            if is_ssl_error(exc):
                logger.warning("SSL error fetching %s — retrying without verification", url)
                return await _insecure_get()
            raise

    # ── Legacy compat ──────────────────────────────────────────────────────────
    async def _fetch_html(self, site: SiteConfig, client: httpx.AsyncClient, browser: object) -> str:
        return await self._fetch_html_for(
            site.source_url, site.render_js, client, client,
            None, site.selectors, verify_ssl=site.verify_ssl,
        )


# ── Playwright pool runner (called inside a thread via run_in_executor) ────────

def _run_playwright_pool(
    site_configs: list[SiteConfig],
    browser_count: int,
    concurrency: int,
    timeout_seconds: float,
) -> list[ScrapeResult]:
    """
    Runs a PlaywrightPool in an isolated event loop inside a worker thread.
    This isolates Playwright's Chromium subprocesses from the uvicorn event loop
    on Windows (ProactorEventLoop) where mixing them causes hangs.
    """

    async def _run() -> list[ScrapeResult]:
        async with async_playwright() as pw:
            pool = PlaywrightPool(browser_count=browser_count)
            await pool.start(pw)

            async with (
                httpx.AsyncClient(
                    follow_redirects=True, headers=DEFAULT_HEADERS, timeout=timeout_seconds,
                ) as client,
                httpx.AsyncClient(
                    follow_redirects=True, headers=DEFAULT_HEADERS, timeout=timeout_seconds, verify=False,
                ) as insecure_client,
            ):
                sem = asyncio.Semaphore(concurrency)
                engine = ScraperEngine(
                    site_configs=site_configs,
                    concurrency=concurrency,
                    timeout_seconds=timeout_seconds,
                    playwright_browsers=browser_count,
                )
                try:
                    return await asyncio.gather(*[
                        engine._scrape_site(s, sem, client, insecure_client, pool)
                        for s in site_configs
                    ])
                finally:
                    await pool.close()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()

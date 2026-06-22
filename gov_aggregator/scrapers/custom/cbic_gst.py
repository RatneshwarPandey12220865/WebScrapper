from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.cbic_gst")

_BASE    = "https://cbic-gst.gov.in"
_HOME    = f"{_BASE}/"
_TICKERS = f"{_BASE}/tickers.html"
_TIMEOUT = 45  # seconds — increased from 30 for bulk-crawl concurrency
_RETRIES = 2


def _clean(value: str) -> str:
    return " ".join(value.split())


def _parse_news(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    for li in soup.select("#vmarquee ul li"):
        full_text = _clean(li.get_text())
        if not full_text:
            continue
        a = li.find("a", href=True)
        link = urljoin(_BASE, a["href"]) if a else ""
        items.append(ScrapedItem(
            title=full_text,
            link=link,
            is_pdf=link.lower().endswith(".pdf") if link else False,
            section_label="What's New",
        ))
    return items


def _parse_tickers(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []
    for row in soup.select(".innerpage-tab-content table tbody tr"):
        td = row.find("td")
        if not td:
            continue
        full_text = _clean(td.get_text())
        if not full_text:
            continue
        a = td.find("a", href=True)
        link = urljoin(_BASE, a["href"]) if a else ""
        items.append(ScrapedItem(
            title=full_text,
            link=link,
            is_pdf=link.lower().endswith(".pdf") if link else False,
            section_label="Tickers",
        ))
    return items


async def _fetch_with_retry(client: httpx.AsyncClient, url: str) -> str:
    """Fetch URL with up to _RETRIES attempts on transient errors; raises on persistent failure."""
    import asyncio
    last_exc: Exception | None = None
    for attempt in range(1, _RETRIES + 2):  # _RETRIES extra attempts after first try
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as exc:
            last_exc = exc
            logger.warning("[cbic-gst] fetch %s attempt %d failed: %s", url, attempt, exc)
            if attempt <= _RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
                continue
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 502, 503) and attempt <= _RETRIES:
                last_exc = exc
                await asyncio.sleep(3 * attempt)
                continue
            raise
    raise last_exc  # type: ignore[misc]


async def crawl_cbic_gst(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=_TIMEOUT,
        verify=getattr(_config, "verify_ssl", True),
    ) as client:
        items: list[ScrapedItem] = []

        try:
            html = await _fetch_with_retry(client, _HOME)
            news = _parse_news(html)
            logger.info("[cbic-gst] homepage: %d items", len(news))
            items.extend(news)
        except Exception as exc:
            logger.error("[cbic-gst] homepage fetch failed: %s", exc)
            raise  # let services.py handle SSL retry / error reporting

        try:
            html = await _fetch_with_retry(client, _TICKERS)
            tickers = _parse_tickers(html)
            logger.info("[cbic-gst] tickers: %d items", len(tickers))
            items.extend(tickers)
        except Exception as exc:
            # Tickers page is secondary — log and continue rather than aborting
            logger.warning("[cbic-gst] tickers fetch failed (non-fatal): %s", exc)

    return items

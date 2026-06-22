"""Custom scraper for Department of Animal Husbandry and Dairying (DAHD).

The listing page (https://dahd.gov.in/whats-new) returns node URLs
(e.g. /node/4067) as links.  Each node page contains the actual PDF at:

    <span class="file file--application-pdf">
        <a href="/sites/default/files/.../file.pdf" type="application/pdf">
    </span>

This scraper fetches the listing, then concurrently resolves each node
link to its final PDF URL.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dahd")

_BASE_URL = "https://dahd.gov.in"
_LISTING_URL = "https://dahd.gov.in/whats-new"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CONCURRENCY = 8
_MAX_PAGES = 5


def _clean(v: str | None) -> str:
    return " ".join((v or "").split())


def _parse_listing_page(html: str) -> list[dict]:
    """
    Extract (title, node_url) pairs from one listing page.

    Drupal listing structure:
        .view-what-s-new ol li
            .views-field-title a  →  title + href (relative node path)
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    for li in soup.select(".view-what-s-new ol li"):
        a = li.select_one(".views-field-title a")
        if not a:
            continue
        title = _clean(a.get_text())
        href = (a.get("href") or "").strip()
        if not title or not href:
            continue
        node_url = urljoin(_BASE_URL, href)
        items.append({"title": title, "node_url": node_url})

    return items


def _extract_pdf_from_detail(html: str) -> tuple[str | None, datetime | None]:
    """
    From a node detail page, return (pdf_url, published_at).

    PDF lives at:
        span.file--application-pdf a[href]   (preferred)
        a[type="application/pdf"]             (fallback)

    Date lives at:
        .field--name-field-date-circular time[datetime]
        or any <time datetime="..."> on the page
    """
    soup = BeautifulSoup(html, "html.parser")

    # ── PDF URL ──────────────────────────────────────────────────────────────
    pdf_url: str | None = None

    a_tag = soup.select_one("span.file--application-pdf a[href]")
    if not a_tag:
        a_tag = soup.select_one("a[type='application/pdf']")
    if not a_tag:
        # Broader fallback: any .pdf link in the node content
        a_tag = soup.select_one(".node__content a[href$='.pdf']")

    if a_tag:
        href = (a_tag.get("href") or "").strip()
        if href:
            pdf_url = urljoin(_BASE_URL, href)

    # ── Published date ───────────────────────────────────────────────────────
    published_at: datetime | None = None

    # Prefer the first <time> inside the date field
    time_tag = soup.select_one(".field--name-field-date-circular time[datetime]")
    if not time_tag:
        time_tag = soup.select_one("time[datetime]")
    if time_tag:
        published_at = _parse_date(time_tag.get("datetime", ""))

    return pdf_url, published_at


async def _resolve_node(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    item: dict,
) -> ScrapedItem | None:
    """
    Fetch a node detail page and return a ScrapedItem with the PDF URL.
    Falls back to the node URL itself if no PDF is found.
    """
    async with semaphore:
        try:
            resp = await _get(client, item["node_url"])
            pdf_url, published_at = _extract_pdf_from_detail(resp.text)
        except Exception as exc:
            logger.debug("[dahd] Failed to fetch %s: %s", item["node_url"], exc)
            pdf_url, published_at = None, None

    final_link = pdf_url or item["node_url"]
    is_pdf = bool(pdf_url)

    return ScrapedItem(
        title=item["title"],
        link=final_link,
        published_at=published_at,
        is_pdf=is_pdf,
        section_label="What's New",
    )


async def _get(client: httpx.AsyncClient, url: str, *, retries: int = 2) -> httpx.Response:
    """GET with retry on transient errors; raises on persistent failure."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 502, 503) and attempt < retries:
                last_exc = exc
                await asyncio.sleep(3 * (attempt + 1))
                continue
            raise
    raise last_exc  # type: ignore[misc]


async def crawl_dahd(config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
        verify=getattr(config, "verify_ssl", True),
    ) as client:

        # ── 1. Collect raw items from all listing pages ───────────────────
        raw_items: list[dict] = []
        for page_num in range(_MAX_PAGES):
            url = f"{_LISTING_URL}?page={page_num}"
            try:
                resp = await _get(client, url)
            except Exception as exc:
                logger.warning("[dahd] Listing page %d failed: %s", page_num, exc)
                if page_num == 0:
                    raise  # propagate so services.py can SSL-retry / report error
                break

            page_items = _parse_listing_page(resp.text)
            if not page_items:
                logger.info("[dahd] Page %d returned 0 items — stopping pagination", page_num)
                break

            raw_items.extend(page_items)
            logger.info("[dahd] Page %d: %d items (total so far: %d)", page_num, len(page_items), len(raw_items))

        if not raw_items:
            logger.warning("[dahd] No items found on any listing page")
            return []

        # ── 2. Resolve each node URL to its PDF concurrently ─────────────
        logger.info("[dahd] Resolving PDF URLs for %d items", len(raw_items))
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        results = await asyncio.gather(*[
            _resolve_node(client, semaphore, item)
            for item in raw_items
        ])

        # ── 3. Filter by min date and deduplicate by link ─────────────────
        seen_links: set[str] = set()
        items: list[ScrapedItem] = []
        for item in results:
            if item is None:
                continue
            if item.published_at and item.published_at < _MIN_DATE:
                continue
            if item.link in seen_links:
                continue
            seen_links.add(item.link)
            items.append(item)

        logger.info("[dahd] Final items after filter+dedup: %d", len(items))
        return items

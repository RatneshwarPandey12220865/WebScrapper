"""
Custom scraper for Bihar CMO Press Releases.

The page uses server-side jQuery DataTables. The table rows are NOT in the initial
HTML — they are injected via a POST AJAX request to the same URL with DataTables
parameters and a CSRF token.

Strategy:
  1. GET the page → extract _csrf token from <meta name="_csrf" content="...">
  2. POST to the same URL with DataTables payload (sEcho, iDisplayStart, iDisplayLength, rowId)
  3. Response is JSON: {"aaData": [["col1_html","col2_html",...], ...], "iTotalRecords": 339}
  4. Increment iDisplayStart by iDisplayLength to paginate through all 339 rows.
  5. Parse HTML in aaData cells for title, date, and download link.

Column positions (0-indexed in aaData rows):
  0 → counter
  1 → Sr. No.
  2 → PR No.
  3 → Subject / Title
  4 → Date (dd/mm/yyyy)
  5 → Download link HTML
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.bihar_cm")

_BASE = "https://state.bihar.gov.in"
_URL = "https://state.bihar.gov.in/main/SectionInformation.html?editForm&rowId=8929"
_ROW_ID = "8929"
_BATCH_SIZE = 100       # rows per AJAX request
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": _URL,
}

_AJAX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": _URL,
    "Origin": _BASE,
}


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_csrf(html: str) -> str:
    """Extract CSRF token from <meta name="_csrf" content="...">."""
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "_csrf"})
    if meta:
        return (meta.get("content") or "").strip()
    # Fallback: look for it in a hidden input
    inp = soup.find("input", attrs={"name": "_csrf"})
    if inp:
        return (inp.get("value") or "").strip()
    return ""


def _resolve_link(cell_html: str) -> str:
    """
    Extract absolute URL from a DataTable cell HTML string.
    Handles both <a href="..."> and onclick="downloadFile('...')" patterns.
    """
    soup = BeautifulSoup(cell_html, "html.parser")

    a = soup.find("a", href=True)
    if a:
        href = (a.get("href") or "").strip()
        if href and href != "#":
            return href if href.startswith("http") else urljoin(_BASE, href)

    # onclick="downloadFile('/path/to/file.pdf')"
    for el in soup.find_all(onclick=True):
        match = re.search(r"downloadFile\(['\"]([^'\"]+)['\"]", el.get("onclick", ""))
        if match:
            path = match.group(1)
            return path if path.startswith("http") else urljoin(_BASE, path)

    return ""


def _row_to_item(row: list) -> ScrapedItem | None:
    """Convert a single aaData row (list of HTML strings) to a ScrapedItem."""
    if len(row) < 6:
        return None

    # Cell 3 (index 3) = subject/title — may be plain text or HTML
    title_html = str(row[3])
    title = BeautifulSoup(title_html, "html.parser").get_text(" ", strip=True)
    title = " ".join(title.split())
    if not title:
        return None

    # Cell 4 (index 4) = date
    date_raw = BeautifulSoup(str(row[4]), "html.parser").get_text(strip=True)
    published_at = _parse_date(date_raw)
    if published_at and published_at < _MIN_DATE:
        return None

    # Cell 5 (index 5) = download link HTML
    link = _resolve_link(str(row[5]))
    if not link:
        return None

    return ScrapedItem(
        title=title,
        link=link,
        published_at=published_at,
        is_pdf=link.lower().endswith(".pdf"),
        section_label="CM Press Release",
    )


async def _fetch_page(
    client: httpx.AsyncClient,
    csrf: str,
    start: int,
    echo: int,
) -> dict:
    """POST one batch to the DataTables AJAX endpoint."""
    payload = {
        "sEcho": str(echo),
        "iDisplayStart": str(start),
        "iDisplayLength": str(_BATCH_SIZE),
        "rowId": _ROW_ID,
        "_csrf": csrf,
    }
    headers = {**_AJAX_HEADERS, "X-CSRF-TOKEN": csrf}
    try:
        resp = await client.post(_URL, data=payload, headers=headers, timeout=45)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("[bihar] AJAX POST failed (start=%d): %s", start, exc)
        return {}


async def crawl_bihar_cm(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=30,
    ) as client:

        # Step 1: GET the page to obtain the CSRF token
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[bihar] Failed to load page: %s", exc)
            return []

        csrf = _extract_csrf(resp.text)
        if not csrf:
            logger.warning("[bihar] CSRF token not found — AJAX requests will likely be rejected")

        logger.info("[bihar] CSRF token: %s…", csrf[:8] if csrf else "(none)")

        # Step 2: Fetch first batch to find iTotalRecords
        first = await _fetch_page(client, csrf, start=0, echo=1)
        total = int(first.get("iTotalRecords") or first.get("iTotalDisplayRecords") or 0)
        logger.info("[bihar] Total records reported: %d", total)

        raw_rows: list[list] = list(first.get("aaData") or [])

        # Step 3: Paginate remaining batches concurrently (cap at 10 parallel)
        if total > _BATCH_SIZE:
            starts = range(_BATCH_SIZE, total, _BATCH_SIZE)
            sem = asyncio.Semaphore(10)

            async def _guarded(start: int, echo: int) -> list[list]:
                async with sem:
                    data = await _fetch_page(client, csrf, start, echo)
                    return list(data.get("aaData") or [])

            batches = await asyncio.gather(
                *[_guarded(s, i + 2) for i, s in enumerate(starts)]
            )
            for batch in batches:
                raw_rows.extend(batch)

    # Step 4: Convert rows → ScrapedItems, deduplicate by link
    seen: set[str] = set()
    items: list[ScrapedItem] = []
    for row in raw_rows:
        item = _row_to_item(row)
        if item and item.link not in seen:
            seen.add(item.link)
            items.append(item)

    logger.info("[bihar] %d items after parsing and dedup (from %d raw rows)", len(items), len(raw_rows))
    return items

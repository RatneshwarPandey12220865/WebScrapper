from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.icar")

_BASE          = "https://www.icar.org.in"
_LATEST_URL    = f"{_BASE}/en/latest-update"
_CIRCULARS_URL = f"{_BASE}/index.php/en/circulars-data"
_MIN_DATE      = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CONCURRENCY   = 6
_MAX_PAGES     = 5

_ISO_RE  = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")          # 2022-12-05
_DMY_RE  = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b") # 05/12/2022
_MONTHS  = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1
)}
_TEXT_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")  # 05 Dec 2022


def _clean(v: str | None) -> str:
    return " ".join((v or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = raw.strip()

    # ISO datetime attribute: 2022-12-05 or 2022-12-05T00:00:00
    m = _ISO_RE.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass

    # Text like "05 Dec 2022"
    m = _TEXT_RE.search(s)
    if m:
        mon = _MONTHS.get(m.group(2).lower()[:3])
        if mon:
            try:
                return datetime(int(m.group(3)), mon, int(m.group(1)), tzinfo=timezone.utc)
            except ValueError:
                pass

    # DD/MM/YYYY or DD-MM-YYYY
    m = _DMY_RE.search(s)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


def _next_page(soup: BeautifulSoup, current_url: str) -> str | None:
    a = soup.select_one("li.pager__item--next a")
    return urljoin(current_url, a["href"]) if a and a.get("href") else None


# ── Latest Updates ────────────────────────────────────────────────────────────

def _parse_latest_listing(html: str, page_url: str) -> tuple[list[dict], str | None]:
    """Return list of {title, node_url} and next-page URL."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    for row in soup.select("table.views-view-table tbody tr"):
        a = row.select_one(".views-field-title a")
        if not a:
            continue
        title = _clean(a.get_text())
        href  = (a.get("href") or "").strip()
        if not title or not href:
            continue
        items.append({"title": title, "node_url": urljoin(_BASE, href)})

    return items, _next_page(soup, page_url)


def _parse_detail(html: str, node_url: str) -> tuple[str | None, datetime | None]:
    """
    From a detail page return (doc_url, published_at).

    Date:    time.datetime  (Drupal standard)
    Doc URL: .field--name-field-c a  →  fallback: any a[href$=".pdf"]
    """
    soup = BeautifulSoup(html, "html.parser")

    # Date
    published_at: datetime | None = None
    time_tag = soup.select_one("time.datetime")
    if time_tag:
        published_at = (
            _parse_date(time_tag.get("datetime"))
            or _parse_date(_clean(time_tag.get_text()))
        )

    # Document link
    doc_url: str | None = None
    a = soup.select_one(".field--name-field-c a[href]")
    if not a:
        a = soup.select_one("a[href$='.pdf']")
    if a:
        href = (a.get("href") or "").strip()
        if href:
            doc_url = urljoin(_BASE, href)

    return doc_url, published_at


async def _resolve_detail(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    item: dict,
) -> ScrapedItem | None:
    async with sem:
        try:
            resp = await client.get(item["node_url"])
            resp.raise_for_status()
            doc_url, published_at = _parse_detail(resp.text, item["node_url"])
        except Exception as exc:
            logger.debug("[icar] detail fetch failed %s: %s", item["node_url"], exc)
            doc_url, published_at = None, None

    if published_at and published_at < _MIN_DATE:
        return None

    link = doc_url or item["node_url"]
    return ScrapedItem(
        title=item["title"],
        link=link,
        published_at=published_at,
        is_pdf=bool(doc_url) and link.lower().endswith(".pdf"),
        section_label="Latest Updates",
    )


async def _crawl_latest(client: httpx.AsyncClient) -> list[ScrapedItem]:
    raw: list[dict] = []
    url: str | None = _LATEST_URL
    page_num = 0

    while url and page_num < _MAX_PAGES:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[icar] latest-update page %d failed: %s", page_num, exc)
            if page_num == 0:
                raise
            break

        page_items, url = _parse_latest_listing(resp.text, str(resp.url))
        if not page_items:
            break
        raw.extend(page_items)
        logger.info("[icar] latest-update page %d: %d items", page_num, len(page_items))
        page_num += 1

    if not raw:
        return []

    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(*[_resolve_detail(client, sem, it) for it in raw])
    items = [it for it in results if it is not None]
    logger.info("[icar] latest-update resolved: %d items", len(items))
    return items


# ── Circulars ─────────────────────────────────────────────────────────────────

def _parse_circulars(html: str, page_url: str) -> tuple[list[ScrapedItem], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select(".view-id-budget_vis_s_vis_progressive table tbody tr, .view-content table tbody tr"):
        cells = row.select("td")
        if len(cells) < 2:
            continue

        # col 2: title, col 4: date, col 5: download link
        title_cell = cells[1]
        title = _clean(title_cell.get_text())
        if not title:
            continue

        date_text = _clean(cells[3].get_text()) if len(cells) >= 4 else ""
        published_at = _parse_date(date_text)
        if published_at and published_at < _MIN_DATE:
            continue

        link_cell = cells[4] if len(cells) >= 5 else title_cell
        a = link_cell.find("a", href=True) or title_cell.find("a", href=True)
        if not a:
            continue
        link = urljoin(page_url, a["href"])

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Circulars",
        ))

    return items, _next_page(soup, page_url)


async def _crawl_circulars(client: httpx.AsyncClient) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    url: str | None = _CIRCULARS_URL
    page_num = 0

    while url and page_num < _MAX_PAGES:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[icar] circulars page %d failed: %s", page_num, exc)
            if page_num == 0:
                raise
            break

        page_items, url = _parse_circulars(resp.text, str(resp.url))
        if not page_items:
            break
        all_items.extend(page_items)
        logger.info("[icar] circulars page %d: %d items", page_num, len(page_items))
        page_num += 1

    return all_items


# ── Entry point ───────────────────────────────────────────────────────────────

async def crawl_icar(config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=45,
        verify=getattr(config, "verify_ssl", True),
    ) as client:
        latest, circulars = await asyncio.gather(
            _crawl_latest(client),
            _crawl_circulars(client),
            return_exceptions=True,
        )

    items: list[ScrapedItem] = []
    for result, label in ((latest, "latest-update"), (circulars, "circulars")):
        if isinstance(result, Exception):
            logger.error("[icar] %s section failed: %s", label, result)
        else:
            items.extend(result)

    logger.info("[icar] grand total: %d items", len(items))
    return items

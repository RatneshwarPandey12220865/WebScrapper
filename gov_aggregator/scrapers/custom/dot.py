from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://www.dot.gov.in"
CMS_BASE_URL = f"{BASE_URL}/cms"
CMS_API_BASE = f"{CMS_BASE_URL}/wp-json"

WHATS_NEW_URL = f"{BASE_URL}/whats-new"
ORDERS_URL = f"{BASE_URL}/documents"
PRESS_URL = f"{BASE_URL}/documents/press-release"

WHATS_NEW_API = f"{CMS_API_BASE}/custom/api/new-posts"
WHATS_NEW_FALLBACK_API = f"{CMS_API_BASE}/post-page/whats_new"
DOCUMENTS_API = f"{CMS_API_BASE}/document/documents"
FILE_DETAIL_API = f"{CMS_API_BASE}/post-page/post"

ORDERS_CATEGORY = "orders-and-notices"
PRESS_CATEGORY = "press-release"

MAX_ORDERS_PAGES = 15
MAX_PRESS_PAGES = 5
PAGE_SIZE = 50
CONCURRENCY = 4

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": f"{BASE_URL}/",
}

API_HEADERS = {
    **DEFAULT_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "apikey": "4bW5t13453pa",
}


def _parse_dot_date(raw: str | None) -> datetime | None:
    if not raw:
        return None

    cleaned = raw.strip()
    if not cleaned:
        return None

    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _extract_announcementbox_items(
    html: str,
    *,
    base_url: str,
    section_label: str,
) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for row in soup.select("div[role='row'].announcementbox"):
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
            published_at = _parse_dot_date(candidate.get("aria-label") or candidate.get_text(strip=True))
            if published_at:
                break

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                summary=None,
                published_at=published_at,
                is_pdf=link.lower().endswith(".pdf") or (link_tag.get("type", "").lower() == "pdf"),
                section_label=section_label,
            )
        )

    return items


async def _fetch_html(client: httpx.AsyncClient, url: str, *, params: dict | None = None) -> str:
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        print(f"  [DoT] fetch error {url}: {exc}")
        return ""


async def _fetch_json(client: httpx.AsyncClient, url: str, *, params: dict | None = None) -> dict | list | None:
    try:
        response = await client.get(url, params=params, headers=API_HEADERS)
        response.raise_for_status()
        if "application/json" not in (response.headers.get("content-type") or ""):
            return None
        return response.json()
    except Exception:
        return None


def _api_records(data: dict | list | None) -> list[dict]:
    if isinstance(data, list):
        return [record for record in data if isinstance(record, dict)]
    if isinstance(data, dict):
        for key in ("posts", "data", "items", "results", "documents"):
            value = data.get(key)
            if isinstance(value, list):
                return [record for record in value if isinstance(record, dict)]
    return []


def _total_pages(data: dict | list | None, fallback: int) -> int:
    if isinstance(data, dict):
        try:
            return max(1, int(data.get("total_pages") or fallback))
        except (TypeError, ValueError):
            pass
    return fallback


def _first_attachment_id(record: dict) -> int | None:
    acf = record.get("acf_data") or {}
    for file_entry in acf.get("file") or []:
        if not isinstance(file_entry, dict):
            continue
        file_ids = file_entry.get("file") or []
        for file_id in file_ids:
            try:
                return int(file_id)
            except (TypeError, ValueError):
                continue
    return None


def _external_link(record: dict) -> str | None:
    acf = record.get("acf_data") or {}
    for file_entry in acf.get("file") or []:
        if not isinstance(file_entry, dict):
            continue
        href = (file_entry.get("external_link") or "").strip()
        if href:
            return href
    return None


async def _resolve_attachment_url(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    cache: dict[int, str | None],
    attachment_id: int,
) -> str | None:
    if attachment_id in cache:
        return cache[attachment_id]

    async with semaphore:
        data = await _fetch_json(client, FILE_DETAIL_API, params={"id": attachment_id})

    url: str | None = None
    if isinstance(data, dict):
        post = data.get("posts")
        if isinstance(post, dict):
            acf = post.get("acf_data") or {}
            pdf = acf.get("pdf")
            if isinstance(pdf, dict):
                pdf_url = (pdf.get("url") or "").strip()
                if pdf_url:
                    url = pdf_url

    cache[attachment_id] = url
    return url


async def _record_to_item(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    attachment_cache: dict[int, str | None],
    *,
    record: dict,
    section_label: str,
) -> ScrapedItem | None:
    acf = record.get("acf_data") or {}
    title = (acf.get("title") or record.get("post_title") or "").strip()
    if not title:
        return None

    published_at = (
        _parse_dot_date(acf.get("date"))
        or _parse_dot_date(acf.get("file_date"))
        or _parse_dot_date(record.get("post_date"))
        or _parse_dot_date(record.get("post_modified"))
    )

    link = _external_link(record)
    attachment_id = _first_attachment_id(record)
    if not link and attachment_id is not None:
        link = await _resolve_attachment_url(client, semaphore, attachment_cache, attachment_id)

    if not link:
        post_slug = (record.get("post_slug") or "").strip()
        if post_slug:
            link = f"{BASE_URL}/documents/{post_slug}"
        else:
            link = (record.get("guid") or "").strip()

    if not link:
        return None

    is_pdf = link.lower().endswith(".pdf")
    if not is_pdf:
        for file_entry in acf.get("file") or []:
            if isinstance(file_entry, dict) and str(file_entry.get("type") or "").strip().upper() == "PDF":
                is_pdf = True
                break

    return ScrapedItem(
        title=title,
        link=link,
        summary=None,
        published_at=published_at,
        is_pdf=is_pdf,
        section_label=section_label,
    )


async def _records_to_items(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    attachment_cache: dict[int, str | None],
    *,
    records: list[dict],
    section_label: str,
) -> list[ScrapedItem]:
    tasks = [
        _record_to_item(
            client,
            semaphore,
            attachment_cache,
            record=record,
            section_label=section_label,
        )
        for record in records
    ]
    resolved = await asyncio.gather(*tasks)
    return [item for item in resolved if item is not None]


async def _fetch_whats_new_items(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    attachment_cache: dict[int, str | None],
) -> list[ScrapedItem]:
    data = await _fetch_json(client, WHATS_NEW_API, params={"s": ""})
    records = _api_records(data)
    if records:
        items = await _records_to_items(
            client,
            semaphore,
            attachment_cache,
            records=records,
            section_label="What's New",
        )
        print(f"  [DoT] What's New via CMS API: {len(items)} items")
        return items

    data = await _fetch_json(client, WHATS_NEW_FALLBACK_API)
    records = _api_records(data)
    if records:
        items = await _records_to_items(
            client,
            semaphore,
            attachment_cache,
            records=records,
            section_label="What's New",
        )
        print(f"  [DoT] What's New via fallback CMS API: {len(items)} items")
        return items

    html = await _fetch_html(client, WHATS_NEW_URL)
    if not html:
        return []

    items = _extract_announcementbox_items(
        html,
        base_url=BASE_URL,
        section_label="What's New",
    )
    print(f"  [DoT] What's New via HTML fallback: {len(items)} items")
    return items


async def _scrape_paginated_section(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    attachment_cache: dict[int, str | None],
    *,
    section_label: str,
    category_slug: str,
    page_url: str,
    max_pages: int,
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    seen_links: set[str] = set()

    def add_items(new_items: list[ScrapedItem]) -> None:
        for item in new_items:
            if item.link in seen_links:
                continue
            seen_links.add(item.link)
            item.section_label = section_label
            items.append(item)

    api_success = False
    total_pages = max_pages
    for page in range(1, max_pages + 1):
        data = await _fetch_json(
            client,
            DOCUMENTS_API,
            params={
                "document_category": category_slug,
                "limit": PAGE_SIZE,
                "page": page,
            },
        )

        records = _api_records(data)
        if not records:
            break

        total_pages = min(max_pages, _total_pages(data, max_pages))
        page_items = await _records_to_items(
            client,
            semaphore,
            attachment_cache,
            records=records,
            section_label=section_label,
        )
        if not page_items:
            break

        add_items(page_items)
        api_success = True
        print(f"  [DoT] {section_label} page {page}/{total_pages}: {len(page_items)} items via CMS API")

        if page >= total_pages:
            break

    if api_success:
        return items

    print(f"  [DoT] CMS API failed for {section_label}, trying HTML fallback ...")
    for page in range(1, max_pages + 1):
        url = f"{page_url}?page={page}" if page > 1 else page_url
        html = await _fetch_html(client, url)
        if not html:
            break

        page_items = _extract_announcementbox_items(
            html,
            base_url=BASE_URL,
            section_label=section_label,
        )
        if not page_items:
            break

        add_items(page_items)
        print(f"  [DoT] {section_label} page {page}: {len(page_items)} items via HTML")

    return items


async def crawl_dot(config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    attachment_cache: dict[int, str | None] = {}
    semaphore = asyncio.Semaphore(CONCURRENCY)

    client_kwargs = {
        "follow_redirects": True,
        "headers": DEFAULT_HEADERS,
        "timeout": 60.0,
    }
    if config.verify_ssl is False:
        client_kwargs["verify"] = False

    async with httpx.AsyncClient(**client_kwargs) as client:
        print("  [DoT] Fetching What's New ...")
        whats_new_items = await _fetch_whats_new_items(client, semaphore, attachment_cache)
        all_items.extend(whats_new_items)

        print(f"  [DoT] Fetching Orders and Notices (up to {MAX_ORDERS_PAGES} pages) ...")
        orders_items = await _scrape_paginated_section(
            client,
            semaphore,
            attachment_cache,
            section_label="Orders and Notices",
            category_slug=ORDERS_CATEGORY,
            page_url=ORDERS_URL,
            max_pages=MAX_ORDERS_PAGES,
        )
        all_items.extend(orders_items)
        print(f"  [DoT] Orders and Notices total: {len(orders_items)} items")

        print(f"  [DoT] Fetching Press Releases (up to {MAX_PRESS_PAGES} pages) ...")
        press_items = await _scrape_paginated_section(
            client,
            semaphore,
            attachment_cache,
            section_label="Press Releases",
            category_slug=PRESS_CATEGORY,
            page_url=PRESS_URL,
            max_pages=MAX_PRESS_PAGES,
        )
        all_items.extend(press_items)
        print(f"  [DoT] Press Releases total: {len(press_items)} items")

    unique: list[ScrapedItem] = []
    seen_links: set[str] = set()
    for item in all_items:
        if item.link in seen_links:
            continue
        seen_links.add(item.link)
        unique.append(item)

    print(f"  [DoT] Total unique items: {len(unique)}")
    return unique

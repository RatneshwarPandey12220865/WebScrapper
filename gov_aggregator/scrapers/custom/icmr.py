from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://www.icmr.gov.in"
WHATS_NEW_URL = f"{BASE_URL}/whats-new"
PRESS_URL = f"{BASE_URL}/press-releases"

MAX_PAGES = 3
MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _parse_icmr_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    try:
        response = await client.get(url, headers=DEFAULT_HEADERS, timeout=30.0)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"  [ICMR] fetch error: {e}")
        return ""


def _extract_whats_new(html: str, base_url: str, apply_time_filter: bool = False) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    container = soup.select_one("div.colorBox.mt-3")
    if not container:
        return items

    for link in container.select("a.colorBox__list.extLinks"):
        href = link.get("href", "").strip()
        if not href:
            continue

        if href.startswith("/post/"):
            link_url = urljoin(base_url, href)
        elif href.startswith("http"):
            link_url = href
        else:
            link_url = urljoin(base_url, href)

        title_elem = link.select_one("span.details")
        title = title_elem.get_text(" ", strip=True) if title_elem else ""
        if not title:
            continue

        category_elem = link.select_one("h2.title")
        category = category_elem.get_text(" ", strip=True) if category_elem else ""

        published_at: datetime | None = None
        date_match = title
        if "(" in date_match and ")" in date_match:
            date_part = date_match.split("(")[-1].rstrip(")")
            published_at = _parse_icmr_date(date_part)
            if published_at and published_at < MIN_DATE and apply_time_filter:
                continue

        item = ScrapedItem(
            title=title,
            link=link_url,
            summary=category,
            published_at=published_at,
            is_pdf=link_url.lower().endswith(".pdf"),
            section_label="What's New",
        )

        if apply_time_filter:
            if published_at and published_at >= MIN_DATE:
                items.append(item)
        else:
            items.append(item)

    return items


def _extract_press_releases(html: str, base_url: str, apply_time_filter: bool = False) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    rows = soup.select("table tbody tr")
    if not rows:
        rows = soup.select("table tr")
        if len(rows) > 1:
            rows = rows[1:]

    for row in rows:
        cells = row.select("td")
        if len(cells) < 2:
            continue

        date_cell = cells[2]  # Date is in column 3
        title_cell = cells[1]  # Title is in column 2
        link_cell = cells[3] if len(cells) > 3 else None  # Link is in column 4

        title = title_cell.get_text(" ", strip=True) if title_cell else ""
        if not title:
            continue

        link = ""
        if link_cell:
            link_tag = link_cell.select_one("a[href]")
            if link_tag:
                href = link_tag.get("href", "").strip()
                if href.startswith("/"):
                    link = urljoin(base_url, href)
                elif href.startswith("http"):
                    link = href

        if not link:
            continue

        published_at = None
        date_text = date_cell.get_text(" ", strip=True) if date_cell else ""
        published_at = _parse_icmr_date(date_text)

        item = ScrapedItem(
            title=title,
            link=link,
            summary=None,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Press Releases",
        )

        if apply_time_filter:
            if published_at and published_at >= MIN_DATE:
                items.append(item)
        elif published_at is None:
            items.append(item)
        else:
            items.append(item)

    return items


def _extract_pdf_from_detail(html: str) -> str | None:
    """
    From a detail page, return the English PDF href (preferred) or the only PDF found.
    Looks for <a class="descView__link" href="...pdf">.
    """
    soup = BeautifulSoup(html, "html.parser")
    pdf_links = soup.select("a.descView__link[href]")
    pdf_links = [a for a in pdf_links if a.get("href", "").lower().endswith(".pdf")]

    if not pdf_links:
        return None
    if len(pdf_links) == 1:
        return pdf_links[0]["href"]

    # Multiple PDFs — prefer English
    for a in pdf_links:
        label = (a.get("aria-label") or "").lower()
        span_text = (a.select_one("span.value") or a).get_text().lower()
        if "english" in label or "english" in span_text:
            return a["href"]

    # Fallback: first one
    return pdf_links[0]["href"]


async def _resolve_pdf(
    client: httpx.AsyncClient,
    item: ScrapedItem,
    sem: asyncio.Semaphore,
) -> ScrapedItem:
    """Follow a detail-page link and swap it for the actual PDF URL if found."""
    if item.is_pdf or not item.link:
        return item

    async with sem:
        html = await _fetch_page(client, item.link)

    if not html:
        return item

    pdf_href = _extract_pdf_from_detail(html)
    if not pdf_href:
        return item

    pdf_url = pdf_href if pdf_href.startswith("http") else urljoin(BASE_URL, pdf_href)
    return ScrapedItem(
        title=item.title,
        link=pdf_url,
        summary=item.summary,
        published_at=item.published_at,
        is_pdf=True,
        section_label=item.section_label,
    )


async def crawl_icmr(config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        print("  [ICMR] Fetching What's New ...")
        for page in range(1, MAX_PAGES + 1):
            if page == 1:
                url = WHATS_NEW_URL
            else:
                url = f"{WHATS_NEW_URL}?page={page}"

            print(f"  [ICMR] Page {page}: {url}")
            html = await _fetch_page(client, url)
            if not html:
                break

            page_items = _extract_whats_new(html, BASE_URL, apply_time_filter=False)
            if not page_items:
                break

            all_items.extend(page_items)
            print(f"  [ICMR] What's New page {page}: {len(page_items)} items")

        print(f"  [ICMR] Fetching Press Releases ...")
        for page in range(1, MAX_PAGES + 1):
            if page == 1:
                url = PRESS_URL
            else:
                url = f"{PRESS_URL}?page={page}"

            print(f"  [ICMR] Press page {page}: {url}")
            html = await _fetch_page(client, url)
            if not html:
                break

            page_items = _extract_press_releases(html, BASE_URL, apply_time_filter=True)
            if not page_items:
                break

            all_items.extend(page_items)
            print(f"  [ICMR] Press Release page {page}: {len(page_items)} items")

    # Follow detail pages to resolve actual PDF links (max 5 concurrent requests)
    print(f"  [ICMR] Resolving PDFs for {len(all_items)} items ...")
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        sem = asyncio.Semaphore(5)
        resolved = await asyncio.gather(
            *[_resolve_pdf(client, item, sem) for item in all_items]
        )

    unique: list[ScrapedItem] = []
    seen_links: set[str] = set()
    for item in resolved:
        if item.link in seen_links:
            continue
        seen_links.add(item.link)
        unique.append(item)

    print(f"  [ICMR] Total unique items: {len(unique)}")
    return unique
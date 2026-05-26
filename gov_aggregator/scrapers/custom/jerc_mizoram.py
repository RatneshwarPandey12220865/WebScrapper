from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://jerc.mizoram.gov.in"

# Sections to crawl: (path, section_label)
_SECTIONS = [
    ("/category/notifications", "Notifications"),
    ("/category/notice-board", "Notice Board"),
    ("/category/press-releases", "Press Releases"),
    ("/category/news", "News"),
    ("/category/events", "Events"),
    ("/posts", "All Posts"),
]

_ORDINAL_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
# "2nd May 25 8:36 AM"  or  "2nd May 2025, 8:36 AM"
_DATE_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{2,4})(?:[,\s]+(\d{1,2}:\d{2}\s*[AP]M))?",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if not m:
        return None
    day = int(m.group(1))
    month_str = m.group(2)[:3].lower()
    month = _MONTHS.get(month_str)
    if not month:
        return None
    year_raw = int(m.group(3))
    year = year_raw + 2000 if year_raw < 100 else year_raw
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _extract_post_links(html: str) -> list[str]:
    """Return all unique /post/{slug} hrefs found in the page."""
    soup = BeautifulSoup(html, "html.parser")
    slugs: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=re.compile(r"^/post/")):
        href = tag.get("href", "")
        slug = href[len("/post/"):].strip("/")
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


def _scrape_post_page(html: str, post_url: str, section_label: str) -> ScrapedItem | None:
    """Extract a ScrapedItem from a single post page HTML."""
    soup = BeautifulSoup(html, "html.parser")

    content_block = soup.select_one("div#content-block")
    if not content_block:
        content_block = soup  # fallback to full page

    # Title: try common selectors in order
    title_el = (
        content_block.select_one(".post-title")
        or content_block.select_one("h1")
        or content_block.select_one("h2")
    )
    title = _clean(title_el.get_text() if title_el else "")
    if not title:
        return None

    # Date: look for "Post Created On:" or similar meta text
    date: datetime | None = None
    for text_node in content_block.stripped_strings:
        if "created" in text_node.lower() or "posted" in text_node.lower():
            date = _parse_date(text_node)
            if date:
                break
    if not date:
        # Try any date-like string in .post-meta or .post-info
        meta = content_block.select_one(".post-meta, .post-info, .entry-meta")
        if meta:
            date = _parse_date(meta.get_text())

    # PDF / attachment link: prefer direct uploads, then any .pdf href
    pdf_link: str | None = None
    wrapper = content_block.select_one("div#post-content-wrapper, .post-content, .entry-content")
    search_root = wrapper or content_block
    for a_tag in search_root.find_all("a", href=True):
        href: str = a_tag["href"]
        if "/uploads/" in href or href.lower().endswith(".pdf"):
            pdf_link = urljoin(BASE_URL, href)
            break

    final_link = pdf_link or post_url
    is_pdf = bool(pdf_link)

    return ScrapedItem(
        title=title,
        link=final_link,
        published_at=date,
        is_pdf=is_pdf,
        section_label=section_label,
    )


async def _fetch_post_attachment(client: httpx.AsyncClient, post_id: int) -> str | None:
    """Call /post-attachment/{id} and return the first file URL from the JSON array."""
    try:
        resp = await client.get(f"{BASE_URL}/post-attachment/{post_id}", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                first = data[0]
                return first.get("url") or first.get("file_url") or first.get("path")
    except Exception:
        pass
    return None


async def crawl_jerc_mizoram(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    seen_slugs: set[str] = set()
    # slug -> section_label (first match wins)
    slug_to_label: dict[str, str] = {}

    async with httpx.AsyncClient(
        follow_redirects=True, headers=DEFAULT_HEADERS, timeout=60
    ) as client:
        # --- Phase 1: collect post slugs from listing / category pages ---
        for path, label in _SECTIONS:
            url = BASE_URL + path
            try:
                resp = await client.get(url)
            except httpx.RequestError:
                continue
            if resp.status_code != 200:
                continue
            for slug in _extract_post_links(resp.text):
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    slug_to_label[slug] = label

        # --- Phase 2: scrape each unique post page ---
        for slug, label in slug_to_label.items():
            post_url = f"{BASE_URL}/post/{slug}"
            try:
                resp = await client.get(post_url)
            except httpx.RequestError:
                continue
            if resp.status_code != 200:
                continue

            item = _scrape_post_page(resp.text, post_url, label)
            if item:
                items.append(item)

    return items

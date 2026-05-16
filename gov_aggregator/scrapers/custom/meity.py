"""
Custom crawler for Ministry of Electronics and Information Technology (MeitY).

The site migrated to a Next.js + WordPress CMS stack. Content is served via
WordPress REST API endpoints under the IDN domain (Hindi script TLD) which
is accessible without bot-protection. www.meity.gov.in returns 403 for
headless browsers due to Akamai WAF.

API base: https://xn--m1bdba5a7gresc7dsa.xn--11b7cb3a6a.xn--h2brj9c/cms/wp-json/
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.meity")

_API_BASE = "https://xn--m1bdba5a7gresc7dsa.xn--11b7cb3a6a.xn--h2brj9c/cms/wp-json"
_SITE_BASE = "https://xn--m1bdba5a7gresc7dsa.xn--11b7cb3a6a.xn--h2brj9c"
_TIMEOUT = 20.0

# Document type slugs to (API type param, section label)
_DOCUMENT_SECTIONS = [
    ("Orders and Notices", "Orders & Notices"),
    ("Press Release", "Press Releases"),
    ("Gazettes Notifications", "Gazettes Notifications"),
]


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_link_from_post(post: dict) -> str:
    """Pull the best download/view link from an acf_data document post."""
    acf = post.get("acf_data") or {}
    files = acf.get("file") or []
    if isinstance(files, list):
        for f in files:
            if not isinstance(f, dict):
                continue
            pdf_obj = f.get("pdf") or {}
            pdf_url = pdf_obj.get("url", "").strip() if isinstance(pdf_obj, dict) else ""
            if pdf_url:
                return pdf_url
            ext = (f.get("external_link") or "").strip()
            if ext:
                return ext
    # Fallback: external_link at top-level acf
    ext = (acf.get("external_link") or "").strip()
    if ext:
        return ext
    return ""


def _parse_document_post(post: dict, section_label: str) -> ScrapedItem | None:
    acf = post.get("acf_data") or {}
    title = (acf.get("title") or post.get("post_title") or "").strip()
    if not title:
        return None

    link = _extract_link_from_post(post)
    if not link:
        return None

    published_at = _parse_date(acf.get("date")) or _parse_date(post.get("post_date"))
    is_pdf = link.lower().endswith(".pdf")

    return ScrapedItem(
        title=title,
        link=link,
        published_at=published_at,
        is_pdf=is_pdf,
        section_label=section_label,
    )


def _parse_whatsnew_post(post: dict) -> ScrapedItem | None:
    acf = post.get("acf_data") or {}
    title = (acf.get("title") or post.get("post_title") or "").strip()
    if not title:
        return None

    link_type = (acf.get("type") or "").strip()
    if link_type == "Internal Link":
        rel = (acf.get("internal_link") or "").strip()
        link = urljoin(_SITE_BASE, rel) if rel else ""
    elif link_type == "External Link":
        link = (acf.get("external_link") or "").strip()
    elif link_type == "PDF":
        pdf_obj = acf.get("pdf") or {}
        link = (pdf_obj.get("url") if isinstance(pdf_obj, dict) else "") or ""
    else:
        # Fall through to any available URL field
        link = (acf.get("external_link") or acf.get("internal_link") or "").strip()
        if link and not link.startswith("http"):
            link = urljoin(_SITE_BASE, link)

    if not link:
        return None

    # NOTE: What's New section deliberately has no date filtering
    # to ensure all items are returned regardless of publish date
    is_pdf = link.lower().endswith(".pdf")

    return ScrapedItem(
        title=title,
        link=link,
        published_at=None,  # No date - bypasses global min_date filter
        is_pdf=is_pdf,
        section_label="What's New",
    )


async def _fetch_document_section(
    client: httpx.AsyncClient,
    doc_type: str,
    section_label: str,
    max_pages: int = 10,
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    seen: set[str] = set()

    page = 1
    while page <= max_pages:
        url = f"{_API_BASE}/document/documents"
        params = {"type": doc_type, "limit": "50", "page": str(page)}
        try:
            resp = await client.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("[meity] %s page %d fetch failed: %s", section_label, page, exc)
            break

        posts = data.get("posts") or []
        total_pages = int(data.get("total_pages") or 1)

        for post in posts:
            item = _parse_document_post(post, section_label)
            if item and item.link not in seen:
                seen.add(item.link)
                items.append(item)

        if page >= total_pages or not posts:
            break
        page += 1

    logger.info("[meity] %s: scraped %d items", section_label, len(items))
    return items


async def _fetch_whatsnew(client: httpx.AsyncClient) -> list[ScrapedItem]:
    url = f"{_API_BASE}/post-page/whats_new"
    items: list[ScrapedItem] = []
    try:
        resp = await client.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("[meity] What's New fetch failed: %s", exc)
        return items

    for post in data.get("posts") or []:
        item = _parse_whatsnew_post(post)
        if item:
            items.append(item)

    logger.info("[meity] What's New: scraped %d items", len(items))
    return items


async def crawl_meity(config: SiteConfig) -> list[ScrapedItem]:
    headers = {k: v for k, v in DEFAULT_HEADERS.items()}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        all_items: list[ScrapedItem] = []

        # What's New
        all_items.extend(await _fetch_whatsnew(client))

        # Document sections driven by config sections or defaults
        sections_to_fetch = _DOCUMENT_SECTIONS[:]
        if config.sections:
            sections_to_fetch = [
                (s.selectors.get("api_type", ""), s.section_label)
                for s in config.sections
                if s.selectors.get("api_type")
            ] or _DOCUMENT_SECTIONS[:]

        for doc_type, label in sections_to_fetch:
            all_items.extend(await _fetch_document_section(client, doc_type, label))

    # Deduplicate by link
    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in all_items:
        if item.link not in seen:
            seen.add(item.link)
            unique.append(item)

    return unique

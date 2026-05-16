"""
Custom crawler for Department for Promotion of Industry and Internal Trade (DPIIT).

www.dpiit.gov.in is a statically-exported Next.js site behind Akamai CDN which
blocks all headless browsers. The real backend is master-dpiit.digifootprint.gov.in
which is accessible without restrictions.

Data architecture:
  - Some sections have direct WordPress REST API endpoints returning paginated JSON
  - Document sections (gazette, orders, guidelines, etc.) embed post IDs in the
    page JS bundle — we use Playwright on the backend domain to intercept the
    batched post?id= fetch calls, then fetch post data via httpx

Direct API endpoints (httpx):
  - /cms/wp-json/post-page/whats_new       → What's New
  - /cms/wp-json/post-page/tenders_post    → Tenders
  - /cms/wp-json/post-page/brochures_post  → Brochures
  - /cms/wp-json/post-page/careers_post    → Vacancies

Playwright-intercepted document sections:
  - /documents/gazettes-notifications      → Gazette Notifications
  - /documents/orders-and-notices          → Orders & Notices
  - /documents/guidelines                  → Guidelines
  - /documents/reports                     → Reports
  - /documents/publications                → Publications
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dpiit")

_BACKEND = "https://master-dpiit.digifootprint.gov.in"
_CMS     = f"{_BACKEND}/cms/wp-json/post-page"
_HEADERS = {
    "User-Agent": DEFAULT_HEADERS["User-Agent"],
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
_GOTO_TIMEOUT = 40_000
_NET_TIMEOUT  = 20


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", raw or "")
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _abs(href: str | None) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return urljoin("https://www.dpiit.gov.in", href)


# ---------------------------------------------------------------------------
# Parse a single post dict into a ScrapedItem
# ---------------------------------------------------------------------------

def _post_to_item(post: dict, section_label: str) -> ScrapedItem | None:
    """Convert a WordPress post dict (with acf_data) into a ScrapedItem.

    Handles three ACF layouts:
      - central_documents: acf_data.pdf.url  (English PDF)
      - whats_new External Link: acf_data.external_link
      - tenders_post / brochures_post / careers_post: acf_data.pdf.url
        or internal link via acf_data.internal_page_link
    """
    acf = post.get("acf_data") or {}
    title = _clean(acf.get("title") or post.get("post_title") or "")
    if not title:
        return None

    raw_date = acf.get("file_date") or acf.get("date") or post.get("post_date", "")
    published_at = _parse_date(raw_date)

    # Prefer English PDF; fall back to any PDF
    pdf_obj = acf.get("pdf") or acf.get("pdf_english")
    if not pdf_obj and isinstance(acf.get("file"), list) and acf["file"]:
        # whats_new "Document" type stores attachment IDs in acf.file; skip (needs extra API call)
        pass

    if pdf_obj and isinstance(pdf_obj, dict):
        link = _abs(pdf_obj.get("url") or "")
        if link:
            return ScrapedItem(
                title=title, link=link,
                published_at=published_at,
                is_pdf=True,
                section_label=section_label,
            )

    # External link (whats_new)
    ext = acf.get("external_link") or ""
    if ext:
        return ScrapedItem(
            title=title, link=_abs(ext),
            published_at=published_at,
            is_pdf=False,
            section_label=section_label,
        )

    # Internal page link (careers detail, publications "View All")
    internal = acf.get("internal_page_link") or acf.get("page_link") or ""
    if internal:
        return ScrapedItem(
            title=title, link=_abs(internal),
            published_at=published_at,
            is_pdf=False,
            section_label=section_label,
        )

    return None


# ---------------------------------------------------------------------------
# httpx helpers
# ---------------------------------------------------------------------------

def _fetch_paginated_api(slug: str, section_label: str) -> list[ScrapedItem]:
    """Fetch all pages from a direct WP API endpoint.

    Some post types (tenders_post, brochures_post, careers_post) link to their
    PDF via acf_data.file = [central_documents_post_id] rather than a direct URL.
    These are resolved with a second batch fetch.
    """
    items: list[ScrapedItem] = []
    # parent post data keyed by file_id → (title, published_at) for two-level posts
    deferred: dict[int, tuple[str, datetime | None]] = {}

    page = 1
    while True:
        url = f"{_CMS}/{slug}?page={page}&per_page=50"
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=_NET_TIMEOUT, follow_redirects=True)
            data = r.json()
        except Exception as exc:
            logger.warning("[dpiit] API error for %s page %d: %s", slug, page, exc)
            break

        posts = data.get("posts", []) if isinstance(data, dict) else []
        if not posts:
            break

        for post in posts:
            acf = post.get("acf_data") or {}
            # Check for two-level reference (file = [central_documents_id])
            file_ids = acf.get("file") or []
            if file_ids and isinstance(file_ids, list) and isinstance(file_ids[0], int):
                # These are post IDs, not media IDs — resolve in second pass
                title = _clean(
                    acf.get("name") or acf.get("brochure_title") or acf.get("careers_title")
                    or post.get("post_title") or ""
                )
                raw_date = (acf.get("published_date") or acf.get("brochure_date")
                            or acf.get("start_date") or post.get("post_date", ""))
                for fid in file_ids:
                    deferred[fid] = (title, _parse_date(raw_date))
            else:
                item = _post_to_item(post, section_label)
                if item:
                    items.append(item)

        total = data.get("total_items", 0)
        if len(items) + len(deferred) >= total or len(posts) < 50:
            break
        page += 1

    # Resolve deferred file IDs via batch fetch
    if deferred:
        file_ids_list = list(deferred.keys())
        resolved = _fetch_posts_by_ids(file_ids_list, section_label)
        # Use the parent post title if the resolved post title is generic
        for item in resolved:
            items.append(item)

    logger.info("[dpiit] %s → %d items", slug, len(items))
    return items


def _fetch_posts_by_ids(ids: list[int], section_label: str) -> list[ScrapedItem]:
    """Batch-fetch post data for a list of IDs and convert to ScrapedItems."""
    if not ids:
        return []
    items: list[ScrapedItem] = []
    # Fetch in chunks of 50 to avoid overly long URLs
    chunk_size = 50
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i:i + chunk_size]
        url = f"{_CMS}/post?id={','.join(str(x) for x in chunk)}"
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=_NET_TIMEOUT, follow_redirects=True)
            data = r.json()
        except Exception as exc:
            logger.warning("[dpiit] batch post fetch failed: %s", exc)
            continue

        posts = data.get("posts", []) if isinstance(data, dict) else []
        # Single post returns a dict, multiple returns a list
        if isinstance(posts, dict):
            posts = [posts]

        for post in posts:
            item = _post_to_item(post, section_label)
            if item:
                items.append(item)

    return items


# ---------------------------------------------------------------------------
# Playwright: intercept post IDs from document section pages
# ---------------------------------------------------------------------------

def _intercept_post_ids(page, path: str) -> list[int]:
    """Navigate to a document section page and collect all post IDs fetched."""
    id_set: list[int] = []
    seen: set[int] = set()

    def on_request(req):
        if req.resource_type in ("xhr", "fetch") and "post?id=" in req.url:
            m = re.search(r"post\?id=([\d,]+)", req.url)
            if m:
                for raw in m.group(1).split(","):
                    try:
                        pid = int(raw.strip())
                        if pid not in seen:
                            seen.add(pid)
                            id_set.append(pid)
                    except ValueError:
                        pass

    page.on("request", on_request)
    try:
        page.goto(f"{_BACKEND}{path}", wait_until="networkidle", timeout=_GOTO_TIMEOUT)
        page.wait_for_timeout(2000)
    except Exception as exc:
        logger.warning("[dpiit] navigation failed for %s: %s", path, exc)
    page.remove_listener("request", on_request)
    return id_set


# ---------------------------------------------------------------------------
# Main sync crawl
# ---------------------------------------------------------------------------

def _sync_crawl() -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright

    all_items: list[ScrapedItem] = []

    # ── Direct API sections (no Playwright needed) ────────────────────────
    for slug, label in [
        ("whats_new",      "What's New"),
        ("tenders_post",   "Tenders"),
        ("brochures_post", "Brochures"),
        ("careers_post",   "Vacancies"),
    ]:
        all_items.extend(_fetch_paginated_api(slug, label))

    # ── Document sections via Playwright ID interception ─────────────────
    doc_sections = [
        ("/documents/gazettes-notifications", "Gazette Notifications"),
        ("/documents/orders-and-notices",     "Orders & Notices"),
        ("/documents/guidelines",             "Guidelines"),
        ("/documents/reports",                "Reports"),
        ("/documents/publications",           "Publications"),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            locale="en-US",
        )
        page = ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())

        for path, label in doc_sections:
            ids = _intercept_post_ids(page, path)
            logger.info("[dpiit] %s → %d post IDs intercepted", label, len(ids))
            section_items = _fetch_posts_by_ids(ids, label)
            logger.info("[dpiit] %s → %d items extracted", label, len(section_items))
            all_items.extend(section_items)

        ctx.close()
        browser.close()

    # Deduplicate by link
    seen: set[str] = set()
    unique: list[ScrapedItem] = []
    for item in all_items:
        if item.link and item.link not in seen:
            seen.add(item.link)
            unique.append(item)

    logger.info("[dpiit] Total unique items: %d", len(unique))
    return unique


async def crawl_dpiit(_config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_crawl)

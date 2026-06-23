"""Ministry of Health and Family Welfare — https://www.mohfw-dohfw.gov.in

The public site is a Next.js SPA on the NIC DigiFootprint platform.
Plain httpx fetches return an empty shell (~7 KB); the actual lists are
loaded client-side from a WordPress REST API at /cms/ on the same host
(not blocked by the Akamai WAF, unlike headless browsers).

This crawler replaces the previous Playwright-based approach with direct
httpx calls to the /cms/ WP REST API. Feeds pulled:
  • post-page/whats_new
  • document/documents?document_category=orders-and-notices
  • document/documents?document_category=circulars
  • document/documents?document_category=press-release
  • document/documents?document_category=guidelines

Link resolution:
  - Press releases / Link-type: acf_data.file[0].external_link (PIB URL)
  - PDF/Doc-type: front-end detail page (<site>/<post_type>/<slug>)
  - whats_new items: <site>/whats-new/<slug>

acf_data.date carries the real formatted date ("08/06/2026") and is used
in preference to post_date.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.mohfw_dohfw")

_SITE_BASE = "https://www.mohfw-dohfw.gov.in"
_CMS_BASE  = f"{_SITE_BASE}/cms"

_FEEDS: list[tuple[str, str]] = [
    ("What's New",       "/wp-json/post-page/whats_new"),
    ("Orders & Notices", "/wp-json/document/documents?document_category=orders-and-notices"),
    ("Circulars",        "/wp-json/document/documents?document_category=circulars"),
    ("Press Releases",   "/wp-json/document/documents?document_category=press-release"),
    ("Guidelines",       "/wp-json/document/documents?document_category=guidelines"),
]

_DOC_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
_DATE_FMTS = ("%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": f"{_SITE_BASE}/whats-new",
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = raw.strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _as_list(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, list):
                return v
    return []


def _find_doc_url(acf: dict) -> str | None:
    """Extract the best document link from acf_data.

    Priority:
      1. external_link inside any file entry (press-release → PIB URL)
      2. Any direct document URL in the acf blob
    Media IDs (integers) are NOT resolvable via the public API.
    """
    for file_entry in acf.get("file") or []:
        if not isinstance(file_entry, dict):
            continue
        ext_link = (file_entry.get("external_link") or "").strip()
        if ext_link.startswith("http"):
            return ext_link
        content = (file_entry.get("content") or "").strip()
        if content.startswith("http"):
            return content

    # Scan entire acf blob for any direct document URL
    for m in re.finditer(r'https?://[^\s"\']+', str(acf)):
        u = m.group()
        if u.lower().split("?")[0].endswith(_DOC_EXTS):
            return u

    return None


def _shape(post: dict) -> ScrapedItem | None:
    acf = post.get("acf_data") or {}

    # acf_data.date ("08/06/2026") is more accurate than post_date
    published_at = _parse_date(acf.get("date")) or _parse_date(post.get("post_date"))

    title = (acf.get("title") or post.get("post_title") or "").strip()
    if not title:
        return None

    slug = post.get("post_slug") or post.get("post_name") or ""
    post_type = (post.get("post_type") or "post").replace("_", "-")
    doc_url = _find_doc_url(acf)

    if doc_url:
        link = doc_url
        is_pdf = doc_url.lower().split("?")[0].endswith(".pdf")
    elif slug:
        link = f"{_SITE_BASE}/{post_type}/{slug}"
        is_pdf = False
    else:
        return None

    return ScrapedItem(
        title=title,
        link=link,
        published_at=published_at,
        is_pdf=is_pdf,
    )


async def crawl_mohfw_dohfw(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(
        headers=_HEADERS,
        verify=False,
        follow_redirects=True,
        timeout=45.0,
    ) as client:
        for label, endpoint in _FEEDS:
            try:
                resp = await client.get(_CMS_BASE + endpoint)
                if resp.status_code != 200:
                    logger.warning("[mohfw_dohfw] %s -> HTTP %s", endpoint, resp.status_code)
                    continue
                posts = _as_list(resp.json())
            except Exception as exc:  # noqa: BLE001
                logger.warning("[mohfw_dohfw] %s failed: %s", endpoint, exc)
                continue

            feed_count = 0
            for post in posts:
                if not isinstance(post, dict):
                    continue
                item = _shape(post)
                if not item or item.link in seen:
                    continue
                seen.add(item.link)
                item.section_label = label
                items.append(item)
                feed_count += 1

            logger.info("[mohfw_dohfw] %s: %d items", label, feed_count)

    logger.info("[mohfw_dohfw] total: %d items", len(items))
    return items

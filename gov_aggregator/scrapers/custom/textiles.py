"""Ministry of Textiles — https://www.texmin.gov.in

The public site is a Next.js SPA sitting behind an Akamai WAF that returns
HTTP 403 to headless browsers, so the old render_js + CSS-selector approach
returned 0 items. The page itself ships no data — the lists are fetched
client-side from a WordPress REST API mounted under the ``/cms/`` path on the
same domain (plain httpx is NOT blocked by the WAF).

This crawler talks to that API directly:

  • /cms/wp-json/post-page/whats_new      — the "What's New" feed (freshest items)
  • /cms/wp-json/custom/api/new-posts?s=  — 20 most recent posts across types

Each WordPress post carries ``post_title`` and ``post_date`` (a real published
date, so no PDF date-extraction is needed). The document link is the direct
file URL embedded in ``acf_data`` when present; otherwise we build the
canonical front-end detail-page URL from the post type and slug.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.textiles")

SITE_BASE = "https://www.texmin.gov.in"
# WordPress backend is served under the /cms/ path prefix on the same host.
CMS_BASE = f"{SITE_BASE}/cms"

# Feeds to pull, in priority order. (section_label, endpoint)
_FEEDS: list[tuple[str, str]] = [
    ("What's New", "/wp-json/post-page/whats_new"),
    ("Latest",     "/wp-json/custom/api/new-posts?s="),
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": f"{SITE_BASE}/whats-new",
}


def _parse_wp_date(raw: str | None) -> datetime | None:
    """Parse WordPress timestamps: '2026-06-04 17:23:39' or '2026-06-04'."""
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _as_list(payload: Any) -> list:
    """The API sometimes wraps the array in a dict — return the first list found."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return value
    return []


def _find_file_url(acf: Any) -> str | None:
    """Return the first direct document URL found anywhere inside acf_data.

    Posts of type 'document'/'scheme' embed a ready-made file URL under
    /static/uploads/...; 'whats_new' posts instead store a bare attachment ID
    (which the public API won't resolve) — those fall back to the page URL.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and node.startswith("http"):
            # Only real documents — NOT scheme thumbnail images (.jpg/.png).
            lo = node.lower().split("?")[0]
            if lo.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")):
                found.append(node)

    walk(acf)
    return found[0] if found else None


def _shape(post: dict) -> ScrapedItem | None:
    title = (post.get("post_title") or "").strip()
    if not title:
        return None

    published_at = _parse_wp_date(post.get("post_date") or post.get("post_date_gmt"))
    file_url = _find_file_url(post.get("acf_data") or {})
    slug = post.get("post_slug") or post.get("post_name") or ""

    if file_url:
        link = file_url
        is_pdf = file_url.lower().endswith(".pdf")
    elif slug:
        # Front-end detail page, e.g. /whats-new/<slug> or /schemes-and-services/<slug>.
        route = (post.get("post_type") or "post").replace("_", "-")
        link = f"{SITE_BASE}/{route}/{slug}"
        is_pdf = False
    else:
        return None

    return ScrapedItem(title=title, link=link, published_at=published_at, is_pdf=is_pdf)


async def crawl_textiles(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        verify=config.verify_ssl,
        follow_redirects=True,
        timeout=45.0,
    ) as client:
        for label, endpoint in _FEEDS:
            try:
                resp = await client.get(CMS_BASE + endpoint)
                if resp.status_code != 200:
                    logger.warning("[textiles] %s -> HTTP %s", endpoint, resp.status_code)
                    continue
                posts = _as_list(resp.json())
            except Exception as exc:  # noqa: BLE001
                logger.warning("[textiles] %s failed: %s", endpoint, exc)
                continue

            for post in posts:
                if not isinstance(post, dict):
                    continue
                item = _shape(post)
                if not item or item.link in seen:
                    continue
                seen.add(item.link)
                item.section_label = label
                items.append(item)

    logger.info("[textiles] extracted %d items across %d feeds", len(items), len(_FEEDS))
    return items

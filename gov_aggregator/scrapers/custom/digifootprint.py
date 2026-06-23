"""DigiFootprint platform crawler — shared by ministries built on the NIC
"DigiFootprint" stack (Next.js SPA + WordPress backend).

Several ministry sites (e.g. Textiles texmin.gov.in, MSME msme.gov.in) run on
this identical platform. The public site is a Next.js SPA behind an Akamai WAF
that returns HTTP 403 to headless browsers, and the page ships no data — the
lists are fetched client-side from a WordPress REST API mounted under the
``/cms/`` path on the same host. Plain httpx is NOT blocked by the WAF, so we
call that API directly.

Feeds pulled (relative to ``<site>/cms``):
  • /wp-json/post-page/whats_new      — the "What's New" feed (freshest items)
  • /wp-json/custom/api/new-posts?s=  — 20 most recent posts across types

Each WordPress post carries ``post_title`` and ``post_date`` (a real published
date, so no PDF date-extraction is needed). The document link is the direct
file URL embedded in ``acf_data`` when present; otherwise we build the
canonical front-end detail-page URL from the post type and slug.

The site host is taken from ``config.base_url`` so a single implementation
serves every ministry on the platform.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.digifootprint")

# Default feeds for any DigiFootprint site not listed below.
_DEFAULT_FEEDS: list[tuple[str, str]] = [
    ("What's New", "/wp-json/post-page/whats_new"),
    ("Latest",     "/wp-json/custom/api/new-posts?s="),
]

# Per-site overrides keyed by site_key.
# MSME: new-posts returns only generic portal pages (noise); use the
# structured document-category endpoints that carry real notifications/MOUs.
_SITE_FEEDS: dict[str, list[tuple[str, str]]] = {
    "ministry-of-micro-small-medium-enterprises": [
        ("What's New",      "/wp-json/post-page/whats_new"),
        ("Orders & Notices","/wp-json/document/documents?document_category=orders-and-notices"),
        ("MOUs",            "/wp-json/document/documents?document_category=guidelines"),
        ("Press Releases",  "/wp-json/document/documents?document_category=press-release"),
    ],
}

_DOC_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")


def _bases(config: SiteConfig) -> tuple[str, str]:
    """Return (site_base, cms_base) derived from the site's configured base_url."""
    site = (config.base_url or "").rstrip("/")
    return site, f"{site}/cms"


def _headers(site_base: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": f"{site_base}/whats-new",
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

    Only real documents — NOT scheme thumbnail images (.jpg/.png). Posts that
    store a bare attachment ID (which the public API won't resolve) fall back
    to the front-end page URL.
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
            if node.lower().split("?")[0].endswith(_DOC_EXTS):
                found.append(node)

    walk(acf)
    return found[0] if found else None


def _shape(post: dict, site_base: str) -> ScrapedItem | None:
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
        link = f"{site_base}/{route}/{slug}"
        is_pdf = False
    else:
        return None

    return ScrapedItem(title=title, link=link, published_at=published_at, is_pdf=is_pdf)


async def crawl_digifootprint(config: SiteConfig) -> list[ScrapedItem]:
    site_base, cms_base = _bases(config)
    if not site_base:
        logger.warning("[digifootprint] %s has no base_url configured", config.site_key)
        return []

    items: list[ScrapedItem] = []
    seen: set[str] = set()

    feeds = _SITE_FEEDS.get(config.site_key, _DEFAULT_FEEDS)

    async with httpx.AsyncClient(
        headers=_headers(site_base),
        verify=config.verify_ssl,
        follow_redirects=True,
        timeout=45.0,
    ) as client:
        for label, endpoint in feeds:
            try:
                resp = await client.get(cms_base + endpoint)
                if resp.status_code != 200:
                    logger.warning("[%s] %s -> HTTP %s", config.site_key, endpoint, resp.status_code)
                    continue
                posts = _as_list(resp.json())
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] %s failed: %s", config.site_key, endpoint, exc)
                continue

            for post in posts:
                if not isinstance(post, dict):
                    continue
                item = _shape(post, site_base)
                if not item or item.link in seen:
                    continue
                seen.add(item.link)
                item.section_label = label
                items.append(item)

    logger.info("[%s] digifootprint extracted %d items", config.site_key, len(items))
    return items

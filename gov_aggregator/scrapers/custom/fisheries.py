"""Custom scraper for Department of Fisheries (dof.gov.in).

The site is a Next.js CSR app backed by a WordPress REST API.
Direct httpx calls to the WP JSON endpoints bypass the Akamai bot
protection that blocks headless Playwright.

Endpoints used:
  POST-PAGE (section lists):
    /cms/wp-json/post-page/whats_new      → What's New announcements
    /cms/wp-json/post-page/tenders_post   → Tenders

  FILE RESOLVER:
    /cms/wp-json/post-page/post?id={id}  → PDF URL + exact date for each item
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_CMS = "https://www.dof.gov.in/cms/wp-json"
_POST_URL = f"{_CMS}/post-page/post"

# Sections to scrape: (endpoint_slug, section_label)
# Items with a parsed date are filtered by the global Jan 2026 cutoff.
# Items where no date is found pass through regardless (WP post_date is used as fallback).
_SECTIONS = [
    ("whats_new",     "What's New"),
    ("tenders_post",  "Tenders"),
    ("press_release", "Press Release"),
    ("circulars",     "Circulars"),
    ("orders",        "Orders"),
    ("office_orders", "Office Orders"),
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.dof.gov.in/",
}

_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_TIMEOUT = httpx.Timeout(30.0)


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d, tzinfo=timezone.utc)
        except ValueError:
            pass
    # Fallback: YYYY-MM-DD HH:MM:SS (post_date format)
    try:
        return datetime.strptime(raw.strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    return None


def _pdf_url_from_resolved(resolved: dict[str, Any]) -> str:
    acf = resolved.get("acf_data", {})
    pdf_obj = acf.get("pdf")
    if isinstance(pdf_obj, dict):
        url = pdf_obj.get("url", "")
        if url:
            return url
    return resolved.get("guid", "")


async def _fetch_json(client: httpx.AsyncClient, url: str, **params: Any) -> Any:
    r = await client.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


async def _resolve_item(
    client: httpx.AsyncClient,
    post: dict[str, Any],
    section_label: str,
) -> ScrapedItem | None:
    title = (post.get("post_title") or "").strip()
    if not title:
        return None

    acf = post.get("acf_data", {})

    # Use explicit ACF dates first; fall back to post_date (WP creation date).
    # Items without any explicit ACF date use post_date so they are still
    # filtered by the global Jan 2026 cutoff.
    published_at = (
        _parse_date(acf.get("published_date"))
        or _parse_date(acf.get("file_date"))
        or _parse_date(post.get("post_date"))
    )

    # guid is always present and links to the WP post — use as fallback link
    guid = post.get("guid", "")
    file_ids: list[int] = acf.get("file", [])
    link = guid
    is_pdf = False

    if file_ids:
        try:
            file_data = await _fetch_json(client, _POST_URL, id=file_ids[0])
            resolved = file_data.get("posts", {})
            resolved_link = _pdf_url_from_resolved(resolved)
            if resolved_link:
                link = resolved_link
                is_pdf = resolved_link.lower().endswith(".pdf")
            # Refine date from resolved file ACF if available
            resolved_acf = resolved.get("acf_data", {})
            d = (
                _parse_date(resolved_acf.get("file_date"))
                or _parse_date(resolved.get("post_date"))
            )
            if d:
                published_at = d
        except Exception as e:
            print(f"[fisheries] file resolve {file_ids[0]} FAILED: {type(e).__name__}: {e}")

    return ScrapedItem(
        title=title,
        link=link,
        published_at=published_at,
        is_pdf=is_pdf,
        section_label=section_label,
    )


async def crawl_fisheries(_config: SiteConfig) -> list[ScrapedItem]:
    # Limit concurrent file-resolution requests to avoid rate-limiting
    sem = asyncio.Semaphore(3)

    async def _resolve_throttled(post: dict[str, Any], label: str) -> ScrapedItem | None:
        async with sem:
            return await _resolve_item(client, post, label)

    async with httpx.AsyncClient(follow_redirects=True, verify=False, timeout=_TIMEOUT) as client:
        # Fetch all section lists concurrently
        section_responses = await asyncio.gather(
            *[_fetch_json(client, f"{_CMS}/post-page/{slug}") for slug, _ in _SECTIONS],
            return_exceptions=True,
        )

        # Build flat list of (post, section_label) pairs
        all_posts: list[tuple[dict[str, Any], str]] = []
        for (slug, label), result in zip(_SECTIONS, section_responses):
            if isinstance(result, Exception):
                print(f"[fisheries] {slug} FAILED: {type(result).__name__}: {result}")
                continue
            posts = result.get("posts", []) if isinstance(result, dict) else []
            print(f"[fisheries] {slug}: {len(posts)} posts")
            for post in posts:
                all_posts.append((post, label))

        # Resolve file IDs with throttling (max 3 concurrent)
        results = await asyncio.gather(
            *[_resolve_throttled(post, label) for post, label in all_posts],
            return_exceptions=True,
        )

    return [r for r in results if isinstance(r, ScrapedItem)]

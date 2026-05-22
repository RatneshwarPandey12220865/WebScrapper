"""Custom scraper for Department of Fisheries (dof.gov.in).

The site is a Next.js CSR app backed by a WordPress REST API at /cms/wp-json.
httpx calls to the WP JSON endpoints bypass the Akamai bot protection that
blocks headless Playwright.

Sections scraped:
  • What's New     — /post-page/whats_new
  • Tenders        — /post-page/tenders_post
  • Documents      — /document/documents?document_category=<slug>  (multiple categories)
  • Press Releases — /document/documents?document_category=press-release

Skipped (as requested):
  • Schemes & Services — post_type: schemes_and_services (static info pages)
  • Resources/Gallery  — post_type: photos_post, videos_post

API structures observed per post type
--------------------------------------
whats_new:
    acf_data = {"type": "PDF", "file": [<int id>]}
    date:  post_date

tenders_post:
    acf_data = {"tender_id": "...", "name": "...",
                "published_date": "DD.MM.YYYY", "file": [<int id>]}
    date:  acf_data.published_date

documents / press-release posts (GET /document/documents?document_category=<slug>):
    Response: {"posts": [{post_type, post_title, post_date, acf_data, documents_category}]}
    Each post's acf_data.file is a LIST of entries — one ScrapedItem per entry:

      type "PDF":  {"type":"PDF", "title":"...", "file":[<int id>], ...}
                   → resolve PDF URL via GET /post-page/post?id=<id>

      type "Link": {"type":"Link", "title":"...", "external_link":"https://...", ...}
                   → use external_link directly (e.g. PIB press releases)

Each PDF file id resolved via: GET /post-page/post?id=<id>
  → response["posts"]["acf_data"]["pdf"]["url"]  or  response["posts"]["guid"]
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

# Headers for /post-page/* and /document/* endpoints (no apikey needed).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,en-IN;q=0.8",
    "Referer": "https://www.dof.gov.in/",
}

# Headers for WP v2 REST API endpoints — apikey required (confirmed via HAR).
_HEADERS_WP = {**_HEADERS, "apikey": "4bW5t13453pa"}

_TIMEOUT = httpx.Timeout(30.0)

# What's New + Tenders use the simple post-page listing endpoints.
_POST_SECTIONS: list[tuple[str, str]] = [
    ("whats_new",    "What's New"),
    ("tenders_post", "Tenders"),
]

# Document categories to scrape via /document/documents?document_category=<slug>.
# Maps category_slug → section_label shown in the UI.
# "press-release" is the WP taxonomy slug observed in the browser HAR response.
_DOC_CATEGORIES: list[tuple[str, str]] = [
    ("press-release", "Press Releases"),
    # Additional categories discovered dynamically at runtime (see _discover_extra_categories).
    # Fallback hardcoded categories are also appended if discovery fails.
]

_FALLBACK_DOC_CATEGORIES: list[tuple[str, str]] = [
    ("reports",       "Documents"),
    ("circulars",     "Documents"),
    ("notifications", "Documents"),
    ("annual-reports","Documents"),
    ("budget",        "Documents"),
    ("guidelines",    "Documents"),
]

_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(str(raw))
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.strptime(str(raw).strip()[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        pass
    return None


async def _fetch_json(
    client: httpx.AsyncClient,
    url: str,
    headers: dict | None = None,
    **params: Any,
) -> Any:
    hdrs = headers if headers is not None else _HEADERS
    r = await client.get(url, params=params, headers=hdrs, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


async def _resolve_pdf_url(client: httpx.AsyncClient, file_id: int) -> str:
    """Resolve a WP attachment ID → direct PDF URL."""
    try:
        data = await _fetch_json(client, _POST_URL, _HEADERS_WP, id=file_id)
        post = data.get("posts") or {}
        if isinstance(post, list):
            post = post[0] if post else {}
        acf = post.get("acf_data") or {}
        pdf_obj = acf.get("pdf")
        if isinstance(pdf_obj, dict):
            url = pdf_obj.get("url", "")
            if url:
                return url
        return post.get("guid", "")
    except Exception:
        return ""


# ── What's New / Tenders resolver ────────────────────────────────────────────

def _extract_simple_file_id(acf: dict) -> int | None:
    """whats_new / tenders_post: acf["file"] = [int_id]."""
    file_val = acf.get("file")
    if not file_val or not isinstance(file_val, list):
        return None
    first = file_val[0]
    return first if isinstance(first, int) else None


async def _resolve_post(
    client: httpx.AsyncClient,
    post: dict,
    section_label: str,
    sem: asyncio.Semaphore,
) -> ScrapedItem | None:
    try:
        title = (post.get("post_title") or "").strip()
        if not title:
            return None

        acf = post.get("acf_data") or {}
        published_at = (
            _parse_date(acf.get("published_date"))
            or _parse_date(acf.get("date"))
            or _parse_date(acf.get("file_date"))
            or _parse_date(post.get("post_date"))
        )

        file_id = _extract_simple_file_id(acf)
        link = ""
        is_pdf = False
        if file_id:
            async with sem:
                link = await _resolve_pdf_url(client, file_id)
            if link:
                is_pdf = link.lower().endswith(".pdf") or "/static/uploads/" in link

        return ScrapedItem(
            title=title, link=link, published_at=published_at,
            is_pdf=is_pdf, section_label=section_label,
        )
    except Exception:
        return None


# ── Document / Press Release file-entry expansion ────────────────────────────

async def _items_from_file_entry(
    client: httpx.AsyncClient,
    entry: dict,
    post_title: str,
    post_date: str | None,
    section_label: str,
    sem: asyncio.Semaphore,
) -> ScrapedItem | None:
    """Convert one acf_data.file[] entry into a ScrapedItem.

    Handles both observed entry shapes:

      type "PDF"  → {"type":"PDF", "title":"...", "file":[<int id>], ...}
                    Resolve file ID → PDF URL via /post-page/post?id=<id>

      type "Link" → {"type":"Link", "title":"...", "external_link":"https://...", ...}
                    Use external_link directly (e.g. PIB press releases).
    """
    try:
        entry_type = (entry.get("type") or "").strip()
        title = (entry.get("title") or post_title).strip()
        if not title:
            return None

        # Date: prefer the entry's own date, fall back to the parent post date
        published_at = _parse_date(entry.get("date")) or _parse_date(post_date)

        # ── Link type (e.g. press releases pointing to PIB) ──────────────
        if entry_type == "Link":
            link = (entry.get("external_link") or "").strip()
            if not link:
                return None
            return ScrapedItem(
                title=title, link=link, published_at=published_at,
                is_pdf=False, section_label=section_label,
            )

        # ── PDF type ─────────────────────────────────────────────────────
        if entry_type == "PDF":
            file_val = entry.get("file") or []
            if not isinstance(file_val, list) or not file_val:
                return None
            file_id = file_val[0] if isinstance(file_val[0], int) else None
            if not file_id:
                return None
            async with sem:
                link = await _resolve_pdf_url(client, file_id)
            if not link:
                return None
            is_pdf = link.lower().endswith(".pdf") or "/static/uploads/" in link
            return ScrapedItem(
                title=title, link=link, published_at=published_at,
                is_pdf=is_pdf, section_label=section_label,
            )

        # ── Unknown / fallback ────────────────────────────────────────────
        # Try external_link first, then file ID
        link = (entry.get("external_link") or "").strip()
        if link:
            return ScrapedItem(
                title=title, link=link, published_at=published_at,
                is_pdf=False, section_label=section_label,
            )
        file_val = entry.get("file") or []
        if isinstance(file_val, list) and file_val and isinstance(file_val[0], int):
            async with sem:
                link = await _resolve_pdf_url(client, file_val[0])
            if link:
                is_pdf = link.lower().endswith(".pdf") or "/static/uploads/" in link
                return ScrapedItem(
                    title=title, link=link, published_at=published_at,
                    is_pdf=is_pdf, section_label=section_label,
                )

        return None
    except Exception:
        return None


async def _items_from_doc_post(
    client: httpx.AsyncClient,
    post: dict,
    section_label: str,
    sem: asyncio.Semaphore,
) -> list[ScrapedItem]:
    """Expand ALL acf_data.file entries of a document post into ScrapedItems."""
    try:
        post_title = (post.get("post_title") or "").strip()
        acf = post.get("acf_data") or {}
        post_date = acf.get("date") or post.get("post_date")
        file_entries = acf.get("file") or []

        if not isinstance(file_entries, list) or not file_entries:
            return []

        results = await asyncio.gather(
            *[
                _items_from_file_entry(client, entry, post_title, post_date, section_label, sem)
                for entry in file_entries
                if isinstance(entry, dict)
            ],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, ScrapedItem)]
    except Exception:
        return []


# ── Category discovery ────────────────────────────────────────────────────────

async def _discover_extra_categories(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    """Discover additional document categories (excluding press-release, already explicit).

    Tries:
      1. WP taxonomy REST API: GET /wp/v2/documents_category?per_page=100
      2. WP child pages:       GET /wp/v2/pages?parent=<documents_page_id>
    Falls back to _FALLBACK_DOC_CATEGORIES.
    """
    try:
        # Approach 1: taxonomy terms
        cats_resp = await _fetch_json(
            client,
            f"{_CMS}/wp/v2/documents_category",
            _HEADERS_WP,
            per_page=100,
        )
        if isinstance(cats_resp, list) and cats_resp:
            discovered = []
            skip = {"press-release", "general_press_release"}
            for term in cats_resp:
                if not isinstance(term, dict):
                    continue
                slug = term.get("slug", "")
                name = term.get("name", slug)
                if slug and slug not in skip:
                    discovered.append((slug, "Documents"))
            if discovered:
                print(f"[fisheries] Discovered {len(discovered)} document categories via taxonomy")
                return discovered
    except Exception as exc:
        print(f"[fisheries] Taxonomy discovery failed: {exc}")

    try:
        # Approach 2: child pages under "documents"
        parent_resp = await _fetch_json(
            client, f"{_CMS}/wp/v2/pages", _HEADERS_WP, slug="documents", _fields="id",
        )
        if isinstance(parent_resp, list) and parent_resp:
            parent_id = parent_resp[0].get("id") if isinstance(parent_resp[0], dict) else None
            if parent_id:
                children = await _fetch_json(
                    client, f"{_CMS}/wp/v2/pages", _HEADERS_WP,
                    parent=parent_id, _fields="slug,title", per_page=100,
                )
                if isinstance(children, list):
                    discovered = [
                        (p["slug"], "Documents")
                        for p in children
                        if isinstance(p, dict) and p.get("slug")
                        and p["slug"] not in {"press-release", "general_press_release"}
                    ]
                    if discovered:
                        print(f"[fisheries] Discovered {len(discovered)} categories via child pages")
                        return discovered
    except Exception as exc:
        print(f"[fisheries] Child-page discovery failed: {exc}")

    print("[fisheries] Using fallback document categories")
    return _FALLBACK_DOC_CATEGORIES


# ── Fetch all posts for one document category ─────────────────────────────────

async def _fetch_category_posts(
    client: httpx.AsyncClient,
    category_slug: str,
    section_label: str,
    sem: asyncio.Semaphore,
) -> list[ScrapedItem]:
    """Fetch all document posts for one category and expand their file entries."""
    all_posts: list[dict] = []

    for page_num in range(1, 11):   # up to 10 pages × 100 = 1000 items
        try:
            result = await _fetch_json(
                client,
                f"{_CMS}/document/documents",
                None,
                document_category=category_slug,
                limit=100,
                page=page_num,
                modified_date="",
            )
        except Exception as exc:
            print(f"[fisheries] {section_label}/{category_slug} page {page_num} failed: {exc}")
            break

        if not isinstance(result, dict):
            break

        posts = result.get("posts", [])
        if isinstance(posts, dict):
            posts = [posts]
        if not isinstance(posts, list) or not posts:
            break

        all_posts.extend(p for p in posts if isinstance(p, dict))
        if len(posts) < 100:
            break

    if not all_posts:
        return []

    print(f"[fisheries] {section_label}/{category_slug}: {len(all_posts)} posts")

    # Expand each post's file array into individual ScrapedItems
    results = await asyncio.gather(
        *[_items_from_doc_post(client, post, section_label, sem) for post in all_posts],
        return_exceptions=True,
    )
    items: list[ScrapedItem] = []
    for r in results:
        if isinstance(r, list):
            items.extend(r)
    return items


# ── main entry point ──────────────────────────────────────────────────────────

async def crawl_fisheries(_config: SiteConfig) -> list[ScrapedItem]:
    sem = asyncio.Semaphore(3)

    async with httpx.AsyncClient(follow_redirects=True) as client:

        # ── What's New + Tenders ──────────────────────────────────────────
        listing_results = await asyncio.gather(
            *[_fetch_json(client, f"{_CMS}/post-page/{slug}") for slug, _ in _POST_SECTIONS],
            return_exceptions=True,
        )

        all_posts: list[tuple[dict, str]] = []
        seen_ids: set[int] = set()

        for (slug, label), result in zip(_POST_SECTIONS, listing_results):
            if isinstance(result, Exception):
                print(f"[fisheries] {label} listing failed: {result}")
                continue
            raw_posts = result.get("posts", []) if isinstance(result, dict) else []
            if isinstance(raw_posts, dict):
                raw_posts = [raw_posts]
            added = 0
            for post in raw_posts:
                if not isinstance(post, dict):
                    continue
                pid = post.get("ID")
                if pid is None or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                all_posts.append((post, label))
                added += 1
            print(f"[fisheries] {label}: {added} posts")

        resolve_results = await asyncio.gather(
            *[_resolve_post(client, post, label, sem) for post, label in all_posts],
            return_exceptions=True,
        )
        items: list[ScrapedItem] = [r for r in resolve_results if isinstance(r, ScrapedItem)]

        # ── Documents + Press Releases ────────────────────────────────────
        # Discover extra doc categories (excluding press-release, already in _DOC_CATEGORIES)
        extra_cats = await _discover_extra_categories(client)
        all_doc_cats = _DOC_CATEGORIES + extra_cats

        doc_results = await asyncio.gather(
            *[
                _fetch_category_posts(client, slug, label, sem)
                for slug, label in all_doc_cats
            ],
            return_exceptions=True,
        )

        seen_links: set[str] = set()
        for result in doc_results:
            if not isinstance(result, list):
                continue
            for item in result:
                key = item.link or item.title
                if key and key not in seen_links:
                    seen_links.add(key)
                    items.append(item)

    print(f"[fisheries] done — {len(items)} items scraped")
    return items

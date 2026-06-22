from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.power")

_CMS = "https://www.powermin.gov.in/cms/wp-json"
_WHATS_NEW_API = f"{_CMS}/post-page/whats_new"
_PRESS_API = f"{_CMS}/document/documents"
_POST_API = f"{_CMS}/post-page/post"
_MAX_PAGES = 10
_CONCURRENCY = 10  # parallel file-detail fetches

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _parse_date(raw: str | None) -> datetime | None:
    """Parse DD/MM/YYYY or YYYY-MM-DD HH:MM:SS."""
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip()[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def _fetch_file_detail(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    file_id: int,
) -> dict:
    """Return acf_data dict for a central_documents post, or {} on failure."""
    async with sem:
        try:
            resp = await client.get(_POST_API, params={"id": file_id})
            resp.raise_for_status()
            return resp.json().get("posts", {}).get("acf_data", {})
        except Exception as exc:
            logger.warning("[power] file detail %d failed: %s", file_id, exc)
            return {}


def _extract_file_id_whats_new(acf: dict) -> int | None:
    """acf_data.file = [int]"""
    f = acf.get("file")
    if isinstance(f, list) and f and isinstance(f[0], int):
        return f[0]
    return None


def _extract_file_id_press(acf: dict) -> int | None:
    """acf_data.file = [{"file": [int], ...}]"""
    f = acf.get("file")
    if isinstance(f, list) and f and isinstance(f[0], dict):
        inner = f[0].get("file")
        if isinstance(inner, list) and inner and isinstance(inner[0], int):
            return inner[0]
    return None


async def crawl_power(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_HEADERS,
        timeout=45,
    ) as client:

        # ── What's New ──────────────────────────────────────────────────────
        wn_posts: list[dict] = []
        try:
            resp = await client.get(_WHATS_NEW_API)
            resp.raise_for_status()
            wn_posts = resp.json().get("posts", [])
            logger.info("[power] What's New list: %d posts", len(wn_posts))
        except Exception as exc:
            logger.warning("[power] What's New list failed: %s", exc)

        # ── Press Releases (paginated) ───────────────────────────────────────
        pr_posts: list[dict] = []
        try:
            for page_n in range(1, _MAX_PAGES + 1):
                resp = await client.get(
                    _PRESS_API,
                    params={
                        "document_category": "press-release",
                        "limit": 10,
                        "page": page_n,
                        "sort": "acf",
                        "order": "DESC",
                        "search": "",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                page_posts = data.get("posts", [])
                pr_posts.extend(page_posts)
                logger.info("[power] Press Releases page %d: %d posts", page_n, len(page_posts))
                if page_n >= data.get("total_pages", 1):
                    break
        except Exception as exc:
            logger.warning("[power] Press Releases fetch failed: %s", exc)

        # ── Collect file IDs and batch-fetch details ─────────────────────────
        # Build (post, file_id, section_label) triples
        pending: list[tuple[dict, int, str]] = []
        for post in wn_posts:
            fid = _extract_file_id_whats_new(post.get("acf_data") or {})
            if fid:
                pending.append((post, fid, "What's New"))
        for post in pr_posts:
            fid = _extract_file_id_press(post.get("acf_data") or {})
            if fid:
                pending.append((post, fid, "Press Releases"))

        if not pending:
            logger.warning("[power] no file IDs found — returning 0 items")
            return []

        # Parallel fetch all file details
        file_details: list[dict] = await asyncio.gather(
            *[_fetch_file_detail(client, sem, fid) for _, fid, _ in pending]
        )

        # ── Build ScrapedItems ───────────────────────────────────────────────
        seen: set[str] = set()
        for (post, _fid, section_label), detail in zip(pending, file_details):
            # Title: prefer the file's own acf title, fall back to post_title
            title = (detail.get("title") or post.get("post_title") or "").strip()
            title = " ".join(title.split())
            if not title:
                continue

            # Date: prefer file_date (DD/MM/YYYY), fall back to post_date
            published_at = _parse_date(detail.get("file_date") or detail.get("date")) or \
                           _parse_date(post.get("post_date"))

            # PDF URL
            pdf_info = detail.get("pdf") or {}
            link = (pdf_info.get("url") or "").strip()
            if not link:
                continue
            if link in seen:
                continue
            seen.add(link)

            all_items.append(ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                is_pdf=link.lower().endswith(".pdf"),
                section_label=section_label,
            ))

    logger.info("[power] total: %d items", len(all_items))
    return all_items

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.doppw")

_BASE_API = "https://www.doppw.gov.in/cms/wp-json/post-page"
_WHATS_NEW_URL = f"{_BASE_API}/whats_new"
_POST_URL = f"{_BASE_API}/post"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CONCURRENCY = 6
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.doppw.gov.in/",
}


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def _fetch_pdf_url(client: httpx.AsyncClient, sem: asyncio.Semaphore, file_id: int | str, title: str, post_date: str | None) -> ScrapedItem | None:
    async with sem:
        try:
            resp = await client.get(_POST_URL, params={"id": file_id}, timeout=20)
            resp.raise_for_status()
            detail = resp.json()
            acf = (detail.get("posts") or {}).get("acf_data", {})
            pdf_url = (acf.get("pdf_both") or {}).get("url", "")
            if not pdf_url:
                return None
            # prefer file_date from detail over post_date from listing
            published_at = _parse_date(acf.get("file_date")) or _parse_date(post_date)
            if published_at and published_at < _MIN_DATE:
                return None
            return ScrapedItem(
                title=" ".join((title or "").split()),
                link=pdf_url,
                published_at=published_at,
                is_pdf=pdf_url.lower().endswith(".pdf"),
                section_label="What's New",
            )
        except Exception as exc:
            logger.warning("[doppw] detail fetch failed for id=%s: %s", file_id, exc)
            return None


async def crawl_doppw(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        try:
            resp = await client.get(_WHATS_NEW_URL)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("[doppw] main API fetch failed: %s", exc)
            return []

        posts = data.get("posts") or []
        logger.info("[doppw] %d posts found", len(posts))

        sem = asyncio.Semaphore(_CONCURRENCY)
        tasks = []
        for post in posts:
            file_id = (post.get("acf_data") or {}).get("file", [None])[0] if (post.get("acf_data") or {}).get("file") else None
            if not file_id:
                continue
            title = post.get("post_title", "")
            post_date = post.get("post_date") or post.get("post_modified")
            tasks.append(_fetch_pdf_url(client, sem, file_id, title, post_date))

        results = await asyncio.gather(*tasks)

    items = [r for r in results if r is not None]
    logger.info("[doppw] total: %d items", len(items))
    return items

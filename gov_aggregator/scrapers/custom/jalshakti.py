from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.jalshakti")

_BASE = "https://www.jalshakti-dowr.gov.in"
_CMS = f"{_BASE}/cms/wp-json/post-page"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MIN_DATE_SCHEMES = datetime(2020, 1, 1, tzinfo=timezone.utc)  # schemes are evergreen; use wide window
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.jalshakti-dowr.gov.in/",
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def _fetch_whats_new(client: httpx.AsyncClient) -> list[ScrapedItem]:
    try:
        resp = await client.get(f"{_CMS}/whats_new")
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[jalshakti] whats_new fetch failed: %s", exc)
        return []

    data = resp.json()
    posts = data.get("posts", []) if isinstance(data, dict) else data
    logger.info("[jalshakti] whats_new: %d posts received", len(posts))

    items: list[ScrapedItem] = []
    for post in posts:
        published_at = _parse_date(post.get("post_date"))
        if published_at and published_at < _MIN_DATE:
            continue

        acf = post.get("acf_data") or {}
        title = (acf.get("title") or post.get("post_title") or "").strip()
        if not title:
            continue

        link = ""
        is_pdf = False
        content_type = (acf.get("type") or "").upper()

        if content_type == "PDF":
            pdf = acf.get("pdf") or {}
            link = (pdf.get("url") or "").strip()
            is_pdf = True
        else:
            link = (acf.get("link") or acf.get("url") or "").strip()

        if not link:
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label="What's New",
        ))

    return items


async def _fetch_documents(client: httpx.AsyncClient) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    page = 1

    while True:
        try:
            resp = await client.get(
                f"{_CMS}/documents",
                params={"limit": "20", "page": str(page)},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[jalshakti] documents fetch failed page=%d: %s", page, exc)
            break

        data = resp.json()
        posts = data.get("posts", [])
        total_pages = data.get("total_pages", 1)
        stop_early = False

        for post in posts:
            published_at = _parse_date(post.get("post_date"))
            if published_at and published_at < _MIN_DATE:
                stop_early = True
                continue

            acf = post.get("acf_data") or {}
            title = (acf.get("title") or post.get("post_title") or "").strip()
            if not title:
                continue

            doc_type = (acf.get("select_documents_type") or "Documents").strip()

            # acf_data.file is a list; each entry may have pdf.url or external_link
            for file_entry in acf.get("file") or []:
                link = ""
                is_pdf = False

                if (file_entry.get("type") or "").upper() == "PDF":
                    pdf = file_entry.get("pdf") or {}
                    link = (pdf.get("url") or "").strip()
                    is_pdf = True
                else:
                    link = (file_entry.get("external_link") or "").strip()

                if not link:
                    continue

                items.append(ScrapedItem(
                    title=title,
                    link=link,
                    published_at=published_at,
                    is_pdf=is_pdf,
                    section_label=doc_type,
                ))
                break  # one link per document post is enough

        logger.info("[jalshakti] documents page=%d: %d posts", page, len(posts))

        if stop_early or page >= total_pages:
            break
        page += 1

    return items


async def _fetch_schemes(client: httpx.AsyncClient) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    page = 1

    while True:
        try:
            resp = await client.get(
                f"{_CMS}/schemes_and_services",
                params={"limit": "20", "page": str(page), "orderby": "menu_order"},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[jalshakti] schemes fetch failed page=%d: %s", page, exc)
            break

        data = resp.json()
        posts = data.get("posts", [])
        total_pages = data.get("total_pages", 1)

        for post in posts:
            acf = post.get("acf_data") or {}
            title = (acf.get("scheme_title") or acf.get("title") or post.get("post_title") or "").strip()
            if not title:
                continue

            # Prefer scheme portal URL; fall back to external link
            link = (acf.get("android_url") or acf.get("ios_url") or acf.get("url") or "").strip()
            if not link:
                continue

            category = (acf.get("category") or "Schemes & Services").strip()
            published_at = _parse_date(post.get("post_date"))

            items.append(ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                is_pdf=False,
                section_label=category,
            ))

        logger.info("[jalshakti] schemes page=%d: %d posts", page, len(posts))

        if page >= total_pages:
            break
        page += 1

    return items


async def crawl_jalshakti(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        whats_new, documents, schemes = await asyncio.gather(
            _fetch_whats_new(client),
            _fetch_documents(client),
            _fetch_schemes(client),
        )

    seen: set[str] = set()
    all_items: list[ScrapedItem] = []
    for item in whats_new + documents + schemes:
        if item.link not in seen:
            seen.add(item.link)
            all_items.append(item)

    logger.info("[jalshakti] total combined: %d items", len(all_items))
    return all_items

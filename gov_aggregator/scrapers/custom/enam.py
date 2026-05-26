from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.enam")

_HOME_URL = "https://enam.gov.in/web/"
_ARCHIVE_URL = "https://enam.gov.in/web/all_news_desc"
_BASE = "https://enam.gov.in"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://enam.gov.in/",
}


def _make_link(href: str) -> str:
    href = href.strip()
    return href if href.startswith("http") else urljoin(_BASE, href)


def _parse_archive(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    lis = soup.select(".content-9 .b-line li")
    logger.info("[enam] archive: %d list items found", len(lis))

    items: list[ScrapedItem] = []
    for li in lis:
        a = li.select_one("a[href]")
        if not a:
            continue
        link = _make_link(a.get("href") or "")

        strong = li.select_one("p strong")
        title = " ".join(strong.get_text().split()) if strong else ""

        clean = title.lower().strip("- .")
        if clean in ("click here", ""):
            full = " ".join(li.get_text().split())
            for suffix in (" Click Here", " click here", "Click Here"):
                full = full.rstrip(suffix).strip()
            title = full

        if not title or title.lower().strip("- .") in ("click here",):
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=None,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="News Archive",
        ))

    return items


def _parse_modal(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    modal_body = soup.select_one("#myModalbankdetail .modal-body")
    if not modal_body:
        logger.warning("[enam] modal body not found")
        return []

    items: list[ScrapedItem] = []
    current_section = "Announcements"

    for el in modal_body.descendants:
        if not isinstance(el, Tag):
            continue

        # Update current section when we hit a green bold header
        if el.name == "b" and (el.get("style") or "").find("green") != -1:
            header = " ".join(el.get_text().split()).rstrip(":").strip()
            if header:
                current_section = header

        # Collect linked items
        if el.name == "a" and el.get("href"):
            link = _make_link(el["href"])
            # Build title from the nearest enclosing <p> or <b> sibling text
            parent = el.parent
            title = " ".join(parent.get_text().split()) if parent else ""
            # Strip "Click Here" / "click here" / "यहां क्लिक करें" link text from title
            link_text = " ".join(el.get_text().split())
            if title.endswith(link_text):
                title = title[: -len(link_text)].strip(" -–")
            if not title:
                title = link_text
            if not title:
                continue

            items.append(ScrapedItem(
                title=title,
                link=link,
                published_at=None,
                is_pdf=link.lower().endswith(".pdf"),
                section_label=current_section,
            ))

    logger.info("[enam] modal: %d items found", len(items))
    return items


async def crawl_enam(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        try:
            archive_resp, home_resp = await asyncio.gather(
                client.get(_ARCHIVE_URL),
                client.get(_HOME_URL),
            )
            archive_resp.raise_for_status()
            home_resp.raise_for_status()
        except Exception as exc:
            logger.warning("[enam] fetch failed: %s", exc)
            return []

    archive_items = _parse_archive(archive_resp.text)
    modal_items = _parse_modal(home_resp.text)

    seen: set[str] = set()
    all_items: list[ScrapedItem] = []
    for item in archive_items + modal_items:
        if item.link not in seen:
            seen.add(item.link)
            all_items.append(item)

    logger.info("[enam] total combined: %d items", len(all_items))
    return all_items

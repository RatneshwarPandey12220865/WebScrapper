from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.sfac")

_URL = "https://sfacindia.com/View_What's_New.aspx"
_BASE = "https://sfacindia.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://sfacindia.com/",
}


async def crawl_sfac(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[sfac] fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items: list[ScrapedItem] = []

    for box in soup.select(".box"):
        h4 = box.select_one("h4")
        section = h4.get_text(strip=True) if h4 else "General"
        # Strip trailing "more.." / "More.." from section header
        for suffix in ("more..", "More..", "more...", "More..."):
            if section.endswith(suffix):
                section = section[: -len(suffix)].strip()

        for li in box.select(".block-contant li"):
            a = li.select_one("a[href]")
            if not a:
                continue
            href = (a.get("href") or "").strip()
            if not href:
                continue
            link = href if href.startswith("http") else urljoin(_BASE, href)

            title = " ".join(a.get_text().split())
            if not title:
                continue

            items.append(ScrapedItem(
                title=title,
                link=link,
                published_at=None,
                is_pdf=link.lower().endswith(".pdf"),
                section_label=section,
            ))

    logger.info("[sfac] total: %d items", len(items))
    return items

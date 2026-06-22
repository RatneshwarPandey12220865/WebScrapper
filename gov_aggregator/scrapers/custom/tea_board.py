from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.tea_board")

_URL = "https://www.teaboard.gov.in/LATEST-NEWS"
_BASE = "https://www.teaboard.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.teaboard.gov.in/",
}


async def crawl_tea_board(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[tea_board] fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Locate the news table — try current selector first, fall back to broad search
    table = (
        soup.select_one("#maincontent table")
        or soup.select_one("#contn_contnaar table")
        or soup.select_one("table")
    )
    if not table:
        logger.warning("[tea_board] news table not found")
        return []

    rows = table.select("tr")
    logger.info("[tea_board] %d rows found", len(rows))

    items: list[ScrapedItem] = []
    for row in rows:
        cells = row.select("td")
        if not cells:
            continue  # skip header rows

        # ── Title: first <td>, strip date metadata ────────────────────────
        title_td = cells[0]

        # Date may be in a .divCss span or just inline text matching DD/MM/YYYY
        date_node = title_td.select_one(".divCss, span")
        raw_date = date_node.get_text() if date_node else ""
        published_at = _parse_date(raw_date)
        if published_at and published_at < _MIN_DATE:
            continue

        if date_node:
            date_node.decompose()

        raw_title = title_td.get_text(separator=" ")
        # Strip "Category:- " style prefix
        if ":-" in raw_title:
            section_part, _, title_part = raw_title.partition(":-")
            section = section_part.strip()
            title   = title_part.strip()
        else:
            section = "Latest News"
            title   = raw_title.strip()
        title = " ".join(title.split())

        if not title:
            continue

        # ── Link: prefer second <td>'s <a>, fall back to any <a> in row ──
        href = ""
        if len(cells) > 1:
            a = cells[1].select_one("a[href]")
            if a:
                href = (a.get("href") or "").strip()
        if not href:
            a = row.select_one("a[href]")
            href = (a.get("href") or "").strip() if a else ""

        if not href:
            continue

        link = href if href.startswith("http") else urljoin(_BASE, href)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf") or "/pdf/" in link.lower(),
            section_label=section,
        ))

    logger.info("[tea_board] total: %d items", len(items))
    return items

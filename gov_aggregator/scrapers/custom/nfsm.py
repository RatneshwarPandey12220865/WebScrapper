from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.date_utils import parse_date as _parse_date
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.nfsm")

_BASE = "https://www.nfsm.gov.in"
_HOME_URL = f"{_BASE}/"
_CIRCULARS_URL = f"{_BASE}/Circulars.aspx"
_ADMIN_APPROVALS_URL = f"{_BASE}/Administrativeapprovals.aspx"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _parse_home_news(html: str) -> list[ScrapedItem]:
    """Extract news items from the homepage GridView."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "ctl00_ContentPlaceHolder1_GridView1"})
    if not table:
        return []

    items: list[ScrapedItem] = []
    for span in table.find_all("span", id=re.compile(r"lblSubject")):
        text = " ".join(span.get_text().split())
        if not text:
            continue

        row = span.find_parent("tr")
        a = row.find("a", href=True) if row else None
        link = urljoin(_BASE, a["href"]) if a else _HOME_URL
        is_pdf = link.lower().endswith(".pdf")

        items.append(ScrapedItem(
            title=text,
            link=link,
            is_pdf=is_pdf,
            section_label="News",
        ))

    return items


def _parse_table(html: str, page_url: str, section_label: str) -> list[ScrapedItem]:
    """
    Parse the default GridView table on Circulars.aspx / Administrativeapprovals.aspx.
    Columns: SL No | Scheme | Subject | Date | Download
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "ctl00_ContentPlaceHolder1_GridView1"})
    if not table:
        return []

    items: list[ScrapedItem] = []
    rows = table.find_all("tr")[1:]  # skip header

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        subject_cell = cells[2]
        subject = " ".join(subject_cell.get_text().split())
        if not subject:
            continue

        date_raw = " ".join(cells[3].get_text().split())
        published_at = _parse_date(date_raw)

        # Try to find a direct link; ImageButton src is just the icon image,
        # check onclick for an embedded PDF path first.
        link = page_url
        is_pdf = False

        if len(cells) > 4:
            a = cells[4].find("a", href=True)
            if a:
                link = urljoin(_BASE, a["href"])
                is_pdf = link.lower().endswith(".pdf")
            else:
                input_tag = cells[4].find("input")
                if input_tag:
                    onclick = input_tag.get("onclick", "")
                    url_match = re.search(r"['\"]([^'\"]+\.pdf)['\"]", onclick, re.I)
                    if url_match:
                        link = urljoin(_BASE, url_match.group(1))
                        is_pdf = True

        items.append(ScrapedItem(
            title=subject,
            link=link,
            summary=date_raw or None,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label=section_label,
        ))

    logger.info("[nfsm] %s → %d items", section_label, len(items))
    return items


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, headers=_HEADERS, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return resp.text


async def crawl_nfsm(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(verify=False) as client:
        all_items: list[ScrapedItem] = []
        seen: set[str] = set()

        sections = [
            (_HOME_URL, _parse_home_news),
            (_CIRCULARS_URL, lambda h: _parse_table(h, _CIRCULARS_URL, "Circulars")),
            (_ADMIN_APPROVALS_URL, lambda h: _parse_table(h, _ADMIN_APPROVALS_URL, "Administrative Approvals")),
        ]

        for url, parser in sections:
            try:
                html = await _fetch(client, url)
                for item in parser(html):
                    key = f"{item.title}|{item.section_label}"
                    if key not in seen:
                        seen.add(key)
                        all_items.append(item)
            except Exception as exc:
                logger.error("[nfsm] Failed to load %s: %s", url, exc)

        return all_items

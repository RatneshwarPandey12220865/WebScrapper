from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.nfsm")

_BASE = "https://www.nfsm.gov.in"
_CIRCULARS_URL = f"{_BASE}/Circulars.aspx"
_ADMIN_APPROVALS_URL = f"{_BASE}/Administrativeapprovals.aspx"
_YEARS = ["2025-2026", "2024-2025"]

_DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _parse_date(raw: str) -> datetime | None:
    m = _DATE_RE.search(raw.strip())
    if not m:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(m.group(1), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_viewstate(html: str) -> dict[str, str]:
    """Pull ASP.NET hidden fields needed for POST."""
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        tag = soup.find("input", {"name": name})
        if tag:
            fields[name] = tag.get("value", "")
    return fields


def _parse_table(html: str, page_url: str, section_label: str) -> list[ScrapedItem]:
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

        # Try to get a PDF link from a hidden field name like
        # ctl00$ContentPlaceHolder1$GridView1$ctl02$ImageButton1
        # We construct a best-effort download URL; fall back to page URL.
        input_tag = cells[4].find("input") if len(cells) > 4 else None
        link = page_url  # fallback
        is_pdf = False

        if input_tag:
            btn_name = input_tag.get("name", "")
            # Row index is encoded in the button name (e.g., ctl02 = row 2)
            row_match = re.search(r"ctl(\d+)\$ImageButton", btn_name)
            if row_match:
                # The PDF filename is sometimes in a hidden span/label nearby
                span = subject_cell.find("span")
                if span and span.get("id"):
                    # No direct href — keep page_url but mark is_pdf hint from section
                    pass

        items.append(ScrapedItem(
            title=subject,
            link=link,
            summary=date_raw or None,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label=section_label,
        ))

    return items


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, headers=_HEADERS, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return resp.text


async def _fetch_year(
    client: httpx.AsyncClient,
    url: str,
    year: str,
    initial_html: str,
    section_label: str,
) -> list[ScrapedItem]:
    fields = _extract_viewstate(initial_html)
    if not fields:
        logger.warning("[nfsm] No VIEWSTATE found for %s", url)
        return []

    form_data = {
        **fields,
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$DdlYear",
        "__EVENTARGUMENT": "",
        "ctl00$ContentPlaceHolder1$DdlYear": year,
        "ctl00$ContentPlaceHolder1$DdlScheme": "0",
    }

    resp = await client.post(
        url,
        data=form_data,
        headers={
            **_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url,
        },
        follow_redirects=True,
        timeout=30,
    )
    resp.raise_for_status()
    items = _parse_table(resp.text, url, section_label)
    logger.info("[nfsm] %s / %s → %d items", section_label, year, len(items))
    return items


async def crawl_nfsm(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(verify=False) as client:
        sections = [
            (_CIRCULARS_URL, "Circulars"),
            (_ADMIN_APPROVALS_URL, "Administrative Approvals"),
        ]

        all_items: list[ScrapedItem] = []
        seen_titles: set[str] = set()

        for url, label in sections:
            try:
                initial_html = await _fetch_page(client, url)
            except Exception as exc:
                logger.error("[nfsm] Failed to load %s: %s", url, exc)
                continue

            for year in _YEARS:
                try:
                    items = await _fetch_year(client, url, year, initial_html, label)
                    for item in items:
                        key = f"{item.title}|{item.section_label}"
                        if key not in seen_titles:
                            seen_titles.add(key)
                            all_items.append(item)
                except Exception as exc:
                    logger.error("[nfsm] Error fetching %s year %s: %s", label, year, exc)

        return all_items

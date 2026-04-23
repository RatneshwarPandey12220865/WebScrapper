from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    cleaned = raw.strip()
    match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", cleaned)
    if match:
        try:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


async def crawl_asi(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=DEFAULT_HEADERS, timeout=60) as client:
        # Section 1: What's New
        whats_new_base = "https://asi.nic.in/HQ/whatsnew/"
        for page in range(71):
            url = f"{whats_new_base}?p={page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.select_one("table.tabel-border")
            if not table:
                break
            
            rows = table.select("tbody tr")
            if not rows:
                break
            
            for row in rows:
                tds = row.find_all("td")
                if len(tds) < 2:
                    continue
                
                title_cell = tds[1]
                link_tag = title_cell.select_one("a[href]")
                if not link_tag:
                    continue
                
                title = _clean_text(link_tag.get_text())
                if not title:
                    continue
                
                # Date is in a nested td within the title cell (malformed HTML)
                # or use the last td if available
                date_text = ""
                if len(tds) >= 3:
                    date_text = _clean_text(tds[2].get_text())
                else:
                    # Try to find nested td
                    nested_td = title_cell.select_one("td")
                    if nested_td:
                        date_text = _clean_text(nested_td.get_text())
                
                # Clean title - remove date suffix if present
                title = re.sub(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}\s*$", "", title).strip()
                
                href = link_tag.get("href", "")
                link = urljoin("https://asi.nic.in", href)
                is_pdf = href.lower().endswith(".pdf") or "/download" in href
                
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        published_at=_parse_date(date_text) if date_text else None,
                        is_pdf=is_pdf,
                        section_label="What's New",
                    )
                )

        # Section 2: Circulars
        circulars_base = "https://asi.nic.in/HQ/circulars/"
        for page in range(14):
            url = f"{circulars_base}?p={page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.select_one("table.tabel-border")
            if not table:
                break
            
            rows = table.select("tbody tr")
            if not rows:
                break
            
            for row in rows:
                tds = row.find_all("td")
                if len(tds) < 2:
                    continue
                
                title_cell = tds[1]
                link_tag = title_cell.select_one("a[href]")
                if not link_tag:
                    continue
                
                title = _clean_text(link_tag.get_text())
                if not title:
                    continue
                
                date_text = ""
                if len(tds) >= 3:
                    date_text = _clean_text(tds[2].get_text())
                
                # Clean title
                title = re.sub(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}\s*$", "", title).strip()
                
                href = link_tag.get("href", "")
                link = urljoin("https://asi.nic.in", href)
                is_pdf = href.lower().endswith(".pdf") or "/download" in href
                
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        published_at=_parse_date(date_text) if date_text else None,
                        is_pdf=is_pdf,
                        section_label="Circulars",
                    )
                )

    return items

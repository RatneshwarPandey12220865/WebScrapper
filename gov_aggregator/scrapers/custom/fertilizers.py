from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://fert.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _clean(v: str | None) -> str:
    return " ".join((v or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # Prefer ISO datetime attr on <time> tag: "2025-10-29T12:00:00Z"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_table(html: str, section_label: str, base_url: str = _BASE) -> list[ScrapedItem]:
    """
    Parse fert.gov.in table rows, skipping any row whose Language column
    is not 'English'.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.cols-8")
    if not table:
        return []

    items: list[ScrapedItem] = []

    for row in table.select("tbody tr"):
        # ── Language filter ────────────────────────────────────────────────
        lang_td = row.select_one("td.views-field-field-language")
        if lang_td:
            lang = _clean(lang_td.get_text()).lower()
            if lang and lang != "english":
                continue          # skip Hindi and any other non-English rows

        # ── Title ──────────────────────────────────────────────────────────
        title_td = row.select_one("td.views-field-title")
        if not title_td:
            continue
        title = _clean(title_td.get_text())
        if not title:
            continue

        # ── Link ───────────────────────────────────────────────────────────
        link_tag = row.select_one("td.views-field-nothing a.table_view_btn")
        if not link_tag:
            continue
        href = link_tag.get("href", "").strip()
        if not href:
            continue
        link = urljoin(base_url, href)

        # ── Date ───────────────────────────────────────────────────────────
        time_tag = row.select_one("td.views-field-field-date time")
        raw_date = (time_tag.get("datetime") or time_tag.get_text()) if time_tag else ""
        published_at = _parse_date(raw_date)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=True,
            section_label=section_label,
        ))

    return items


async def crawl_fertilizers(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
    ) as client:
        resp = await client.get("https://fert.gov.in/what-s-new")
        if resp.status_code != 200:
            return []

    return _parse_table(resp.text, "What's New")

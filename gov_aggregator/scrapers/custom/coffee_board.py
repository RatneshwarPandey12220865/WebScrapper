from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.coffee_board")

_NEWS_URL = "https://coffeeboard.gov.in/News.aspx"
_BASE_URL = "https://coffeeboard.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)'")
_CONCURRENCY = 5

# ASP.NET hidden form fields required for postback
_ASPNET_FIELDS = (
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
    "__SCROLLPOSITIONX",
    "__SCROLLPOSITIONY",
)


def _clean(v: str | None) -> str:
    return " ".join((v or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _extract_form_values(soup: BeautifulSoup) -> dict[str, str]:
    """Pull ASP.NET hidden field values needed to submit a valid postback."""
    values: dict[str, str] = {}
    for field in _ASPNET_FIELDS:
        tag = soup.find("input", {"name": field})
        if tag:
            values[field] = tag.get("value", "")
    return values


def _parse_datalist_raw(soup: BeautifulSoup, list_id: str, section_label: str) -> list[dict]:
    """
    Extract raw item data from DataList1 or DataList2.

    Each row structure:
      <span id="{list_id}_Label1_N">DD/MM/YYYY</span>      ← date
      <a class="arch" href="javascript:__doPostBack(...)">Title</a>   ← title + postback target
    """
    container = soup.select_one(f"#{list_id}")
    if not container:
        logger.warning("[coffee_board] Container #%s not found", list_id)
        return []

    raw_items: list[dict] = []
    for row in container.select("tr"):
        date_span = row.select_one(f'span[id^="{list_id}_Label1_"]')
        published_at = _parse_date(_clean(date_span.get_text()) if date_span else "")

        if published_at and published_at < _MIN_DATE:
            continue

        link_tag = row.select_one("a.arch")
        if not link_tag:
            continue
        title = _clean(link_tag.get_text())
        if not title:
            continue

        # e.g. href="javascript:__doPostBack('DataList1$ctl00$LinkButton1','')"
        href = link_tag.get("href", "")
        m = _POSTBACK_RE.search(href)
        event_target = m.group(1) if m else ""

        raw_items.append({
            "title": title,
            "published_at": published_at,
            "event_target": event_target,
            "section_label": section_label,
        })

    return raw_items


async def _resolve_url(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    event_target: str,
    form_values: dict[str, str],
) -> str:
    """
    POST the ASP.NET form with the given event_target to get the real document URL.

    ASP.NET LinkButton postbacks typically respond with a 302 redirect to the
    actual document (PDF or detail page).  We capture that Location header.
    If the server returns 200 instead (e.g. renders inline), fall back to the
    news page URL so the item still has a usable link.
    """
    if not event_target:
        return _NEWS_URL

    post_data: dict[str, str] = {
        **form_values,
        "__EVENTTARGET": event_target,
        "__EVENTARGUMENT": "",
    }

    async with semaphore:
        try:
            resp = await client.post(
                _NEWS_URL,
                data=post_data,
                follow_redirects=False,
                timeout=20,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location", "").strip()
                if location:
                    resolved = urljoin(_BASE_URL, location)
                    logger.debug("[coffee_board] %s → %s", event_target, resolved)
                    return resolved
        except Exception as exc:
            logger.debug("[coffee_board] postback resolution failed for %s: %s", event_target, exc)

    return _NEWS_URL


async def crawl_coffee_board(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=60,
    ) as client:
        resp = await client.get(_NEWS_URL)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        form_values = _extract_form_values(soup)
        logger.info("[coffee_board] Loaded News.aspx, form fields found: %s", list(form_values))

        # DataList1 = "News", DataList2 = "Coffee News" (largely the same items)
        raw_items = _parse_datalist_raw(soup, "DataList1", "News")
        raw_items += _parse_datalist_raw(soup, "DataList2", "Coffee News")
        logger.info("[coffee_board] Raw items before URL resolution: %d", len(raw_items))

        if not raw_items:
            return []

        # Resolve real document URLs concurrently via ASP.NET postback
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        urls = await asyncio.gather(*[
            _resolve_url(client, semaphore, item["event_target"], form_values)
            for item in raw_items
        ])

        # Build ScrapedItems, dedup by (title, date) since DataList1/2 overlap
        seen: set[tuple[str, str]] = set()
        items: list[ScrapedItem] = []
        for raw, url in zip(raw_items, urls):
            key = (raw["title"], str(raw["published_at"]))
            if key in seen:
                continue
            seen.add(key)
            items.append(ScrapedItem(
                title=raw["title"],
                link=url,
                published_at=raw["published_at"],
                is_pdf=url.lower().endswith(".pdf"),
                section_label=raw["section_label"],
            ))

        logger.info("[coffee_board] Final unique items: %d", len(items))
        return items

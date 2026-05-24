from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", cleaned)
    if match:
        try:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


_DATE_CELL_RE = re.compile(r"^\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s*$")

# Notifications-only floor — drop anything dated before 2026-01-01 per spec.
_NOTIF_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _parse_date_strict_dmy(raw: str | None) -> datetime | None:
    """Strict DD/MM/YYYY — the cell must BE a date, not merely contain one.

    Used for the Notifications and Circulars sections: per spec, only rows
    whose date column is an explicit date should be kept.
    """
    if not raw:
        return None
    match = _DATE_CELL_RE.fullmatch(raw.strip())
    if not match:
        return None
    try:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


async def _resolve_whats_new_detail(
    client: httpx.AsyncClient,
    title: str,
    detail_url: str,
    semaphore: asyncio.Semaphore,
) -> ScrapedItem:
    """Follow a What's New detail-page URL and pull the actual PDF + date.

    The detail page renders one table.data-table-1 row whose cell 1 anchor
    points at the PDF and cell 2 carries the date. If the table is missing
    or unparseable, fall back to emitting the detail URL itself as a Link.
    """
    async with semaphore:
        try:
            resp = await client.get(detail_url, timeout=20)
            resp.raise_for_status()
        except Exception:
            return ScrapedItem(
                title=title,
                link=detail_url,
                is_pdf=False,
                section_label="What's New",
            )

    soup = BeautifulSoup(resp.text, "html.parser")
    row = soup.select_one("table.data-table-1 tbody tr")
    if row is None:
        return ScrapedItem(
            title=title,
            link=detail_url,
            is_pdf=False,
            section_label="What's New",
        )

    anchor = row.select_one("td:nth-child(1) a[href]") or row.select_one("td:nth-child(3) a[href]")
    pdf_url = (anchor.get("href") or "").strip() if anchor else ""
    if not pdf_url:
        return ScrapedItem(
            title=title,
            link=detail_url,
            is_pdf=False,
            section_label="What's New",
        )

    date_cell = row.select_one("td:nth-child(2)")
    date_text = _clean_text(date_cell.get_text()) if date_cell else ""
    parsed_date = _parse_date(date_text)

    return ScrapedItem(
        title=title,
        link=pdf_url,
        published_at=parsed_date,
        is_pdf=pdf_url.lower().endswith(".pdf"),
        section_label="What's New",
    )


async def crawl_dolr(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=DEFAULT_HEADERS, timeout=60) as client:
        # Section 1: What's New — each <a> on the homepage points to a
        # /document/<slug>/ detail page that contains the real PDF + date.
        # Follow each link concurrently and emit one PDF item per row.
        whats_new_url = "https://dolr.gov.in/"
        resp = await client.get(whats_new_url)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            raw_wn: list[tuple[str, str]] = []
            seen_wn_links: set[str] = set()
            for row in soup.select("#whats-new ul li"):
                link_tag = row.select_one("a[href]")
                if not link_tag:
                    continue
                title = _clean_text(link_tag.get_text())
                if not title:
                    continue
                href = (link_tag.get("href") or "").strip()
                if not href:
                    continue
                detail_url = urljoin("https://dolr.gov.in", href)
                if detail_url in seen_wn_links:
                    continue
                seen_wn_links.add(detail_url)
                raw_wn.append((title, detail_url))

            if raw_wn:
                sem = asyncio.Semaphore(4)
                resolved = await asyncio.gather(*[
                    _resolve_whats_new_detail(client, title, url, sem)
                    for title, url in raw_wn
                ])
                items.extend(it for it in resolved if it is not None)

        # Section 2: Orders & Notices
        # WordPress 301-redirects /page/1 → root, so without dedup the same
        # 12 rows are emitted twice. Track seen links and break the loop as
        # soon as a page adds nothing new.
        orders_url = "https://dolr.gov.in/document-category/orders-notices/"
        seen_orders_links: set[str] = set()
        for page in range(10):
            url = orders_url if page == 0 else f"{orders_url}page/{page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.data-table-1 tbody tr")
            if not rows:
                break
            new_in_page = 0
            for row in rows:
                title_elem = row.select_one("td:nth-child(1)")
                link_elem = row.select_one("td:nth-child(3) a")
                date_elem = row.select_one("td:nth-child(2)")
                if not title_elem or not link_elem:
                    continue
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                date_text = _clean_text(date_elem.get_text()) if date_elem else ""
                parsed_date = _parse_date(date_text) if date_text else None
                if not parsed_date:
                    continue
                href = link_elem.get("href", "")
                link = urljoin("https://dolr.gov.in", href) if href else ""
                if not link or link in seen_orders_links:
                    continue
                seen_orders_links.add(link)
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        published_at=parsed_date,
                        is_pdf=href.lower().endswith(".pdf") if href else False,
                        section_label="Orders & Notices",
                    )
                )
                new_in_page += 1
            if new_in_page == 0:
                break

        # Section 3: Notifications — strict DMY date cell, >= 2026-01-01,
        # plus link dedup to neutralise the /page/1 → root redirect.
        notifications_url = "https://dolr.gov.in/document-category/notification/"
        seen_notif_links: set[str] = set()
        for page in range(10):
            url = notifications_url if page == 0 else f"{notifications_url}page/{page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.data-table-1 tbody tr")
            if not rows:
                break
            new_in_page = 0
            for row in rows:
                title_elem = row.select_one("td:nth-child(1)")
                link_elem = row.select_one("td:nth-child(3) a")
                date_elem = row.select_one("td:nth-child(2)")
                if not title_elem or not link_elem:
                    continue
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                date_text = _clean_text(date_elem.get_text()) if date_elem else ""
                parsed_date = _parse_date_strict_dmy(date_text)
                if not parsed_date:
                    continue
                if parsed_date < _NOTIF_MIN_DATE:
                    continue
                href = link_elem.get("href", "")
                link = urljoin("https://dolr.gov.in", href) if href else ""
                if not link or link in seen_notif_links:
                    continue
                seen_notif_links.add(link)
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        published_at=parsed_date,
                        is_pdf=href.lower().endswith(".pdf") if href else False,
                        section_label="Notifications",
                    )
                )
                new_in_page += 1
            if new_in_page == 0:
                break

        # Section 4: Circulars — strict DMY date cell, plus link dedup.
        circular_url = "https://dolr.gov.in/document-category/circular/"
        seen_circ_links: set[str] = set()
        for page in range(10):
            url = circular_url if page == 0 else f"{circular_url}page/{page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.data-table-1 tbody tr")
            if not rows:
                break
            new_in_page = 0
            for row in rows:
                title_elem = row.select_one("td:nth-child(1)")
                link_elem = row.select_one("td:nth-child(3) a")
                date_elem = row.select_one("td:nth-child(2)")
                if not title_elem or not link_elem:
                    continue
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                date_text = _clean_text(date_elem.get_text()) if date_elem else ""
                parsed_date = _parse_date_strict_dmy(date_text)
                if not parsed_date:
                    continue
                href = link_elem.get("href", "")
                link = urljoin("https://dolr.gov.in", href) if href else ""
                if not link or link in seen_circ_links:
                    continue
                seen_circ_links.add(link)
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        published_at=parsed_date,
                        is_pdf=href.lower().endswith(".pdf") if href else False,
                        section_label="Circulars",
                    )
                )
                new_in_page += 1
            if new_in_page == 0:
                break

    # Dedup across sections by PDF link — the same PDF often shows up in
    # both What's New (resolved via the detail page) and one of the
    # /document-category/* listings.
    #
    # When that happens, prefer the SPECIFIC section label (Orders &
    # Notices, Notifications, Circulars) over the catch-all "What's New"
    # feed, so the Orders & Notices section stays visible in the UI and
    # the item carries its proper date from the listing.
    #
    # If both versions share the same specificity, the first-seen wins.
    _WN_LABEL = "What's New"
    by_link: dict[str, ScrapedItem] = {}
    for item in items:
        if not item.link:
            continue
        existing = by_link.get(item.link)
        if existing is None:
            by_link[item.link] = item
            continue
        # Replace a What's New entry when a more specific section also has it.
        if existing.section_label == _WN_LABEL and item.section_label != _WN_LABEL:
            by_link[item.link] = item
    return list(by_link.values())

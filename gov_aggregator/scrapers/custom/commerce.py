from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig


_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

BASE_URL = "https://www.commerce.gov.in"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

SECTIONS = [
    {
        "label": "Press Releases",
        "base_url": "https://www.commerce.gov.in/press-releases/",
        "page_url": "https://www.commerce.gov.in/press-releases/page/{}/",
        "max_pages": 5,
        "use_onclick": True,
    },
    {
        "label": "Publications and Reports",
        "base_url": "https://www.commerce.gov.in/publications-reports/",
        "page_url": "https://www.commerce.gov.in/publications-reports/page/{}/",
        "max_pages": 2,
        "use_onclick": False,
    },
    {
        "label": "Departmental Updates",
        "base_url": "https://www.commerce.gov.in/departmental-updates/",
        "page_url": "https://www.commerce.gov.in/departmental-updates/page/{}/",
        "max_pages": 2,
        "use_onclick": False,
    },
]


def _parse_commerce_date(raw: str | None) -> datetime | None:
    """
    Handle multiple date formats seen on commerce.gov.in:
      - 25-03-2026
      - 15-March-2026
      - 24 July 2025 - 6:06 pm
      - New Delhi, 28th June 2024
      - 28.03.2024
    """
    if not raw:
        return None
    text = raw.split("|")[0].strip()
    text = re.sub(r"^New Delhi,?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE)

    # DD-MM-YYYY or DD.MM.YYYY
    m = re.search(r"(\d{1,2})[-.](\d{1,2})[-.](\d{4})", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    # DD-MonthName-YYYY (e.g. 15-March-2026)
    m = re.search(r"(\d{1,2})-([A-Za-z]{3,9})-(\d{4})", text)
    if m:
        month = _MONTH_MAP.get(m.group(2).lower())
        if month:
            try:
                return datetime(int(m.group(3)), month, int(m.group(1)), tzinfo=timezone.utc)
            except ValueError:
                pass

    # DD MonthName YYYY or MonthName DD YYYY
    m = re.search(
        r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})|([A-Za-z]{3,9})\s+(\d{1,2})\s+(\d{4})",
        text,
    )
    if m:
        if m.group(1):
            day, month_str, year = int(m.group(1)), m.group(2), int(m.group(3))
        else:
            month_str, day, year = m.group(4), int(m.group(5)), int(m.group(6))
        month = _MONTH_MAP.get(month_str.lower())
        if month:
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                pass

    return None


def _extract_onclick_url(onclick: str | None) -> str | None:
    """Extract URL from onclick="window.open('URL', ...)" pattern."""
    if not onclick:
        return None
    m = re.search(r"window\.open\(['\"]([^'\"]+)['\"]", onclick)
    return m.group(1).strip() if m else None


def _parse_page(html: str, section_label: str, use_onclick: bool) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for wrapper in soup.select("div.whats-new-wrapper"):
        heading_div = wrapper.select_one("div.whats-new-heading")
        if not heading_div:
            continue

        h3 = heading_div.select_one("h3")
        if not h3:
            continue
        title = h3.get_text(strip=True)
        if not title:
            continue

        link = ""
        btn_div = wrapper.select_one("div.whats-new-btn-wrapper")

        if use_onclick:
            btn_anchor = btn_div.select_one("a") if btn_div else None
            if btn_anchor:
                link = _extract_onclick_url(btn_anchor.get("onclick", "")) or ""
        else:
            if btn_div:
                btn_anchor = btn_div.select_one("a[href]")
                if btn_anchor:
                    href = btn_anchor.get("href", "").strip()
                    if href and href not in ("#", "javascript:void(0)"):
                        link = href if href.startswith("http") else urljoin(BASE_URL, href)

            # Fallback: external link embedded in the heading anchor
            if not link:
                heading_anchor = heading_div.select_one("a[href]")
                if heading_anchor:
                    href = heading_anchor.get("href", "").strip()
                    if href and href not in ("#", "javascript:void(0)"):
                        link = href if href.startswith("http") else urljoin(BASE_URL, href)

        if not link:
            continue

        cal = wrapper.select_one("div.whats-new-calander h4")
        published_at = _parse_commerce_date(cal.get_text(strip=True) if cal else None)

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=".pdf" in link.lower(),
            section_label=section_label,
        ))

    return items


async def crawl_commerce(config: SiteConfig) -> list[ScrapedItem]:
    """Crawl Press Releases, Publications, and Departmental Updates from commerce.gov.in."""
    all_items: list[ScrapedItem] = []
    seen_links: set[str] = set()

    async with httpx.AsyncClient(follow_redirects=True, headers=HEADERS, timeout=30.0) as client:
        for section in SECTIONS:
            for page_num in range(1, section["max_pages"] + 1):
                url = section["base_url"] if page_num == 1 else section["page_url"].format(page_num)

                try:
                    response = await client.get(url)
                    response.raise_for_status()
                except Exception:
                    break

                page_items = _parse_page(
                    response.text,
                    section_label=section["label"],
                    use_onclick=section["use_onclick"],
                )

                if not page_items:
                    break

                for item in page_items:
                    if item.link not in seen_links:
                        seen_links.add(item.link)
                        all_items.append(item)

    return all_items

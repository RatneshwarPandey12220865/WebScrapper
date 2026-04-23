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
    if not cleaned:
        return None
    # Match dates like "dated 16-03-2026" or "07.04.2026" or "16.03.2026"
    match = re.search(r"(\d{1,2})[-.](\d{1,2})[-.](\d{4})", cleaned)
    if match:
        try:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


async def crawl_labour(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=DEFAULT_HEADERS, timeout=60) as client:
        # Section 1: What's New (Announcements)
        whats_new_url = "https://www.labour.gov.in/whats-new"
        resp = await client.get(whats_new_url)
        if resp.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Announcements section
            announcement_rows = soup.select(".whats-new-announcements .announcementbox")
            for row in announcement_rows:
                title_elem = row.select_one("p.mb-0")
                link_elem = row.select_one("a.download-btn")
                
                if not title_elem:
                    continue
                
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                
                # Extract date from title (e.g., "FAQs on Labour Codes dated 16-03-2026")
                parsed_date = _parse_date(title)
                
                href = ""
                if link_elem:
                    href = link_elem.get("href", "")
                    if href and not href.startswith("http"):
                        href = urljoin("https://www.labour.gov.in", href)
                
                is_pdf = href.lower().endswith(".pdf") if href else False
                
                items.append(
                    ScrapedItem(
                        title=title,
                        link=href,
                        published_at=parsed_date,
                        is_pdf=is_pdf,
                        section_label="What's New",
                    )
                )

        # Section 2: Orders and Notices
        orders_url = "https://www.labour.gov.in/documents/orders-and-notices"
        for page in range(10):
            url = orders_url if page == 0 else f"{orders_url}?page={page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select('[role="table"][aria-label="Orders and Notices data"] .announcementbox')
            
            if not rows:
                break
            
            for row in rows:
                title_elem = row.select_one("p.mb-0")
                date_elem = row.select_one("small[aria-label]")  # Date is in aria-label like "19.07.2021"
                link_elem = row.select_one("a.download-btn")
                
                if not title_elem:
                    continue
                
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                
                # Try to get date from aria-label
                date_text = ""
                if date_elem:
                    date_text = date_elem.get("aria-label", "")
                
                parsed_date = _parse_date(date_text) if date_text else None
                
                href = ""
                if link_elem:
                    href = link_elem.get("href", "")
                    if href and not href.startswith("http"):
                        href = urljoin("https://www.labour.gov.in", href)
                
                is_pdf = href.lower().endswith(".pdf") if href else False
                
                if href:
                    items.append(
                        ScrapedItem(
                            title=title,
                            link=href,
                            published_at=parsed_date,
                            is_pdf=is_pdf,
                            section_label="Orders & Notices",
                        )
                    )

        # Section 3: Press Release
        press_url = "https://www.labour.gov.in/documents/press-release"
        for page in range(10):
            url = press_url if page == 0 else f"{press_url}?page={page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select('[role="table"][aria-label="Press Release data"] .announcementbox')
            
            if not rows:
                break
            
            for row in rows:
                title_elem = row.select_one("p.mb-0")
                date_elem = row.select_one("small[aria-label]")  # Date is in aria-label like "07.04.2026"
                link_elem = row.select_one("a.download-btn")
                
                if not title_elem:
                    continue
                
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                
                date_text = ""
                if date_elem:
                    date_text = date_elem.get("aria-label", "")
                
                parsed_date = _parse_date(date_text) if date_text else None
                
                href = ""
                if link_elem:
                    href = link_elem.get("href", "")
                    if href and not href.startswith("http"):
                        href = urljoin("https://www.labour.gov.in", href)
                
                is_pdf = href.lower().endswith(".pdf") if href else False
                
                if href:
                    items.append(
                        ScrapedItem(
                            title=title,
                            link=href,
                            published_at=parsed_date,
                            is_pdf=is_pdf,
                            section_label="Press Release",
                        )
                    )

        # Section 4: Gazette Notifications
        gazette_url = "https://www.labour.gov.in/documents/gazettes-notifications"
        for page in range(10):
            url = gazette_url if page == 0 else f"{gazette_url}?page={page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break
            
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select('[role="table"][aria-label="Gazettes Notifications data"] .announcementbox')
            
            if not rows:
                break
            
            for row in rows:
                title_elem = row.select_one("p.mb-0")
                date_elem = row.select_one("small[aria-label]")
                link_elem = row.select_one("a.download-btn")
                
                if not title_elem:
                    continue
                
                title = _clean_text(title_elem.get_text())
                if not title:
                    continue
                
                date_text = ""
                if date_elem:
                    date_text = date_elem.get("aria-label", "")
                
                parsed_date = _parse_date(date_text) if date_text else None
                
                href = ""
                if link_elem:
                    href = link_elem.get("href", "")
                    if href and not href.startswith("http"):
                        href = urljoin("https://www.labour.gov.in", href)
                
                is_pdf = href.lower().endswith(".pdf") if href else False
                
                if href:
                    items.append(
                        ScrapedItem(
                            title=title,
                            link=href,
                            published_at=parsed_date,
                            is_pdf=is_pdf,
                            section_label="Gazette Notifications",
                        )
                    )

    return items

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


async def crawl_chandigarh(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=DEFAULT_HEADERS, timeout=60) as client:
        # Section 1: News/Press Releases Archive
        press_release_url = "https://chandigarh.gov.in/news-press-releases-archieve"
        for page in range(2):
            url = press_release_url if page == 0 else f"{press_release_url}?page={page}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select(".views-row")
            if not rows:
                break

            for row in rows:
                title_link = row.select_one(".views-field-title a")
                if not title_link:
                    continue

                title = _clean_text(title_link.get_text())
                if not title:
                    continue

                # Filter out holiday links (items without dates and without PDF links)
                date_elem = row.select_one(".views-field-created .field-content")
                body_link = row.select_one(".views-field-body a")
                has_pdf = body_link and (body_link.get("href", "").lower().endswith(".pdf") or "/sites/" in body_link.get("href", ""))
                
                # Skip items that are just holiday links (no date AND no PDF)
                if not date_elem and not has_pdf:
                    continue

                link = title_link.get("href", "")
                if link:
                    link = urljoin("https://chandigarh.gov.in", link)

                date_text = ""
                date_elem = row.select_one(".views-field-created .field-content")
                if date_elem:
                    date_text = _clean_text(date_elem.get_text())

                body_link = row.select_one(".views-field-body a")
                pdf_link = None
                if body_link:
                    href = body_link.get("href", "")
                    if href.lower().endswith(".pdf") or "/sites/" in href:
                        pdf_link = urljoin("https://chandigarh.gov.in", href)

                final_link = pdf_link or link

                items.append(
                    ScrapedItem(
                        title=title,
                        link=final_link,
                        published_at=_parse_date(date_text) if date_text else None,
                        is_pdf=pdf_link is not None,
                        section_label="Press Releases",
                    )
                )

        # Section 2: Latest News (Homepage ticker)
        home_url = "https://chandigarh.gov.in/"
        resp = await client.get(home_url)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            ticker_items = soup.select(".newsticker .item-list")
            for item in ticker_items:
                link_tag = item.select_one("h3 a") or item.select_one("li a")
                if not link_tag:
                    continue

                title = _clean_text(link_tag.get_text())
                if not title:
                    continue

                href = link_tag.get("href", "")
                if href:
                    link = urljoin("https://chandigarh.gov.in", href)
                else:
                    link = home_url

                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        is_pdf=False,
                        section_label="Latest News",
                    )
                )

        # Section 3: Public Notices
        public_notices_url = "https://chandigarh.gov.in/information/public-notices"
        resp = await client.get(public_notices_url)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            content = soup.select_one(".node__content")
            if content:
                links = content.select("a[href$='.pdf']")
                for link_tag in links:
                    title = _clean_text(link_tag.get_text())
                    if not title or not title.strip():
                        parent_li = link_tag.find_parent("li")
                        if parent_li:
                            strong = parent_li.select_one("strong")
                            if strong:
                                title = _clean_text(strong.get_text())
                            else:
                                continue
                    else:
                        title_match = re.match(r"([^(]+)", title)
                        if title_match:
                            title = title_match.group(1).strip()
                        else:
                            continue

                    if not title:
                        continue

                    href = link_tag.get("href", "")
                    if href:
                        link = urljoin("https://chandigarh.gov.in", href)
                    else:
                        continue

                    items.append(
                        ScrapedItem(
                            title=title,
                            link=link,
                            is_pdf=True,
                            section_label="Public Notices",
                        )
                    )

    return items

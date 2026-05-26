from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.mea")

_BASE = "https://www.mea.gov.in"
_NEWS_URL = "https://www.mea.gov.in/news.htm"
_MIN_DATE = datetime(2026, 4, 1, tzinfo=timezone.utc)
_NEXT_BTN = "ctl00$ContentPlaceHolder1$NewsList1$CustomPager1$ibtnMoveNext"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.mea.gov.in/news.htm",
}

# Names of all pager submit buttons (to be excluded except the one being "clicked")
_PAGER_BTN_NAMES = {
    "ctl00$ContentPlaceHolder1$NewsList1$CustomPager1$ibtnMovePrev",
    "ctl00$ContentPlaceHolder1$NewsList1$CustomPager1$ibtnMoveNext",
}


def _parse_date(raw: str | None) -> datetime | None:
    for fmt in ("%B %d, %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _base_form_data(soup: BeautifulSoup) -> dict[str, str]:
    """Collect all form fields EXCEPT all pager submit buttons (caller adds the clicked one)."""
    data: dict[str, str] = {}
    for inp in soup.select("form input"):
        name = inp.get("name", "")
        if not name:
            continue
        # Skip all page-number buttons and prev/next buttons
        if name.endswith("$lnkbtnPaging") or name in _PAGER_BTN_NAMES:
            continue
        data[name] = inp.get("value", "")
    data["__EVENTTARGET"] = ""
    data["__EVENTARGUMENT"] = ""
    return data


def _pager_buttons(soup: BeautifulSoup) -> list[tuple[str, int]]:
    """Return (ctrl_name, page_number) for each enabled page-number submit button."""
    result = []
    for inp in soup.select("form input"):
        name = inp.get("name", "")
        if not name.endswith("$lnkbtnPaging"):
            continue
        if "aspNetDisabled" in (inp.get("class") or []):
            continue  # current page — disabled
        try:
            result.append((name, int(inp.get("value", "0"))))
        except ValueError:
            pass
    return result


def _has_next(soup: BeautifulSoup) -> bool:
    btn = soup.select_one(f'input[name="{_NEXT_BTN}"]')
    return bool(btn and "aspNetDisabled" not in (btn.get("class") or []))


def _parse_items(soup: BeautifulSoup) -> list[ScrapedItem]:
    items = []
    for li in soup.select("ul.commonListing li"):
        a = li.select_one("a")
        if not a:
            continue
        title = (a.get("title") or a.get_text()).strip()
        if not title:
            continue
        href = (a.get("href") or "").strip()
        link = href if href.startswith("http") else urljoin(_BASE, href)
        p = li.select_one("p")
        published_at = _parse_date(p.get_text(strip=True) if p else None)
        items.append(ScrapedItem(
            title=title, link=link, published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"), section_label="News",
        ))
    return items


async def _click_btn(client: httpx.AsyncClient, soup: BeautifulSoup, btn_name: str, btn_value: str) -> BeautifulSoup | None:
    """Submit form by 'clicking' a specific submit button (name=value pair)."""
    data = _base_form_data(soup)
    data[btn_name] = btn_value
    try:
        resp = await client.post(
            _NEWS_URL, data=data,
            headers={**_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.warning("[mea] POST failed (%s): %s", btn_name, exc)
        return None


async def crawl_mea(_config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    visited_pages: set[int] = {1}  # page 1 loaded via GET

    async with httpx.AsyncClient(follow_redirects=True, headers=_HEADERS, timeout=30) as client:
        resp = await client.get(_NEWS_URL)
        if resp.status_code != 200:
            logger.warning("[mea] initial fetch failed: %s", resp.status_code)
            return []
        soup = BeautifulSoup(resp.text, "html.parser")

        stop = False
        current_page = 1

        while not stop:
            items = _parse_items(soup)
            logger.info("[mea] page %d: %d items", current_page, len(items))
            for item in items:
                if item.published_at and item.published_at < _MIN_DATE:
                    stop = True
                else:
                    all_items.append(item)
            if stop or not items:
                break

            # Find next unvisited page button in current pager group
            next_btn = next(((n, v) for n, v in _pager_buttons(soup) if v not in visited_pages), None)

            if next_btn:
                btn_name, page_num = next_btn
                new_soup = await _click_btn(client, soup, btn_name, str(page_num))
                if new_soup is None:
                    break
                soup = new_soup
                current_page = page_num
                visited_pages.add(page_num)
            elif _has_next(soup):
                # Load next pager group
                new_soup = await _click_btn(client, soup, _NEXT_BTN, "Next")
                if new_soup is None:
                    break
                soup = new_soup
                # Now click the first unvisited page in the new group
                next_in_group = next(((n, v) for n, v in _pager_buttons(soup) if v not in visited_pages), None)
                if not next_in_group:
                    break
                btn_name, page_num = next_in_group
                new_soup = await _click_btn(client, soup, btn_name, str(page_num))
                if new_soup is None:
                    break
                soup = new_soup
                current_page = page_num
                visited_pages.add(page_num)
            else:
                break

    logger.info("[mea] total: %d items", len(all_items))
    return all_items

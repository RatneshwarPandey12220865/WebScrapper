"""
Custom crawler for EPFO (Employees' Provident Fund Organisation).

Site structure: News cards with date, title, and PDF links.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.epfo")

_BASE = "https://www.epfo.gov.in"
_EPFINDIA_URL = "https://www.epfindia.gov.in/site_en/index.php"
_EPFINDIA_BASE = "https://www.epfindia.gov.in/site_en/"
_GOTO_TIMEOUT = 30_000
_WAIT_TIMEOUT = 12_000

_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u00a0", " ")
    return " ".join(text.split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ["%B %d, %Y", "%d %B %Y", "%b %d, %Y"]:
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return _BASE + href
    return urljoin(_BASE, href)


def _parse_news_cards(soup: BeautifulSoup, section_label: str) -> list[ScrapedItem]:
    items = []
    
    for card in soup.select("div.news-card"):
        title_el = card.select_one("p.news-title")
        link_el = card.select_one("div.link a")
        date_el = card.select_one("p.news-date")
        
        title = _clean(title_el.get_text()) if title_el else ""
        link = _abs(link_el.get("href")) if link_el else ""
        date_text = _clean(date_el.get_text()) if date_el else ""
        
        if not title or not link:
            continue
        
        published_at = _parse_date(date_text)
        
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label=section_label,
        ))
    
    return items


def _parse_whats_new(soup: BeautifulSoup) -> list[ScrapedItem]:
    """Parse the What's New carousel from the homepage."""
    items = []
    
    # Filter out slick-cloned items which are duplicates
    for item_div in soup.select("div.newscrsl-item:not(.slick-cloned)"):
        link_el = item_div.select_one("a[href]")
        if not link_el:
            continue
        
        link = _abs(link_el.get("href"))
        if not link:
            continue
        
        title = _clean(link_el.get_text())
        if not title:
            continue
        
        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=None,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="What's New",
        ))
    
    return items


def _sync_crawl(_config: SiteConfig) -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright
    
    all_items = []
    seen_links = set()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_DEFAULT_UA)
        page = context.new_page()
        
        # Navigate to homepage first to avoid CloudFront block
        try:
            page.goto(_BASE, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
            page.wait_for_timeout(3000)
        except Exception:
            pass
        
        # --- Scrape What's New from homepage ---
        try:
            soup = BeautifulSoup(page.content(), "html.parser")
            whats_new_items = _parse_whats_new(soup)
            all_items.extend(whats_new_items)
            logger.info(f"[epfo] What's New: {len(whats_new_items)} items")
        except Exception as e:
            logger.warning(f"[epfo] Failed to scrape What's New: {e}")
        
        # Navigate to press release
        url = f"{_BASE}/press-release/"
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
            page.wait_for_timeout(5000)
            
            soup = BeautifulSoup(page.content(), "html.parser")
            items = _parse_news_cards(soup, "Press Release")
            all_items.extend(items)
            logger.info(f"[epfo] Page 1: {len(items)} items")
            
            # Handle pagination - click through pages
            for page_num in range(2, 8):
                try:
                    next_btn = page.locator("button.next-btn")
                    if next_btn.is_disabled():
                        break
                    next_btn.click()
                    page.wait_for_timeout(3000)
                    
                    soup = BeautifulSoup(page.content(), "html.parser")
                    items = _parse_news_cards(soup, "Press Release")
                    if not items:
                        break
                    all_items.extend(items)
                    logger.info(f"[epfo] Page {page_num}: {len(items)} items")
                except Exception:
                    break
            
        except Exception as e:
            logger.warning(f"[epfo] Failed to scrape: {e}")
        
        browser.close()
    
    # Deduplicate
    seen = set()
    unique = []
    for item in all_items:
        if item.link not in seen:
            seen.add(item.link)
            unique.append(item)
    
    return unique


async def _crawl_epfindia() -> list[ScrapedItem]:
    """Scrape What's New items from epfindia.gov.in (plain HTML, no JS needed)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=30) as client:
            resp = await client.get(_EPFINDIA_URL)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("[epfo] epfindia fetch failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    container = soup.select_one("div#news_custom")
    if not container:
        logger.warning("[epfo] div#news_custom not found on epfindia")
        return []

    items: list[ScrapedItem] = []
    for li in container.select("li"):
        a = li.select_one("a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        link = href if href.startswith("http") else urljoin(_EPFINDIA_BASE, href)

        # Title = full text minus the trailing ".... Read" / "Read"
        raw_text = " ".join(li.get_text().split())
        title = re.sub(r"\.{2,}\s*Read\s*$|Read\s*$", "", raw_text, flags=re.IGNORECASE).strip()
        if not title:
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=None,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="What's New",
        ))

    logger.info("[epfo] epfindia: %d items", len(items))
    return items


async def crawl_epfo(_config: SiteConfig) -> list[ScrapedItem]:
    playwright_items, epfindia_items = await asyncio.gather(
        asyncio.get_event_loop().run_in_executor(None, _sync_crawl, _config),
        _crawl_epfindia(),
    )
    seen: set[str] = set()
    all_items: list[ScrapedItem] = []
    for item in list(playwright_items) + list(epfindia_items):
        if item.link not in seen:
            seen.add(item.link)
            all_items.append(item)
    logger.info("[epfo] total combined: %d items", len(all_items))
    return all_items
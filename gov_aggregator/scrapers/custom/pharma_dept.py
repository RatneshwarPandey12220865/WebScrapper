"""
Custom crawler for Department of Pharmaceuticals (pharma-dept.gov.in).

The site uses Drupal with pagination. Main list shows entries with internal links
that need to be followed to get actual PDF attachments.

Sections scraped:
  1. What's New (paginated at /whats-new?page=0,1,2...)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.pharma")

_BASE = "https://pharma-dept.gov.in"
_GOTO_TIMEOUT = 30_000
_WAIT_TIMEOUT = 12_000


def _clean(text: str | None) -> str:
    if not text:
        return ""
    # Replace problematic characters
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return " ".join(text.split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = re.search(r"(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})", raw)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return _BASE + href
    return urljoin(_BASE, href)


def _parse_list_page(soup: BeautifulSoup, url: str, section_label: str) -> list[ScrapedItem]:
    """Parse the main list page - returns items that need detail page follow-up."""
    items = []
    
    for row in soup.select("li.views-row"):
        title_el = row.select_one("span.field-content a")
        if not title_el:
            continue
        
        title = _clean(title_el.get_text())
        link = _abs(title_el.get("href", ""))
        
        if not title or not link:
            continue
        
        items.append(ScrapedItem(
            title=title,
            link=link,
            section_label=section_label,
        ))
    
    return items


def _parse_detail_page(soup: BeautifulSoup, section_label: str) -> list[ScrapedItem]:
    """Parse detail page - extract PDF link from the page content."""
    items = []
    
    # Find heading
    heading_el = soup.select_one("h1.heading") or soup.select_one("h1.field-content")
    title = _clean(heading_el.get_text()) if heading_el else "Untitled"
    
    # Find PDF links in the page
    for link_el in soup.select("div.views-field-php a[href]"):
        href = link_el.get("href", "")
        if href and not href.startswith("#"):
            pdf_link = _abs(href)
            
            # Get file size if available
            size_el = link_el.parent.get_text() if link_el.parent else ""
            size_match = re.search(r"\(([\d.]+)\s*KB\)", size_el)
            file_size = size_match.group(1) + " KB" if size_match else ""
            
            # Check if it's a PDF
            is_pdf = pdf_link.lower().endswith(".pdf") or "pdf" in pdf_link.lower()
            
            if is_pdf or pdf_link.startswith("http"):
                items.append(ScrapedItem(
                    title=title,
                    link=pdf_link,
                    is_pdf=is_pdf,
                    section_label=section_label,
                ))
                return items  # Return first PDF found
    
    # If no PDF found, return the detail page link itself
    if items:
        return items
    
    return []


def _scrape_list_page(page, url: str, section_label: str) -> list[ScrapedItem]:
    """Scrape a single list page using Playwright."""
    items = []
    detail_links = []
    
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
        try:
            page.wait_for_selector("li.views-row", timeout=_WAIT_TIMEOUT)
        except Exception:
            pass
        
        soup = BeautifulSoup(page.content(), "html.parser")
        rows = soup.select("li.views-row")
        
        for row in rows:
            title_el = row.select_one("span.field-content a")
            if not title_el:
                continue
            
            title = _clean(title_el.get_text())
            link = _abs(title_el.get("href", ""))
            
            if not title or not link:
                continue
            
            # Check if it's already a PDF
            if link.lower().endswith(".pdf"):
                items.append(ScrapedItem(
                    title=title,
                    link=link,
                    is_pdf=True,
                    section_label=section_label,
                ))
            else:
                # It's a detail page - need to follow
                detail_links.append((title, link))
        
    except Exception as e:
        logger.warning(f"Failed to scrape {url}: {e}")
    
    return items, detail_links


def _scrape_detail_page(page, url: str, section_label: str) -> list[ScrapedItem]:
    """Follow a detail page to get the PDF."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)
        try:
            page.wait_for_selector("div.views-field-php a", timeout=_WAIT_TIMEOUT)
        except Exception:
            pass
        
        soup = BeautifulSoup(page.content(), "html.parser")
        
        # Get the page title
        heading_el = soup.select_one("h1.heading") or soup.select_one("h1.field-content")
        title = _clean(heading_el.get_text()) if heading_el else "Untitled"
        
        # Find PDF links
        for link_el in soup.select("div.views-field-php a[href]"):
            href = link_el.get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript"):
                pdf_link = _abs(href)
                
                if pdf_link.lower().endswith(".pdf"):
                    return [ScrapedItem(
                        title=title,
                        link=pdf_link,
                        is_pdf=True,
                        section_label=section_label,
                    )]
        
        # If no direct PDF, might need to check other links
        for link_el in soup.select("a[href]"):
            href = link_el.get("href", "")
            if href and (href.lower().endswith(".pdf") or "/sites/default/files/" in href):
                pdf_link = _abs(href)
                return [ScrapedItem(
                    title=title,
                    link=pdf_link,
                    is_pdf=True,
                    section_label=section_label,
                )]
    
    except Exception as e:
        logger.warning(f"Failed to scrape detail {url}: {e}")
    
    return []


def _sync_crawl(_config: SiteConfig) -> list[ScrapedItem]:
    from playwright.sync_api import sync_playwright
    
    all_items = []
    seen_links = set()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Scrape pages 0, 1, 2 (3 pages)
        for page_num in range(3):
            url = f"{_BASE}/whats-new?title=&page={page_num}"
            
            # Scrape list page
            items, detail_links = _scrape_list_page(page, url, "What's New")
            
            if not items and not detail_links:
                break
            
            # Add direct items (PDF links)
            for item in items:
                if item.link not in seen_links:
                    seen_links.add(item.link)
                    all_items.append(item)
            
            # Follow ALL detail pages to get PDFs
            for title, detail_url in detail_links:
                if detail_url in seen_links:
                    continue
                    
                detail_items = _scrape_detail_page(page, detail_url, "What's New")
                
                if detail_items:
                    for item in detail_items:
                        if item.link not in seen_links:
                            seen_links.add(item.link)
                            all_items.append(item)
                            # Mark detail URL as seen too
                            seen_links.add(detail_url)
                else:
                    # No PDF found - still add the detail link
                    if detail_url not in seen_links:
                        seen_links.add(detail_url)
                        all_items.append(ScrapedItem(
                            title=title,
                            link=detail_url,
                            is_pdf=False,
                            section_label="What's New",
                        ))
            
            logger.info(f"[pharma] Page {page_num}: {len(items)} PDFs, {len(detail_links)} detail pages")
        
        browser.close()
    
    return all_items


async def crawl_pharma(_config: SiteConfig) -> list[ScrapedItem]:
    """Async wrapper."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_crawl, _config)
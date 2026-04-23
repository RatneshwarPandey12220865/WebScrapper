from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig


async def crawl_nccd(config: SiteConfig) -> list[ScrapedItem]:
    """Custom scraper for National Centre for Cold Chain Development.
    
    Scrapes only the Announcements section (css-xintly container).
    """
    from playwright.async_api import async_playwright
    
    items: list[ScrapedItem] = []
    seen_links: set[str] = set()
    base_url = config.base_url or "https://www.nccd.gov.in"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            await page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            
            # Wait for the announcement container to load
            await page.wait_for_selector(".MuiBox-root.css-xintly", timeout=15000)
            
            # Additional wait for dynamic content
            await page.wait_for_timeout(3000)
            
            # Get the rendered HTML
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            
            # Find ONLY the css-xintly container as specified by user
            container = soup.select_one("div.MuiBox-root.css-xintly")
            
            if not container:
                return items
            
            # Get all anchor tags inside this specific container
            links = container.select("a[href]")
            
            for link_elem in links:
                href = link_elem.get("href", "")
                
                # Only process /uploads/ links (documents)
                if not href or "/uploads/" not in href:
                    continue
                
                # Build full URL
                full_url = urljoin(base_url, href)
                
                # Get title from: <a> > div > div (css-1cd9jrv) > div > p
                title = None
                title_elem = link_elem.select_one("div div div p")
                if title_elem:
                    title = title_elem.get_text(strip=True)
                
                if not title:
                    # Fallback to filename
                    filename = href.split("/")[-1] if "/" in href else href
                    title = filename.replace("_", " ").replace("-", " ").replace(".pdf", "").replace(".docx", "")
                
                # If title is just a date string, use filename instead
                if re.fullmatch(r"\d{2}/\d{2}/\d{4}", title):
                    filename = href.split("/")[-1] if "/" in href else href
                    title = filename.replace("_", " ").replace("-", " ").replace(".pdf", "").replace(".docx", "")
                
                # Get date from: <a> > div > div (css-1r2d4z3) > div > div > p
                date = None
                date_elem = link_elem.select_one("div div div div p")
                if date_elem:
                    text = date_elem.get_text(strip=True)
                    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
                        try:
                            date = datetime.strptime(text, "%d/%m/%Y")
                        except ValueError:
                            pass
                
                if title and full_url and full_url not in seen_links:
                    seen_links.add(full_url)
                    items.append(
                        ScrapedItem(
                            title=title[:500] if title else "Untitled",
                            link=full_url,
                            summary=None,
                            published_at=date,
                            is_pdf=full_url.lower().endswith(".pdf"),
                            section_label="Announcements"
                        )
                    )
            
        except Exception as e:
            print(f"Error scraping NCCD: {e}")
        finally:
            await browser.close()
    
    return items
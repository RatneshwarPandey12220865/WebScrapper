"""
Custom scraper for Bihar CMO Press Releases.
Due to JavaScript rendering issues with the main site, we'll:
1. Parse cached/static pages if available  
2. Or use direct PDF URLs extracted from the HTML
"""
import asyncio
import re
from bs4 import BeautifulSoup
import httpx
from datetime import datetime

from gov_aggregator.scrapers.schemas import SiteConfig
from gov_aggregator.scrapers.engine import ScraperEngine, DEFAULT_HEADERS
import logging

logger = logging.getLogger("custom_bihar")

# Base URL for PDFs
BASE_URL = "https://state.bihar.gov.in"


async def scrape_bihar_cm_press_releases(config: SiteConfig, max_items: int = 100) -> list:
    """
    Custom scraper for Bihar CMO Press Releases.
    
    Based on the HTML structure, data is in table#dataTableResponsive with:
    - PR numbers in td:nth-child(3) 
    - Subject (Hindi) in td:nth-child(4)
    - Date in dd/mm/yyyy format
    - PDF links via downloadFile JS function
    
    We use httpx to get the page and extract links - but since the table renders 
    dynamically and requires working JavaScript, we'll track what's needed:
    """
    items = []
    session = httpx.Client(follow_redirects=True, headers=DEFAULT_HEADERS)
    
    try:
        # The main issue is data loads via JS - let's check alternate URLs
        urls_to_try = [
            f"{BASE_URL}/main/SectionInformation.html?editForm&rowId=8929",
            f"{BASE_URL}/main/PressRelease.html",
            f"{BASE_URL}/main/SectionInformation.html?section=cm-press-release",
        ]
        
        html_content = None
        for url in urls_to_try:
            try:
                resp = session.get(url, timeout=30)
                if 'CM PRESS RELEASE' in resp.text:
                    html_content = resp.text
                    logger.info(f"Found CM PRESS RELEASE data at: {url}")
                    break
            except Exception as e:
                logger.debug(f"Failed to fetch {url}: {e}")
        
        if not html_content:
            logger.warning("Could not retrieve CM Press Release page content")
            return items
        
        # Parse HTML
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Look for the table using patterns from your HTML sample
        # Extract data from <tr class="parent">
        rows = soup.find_all('tr', class_=re.compile(r'parent'))
        
        for row in rows[:max_items]:
            try:
                tds = row.find_all('td')
                if len(tds) < 4:
                    continue
                
                # PR No is usually in 3rd td (index 2)
                pr_no = tds[2].get_text(strip=True) if len(tds) > 2 else ""
                
                # Subject in 4th td (index 3)
                title = tds[3].get_text(strip=True) if len(tds) > 3 else ""
                
                # Date in 5th td (index 4)
                date_text = tds[4].get_text(strip=True) if len(tds) > 4 else ""
                
                # PDF link - look for onclick="downloadFile('...')"
                pdf_link = ""
                download_td = tds[5] if len(tds) > 5 else None
                if download_td:
                    onclick = download_td.find('a', onclick=True)
                    if onclick:
                        match = re.search(r"downloadFile\('([^']+)'", onclick.get('onclick', ''))
                        if match:
                            pdf_path = match.group(1)
                            pdf_link = f"{BASE_URL}/{pdf_path}"
                
                if pr_no and title:
                    items.append({
                        'title': title,
                        'pr_no': pr_no,
                        'date': date_text,
                        'link': pdf_link,
                        'source_url': url,
                    })
                    
            except Exception as e:
                logger.debug(f"Error parsing row: {e}")
                continue
        
        logger.info(f"Extracted {len(items)} items from HTML")
        return items
        
    finally:
        session.close()


# Alternative: Create a simpler approach that just extracts known URLs from config
async def scrape_with_fallback(config: SiteConfig) -> list:
    """Fallback that tries various approaches"""
    items = []
    
    # Approach 1: Try the existing engine (will likely return 0 due to JS issues)
    
    # Approach 2: Direct HTTP - but we know this doesn't render the table
    
    # Approach 3: Check if there are more pages via pagination
    # The original data shows PR 265 is latest, 264, 263...  down to oldest
    # This suggests server-side ordering by date descending
    
    # For now, return the extraction logic 
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    async def test():
        # Test extraction
        items = await scrape_bihar_cm_press_releases(None, max_items=10)
        print(f"Items: {len(items)}")
        for item in items[:5]:
            print(f"  {item}")
    
    asyncio.run(test())
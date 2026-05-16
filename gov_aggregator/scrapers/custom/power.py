import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin, parse_qs, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


async def crawl_power(config: SiteConfig) -> list[ScrapedItem]:
    """Scrape circulars from Ministry of Power."""
    results = []
    power_base = "https://powermin.gov.in"

    min_date = None
    if config.min_date:
        min_date = datetime.strptime(config.min_date, "%Y-%m-%d")

    await asyncio.sleep(2)

    session = requests.Session()
    
    # Strong browser headers
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

    try:
        # Step 1: Visit homepage first
        print(f"[power] Visiting homepage...")
        home = session.get(power_base, verify=False, timeout=30, allow_redirects=True)
        print(f"[power] Home: {home.status_code}, cookies: {dict(session.cookies)}")
        
        # Step 2: Visit with a subpath to establish session 
        print(f"[power] Visiting /en...")
        en_page = session.get(f"{power_base}/en", verify=False, timeout=30, allow_redirects=True)
        print(f"[power] /en: {en_page.status_code}")
        
        # Step 3: Now try circular
        url = f"{power_base}/en/circular"
        print(f"[power] Fetching circular: {url}")
        
        response = session.get(url, verify=False, timeout=30, allow_redirects=True)
        print(f"[power] Circular: {response.status_code}, len: {len(response.text)}")
        
        # Check for CAPTCHA
        if "captcha" in response.text.lower() or len(response.text) < 5000:
            print(f"[power] Blocked - checking actual content...")
            # Save for debug
            print(f"[power] Content preview: {response.text[:500]}")
            session.close()
            return results
            
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table", {"class": "views-table cols-5"})
        print(f"[power] Table: {table is not None}")
        
        if not table:
            # Try to find any table
            tables = soup.find_all("table")
            print(f"[power] All tables: {len(tables)}")
            session.close()
            return results

        rows = table.find_all("tr")
        print(f"[power] Rows: {len(rows)}")
        
        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            subject = cols[1].get_text(strip=True)
            date_text = cols[2].get_text(strip=True)
            division = cols[3].get_text(strip=True) or "circular"
            doc_cell = cols[4]

            if not subject:
                continue

            pdf_link = None
            pdf_a = doc_cell.find("a", {"href": True})
            if pdf_a and pdf_a.get("href", "").lower().endswith(".pdf"):
                pdf_link = urljoin(power_base, pdf_a["href"])

            date_match = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", date_text)
            if date_match:
                d, m, y = date_match.groups()
                parsed_date = datetime(int(y), int(m), int(d))
            else:
                parsed_date = datetime.now()

            if min_date and parsed_date < min_date:
                continue

            item = ScrapedItem(
                title=subject.strip(),
                link=pdf_link or power_base,
                published_at=parsed_date,
                is_pdf=bool(pdf_link),
                section_label=division or "Circulars",
            )
            results.append(item)

    except Exception as e:
        print(f"[power] Error: {e}")

    session.close()
    return results
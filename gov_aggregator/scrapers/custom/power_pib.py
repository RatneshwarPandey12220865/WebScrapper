import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


async def crawl_power_pib(config: SiteConfig) -> list[ScrapedItem]:
    """Scrape press releases from PIB Ministry of Power."""
    results: list[ScrapedItem] = []
    base_url = "https://www.pib.gov.in"

    min_date: datetime | None = None
    if config.min_date:
        min_date = datetime.strptime(config.min_date, "%Y-%m-%d")

    await asyncio.sleep(2)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    })

    url = f"{base_url}/newsite/pmreleases.aspx?mincode=52&reg=3&lang=2"
    
    try:
        response = session.get(url, verify=False, timeout=30)
        if response.status_code != 200:
            return results

        soup = BeautifulSoup(response.text, "html.parser")
        container = soup.find("ul", {"class": "link1"})
        if not container:
            return results

        items_container = container.find("ul", {"class": "rel-display11"})
        if not items_container:
            return results

        li_items = items_container.find_all("li", {"class": "rel-list"})
        
        for li in li_items:
            title_elem = li
            date_elem = li.find("p", {"class": "regDate"})
            
            title_text = title_elem.get_text(strip=True) if title_elem else ""
            title_text = re.sub(r"\s*\(\d+-January-\d+\)\s*$", "", title_text).strip()
            
            if not title_text or len(title_text) < 10:
                continue

            date_text = ""
            if date_elem:
                date_text = date_elem.get_text(strip=True)
            
            date_match = re.search(r"(\d+)-(\w+)-(\d+)", date_text)
            if date_match:
                day, month_name, year = date_match.groups()
                try:
                    parsed_date = datetime.strptime(f"{day} {month_name} {year}", "%d %B %Y")
                except ValueError:
                    parsed_date = datetime.now()
            else:
                parsed_date = datetime.now()

            if min_date and parsed_date < min_date:
                continue

            onclick = li.get("onclick", "")
            link = f"{base_url}/newsite/printrelease.aspx?rid="
            rel_match = re.search(r"Getrelease\((\d+)\)", onclick)
            if rel_match:
                rel_id = rel_match.group(1)
                link = f"{base_url}/newsite/printrelease.aspx?rid={rel_id}"
            else:
                link = url

            item = ScrapedItem(
                title=title_text,
                link=link,
                published_at=parsed_date,
                is_pdf=False,
                section_label="Press Releases",
            )

            results.append(item)

    except Exception as e:
        print(f"Error scraping power PIB: {e}")
        import traceback
        traceback.print_exc()

    session.close()
    return results
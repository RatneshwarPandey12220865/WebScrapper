from __future__ import annotations

import httpx
from datetime import datetime, timezone
import re
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

BASE_URL = "https://chemexcil.in"

def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%d %b %Y").replace(tzinfo=timezone.utc)
    except:
        pass
    return None

async def crawl_chemexcil(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    
    async with httpx.AsyncClient(timeout=30.0, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }) as client:
        resp = await client.get(f"{BASE_URL}/circulars")
        if resp.status_code == 200:
            html = resp.text
            
            rows = re.findall(r'<tr>(.*?)</tr>', html, re.DOTALL)
            for row in rows:
                date_match = re.search(r'<td[^>]*>(\d{1,2}\s+\w+\s+\d{4})</td>', row)
                link_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', row)
                
                if date_match and link_match:
                    date_text = date_match.group(1)
                    link = link_match.group(1)
                    title = link_match.group(2).strip()
                    
                    if title and link:
                        items.append(ScrapedItem(
                            title=title[:200],
                            link=link,
                            is_pdf=False,
                            published_at=_parse_date(date_text),
                            section_label="Circulars",
                        ))
    
    return items
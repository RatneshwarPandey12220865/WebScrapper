from __future__ import annotations

import httpx
from datetime import datetime, timezone
import re
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

CBIC_BASE = "https://www.cbic.gov.in"

# All available APIs
APIS = {
    "ticker": f"{CBIC_BASE}/api/getTickerData/Tickers",
    "citizen_corner": f"{CBIC_BASE}/api/getContent/citizens-corner",
    "media_links": f"{CBIC_BASE}/api/getContent/MediaLinks",
}

def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parts = raw.replace("+05:30", "").split("T")
        if len(parts) == 2:
            return datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None

def _parse_date_dmy(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d-%m-%Y").replace(tzinfo=timezone.utc)
    except:
        pass
    return None

def _extract_title(item: dict) -> str:
    for key in ["infoEn", "titleEn", "content", "titleHi"]:
        title = item.get(key, "")
        if title and len(title) > 3:
            return title.strip()
    return ""

def _extract_link(item: dict) -> str:
    path = item.get("path", "")
    if path:
        return f"{CBIC_BASE}{path}"
    return f"{CBIC_BASE}/entities/citizen-corner"

def _extract_date(item: dict) -> datetime | None:
    publish = item.get("publishDt")
    if publish:
        return _parse_date_dmy(publish)
    created = item.get("createdDt")
    if created:
        return _parse_date(created)
    return None

def _is_video(item: dict) -> bool:
    """Check if item is a video based on contentType"""
    content_type = item.get("contentType", {})
    ct = content_type.get("contentType", "") if isinstance(content_type, dict) else ""
    return "video" in ct.lower() if ct else False

async def crawl_cbic_customs(config: SiteConfig) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    seen_titles = set()
    
    async with httpx.AsyncClient(timeout=30.0, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }) as client:
        # Fetch Ticker data
        try:
            resp = await client.get(APIS["ticker"])
            if resp.status_code == 200:
                data = resp.json()
                print(f"Ticker: {len(data)} items")
                for entry in data:
                    title = _extract_title(entry)
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    path = entry.get("path", "")
                    link = f"{CBIC_BASE}{path}" if path else f"{CBIC_BASE}/entities/citizen-corner"
                    items.append(ScrapedItem(
                        title=title[:200],
                        link=link,
                        is_pdf=False,
                        published_at=_extract_date(entry),
                        section_label="Ticker",
                    ))
        except Exception as e:
            print(f"Error fetching ticker: {e}")
        
        # Fetch Citizen Corner
        try:
            resp = await client.get(APIS["citizen_corner"])
            if resp.status_code == 200:
                data = resp.json()
                print(f"Citizen Corner: {len(data)} items")
                for entry in data:
                    title = _extract_title(entry)
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    # Check if it's a video
                    is_video = _is_video(entry)
                    section = "Videos" if is_video else "Citizen Corner"
                    items.append(ScrapedItem(
                        title=title[:200],
                        link=_extract_link(entry),
                        is_pdf=False,
                        published_at=_extract_date(entry),
                        section_label=section,
                    ))
        except Exception as e:
            print(f"Error fetching citizen corner: {e}")
        
        # Fetch Media Links (videos, social media)
        try:
            resp = await client.get(APIS["media_links"])
            if resp.status_code == 200:
                data = resp.json()
                print(f"Media Links: {len(data)} items")
                for entry in data:
                    title = _extract_title(entry)
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    path = entry.get("path", "")
                    # Check if it's a video URL
                    is_video = "youtube" in path.lower() or "video" in path.lower() if path else False
                    items.append(ScrapedItem(
                        title=title[:200],
                        link=path if path else _extract_link(entry),
                        is_pdf=False,
                        published_at=_extract_date(entry),
                        section_label="Media Links",
                    ))
        except Exception as e:
            print(f"Error fetching media links: {e}")
    
    return items
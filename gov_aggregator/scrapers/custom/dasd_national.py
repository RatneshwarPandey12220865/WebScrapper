from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.dasd_national")

_URL = "https://www.dasd.gov.in/"


async def crawl_dasd_national(_config: SiteConfig) -> list[ScrapedItem]:
    async with httpx.AsyncClient(follow_redirects=True, headers=DEFAULT_HEADERS, timeout=30) as client:
        try:
            resp = await client.get(_URL)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[dasd_national] Fetch failed: %s", exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items: list[ScrapedItem] = []

    container = soup.select_one(".elementor-element-fc3c10f .elementor-widget-wrap")
    if not container:
        logger.warning("[dasd_national] Main container not found")
        return []

    section_label = " ".join((container.select_one("h2.elementor-heading-title") or BeautifulSoup("", "html.parser")).get_text().split()) or "News"

    for widget in container.select(".elementor-inner-section .elementor-widget-container"):
        a = widget.select_one("a[href]")
        title = " ".join(widget.get_text().split())
        if not title:
            continue
        link = (a.get("href") or "").strip() if a else _URL
        items.append(ScrapedItem(
            title=title,
            link=link,
            section_label=section_label,
        ))

    logger.info("[dasd_national] %d items", len(items))
    return items

from __future__ import annotations

from collections.abc import Awaitable, Callable

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

from .income_tax import scrape_income_tax

CustomCrawler = Callable[[SiteConfig], Awaitable[list[ScrapedItem]]]

CUSTOM_CRAWLERS: dict[str, CustomCrawler] = {
    "income-tax": scrape_income_tax,
}

__all__ = ["CUSTOM_CRAWLERS", "CustomCrawler", "scrape_income_tax"]

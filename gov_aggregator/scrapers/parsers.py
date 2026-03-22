from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

try:
    from dateutil import parser as date_parser
except ImportError:  # pragma: no cover - fallback for local envs missing python-dateutil
    date_parser = None

try:
    from scrapy.selector import Selector as ScrapySelector
except ImportError:  # pragma: no cover - optional dependency for scrapy-style parsing
    ScrapySelector = None

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _parse_date(raw_value: str | None) -> datetime | None:
    cleaned = _clean_text(raw_value)
    if not cleaned:
        return None
    if date_parser is not None:
        try:
            return date_parser.parse(cleaned, fuzzy=True)
        except (ValueError, TypeError, OverflowError):
            pass

    match = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", cleaned)
    if not match:
        return None

    day, month, year = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _bs4_text_for(node: Tag | None, selector: str | None = None) -> str:
    if not node:
        return ""
    target = node.select_one(selector) if selector else node
    return _clean_text(target.get_text(" ", strip=True) if target else "")


def _bs4_link_for(node: Tag | None, selector: str | None, base_url: str) -> str:
    if not node:
        return ""
    target = node.select_one(selector) if selector else node
    if not target:
        return ""
    href = target.get("websiteurl") or target.get("href") or target.get("data-href") or ""
    return urljoin(base_url, href.strip())


def _build_bs4_item(
    *,
    container: Tag,
    config: SiteConfig,
    title_selector: str | None,
    link_selector: str | None,
    summary_selector: str | None,
    date_selector: str | None,
    force_pdf: bool = False,
) -> ScrapedItem | None:
    title = _bs4_text_for(container, title_selector)
    link = _bs4_link_for(container, link_selector, config.base_url)
    if not title or not link:
        return None

    summary = _bs4_text_for(container, summary_selector) if summary_selector else None
    published_at = _parse_date(_bs4_text_for(container, date_selector)) if date_selector else None
    is_pdf = force_pdf or link.lower().endswith(".pdf")
    return ScrapedItem(title=title, link=link, summary=summary, published_at=published_at, is_pdf=is_pdf)


def _scrapy_first(node, selector: str | None = None):
    if selector:
        selected = node.css(selector)
        if not selected:
            return None
        return selected[0]
    return node


def _scrapy_text_for(node, selector: str | None = None) -> str:
    target = _scrapy_first(node, selector)
    if target is None:
        return ""
    return _clean_text(target.xpath("normalize-space(string())").get())


def _scrapy_link_for(node, selector: str | None, base_url: str) -> str:
    target = _scrapy_first(node, selector)
    if target is None:
        return ""
    href = target.attrib.get("websiteurl") or target.attrib.get("href") or target.attrib.get("data-href") or ""
    return urljoin(base_url, href.strip())


def _build_scrapy_item(
    *,
    container,
    config: SiteConfig,
    title_selector: str | None,
    link_selector: str | None,
    summary_selector: str | None,
    date_selector: str | None,
    force_pdf: bool = False,
) -> ScrapedItem | None:
    title = _scrapy_text_for(container, title_selector)
    link = _scrapy_link_for(container, link_selector, config.base_url)
    if not title or not link:
        return None

    summary = _scrapy_text_for(container, summary_selector) if summary_selector else None
    published_at = _parse_date(_scrapy_text_for(container, date_selector)) if date_selector else None
    is_pdf = force_pdf or link.lower().endswith(".pdf")
    return ScrapedItem(title=title, link=link, summary=summary, published_at=published_at, is_pdf=is_pdf)


def parse_list_bs4(config: SiteConfig, html: str) -> list[ScrapedItem]:
    selectors = config.selectors
    soup = BeautifulSoup(html, "html.parser")
    item_selector = selectors.get("item_selector", "li")
    title_selector = selectors.get("title_selector")
    link_selector = selectors.get("link_selector")
    summary_selector = selectors.get("summary_selector")
    date_selector = selectors.get("date_selector")

    items: list[ScrapedItem] = []
    for node in soup.select(item_selector):
        item = _build_bs4_item(
            container=node,
            config=config,
            title_selector=title_selector,
            link_selector=link_selector,
            summary_selector=summary_selector,
            date_selector=date_selector,
        )
        if item:
            items.append(item)
    return items


def parse_table_bs4(config: SiteConfig, html: str) -> list[ScrapedItem]:
    selectors = config.selectors
    soup = BeautifulSoup(html, "html.parser")
    row_selector = selectors.get("row_selector", "table tr")
    title_selector = selectors.get("title_selector")
    link_selector = selectors.get("link_selector", "a")
    summary_selector = selectors.get("summary_selector")
    date_selector = selectors.get("date_selector")

    items: list[ScrapedItem] = []
    for row in soup.select(row_selector):
        item = _build_bs4_item(
            container=row,
            config=config,
            title_selector=title_selector,
            link_selector=link_selector,
            summary_selector=summary_selector,
            date_selector=date_selector,
        )
        if item:
            items.append(item)
    return items


def parse_pdf_index_bs4(config: SiteConfig, html: str) -> list[ScrapedItem]:
    selectors = config.selectors
    soup = BeautifulSoup(html, "html.parser")
    item_selector = selectors.get("item_selector", "a[href$='.pdf']")
    title_selector = selectors.get("title_selector")
    link_selector = selectors.get("link_selector")
    date_selector = selectors.get("date_selector")

    items: list[ScrapedItem] = []
    for node in soup.select(item_selector):
        item = _build_bs4_item(
            container=node,
            config=config,
            title_selector=title_selector,
            link_selector=link_selector,
            summary_selector=None,
            date_selector=date_selector,
            force_pdf=True,
        )
        if item:
            items.append(item)
    return items


def _scrapy_root(html: str):
    if ScrapySelector is None:
        raise RuntimeError(
            "Scrapy backend requested but scrapy is not installed. Install gov_aggregator requirements in your env."
        )
    return ScrapySelector(text=html)


def parse_list_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    selectors = config.selectors
    root = _scrapy_root(html)
    item_selector = selectors.get("item_selector", "li")
    title_selector = selectors.get("title_selector")
    link_selector = selectors.get("link_selector")
    summary_selector = selectors.get("summary_selector")
    date_selector = selectors.get("date_selector")

    items: list[ScrapedItem] = []
    for node in root.css(item_selector):
        item = _build_scrapy_item(
            container=node,
            config=config,
            title_selector=title_selector,
            link_selector=link_selector,
            summary_selector=summary_selector,
            date_selector=date_selector,
        )
        if item:
            items.append(item)
    return items


def parse_table_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    selectors = config.selectors
    root = _scrapy_root(html)
    row_selector = selectors.get("row_selector", "table tr")
    title_selector = selectors.get("title_selector")
    link_selector = selectors.get("link_selector", "a")
    summary_selector = selectors.get("summary_selector")
    date_selector = selectors.get("date_selector")

    items: list[ScrapedItem] = []
    for row in root.css(row_selector):
        item = _build_scrapy_item(
            container=row,
            config=config,
            title_selector=title_selector,
            link_selector=link_selector,
            summary_selector=summary_selector,
            date_selector=date_selector,
        )
        if item:
            items.append(item)
    return items


def parse_pdf_index_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    selectors = config.selectors
    root = _scrapy_root(html)
    item_selector = selectors.get("item_selector", "a[href$='.pdf']")
    title_selector = selectors.get("title_selector")
    link_selector = selectors.get("link_selector")
    date_selector = selectors.get("date_selector")

    items: list[ScrapedItem] = []
    for node in root.css(item_selector):
        item = _build_scrapy_item(
            container=node,
            config=config,
            title_selector=title_selector,
            link_selector=link_selector,
            summary_selector=None,
            date_selector=date_selector,
            force_pdf=True,
        )
        if item:
            items.append(item)
    return items


PARSERS = {
    "bs4": {
        "list": parse_list_bs4,
        "table": parse_table_bs4,
        "pdf_index": parse_pdf_index_bs4,
    },
    "scrapy": {
        "list": parse_list_scrapy,
        "table": parse_table_scrapy,
        "pdf_index": parse_pdf_index_scrapy,
    },
}


def extract_items(config: SiteConfig, html: str) -> list[ScrapedItem]:
    backend = (config.parser_backend or "bs4").lower()
    backend_parsers = PARSERS.get(backend)
    if not backend_parsers:
        raise ValueError(f"Unsupported parser backend: {backend}")

    parser = backend_parsers.get(config.parser)
    if not parser:
        raise ValueError(f"Unsupported parser type: {config.parser}")
    return parser(config, html)

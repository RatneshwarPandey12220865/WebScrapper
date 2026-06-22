"""Scrapy-selector-based HTML parsers.

Drop-in replacements for the BS4 parsers in parsers.py.  Called via the
PARSERS registry; parsers.py wraps every entry here with _with_bs4_fallback
so a failure or zero-result automatically degrades to the BS4 equivalent.

Improvements over the legacy scrapy helpers that lived in parsers.py:
  • <time datetime="..."> elements — the datetime attr is preferred
    automatically; no selector change required.
  • Empty-string selectors treated as None (self-node text / link).
  • id_link_template — config-driven URL pattern for id-based links
    (e.g. {"id_link_template": "https://example.gov.in/item?id={id}"})
    replaces the old hardcoded PIB fallback.
  • No site-specific logic anywhere in this file.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote, urljoin

try:
    from scrapy.selector import Selector as ScrapySelector
    _SCRAPY_OK = True
except ImportError:
    ScrapySelector = None  # type: ignore[assignment]
    _SCRAPY_OK = False

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.scrapy_parsers")

# ── Text / link helpers ────────────────────────────────────────────────────

def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def _sel(s: str | None) -> str | None:
    """Normalise empty-string selectors to None (means 'use the node itself')."""
    return s if s else None


def _scrapy_text(node, selector: str | None) -> str:
    """Extract text from a Scrapy selector node.

    selector=None  → text of the node itself via normalize-space(string())
    "tag::attr(x)" → pass through (caller wants an attribute value directly)
    "tag"          → select child, prefer <time datetime> attr, else full text
    """
    selector = _sel(selector)

    if selector is None:
        return _clean(node.xpath("normalize-space(string())").get())

    # Caller already wrote an attribute extractor — use as-is
    if "::attr(" in selector:
        return _clean(node.css(selector).get())

    target = node.css(selector)
    if not target:
        return ""
    first = target[0]

    # <time datetime="…"> — machine-readable value is more reliable than text
    if getattr(first.root, "tag", None) == "time":
        dt_attr = (first.attrib.get("datetime") or "").strip()
        if dt_attr:
            return _clean(dt_attr)

    return _clean(first.xpath("normalize-space(string())").get())


_HREF_ATTRS = ("websiteurl", "href", "data-href")


def _href_from_node(node) -> str:
    for attr in _HREF_ATTRS:
        v = (node.attrib.get(attr) or "").strip()
        if v:
            return v
    # Try first child <a> if the node itself has no href
    child_a = node.css("a[href]")
    if child_a:
        return (child_a[0].attrib.get("href") or "").strip()
    return ""


def _scrapy_link(node, selector: str | None, base_url: str) -> str:
    """Resolve a URL from a Scrapy selector node.

    Tries the selector first; falls back to the first <a href> anywhere
    inside the container when the selector yields nothing or no href.
    """
    selector = _sel(selector)

    if selector:
        target = node.css(selector)
        if target:
            href = _href_from_node(target[0])
            if href:
                return urljoin(base_url, href)
        # Selector missed — try any <a> in the container
        fallback = node.css("a[href]")
        if fallback:
            href = (fallback[0].attrib.get("href") or "").strip()
            if href:
                logger.debug("link_selector %r missed — using first child <a>", selector)
                return urljoin(base_url, href)
        return ""

    href = _href_from_node(node)
    return urljoin(base_url, href) if href else ""


def _fallback_link(config: SiteConfig, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "item"
    return f"{config.source_url or config.base_url}#{quote(slug)}"


# ── Item builder ───────────────────────────────────────────────────────────

def _build_items(
    *,
    container,
    config: SiteConfig,
    title_selector: str | None,
    link_selector: str | None,
    summary_selector: str | None,
    date_selector: str | None,
    date_format: str | None = None,
    extra_links_selector: str | None = None,
    force_pdf: bool = False,
) -> list[ScrapedItem]:
    # Import here to avoid circular import; _parse_date_range is pure logic
    from gov_aggregator.scrapers.parsers import _parse_date_range

    title = _scrapy_text(container, title_selector)

    primary_link = _scrapy_link(container, link_selector, config.base_url)

    # Config-driven id-based link template (e.g. PIB-style PRID URLs).
    # Set in selectors: {"id_link_template": "https://site.gov.in/item?id={id}"}
    if not primary_link:
        tmpl = config.selectors.get("id_link_template")
        if tmpl:
            node_id = (container.attrib.get("id") or "").strip()
            if node_id:
                primary_link = tmpl.format(id=node_id)

    if not title:
        return []
    if not primary_link and config.selectors.get("allow_missing_link"):
        primary_link = _fallback_link(config, title)
    if not primary_link:
        return []

    summary = _scrapy_text(container, summary_selector) if _sel(summary_selector) else None
    published_at, end_date = (
        _parse_date_range(_scrapy_text(container, date_selector), fmt=date_format)
        if _sel(date_selector) else (None, None)
    )
    is_pdf = force_pdf or primary_link.lower().endswith(".pdf")

    primary = ScrapedItem(
        title=title,
        link=primary_link,
        summary=summary,
        published_at=published_at,
        end_date=end_date,
        is_pdf=is_pdf,
    )

    if not _sel(extra_links_selector):
        return [primary]

    seen: set[str] = {primary_link}
    extras: list[ScrapedItem] = []
    for a_node in container.css(extra_links_selector):
        href = (a_node.attrib.get("href") or "").strip()
        if not href:
            continue
        link = urljoin(config.base_url, href)
        if link in seen:
            continue
        seen.add(link)
        extras.append(ScrapedItem(
            title=title,
            link=link,
            summary=summary,
            published_at=published_at,
            end_date=end_date,
            is_pdf=(force_pdf or link.lower().endswith(".pdf")),
        ))
    return [primary] + extras


# ── Public parse functions ─────────────────────────────────────────────────

def _require_scrapy() -> None:
    if not _SCRAPY_OK:
        raise ImportError(
            "scrapy is not installed — cannot use parser_backend='scrapy'. "
            "Install with: pip install scrapy"
        )


def parse_list_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    """Parse an HTML list page using Scrapy CSS selectors."""
    _require_scrapy()
    root = ScrapySelector(text=html)
    sel = config.selectors
    containers = root.css(sel.get("item_selector") or "li")
    exclude = sel.get("exclude_title_pattern")
    date_fmt = getattr(config, "date_format", None)
    extra = _sel(sel.get("extra_links_selector"))

    items: list[ScrapedItem] = []
    for container in containers:
        for item in _build_items(
            container=container,
            config=config,
            title_selector=sel.get("title_selector"),
            link_selector=sel.get("link_selector"),
            summary_selector=sel.get("summary_selector"),
            date_selector=sel.get("date_selector"),
            date_format=date_fmt,
            extra_links_selector=extra,
        ):
            if exclude and re.search(exclude, item.title, re.IGNORECASE):
                continue
            items.append(item)
    return items


def parse_table_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    """Parse an HTML table page using Scrapy CSS selectors."""
    _require_scrapy()
    root = ScrapySelector(text=html)
    sel = config.selectors
    containers = root.css(sel.get("row_selector") or "table tr")
    date_fmt = getattr(config, "date_format", None)
    extra = _sel(sel.get("extra_links_selector"))

    items: list[ScrapedItem] = []
    for container in containers:
        for item in _build_items(
            container=container,
            config=config,
            title_selector=sel.get("title_selector"),
            link_selector=sel.get("link_selector"),
            summary_selector=sel.get("summary_selector"),
            date_selector=sel.get("date_selector"),
            date_format=date_fmt,
            extra_links_selector=extra,
        ):
            items.append(item)
    return items


def parse_pdf_index_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    """Parse a PDF-index page (list of PDF links) using Scrapy CSS selectors."""
    _require_scrapy()
    root = ScrapySelector(text=html)
    sel = config.selectors
    containers = root.css(sel.get("item_selector") or "a[href$='.pdf']")
    date_fmt = getattr(config, "date_format", None)
    extra = _sel(sel.get("extra_links_selector"))

    items: list[ScrapedItem] = []
    for container in containers:
        for item in _build_items(
            container=container,
            config=config,
            title_selector=sel.get("title_selector"),
            link_selector=sel.get("link_selector"),
            summary_selector=sel.get("summary_selector"),
            date_selector=sel.get("date_selector"),
            date_format=date_fmt,
            extra_links_selector=extra,
            force_pdf=True,
        ):
            items.append(item)
    return items

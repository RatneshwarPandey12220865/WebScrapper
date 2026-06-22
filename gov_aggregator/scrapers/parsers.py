from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup, Tag

try:
    from dateutil import parser as date_parser
except ImportError:  # pragma: no cover - fallback for local envs missing python-dateutil
    date_parser = None

try:
    from scrapy.selector import Selector as ScrapySelector
except ImportError:  # pragma: no cover - optional dependency for scrapy-style parsing
    ScrapySelector = None

# Scrapy-based parsers live in their own module; imported here so the PARSERS
# registry and the BS4-fallback wrapper can reference both backends together.
try:
    from gov_aggregator.scrapers.scrapy_parsers import (
        parse_list_scrapy as _scrapy_list,
        parse_table_scrapy as _scrapy_table,
        parse_pdf_index_scrapy as _scrapy_pdf_index,
    )
    _SCRAPY_PARSERS_OK = True
except ImportError:
    _SCRAPY_PARSERS_OK = False

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.parsers")


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _contains_explicit_year(value: str) -> bool:
    return re.search(r"\b\d{4}\b", value) is not None


def _roll_back_one_year(value: datetime) -> datetime:
    year = value.year - 1
    day = min(value.day, calendar.monthrange(year, value.month)[1])
    return value.replace(year=year, day=day)


def _adjust_yearless_date(value: datetime, reference_date: datetime | None = None) -> datetime:
    if value.tzinfo is not None:
        reference = reference_date or datetime.now(value.tzinfo)
    else:
        reference = reference_date or datetime.now()
    if value > reference:
        return _roll_back_one_year(value)
    return value


_DATE_RANGE_RE = re.compile(
    r"^(.+?)\s+(?:to|-{1,2}|–|—|/)\s+(.+)$",
    re.IGNORECASE,
)


def _parse_date_range(
    raw_value: str | None,
    fmt: str | None = None,
    reference_date: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Parse a date string that may be a range like '01 Jan 2026 - 31 Mar 2026'.

    Returns (start, end). end is None when the value is a single date.
    Only treats the value as a range when BOTH halves parse as valid dates
    and start <= end, to avoid splitting dates that happen to contain
    a dash (e.g. ISO dates like '2026-01-01').
    """
    cleaned = _clean_text(raw_value)
    if not cleaned:
        return None, None

    m = _DATE_RANGE_RE.match(cleaned)
    if m:
        left, right = m.group(1).strip(), m.group(2).strip()
        # Reject if either half looks like a bare number (avoids splitting ISO dates)
        if not left.isdigit() and not right.isdigit():
            start = _parse_date(left, fmt=fmt, reference_date=reference_date)
            end = _parse_date(right, fmt=fmt, reference_date=reference_date)
            if start and end and start <= end:
                return start, end

    return _parse_date(cleaned, fmt=fmt, reference_date=reference_date), None


def _parse_date(
    raw_value: str | None,
    fmt: str | None = None,
    reference_date: datetime | None = None,
) -> datetime | None:
    cleaned = _clean_text(raw_value)
    if not cleaned:
        return None

    # Try the site-configured strptime format first (exact, no ambiguity)
    if fmt:
        try:
            return datetime.strptime(cleaned, fmt)
        except (ValueError, TypeError):
            pass  # fall through to fuzzy parser

    if date_parser is not None:
        try:
            parsed = date_parser.parse(cleaned, fuzzy=True)
            if not _contains_explicit_year(cleaned):
                parsed = _adjust_yearless_date(parsed, reference_date)
            return parsed
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
    if not target:
        return ""
    # Prefer datetime attribute on <time> elements for accuracy
    if target.name == "time" and target.get("datetime"):
        return target["datetime"]
    return _clean_text(target.get_text(" ", strip=True))


def _bs4_link_for(node: Tag | None, selector: str | None, base_url: str) -> str:
    if not node:
        return ""
    target = node.select_one(selector) if selector else node
    if not target:
        # FIX Bug #2: If selector didn't match, try finding first <a> inside container
        if selector:
            fallback_a = node.select_one("a[href]")
            if fallback_a:
                href = (fallback_a.get("href") or "").strip()
                if href:
                    logger.debug("link_selector '%s' didn't match, used fallback <a> tag", selector)
                    return urljoin(base_url, href)
        return ""
    href = (target.get("websiteurl") or target.get("href") or target.get("data-href") or "").strip()
    if not href:
        # FIX Bug #2: If target element has no href (e.g. container is <tr>/<div>),
        # try finding first <a> inside it as fallback
        fallback_a = target.select_one("a[href]") if hasattr(target, "select_one") else None
        if fallback_a:
            href = (fallback_a.get("href") or "").strip()
            if href:
                logger.debug("Container element <%s> has no href, used first child <a> tag", target.name)
                return urljoin(base_url, href)
        return ""
    return urljoin(base_url, href)


def _fallback_link_for(config: SiteConfig, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "item"
    return f"{config.source_url or config.base_url}#{quote(slug)}"


def _build_bs4_items(
    *,
    container: Tag,
    config: SiteConfig,
    title_selector: str | None,
    link_selector: str | None,
    summary_selector: str | None,
    date_selector: str | None,
    date_format: str | None = None,
    extra_links_selector: str | None = None,
    force_pdf: bool = False,
) -> list[ScrapedItem]:
    title = _bs4_text_for(container, title_selector)
    primary_link = _bs4_link_for(container, link_selector, config.base_url)
    if not title:
        return []
    if not primary_link and config.selectors.get("allow_missing_link"):
        primary_link = _fallback_link_for(config, title)
    if not primary_link:
        return []

    summary = _bs4_text_for(container, summary_selector) if summary_selector else None
    published_at, end_date = (
        _parse_date_range(_bs4_text_for(container, date_selector), fmt=date_format)
        if date_selector else (None, None)
    )
    is_pdf = force_pdf or primary_link.lower().endswith(".pdf")
    primary_item = ScrapedItem(title=title, link=primary_link, summary=summary, published_at=published_at, end_date=end_date, is_pdf=is_pdf)

    if not extra_links_selector:
        return [primary_item]

    seen_links: set[str] = {primary_link}
    extra_items: list[ScrapedItem] = []
    for a_tag in container.select(extra_links_selector):
        href = (a_tag.get("href") or "").strip()
        if not href:
            continue
        extra_link = urljoin(config.base_url, href)
        if extra_link in seen_links:
            continue
        seen_links.add(extra_link)
        extra_items.append(ScrapedItem(
            title=title,
            link=extra_link,
            summary=summary,
            published_at=published_at,
            end_date=end_date,
            is_pdf=(force_pdf or extra_link.lower().endswith(".pdf")),
        ))
    return [primary_item] + extra_items




# ---------------------------------------------------------------------------
#  FIX Bug #1: Cross-check selector key names and auto-remap
# ---------------------------------------------------------------------------

def _validate_and_fix_selectors(config: SiteConfig, parser_type: str) -> dict:
    """Validate selector key names match the parser type and auto-remap if wrong.
    
    If parser_type is 'table' but `item_selector` is set (and `row_selector` is not),
    automatically use `item_selector` as `row_selector` and warn.
    
    If parser_type is 'list' but `row_selector` is set (and `item_selector` is not),
    automatically use `row_selector` as `item_selector` and warn.
    """
    selectors = dict(config.selectors)  # work on a copy
    site_key = config.site_key

    if parser_type == "table":
        has_row = "row_selector" in selectors and selectors["row_selector"]
        has_item = "item_selector" in selectors and selectors["item_selector"]
        if has_item and not has_row:
            logger.warning(
                "[%s] parser_type='table' but only 'item_selector' provided ('%s'). "
                "Auto-remapping to 'row_selector'. Use 'row_selector' for table parsers.",
                site_key, selectors["item_selector"],
            )
            selectors["row_selector"] = selectors.pop("item_selector")

    elif parser_type == "list":
        has_item = "item_selector" in selectors and selectors["item_selector"]
        has_row = "row_selector" in selectors and selectors["row_selector"]
        if has_row and not has_item:
            logger.warning(
                "[%s] parser_type='list' but only 'row_selector' provided ('%s'). "
                "Auto-remapping to 'item_selector'. Use 'item_selector' for list parsers.",
                site_key, selectors["row_selector"],
            )
            selectors["item_selector"] = selectors.pop("row_selector")

    return selectors


# ---------------------------------------------------------------------------
#  FIX Bug #3 & #4: Diagnostic wrappers for parse functions
# ---------------------------------------------------------------------------

def _wrap_bs4_parser(parser_func, parser_type: str):
    """Wrap a bs4 parser function to add diagnostic logging."""
    def wrapped(config: SiteConfig, html: str) -> list[ScrapedItem]:
        site_key = config.site_key
        selectors = _validate_and_fix_selectors(config, parser_type)
        
        # Temporarily patch config selectors with fixed version
        original_selectors = config.selectors
        config.selectors = selectors
        
        try:
            # Determine which selector key this parser uses for containers
            if parser_type == "table":
                container_sel = selectors.get("row_selector", "table tr")
            elif parser_type == "pdf_index":
                container_sel = selectors.get("item_selector", "a[href$='.pdf']")
            else:
                container_sel = selectors.get("item_selector", "li")

            # Pre-check: how many containers does the selector find?
            soup = BeautifulSoup(html, "html.parser")
            containers_found = len(soup.select(container_sel))
            
            if containers_found == 0:
                logger.warning(
                    "[%s] CSS selector '%s' matched 0 elements in HTML (parser=%s, backend=bs4). "
                    "Page may need render_js:true, or the selector is wrong.",
                    site_key, container_sel, parser_type,
                )
                # Log a snippet of the HTML to help debug
                html_preview = html[:500].replace("\n", " ").strip()
                logger.debug("[%s] HTML preview (first 500 chars): %s", site_key, html_preview)
            else:
                logger.info("[%s] Selector '%s' matched %d containers (bs4)", site_key, container_sel, containers_found)
            
            # Run the actual parser
            items = parser_func(config, html)
            
            # Diagnostic: report drops
            if containers_found > 0 and len(items) == 0:
                logger.warning(
                    "[%s] Found %d containers but produced 0 items. "
                    "Likely cause: title_selector or link_selector not matching child elements. "
                    "title_selector='%s', link_selector='%s'",
                    site_key, containers_found,
                    selectors.get("title_selector", "(none)"),
                    selectors.get("link_selector", "(none)"),
                )
            elif containers_found > 0:
                dropped = containers_found - len(items)
                if dropped > 0:
                    logger.info(
                        "[%s] Built %d items from %d containers (%d dropped — no title or link)",
                        site_key, len(items), containers_found, dropped,
                    )
            
            return items
        finally:
            config.selectors = original_selectors
    
    return wrapped


def _wrap_scrapy_parser(parser_func, parser_type: str):
    """Wrap a scrapy parser function to add diagnostic logging."""
    def wrapped(config: SiteConfig, html: str) -> list[ScrapedItem]:
        site_key = config.site_key
        selectors = _validate_and_fix_selectors(config, parser_type)
        
        original_selectors = config.selectors
        config.selectors = selectors
        
        try:
            if parser_type == "table":
                container_sel = selectors.get("row_selector", "table tr")
            elif parser_type == "pdf_index":
                container_sel = selectors.get("item_selector", "a[href$='.pdf']")
            else:
                container_sel = selectors.get("item_selector", "li")

            # Pre-check with scrapy selector
            if ScrapySelector is not None:
                root = ScrapySelector(text=html)
                containers_found = len(root.css(container_sel))
                
                if containers_found == 0:
                    logger.warning(
                        "[%s] CSS selector '%s' matched 0 elements in HTML (parser=%s, backend=scrapy). "
                        "Page may need render_js:true, or the selector is wrong.",
                        site_key, container_sel, parser_type,
                    )
                else:
                    logger.info("[%s] Selector '%s' matched %d containers (scrapy)", site_key, container_sel, containers_found)
            else:
                containers_found = -1  # unknown

            items = parser_func(config, html)
            
            if containers_found > 0 and len(items) == 0:
                logger.warning(
                    "[%s] Found %d containers but produced 0 items. "
                    "Likely cause: title_selector or link_selector not matching child elements. "
                    "title_selector='%s', link_selector='%s'",
                    site_key, containers_found,
                    selectors.get("title_selector", "(none)"),
                    selectors.get("link_selector", "(none)"),
                )
            elif containers_found > 0:
                dropped = containers_found - len(items)
                if dropped > 0:
                    logger.info(
                        "[%s] Built %d items from %d containers (%d dropped — no title or link)",
                        site_key, len(items), containers_found, dropped,
                    )
            
            return items
        finally:
            config.selectors = original_selectors
    
    return wrapped


def parse_list_bs4(config: SiteConfig, html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = config.selectors
    containers = soup.select(selectors.get("item_selector", "li"))
    exclude_pattern = selectors.get("exclude_title_pattern")
    date_format = getattr(config, "date_format", None)
    extra_links_selector = selectors.get("extra_links_selector")
    items: list[ScrapedItem] = []
    for container in containers:
        for item in _build_bs4_items(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
            date_format=date_format,
            extra_links_selector=extra_links_selector,
        ):
            if exclude_pattern and re.search(exclude_pattern, item.title, re.IGNORECASE):
                continue
            items.append(item)
    return items


def parse_table_bs4(config: SiteConfig, html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = config.selectors
    containers = soup.select(selectors.get("row_selector", "table tr"))
    date_format = getattr(config, "date_format", None)
    extra_links_selector = selectors.get("extra_links_selector")
    items: list[ScrapedItem] = []
    for container in containers:
        for item in _build_bs4_items(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
            date_format=date_format,
            extra_links_selector=extra_links_selector,
        ):
            items.append(item)
    return items


def parse_pdf_index_bs4(config: SiteConfig, html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = config.selectors
    containers = soup.select(selectors.get("item_selector", "a[href$='.pdf']"))
    date_format = getattr(config, "date_format", None)
    extra_links_selector = selectors.get("extra_links_selector")
    items: list[ScrapedItem] = []
    for container in containers:
        for item in _build_bs4_items(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
            date_format=date_format,
            extra_links_selector=extra_links_selector,
            force_pdf=True,
        ):
            items.append(item)
    return items




def _with_bs4_fallback(scrapy_fn, bs4_fn):
    """Wrap a Scrapy parser so it degrades gracefully to the BS4 equivalent.

    Fallback triggers when:
      • scrapy_fn raises any exception (import error, selector crash, etc.)
      • scrapy_fn returns 0 items AND bs4_fn returns at least 1

    In both cases a warning is logged so the issue is visible in the run log.
    """
    def wrapped(config: SiteConfig, html: str) -> list[ScrapedItem]:
        try:
            items = scrapy_fn(config, html)
        except Exception as exc:
            logger.warning(
                "[%s] Scrapy parser raised %s — falling back to BS4",
                config.site_key, exc,
            )
            try:
                return bs4_fn(config, html)
            except Exception as bs4_exc:
                logger.error("[%s] BS4 fallback also failed: %s", config.site_key, bs4_exc)
                return []

        if items:
            return items

        # Scrapy returned empty — check whether BS4 does better before giving up
        try:
            bs4_items = bs4_fn(config, html)
        except Exception:
            bs4_items = []

        if bs4_items:
            logger.warning(
                "[%s] Scrapy returned 0 items but BS4 found %d — using BS4 fallback",
                config.site_key, len(bs4_items),
            )
            return bs4_items

        return []

    return wrapped


# ── Pre-built wrapped parsers for each backend / parser-type combo ─────────
_bs4_list      = _wrap_bs4_parser(parse_list_bs4,      "list")
_bs4_table     = _wrap_bs4_parser(parse_table_bs4,     "table")
_bs4_pdf_index = _wrap_bs4_parser(parse_pdf_index_bs4, "pdf_index")

if _SCRAPY_PARSERS_OK:
    _scrapy_list_wrapped      = _with_bs4_fallback(_wrap_scrapy_parser(_scrapy_list,      "list"),      _bs4_list)
    _scrapy_table_wrapped     = _with_bs4_fallback(_wrap_scrapy_parser(_scrapy_table,     "table"),     _bs4_table)
    _scrapy_pdf_index_wrapped = _with_bs4_fallback(_wrap_scrapy_parser(_scrapy_pdf_index, "pdf_index"), _bs4_pdf_index)
else:
    # scrapy_parsers module unavailable — silently use BS4 for "scrapy" backend too
    logger.warning("scrapy_parsers module not available — scrapy backend will use BS4 parsers")
    _scrapy_list_wrapped      = _bs4_list
    _scrapy_table_wrapped     = _bs4_table
    _scrapy_pdf_index_wrapped = _bs4_pdf_index


PARSERS = {
    "bs4": {
        "list":      _bs4_list,
        "table":     _bs4_table,
        "pdf_index": _bs4_pdf_index,
    },
    "scrapy": {
        "list":      _scrapy_list_wrapped,
        "table":     _scrapy_table_wrapped,
        "pdf_index": _scrapy_pdf_index_wrapped,
    },
}


def _cutoff_date(min_date: str | None) -> datetime | None:
    if not min_date:
        return None
    try:
        return datetime.strptime(min_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _apply_min_date(items: list[ScrapedItem], min_date: str | None) -> list[ScrapedItem]:
    cutoff = _cutoff_date(min_date)
    if cutoff is None:
        return items
    kept = []
    for item in items:
        if item.published_at is None:
            # No explicit date was parsed for this item, so leave it unset.
            kept.append(item)
            continue
        dt = item.published_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cutoff:
            kept.append(item)
    return kept


def extract_items(config: SiteConfig, html: str) -> list[ScrapedItem]:
    backend = (config.parser_backend or "bs4").lower()
    backend_parsers = PARSERS.get(backend)
    if not backend_parsers:
        raise ValueError(f"Unsupported parser backend: {backend}")

    parser = backend_parsers.get(config.parser)
    if not parser:
        raise ValueError(f"Unsupported parser type: {config.parser}")
    
    logger.info(
        "[%s] Extracting items: parser=%s, backend=%s, url=%s",
        config.site_key, config.parser, backend, config.source_url,
    )
    
    items = parser(config, html)
    before_date_filter = len(items)
    items = _apply_min_date(items, config.min_date)
    
    if config.min_date and before_date_filter > len(items):
        logger.info(
            "[%s] min_date filter (%s) removed %d of %d items",
            config.site_key, config.min_date, before_date_filter - len(items), before_date_filter,
        )
    
    return items

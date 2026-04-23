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


def _parse_date(raw_value: str | None, reference_date: datetime | None = None) -> datetime | None:
    cleaned = _clean_text(raw_value)
    if not cleaned:
        return None
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
    if not title:
        return None
    if not link and config.selectors.get("allow_missing_link"):
        link = _fallback_link_for(config, title)
    if not link:
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
    if selector is None:
        # Get all direct text nodes excluding child element text
        texts = [t.strip() for t in node.css("::text").getall() if t.strip()]
        # Remove date text if present
        date_text = node.css("p.regDate ::text").get("")
        return " ".join(t for t in texts if t not in date_text).strip()

    target = _scrapy_first(node, selector)
    if target is None:
        return ""
    return _clean_text(target.xpath("normalize-space(string())").get())


def _scrapy_link_for(node, selector: str | None, base_url: str) -> str:
    target = _scrapy_first(node, selector)
    if target is None:
        # FIX Bug #2: If selector didn't match, try finding first <a> inside container
        if selector:
            fallback_links = node.css("a[href]")
            if fallback_links:
                href = (fallback_links[0].attrib.get("href") or "").strip()
                if href:
                    logger.debug("link_selector '%s' didn't match (scrapy), used fallback <a>", selector)
                    return urljoin(base_url, href)
        return ""
    href = (target.attrib.get("websiteurl") or target.attrib.get("href") or target.attrib.get("data-href") or "").strip()
    if not href:
        # FIX Bug #2: Container element has no href, try child <a>
        fallback_links = target.css("a[href]")
        if fallback_links:
            href = (fallback_links[0].attrib.get("href") or "").strip()
            if href:
                logger.debug("Scrapy node has no href, used first child <a> tag")
                return urljoin(base_url, href)
        return ""
    return urljoin(base_url, href)


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
    
    if not link:
        release_id = container.attrib.get("id", "")
        if release_id and release_id.isdigit():
            link = f"https://pib.gov.in/PressReleasePage.aspx?PRID={release_id}"

    if not title:
        return None
    if not link and config.selectors.get("allow_missing_link"):
        link = _fallback_link_for(config, title)
    if not link:
        return None

    summary = _scrapy_text_for(container, summary_selector) if summary_selector else None
    published_at = _parse_date(_scrapy_text_for(container, date_selector)) if date_selector else None
    is_pdf = force_pdf or link.lower().endswith(".pdf")
    return ScrapedItem(title=title, link=link, summary=summary, published_at=published_at, is_pdf=is_pdf)


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
    items: list[ScrapedItem] = []
    for container in containers:
        item = _build_bs4_item(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
        )
        if item:
            items.append(item)
    return items


def parse_table_bs4(config: SiteConfig, html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = config.selectors
    containers = soup.select(selectors.get("row_selector", "table tr"))
    items: list[ScrapedItem] = []
    for container in containers:
        item = _build_bs4_item(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
        )
        if item:
            items.append(item)
    return items


def parse_pdf_index_bs4(config: SiteConfig, html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = config.selectors
    containers = soup.select(selectors.get("item_selector", "a[href$='.pdf']"))
    items: list[ScrapedItem] = []
    for container in containers:
        item = _build_bs4_item(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
            force_pdf=True,
        )
        if item:
            items.append(item)
    return items


def parse_list_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    if ScrapySelector is None:
        raise ImportError("scrapy is not installed; install it to use parser_backend='scrapy'")
    root = ScrapySelector(text=html)
    selectors = config.selectors
    containers = root.css(selectors.get("item_selector", "li"))
    items: list[ScrapedItem] = []
    for container in containers:
        item = _build_scrapy_item(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
        )
        if item:
            items.append(item)
    return items


def parse_table_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    if ScrapySelector is None:
        raise ImportError("scrapy is not installed; install it to use parser_backend='scrapy'")
    root = ScrapySelector(text=html)
    selectors = config.selectors
    containers = root.css(selectors.get("row_selector", "table tr"))
    items: list[ScrapedItem] = []
    for container in containers:
        item = _build_scrapy_item(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
        )
        if item:
            items.append(item)
    return items


def parse_pdf_index_scrapy(config: SiteConfig, html: str) -> list[ScrapedItem]:
    if ScrapySelector is None:
        raise ImportError("scrapy is not installed; install it to use parser_backend='scrapy'")
    root = ScrapySelector(text=html)
    selectors = config.selectors
    containers = root.css(selectors.get("item_selector", "a[href$='.pdf']"))
    items: list[ScrapedItem] = []
    for container in containers:
        item = _build_scrapy_item(
            container=container,
            config=config,
            title_selector=selectors.get("title_selector"),
            link_selector=selectors.get("link_selector"),
            summary_selector=selectors.get("summary_selector"),
            date_selector=selectors.get("date_selector"),
            force_pdf=True,
        )
        if item:
            items.append(item)
    return items


PARSERS = {
    "bs4": {
        "list": _wrap_bs4_parser(parse_list_bs4, "list"),
        "table": _wrap_bs4_parser(parse_table_bs4, "table"),
        "pdf_index": _wrap_bs4_parser(parse_pdf_index_bs4, "pdf_index"),
    },
    "scrapy": {
        "list": _wrap_scrapy_parser(parse_list_scrapy, "list"),
        "table": _wrap_scrapy_parser(parse_table_scrapy, "table"),
        "pdf_index": _wrap_scrapy_parser(parse_pdf_index_scrapy, "pdf_index"),
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

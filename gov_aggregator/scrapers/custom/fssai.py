from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

from gov_aggregator.scrapers.parsers import extract_items
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig, SiteSection

BASE_URL = "https://fssai.gov.in"
RECENT_WHATNEW_URL = "https://fssai.gov.in/recent-whatnew.php"

SECTION_MAP = {
    "title10": ("Press Note", "news"),
    "title26": ("Advisories / Orders", "circular"),
    "title11": ("Gazette Notifications", "notification"),
    "title24": ("Public Comments", "notification"),
    "title9": ("jobs@FSSAI", "recruitment"),
    "title29": ("Internship@FSSAI", "recruitment"),
    "title7": ("Tenders", "tender"),
}

DATE_RE = re.compile(r"\[Updated on[:\s]*(\d{2}[-/]\d{2}[-/]\d{4})\]", re.IGNORECASE)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://fssai.gov.in/",
}


def _parse_fssai_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    match = DATE_RE.search(raw)
    if not match:
        return None
    date_str = match.group(1).replace("/", "-")
    try:
        day, month, year = (int(part) for part in date_str.split("-"))
        return datetime(year, month, day, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _clean_title(raw: str | None) -> str:
    if not raw:
        return ""
    cleaned = re.sub(r"\s*\[Updated on[:\s]*[\d\-/]+\].*", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*[\[\(][\d.]+\s*MB[\]\)].*", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip()


def _resolve_link(href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href in {"", "#", "javascript:void(0)"}:
        return None
    if href.startswith("http"):
        return href
    return urljoin(BASE_URL, href)


def _is_pdf(href: str) -> bool:
    return href.lower().endswith(".pdf")


def _best_pattern_b_link(list_node: Tag) -> tuple[str | None, bool]:
    english_pdf: str | None = None
    first_pdf: str | None = None
    first_non_comment: str | None = None
    first_any: str | None = None

    for li in list_node.find_all("li"):
        anchor = li.find("a", href=True)
        if not anchor:
            continue
        href = _resolve_link(anchor.get("href"))
        if not href:
            continue

        link_title = (anchor.get("title") or anchor.get_text(" ", strip=True) or "").lower()
        is_pdf = _is_pdf(href)
        is_online_comment = "online comment" in link_title

        if first_any is None:
            first_any = href
        if not is_online_comment and first_non_comment is None:
            first_non_comment = href
        if is_pdf and first_pdf is None:
            first_pdf = href
        if "english" in link_title and is_pdf:
            english_pdf = href
            break

    chosen = english_pdf or first_pdf or first_non_comment or first_any
    return chosen, bool(chosen and _is_pdf(chosen))


def _extract_section_items(section_div: Tag, section_label: str) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    children = list(section_div.children)
    i = 0

    while i < len(children):
        node = children[i]
        if not isinstance(node, Tag):
            i += 1
            continue

        if node.name == "p" and "mb10" in node.get("class", []):
            anchor = node.find("a", href=True)
            if anchor:
                raw_text = anchor.get_text(" ", strip=True)
                title = _clean_title(raw_text)
                href = _resolve_link(anchor.get("href"))
                if title and href:
                    items.append(
                        ScrapedItem(
                            title=title,
                            link=href,
                            summary=section_label,
                            published_at=_parse_fssai_date(raw_text),
                            is_pdf=_is_pdf(href),
                            section_label=section_label,
                        )
                    )
            i += 1
            continue

        if node.name == "p" and "notifi_h5" in node.get("class", []):
            raw_title = node.get_text(" ", strip=True)
            title = _clean_title(raw_title)
            published_at = _parse_fssai_date(raw_title)

            j = i + 1
            while j < len(children):
                sibling = children[j]
                if isinstance(sibling, Tag):
                    if sibling.name == "ul":
                        href, is_pdf = _best_pattern_b_link(sibling)
                        if title and href:
                            items.append(
                                ScrapedItem(
                                    title=title,
                                    link=href,
                                    summary=section_label,
                                    published_at=published_at,
                                    is_pdf=is_pdf,
                                    section_label=section_label,
                                )
                            )
                        break
                    if sibling.name in {"p", "div", "h2", "h3", "h4"}:
                        break
                j += 1

            i = j + 1
            continue

        i += 1

    return items


def _parse_recent_whatnew(html: str) -> list[ScrapedItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ScrapedItem] = []

    for section_div in soup.select("div.drax"):
        heading = section_div.find("h4")
        if heading is None:
            continue

        section_label, _default_category = SECTION_MAP.get(
            heading.get("id", ""),
            (" ".join(heading.get_text(" ", strip=True).split()).replace("View All", "").strip(), "notification"),
        )
        items.extend(_extract_section_items(section_div, section_label))

    return items


def _paginate_url(url: str, param: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[param] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _pagination_urls(source_url: str, pagination_param: str | None, start_page: int, max_pages: int) -> list[str]:
    if not pagination_param or max_pages <= 1:
        return [source_url]
    return [_paginate_url(source_url, pagination_param, page) for page in range(start_page, start_page + max_pages)]


def _section_config(parent: SiteConfig, section: SiteSection) -> SiteConfig:
    return SiteConfig(
        site_key=parent.site_key,
        ministry=parent.ministry,
        name=parent.name,
        source_url=section.source_url,
        base_url=parent.base_url,
        parser=section.parser,
        parser_backend=section.parser_backend,
        render_js=section.render_js,
        selectors=section.selectors,
        category_mapping=parent.category_mapping,
        default_category=section.default_category,
        pagination_param=section.pagination_param,
        start_page=section.start_page,
        max_pages=section.max_pages,
        max_items=parent.max_items if section.max_items is None else section.max_items,
        verify_ssl=parent.verify_ssl if section.verify_ssl is None else section.verify_ssl,
        min_date=section.min_date,
    )


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text


async def _crawl_archive_sections(config: SiteConfig, client: httpx.AsyncClient) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []

    for section in config.sections:
        if section.render_js:
            continue

        section_config = _section_config(config, section)
        urls = _pagination_urls(
            section_config.source_url,
            section_config.pagination_param,
            section_config.start_page,
            section_config.max_pages,
        )

        for url in urls:
            html = await _fetch(client, url)
            page_items = extract_items(section_config, html)
            if section_config.pagination_param and not page_items:
                break
            for item in page_items:
                item.section_label = item.section_label or section.section_label
                items.append(item)

    return items


async def crawl_fssai_recent(config: SiteConfig) -> list[ScrapedItem]:
    client_kwargs = {
        "follow_redirects": True,
        "headers": DEFAULT_HEADERS,
        "timeout": 60.0,
    }
    if config.verify_ssl is False:
        client_kwargs["verify"] = False

    async with httpx.AsyncClient(**client_kwargs) as client:
        recent_html = await _fetch(client, config.source_url or RECENT_WHATNEW_URL)
        recent_items = _parse_recent_whatnew(recent_html)
        archive_items = await _crawl_archive_sections(config, client)

    merged: list[ScrapedItem] = []
    seen_links: set[str] = set()
    for item in [*recent_items, *archive_items]:
        if item.link in seen_links:
            continue
        seen_links.add(item.link)
        merged.append(item)
    return merged

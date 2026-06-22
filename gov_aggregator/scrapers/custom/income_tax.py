from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

logger = logging.getLogger("gov_aggregator.custom.income_tax")

WHATS_NEW_URL = "https://incometaxindia.gov.in/Pages/communications/whats-new.aspx"
LATEST_NEWS_URL = "https://www.incometax.gov.in/iec/foportal/latest-news"

CARD_SELECTOR = ".card-print-btn-with-date-new-tag"
CARDS_READY_SELECTOR = ".etds-misc-cards"
TITLE_SELECTOR = "p.card-title-with-arrow"
TAG_SELECTOR = ".etds-whats-new-tag"
DATE_SELECTOR = ".date-in-card"
DOWNLOAD_BUTTON_SELECTOR = "button.etds-misc__button"
EXTERNAL_BUTTON_SELECTOR = "button.etds-external-link-button"
CARD_LINK_SELECTOR = ".card-title-texts, p.card-title-with-arrow, .card-title-with-arrow"


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw.strip(), flags=re.IGNORECASE)
    try:
        return datetime.strptime(cleaned, "%d-%b-%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(cleaned, "%B %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _abs_url(href: str, base: str = "https://www.incometax.gov.in") -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return base + href
    return base + "/" + href


def _is_document_url(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in (".pdf", "/download", "download?", "document", "/file", "file?", "#"))


def _fallback_link(title: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or f"item-{index + 1}"
    return f"{LATEST_NEWS_URL}#{quote(slug)}"


async def _launch_browser(playwright):
    try:
        return await playwright.chromium.launch(headless=True, channel="msedge")
    except Exception:
        return await playwright.chromium.launch(headless=True)


async def _locator_text(locator) -> str:
    if await locator.count() == 0:
        return ""
    return _clean_text(await locator.first.text_content())


async def _extract_cards(page) -> list[dict[str, object]]:
    cards = page.locator(CARD_SELECTOR)
    count = await cards.count()
    items: list[dict[str, object]] = []
    for index in range(count):
        card = cards.nth(index)
        title = await _locator_text(card.locator(TITLE_SELECTOR))
        tag = await _locator_text(card.locator(TAG_SELECTOR))
        date_raw = await _locator_text(card.locator(DATE_SELECTOR))
        items.append(
            {
                "index": index,
                "title": title,
                "tag": tag,
                "date_raw": date_raw,
                "has_download": await card.locator(DOWNLOAD_BUTTON_SELECTOR).count() > 0,
                "has_external": await card.locator(EXTERNAL_BUTTON_SELECTOR).count() > 0,
            }
        )
    return items


async def _capture_target(context, index: int, has_download: bool) -> tuple[str | None, str | None]:
    page = await context.new_page()
    captured_urls: list[str] = []

    def capture_request(request) -> None:
        if _is_document_url(request.url):
            captured_urls.append(request.url)

    def capture_response(response) -> None:
        if _is_document_url(response.url):
            captured_urls.append(response.url)

    page.on("request", capture_request)
    page.on("response", capture_response)

    try:
        await page.goto(WHATS_NEW_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_selector(CARDS_READY_SELECTOR, timeout=30000)

        cards = page.locator(CARD_SELECTOR)
        if index >= await cards.count():
            return None, None

        card = cards.nth(index)
        target = (
            card.locator(DOWNLOAD_BUTTON_SELECTOR)
            if has_download
            else card.locator(EXTERNAL_BUTTON_SELECTOR)
        )
        if await target.count() == 0:
            target = card.locator(CARD_LINK_SELECTOR)
        if await target.count() == 0:
            return None, None

        starting_url = page.url
        starting_pages = len(context.pages)
        await target.first.click(timeout=10000)

        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            await page.wait_for_timeout(1500)

        pdf_url = next((url for url in reversed(captured_urls) if _is_document_url(url)), None)
        detail_url = None

        if len(context.pages) > starting_pages:
            popup = context.pages[-1]
            try:
                await popup.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            if popup.url and popup.url != "about:blank":
                detail_url = popup.url
            await popup.close()
        elif page.url and page.url != starting_url:
            detail_url = page.url

        return pdf_url, detail_url
    finally:
        await page.close()


_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_YEAR = "2026"
_MAX_PAGES = 20


def _parse_latest_news_html(html: str) -> tuple[list[ScrapedItem], str | None]:
    """Parse one page of latest-news HTML.

    Returns (items, next_page_url_or_None).
    The site uses Drupal pagination with URLs like ?year=2026&page=%2C1
    so we follow the actual href from the Next button rather than building it.
    """
    items = []
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.select("div.views-row"):
        content_span = row.select_one("span.field-content")
        if not content_span:
            continue

        date_div = content_span.select_one("div.up-date")
        date_text = _clean_text(date_div.get_text()) if date_div else ""
        published_at = _parse_date(date_text)

        gry_ft = content_span.select_one("div.gry-ft")
        if not gry_ft:
            continue

        # Extract link first, then build title without the "Click here" anchor text
        para = gry_ft.find("p")
        link_a = (para or gry_ft).find("a", href=True)
        link = _abs_url(link_a.get("href")) if link_a else ""

        if para:
            if link_a:
                link_a.extract()  # remove anchor so title text is clean
            title = _clean_text(para.get_text())
        else:
            title = _clean_text(gry_ft.get_text().split("Click here")[0])

        if not title:
            continue

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                published_at=published_at,
                is_pdf=link.lower().endswith(".pdf") if link else False,
                section_label="Latest News",
            )
        )

    next_a = soup.select_one(".pager__item--next a")
    next_href = next_a["href"] if next_a else None
    if next_href and not next_href.startswith("http"):
        # href is relative like "?year=2026&page=%2C1" — attach to the news page base
        if next_href.startswith("?"):
            next_href = LATEST_NEWS_URL + next_href
        elif next_href.startswith("/"):
            next_href = "https://www.incometax.gov.in" + next_href
        else:
            next_href = LATEST_NEWS_URL + "/" + next_href
    return items, next_href


async def _fetch_latest_news_url(
    client: httpx.AsyncClient, url: str
) -> tuple[list[ScrapedItem], str | None]:
    """Fetch one page of latest news by full URL.

    Returns (items, next_page_url_or_None).
    """
    try:
        logger.info("[income-tax] Fetching %s", url)
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        items, next_url = _parse_latest_news_html(response.text)
        logger.info("[income-tax] %d items, next=%s", len(items), next_url)
        return items, next_url
    except Exception as exc:
        logger.warning("[income-tax] Failed to fetch %s: %s", url, exc)
        return [], None


async def scrape_income_tax(config: SiteConfig) -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []

    # Drop Accept-Encoding so the server returns gzip/plain instead of brotli,
    # which httpx can't decompress without the optional 'brotli' package.
    headers = {k: v for k, v in DEFAULT_HEADERS.items() if k.lower() != "accept-encoding"}

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=headers,
        timeout=30.0,
    ) as client:
        next_url: str | None = f"{LATEST_NEWS_URL}?year={_YEAR}"
        visited: set[str] = set()
        while next_url and len(visited) < _MAX_PAGES:
            if next_url in visited:
                break
            visited.add(next_url)

            page_items, next_url = await _fetch_latest_news_url(client, next_url)

            # Stop early if we hit items older than the cutoff
            stop = any(
                item.published_at and item.published_at < _MIN_DATE
                for item in page_items
            )
            all_items.extend(page_items)
            if stop or not page_items:
                break

    all_items.sort(
        key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return all_items


if __name__ == "__main__":
    from gov_aggregator.scrapers.config import load_site_configs
    import asyncio

    configs = {site.site_key: site for site in load_site_configs()}
    config = configs.get("income-tax")
    if config:
        result = asyncio.run(scrape_income_tax(config))
        print(
            json.dumps(
                [
                    {
                        "title": item.title,
                        "section_label": item.section_label,
                        "published_at": item.published_at.isoformat() if item.published_at else None,
                        "link": item.link,
                        "is_pdf": item.is_pdf,
                    }
                    for item in result
                ],
                indent=2,
            )
        )
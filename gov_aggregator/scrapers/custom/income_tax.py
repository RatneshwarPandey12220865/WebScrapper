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


def _parse_latest_news_html(html: str) -> list[ScrapedItem]:
    """Parse the latest-news page HTML."""
    items = []
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.select("div.views-row"):
        content_span = row.select_one("span.field-content")
        if not content_span:
            continue

        date_div = content_span.select_one("div.up-date")
        date_text = _clean_text(date_div.get_text()) if date_div else ""

        gry_ft = content_span.select_one("div.gry-ft")
        if not gry_ft:
            continue

        link_a = gry_ft.find("a", href=True)
        link = _abs_url(link_a.get("href")) if link_a else ""

        all_text = gry_ft.get_text()
        title = all_text.split("Click here")[0].strip()

        if not title:
            continue

        items.append(
            ScrapedItem(
                title=title,
                link=link,
                published_at=_parse_date(date_text),
                is_pdf=link.lower().endswith(".pdf") if link else False,
                section_label="Latest News",
            )
        )

    return items


async def _fetch_latest_news_page(client: httpx.AsyncClient, page_num: int) -> list[ScrapedItem]:
    """Fetch a single page of latest news."""
    if page_num == 0:
        url = LATEST_NEWS_URL
    elif page_num == 1:
        url = f"{LATEST_NEWS_URL}?page=%2C1&link=5"
    else:
        url = f"{LATEST_NEWS_URL}?page=%2C2&link=6"

    try:
        logger.info(f"[income-tax] Fetching: {url}")
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        html = response.text
        
        items = _parse_latest_news_html(html)
        logger.info(f"[income-tax] Page {page_num}: {len(items)} items")
        return items
    except Exception as e:
        logger.warning(f"[income-tax] Failed to fetch latest-news page {page_num}: {e}")
        return []


async def scrape_income_tax(config: SiteConfig) -> list[ScrapedItem]:
    import httpx

    all_items: list[ScrapedItem] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        timeout=30.0,
    ) as client:
        for page_num in range(3):
            page_items = await _fetch_latest_news_page(client, page_num)
            all_items.extend(page_items)
            logger.info(f"[income-tax] Latest News page {page_num + 1}: {len(page_items)} items")

            if not page_items:
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
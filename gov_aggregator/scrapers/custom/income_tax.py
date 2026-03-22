from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

from gov_aggregator.scrapers.engine import DEFAULT_HEADERS
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

WHATS_NEW_URL = "https://incometaxindia.gov.in/Pages/communications/whats-new.aspx"
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
        return datetime.strptime(cleaned, "%B %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_document_url(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in (".pdf", "/download", "download?", "document", "/file", "file?"))


def _fallback_link(title: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or f"item-{index + 1}"
    return f"{WHATS_NEW_URL}#{quote(slug)}"


async def _launch_browser(playwright):
    try:
        return await playwright.chromium.launch(headless=True, channel="msedge")
    except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            await page.wait_for_timeout(1500)

        pdf_url = next((url for url in reversed(captured_urls) if _is_document_url(url)), None)
        detail_url = None

        if len(context.pages) > starting_pages:
            popup = context.pages[-1]
            try:
                await popup.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            if popup.url and popup.url != "about:blank":
                detail_url = popup.url
            await popup.close()
        elif page.url and page.url != starting_url:
            detail_url = page.url

        return pdf_url, detail_url
    finally:
        await page.close()


async def scrape_income_tax(config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_income_tax_in_worker, config)


def _run_income_tax_in_worker(config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_scrape_income_tax_async(config))
    finally:
        loop.close()


async def _scrape_income_tax_async(config: SiteConfig) -> list[ScrapedItem]:
    from playwright.async_api import async_playwright

    items: list[ScrapedItem] = []

    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright)
        context = await browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            extra_http_headers={key: value for key, value in DEFAULT_HEADERS.items() if key != "User-Agent"},
        )
        try:
            page = await context.new_page()
            await page.goto(config.source_url or WHATS_NEW_URL, wait_until="networkidle", timeout=60000)
            wait_for_selector = config.selectors.get("wait_for_selector", CARDS_READY_SELECTOR)
            await page.wait_for_selector(wait_for_selector, timeout=30000)

            cards = await _extract_cards(page)
            await page.close()

            for card in cards:
                title = str(card["title"])
                if not title:
                    continue

                pdf_url, detail_url = await _capture_target(context, int(card["index"]), bool(card["has_download"]))
                link = pdf_url or detail_url or _fallback_link(title, int(card["index"]))
                items.append(
                    ScrapedItem(
                        title=title,
                        link=link,
                        summary=str(card["tag"]) if card["tag"] else None,
                        published_at=_parse_date(str(card["date_raw"]) if card["date_raw"] else None),
                        is_pdf=pdf_url is not None,
                        section_label=str(card["tag"]) if card["tag"] else "",
                    )
                )
        finally:
            await context.close()
            await browser.close()

    return items


if __name__ == "__main__":
    from gov_aggregator.scrapers.config import load_site_configs

    configs = {site.site_key: site for site in load_site_configs()}
    result = _run_income_tax_in_worker(configs["income-tax"])
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

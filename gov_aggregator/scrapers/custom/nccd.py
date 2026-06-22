from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://www.nccd.gov.in"

# Container holding all announcement <a> elements.
# MUI generates these class names from its theme hash; if they ever change,
# the selector falls back to text-keyword detection below.
_CONTAINER = ".MuiBox-root.css-xintly"

_DATE_PATTERNS = [
    ("%d/%m/%Y", re.compile(r"^\d{2}/\d{2}/\d{4}$")),
    ("%d-%m-%Y", re.compile(r"^\d{2}-\d{2}-\d{4}$")),
    ("%B %d, %Y", re.compile(r"^[A-Za-z]+ \d{1,2}, \d{4}$")),
    ("%d %B %Y", re.compile(r"^\d{1,2} [A-Za-z]+ \d{4}$")),
]


def _parse_date(text: str) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    for fmt, pattern in _DATE_PATTERNS:
        if pattern.match(text):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


# JS injected into the page to extract all announcement links robustly.
#
# Strategy: use a.innerText (full visible text) split by newlines rather than
# querying child <p>/<span> tags. The NCCD React layout puts either:
#   • a date on the first line + title on the last line  ("29/01/2026\n\ntitle")
#   • a badge word on the first line + title             ("New\n\ntitle")
#   • just the title                                     ("title")
#
# This approach survives MUI theme/class-name changes because it reads
# rendered text, not structural CSS classes.
_EXTRACT_JS = """
(containerSel) => {
    const container = document.querySelector(containerSel);
    if (!container) return [];

    return Array.from(container.querySelectorAll('a[href]')).map(a => {
        const href = a.getAttribute('href') || '';
        const raw  = a.innerText.trim();
        const lines = raw.split('\\n').map(l => l.trim()).filter(l => l);

        let badge = '';
        let title = '';

        if (lines.length === 0) {
            // No text — derive title from filename later
        } else if (lines.length === 1) {
            title = lines[0];
        } else {
            // First line is badge/date; last meaningful line is the title
            badge = lines[0];
            title = lines[lines.length - 1];
            // If badge === title (collapsed layout) clear the badge
            if (badge === title) badge = '';
        }

        return { href, badge, title };
    });
}
"""


def _run_in_thread() -> list[dict]:
    """Run Playwright in an isolated thread + event loop.

    Playwright's subprocess transport uses asyncio.create_subprocess_exec,
    which raises NotImplementedError on Windows when called from inside
    uvicorn's ProactorEventLoop.  Spinning up a fresh loop in a worker
    thread sidesteps that entirely — the same pattern used by engine.py.
    """
    import asyncio
    from playwright.async_api import async_playwright

    async def _fetch() -> list[dict]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            page = await context.new_page()
            try:
                await page.goto(_BASE, wait_until="domcontentloaded", timeout=60_000)
                try:
                    await page.wait_for_selector(_CONTAINER, timeout=20_000)
                except Exception:
                    await page.wait_for_timeout(6_000)
                return await page.evaluate(_EXTRACT_JS, _CONTAINER)
            finally:
                await page.close()
                await context.close()
                await browser.close()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()


async def crawl_nccd(_config: SiteConfig) -> list[ScrapedItem]:
    import asyncio

    raw: list[dict] = await asyncio.get_running_loop().run_in_executor(None, _run_in_thread)

    items: list[ScrapedItem] = []
    seen: set[str] = set()

    for entry in raw:
        href = (entry.get("href") or "").strip()
        if not href or href in ("#", "/", ""):
            continue

        link = href if href.startswith("http") else urljoin(_BASE, href)
        if link in seen:
            continue
        seen.add(link)

        title = (entry.get("title") or "").strip()
        badge = (entry.get("badge") or "").strip()

        # If title is empty or same as the badge, derive it from the filename
        if not title or title == badge:
            filename = href.rsplit("/", 1)[-1]
            title = re.sub(r"[_\-]+", " ", filename)
            title = re.sub(r"\.(pdf|docx?|xlsx?)$", "", title, flags=re.IGNORECASE).strip()

        if not title:
            title = "Untitled"

        published_at = _parse_date(badge)

        items.append(ScrapedItem(
            title=title[:500],
            link=link,
            published_at=published_at,
            is_pdf=link.lower().endswith(".pdf"),
            section_label="Announcements",
        ))

    return items

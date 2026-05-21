"""Custom scraper for Department of Fisheries (dof.gov.in).

Sections:
  • What's New      — all items (announcements with direct PDF links)
  • Press Release   — all items
  • Circulars       — Jan 2026+
  • Orders          — Jan 2026+
  • Office Orders   — Jan 2026+

Strategy: httpx first (site is SSR); Playwright fallback if httpx is blocked.
"""
from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

_BASE = "https://dof.gov.in"
_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_EXECUTOR = ThreadPoolExecutor(max_workers=1)

_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw.strip())
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_rows(
    html: str,
    section_label: str,
    min_date: datetime | None,
    seen: set[str],
) -> list[ScrapedItem]:
    """Parse dof.gov.in ARIA-role row layout: div[role=row].announcementbox."""
    soup = BeautifulSoup(html, "html.parser")

    # Find all rows — BS4 find_all is more reliable than CSS attribute selectors
    rows = [
        tag for tag in soup.find_all("div", attrs={"role": "row"})
        if "announcementbox" in tag.get("class", [])
    ]

    items: list[ScrapedItem] = []

    for row in rows:
        # Title — first non-trivial text in <p class="mb-0"> or <div class="mb-0 text-break">
        title = ""
        for el in row.find_all(["p", "div"]):
            cls = el.get("class", [])
            if "mb-0" in cls:
                t = el.get_text(strip=True)
                # skip size labels like "454.21 KB" or "Type/Size:"
                if t and len(t) > 5 and not re.match(r"^[\d.,]+ (KB|MB|GB)$", t, re.I):
                    title = t
                    break
        if not title:
            continue

        # Date — <small class="ptype"> containing a date string
        published_at = None
        for small in row.find_all("small"):
            if "ptype" in small.get("class", []):
                d = _parse_date(small.get_text(strip=True))
                if d:
                    published_at = d
                    break

        # Link — only <a class="download-btn"> (skips detail/nav links)
        link = ""
        is_pdf = False
        for a in row.find_all("a", href=True):
            if "download-btn" in a.get("class", []):
                href = a["href"].strip()
                if href and href != "#":
                    link = urljoin(_BASE, href) if not href.startswith("http") else href
                    is_pdf = href.lower().endswith(".pdf") or "/static/uploads/" in href
                    break

        if not link or link in seen:
            continue
        seen.add(link)

        if min_date and published_at and published_at < min_date:
            continue

        items.append(ScrapedItem(
            title=title,
            link=link,
            published_at=published_at,
            is_pdf=is_pdf,
            section_label=section_label,
        ))

    return items


def _has_more_pages(html: str, current_page: int) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    nxt = str(current_page + 1)
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if f"page={nxt}" in href or f"page/{nxt}" in href:
            return True
    for btn in soup.find_all(["a", "button"]):
        text = btn.get_text(strip=True).lower()
        if text in ("next", "›", "»") and not btn.get("disabled") and btn.get("aria-disabled") != "true":
            return True
    return False


# ── httpx-based fetcher (preferred — works when site is SSR) ─────────────────

def _fetch_html(url: str, client: httpx.Client) -> str | None:
    try:
        r = client.get(url, timeout=30)
        if r.status_code == 200:
            html = r.text
            # Verify the page actually contains our rows (not a bot-wall/redirect)
            if "announcementbox" in html:
                return html
            print(f"[fisheries] httpx {url}: got {r.status_code} but no announcementbox (len={len(html)})")
        else:
            print(f"[fisheries] httpx {url}: status {r.status_code}")
    except Exception as e:
        print(f"[fisheries] httpx {url}: {e}")
    return None


def _scrape_http(
    client: httpx.Client,
    base_url: str,
    section_label: str,
    min_date: datetime | None,
    seen: set[str],
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    page_num = 1
    while True:
        url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
        html = _fetch_html(url, client)
        if not html:
            break
        new_items = _parse_rows(html, section_label, min_date, seen)
        print(f"[fisheries] {section_label} p{page_num}: {len(new_items)} items")
        if not new_items:
            break
        items.extend(new_items)
        if not _has_more_pages(html, page_num):
            break
        page_num += 1
    return items


# ── Playwright fallback ──────────────────────────────────────────────────────

def _scrape_playwright(
    page,
    base_url: str,
    section_label: str,
    min_date: datetime | None,
    seen: set[str],
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    page_num = 1
    while True:
        url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            try:
                page.wait_for_selector("div.announcementbox", timeout=20000)
            except Exception:
                page.wait_for_timeout(4000)
        except Exception as e:
            print(f"[fisheries] PW {section_label} p{page_num}: {e}")
            break
        html = page.content()
        has_rows = "announcementbox" in html
        new_items = _parse_rows(html, section_label, min_date, seen)
        print(f"[fisheries] PW {section_label} p{page_num}: {len(new_items)} items (has_rows={has_rows})")
        if not new_items:
            break
        items.extend(new_items)
        if not _has_more_pages(html, page_num):
            break
        page_num += 1
    return items


def _find_section_url(html: str, pattern: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    pat = re.compile(pattern, re.IGNORECASE)
    for a in soup.find_all("a", href=True):
        if pat.search(a.get_text(strip=True)) or pat.search(a["href"]):
            href = a["href"].strip()
            if href and "#" not in href:
                return urljoin(_BASE, href) if not href.startswith("http") else href
    return None


def _crawl_sync() -> list[ScrapedItem]:
    all_items: list[ScrapedItem] = []
    seen: set[str] = set()

    # ── Try httpx first ──────────────────────────────────────────────────────
    with httpx.Client(headers=_HEADERS, follow_redirects=True) as client:
        # Quick probe: can we get the whats-new page with announcementbox rows?
        probe = _fetch_html(f"{_BASE}/whats-new", client)
        use_http = probe is not None
        print(f"[fisheries] httpx probe: {'OK' if use_http else 'BLOCKED — switching to Playwright'}")

        if use_http:
            # What's New
            all_items += _parse_rows(probe, "What's New", None, seen)
            # check for more pages on whats-new
            page_num = 2
            while _has_more_pages(probe if page_num == 2 else "", page_num - 1):
                html = _fetch_html(f"{_BASE}/whats-new?page={page_num}", client)
                if not html:
                    break
                new = _parse_rows(html, "What's New", None, seen)
                if not new:
                    break
                all_items += new
                probe = html
                page_num += 1

            # Press Release — try common URL patterns
            for pr_path in ("/media/press-release", "/media/press-releases", "/offerings/press-release"):
                pr_html = _fetch_html(f"{_BASE}{pr_path}", client)
                if pr_html:
                    all_items += _scrape_http(client, f"{_BASE}{pr_path}", "Press Release", None, seen)
                    break

            # Orders-and-notices sub-sections
            on_html = _fetch_html(f"{_BASE}/documents/orders-and-notices", client)
            if on_html:
                circ_url = _find_section_url(on_html, r"circular")
                if circ_url:
                    all_items += _scrape_http(client, circ_url, "Circulars", _MIN_DATE, seen)

                ord_url = None
                for a in BeautifulSoup(on_html, "html.parser").find_all("a", href=True):
                    txt = a.get_text(strip=True)
                    if re.search(r"\border\b", txt, re.I) and not re.search(r"office|circular|notice", txt, re.I):
                        href = a["href"].strip()
                        ord_url = urljoin(_BASE, href) if not href.startswith("http") else href
                        break
                if ord_url:
                    all_items += _scrape_http(client, ord_url, "Orders", _MIN_DATE, seen)

                off_url = _find_section_url(on_html, r"office.?order")
                if off_url:
                    all_items += _scrape_http(client, off_url, "Office Orders", _MIN_DATE, seen)

    if use_http:
        print(f"[fisheries] httpx total: {len(all_items)}")
        return all_items

    # ── Playwright fallback ──────────────────────────────────────────────────
    print("[fisheries] Using Playwright fallback")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                user_agent=_HEADERS["User-Agent"],
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="Asia/Kolkata",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                },
            )
            page = ctx.new_page()

            # What's New
            all_items += _scrape_playwright(page, f"{_BASE}/whats-new", "What's New", None, seen)

            # Press Release
            pr_items = _scrape_playwright(page, f"{_BASE}/media/press-release", "Press Release", None, seen)
            if not pr_items:
                try:
                    page.goto(_BASE, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2000)
                    page.get_by_role("menuitem", name=re.compile("press release", re.I)).click()
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(2000)
                    pr_url = page.url.split("?")[0]
                    pr_items = _scrape_playwright(page, pr_url, "Press Release", None, seen)
                except Exception as e:
                    print(f"[fisheries] PW Press Release nav: {e}")
            all_items += pr_items

            # Orders-and-notices sub-sections
            page.goto(f"{_BASE}/documents/orders-and-notices", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)
            on_html = page.content()

            circ_url = _find_section_url(on_html, r"circular")
            if circ_url:
                all_items += _scrape_playwright(page, circ_url, "Circulars", _MIN_DATE, seen)

            page.goto(f"{_BASE}/documents/orders-and-notices", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)
            on_html = page.content()
            ord_url = None
            for a in BeautifulSoup(on_html, "html.parser").find_all("a", href=True):
                txt = a.get_text(strip=True)
                if re.search(r"\border\b", txt, re.I) and not re.search(r"office|circular|notice", txt, re.I):
                    href = a["href"].strip()
                    ord_url = urljoin(_BASE, href) if not href.startswith("http") else href
                    break
            if ord_url:
                all_items += _scrape_playwright(page, ord_url, "Orders", _MIN_DATE, seen)

            page.goto(f"{_BASE}/documents/orders-and-notices", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)
            on_html = page.content()
            off_url = _find_section_url(on_html, r"office.?order")
            if off_url:
                all_items += _scrape_playwright(page, off_url, "Office Orders", _MIN_DATE, seen)

        finally:
            browser.close()

    print(f"[fisheries] Playwright total: {len(all_items)}")
    return all_items


async def crawl_fisheries(_config: SiteConfig) -> list[ScrapedItem]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, _crawl_sync)

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger("gov_aggregator.services")

from gov_aggregator.scrapers.config import is_ssl_error, load_site_configs
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
KNOWN_SITES_PATH = DATA_DIR / "known_sites.json"
CACHE_TTL = timedelta(minutes=15)

# Global date cutoff — only return items from January 2026 onwards
GLOBAL_MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


_TITLE_SUFFIX_RE = re.compile(
    r"\s*[\[\uff08(]\s*(?:Updated on|Uploaded on|Dated)\s*[:\s]*[\d\-\/\s,A-Za-z]*[\]\uff09)]\s*$"
    r"|\s+Download\s*\([\d\.KMGB]+\)\s*$"
    r"|\s+Link\s*$"
    r"|\s+Size:\s*[\d\.,]+\s*[KMGBT]?B?\s*$"
    r"|\s*-\s*PDF\s+size\s*:\s*\([\d\.\s\w]+\)\s*\.?\s*$"
    r"|\s*-\s*PDF\s*$",
    re.IGNORECASE,
)

DEFAULT_CATEGORY_MAPPING: dict[str, list[str]] = {
    "recruitment": ["recruitment", "vacancy", "apply", "application", "post of", "posts of", "appointment"],
    "tender": ["tender", "bid", "eoi", "rfp", "corrigendum"],
    "circular": ["circular", "guideline", "manual"],
    "notification": ["notification", "notice", "order", "quota", "allocation", "reallocation"],
    "news": ["news", "press release", "update", "celebrates", "portal", "committed"],
}

SITE_KEY_ALIASES: dict[str, str] = {
    "dgft": "directorate-general-of-foreign-trade",
    "rajasthan": "rajasthan-dipr",
}

SESSION_CACHE: dict[str, dict[str, Any]] = {}
SESSION_LOCK = Lock()

# ── Bulk job tracking ──────────────────────────────────────────────────────
ACTIVE_JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = Lock()

BATCH_SIZE = 10  # sites per batch in bulk crawl


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "site"


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _resolve_site_key(site_key: str) -> str:
    return SITE_KEY_ALIASES.get(site_key, site_key)


def _supported_config_map() -> dict[str, SiteConfig]:
    return {site.site_key: site for site in load_site_configs()}


def load_known_sites() -> list[dict[str, Any]]:
    payload = _read_json(KNOWN_SITES_PATH, {"sites": []})
    return payload.get("sites", [])


def _effective_data_since(config: SiteConfig | None, site_key: str = "") -> str | None:
    """Return ISO date string for the earliest item this site will return, or None if no filter."""
    if config is None:
        return None
    if config.min_date:
        return config.min_date
    if (config.site_key or site_key) in _GLOBAL_MIN_DATE_EXEMPT:
        return None  # custom scraper manages its own cutoff
    return GLOBAL_MIN_DATE.strftime("%Y-%m-%d")


def _category_mapping_for(config: SiteConfig | None) -> dict[str, list[str]]:
    mapping = dict(DEFAULT_CATEGORY_MAPPING)
    if config and config.category_mapping:
        mapping.update(config.category_mapping)
    return mapping


def get_site_catalog() -> list[dict[str, Any]]:
    supported = _supported_config_map()
    inventory = load_known_sites()
    catalog: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for site in inventory:
        site_key = site.get("site_key") or _slugify(site.get("name", ""))
        resolved_site_key = _resolve_site_key(site_key)
        if site_key in seen_keys or resolved_site_key in seen_keys:
            continue
        config = supported.get(resolved_site_key)
        catalog.append(
            {
                "site_key": site_key,
                "name": site.get("name"),
                "registry_url": site.get("registry_url"),
                "alternate_url": site.get("alternate_url"),
                "preferred_url": site.get("preferred_url"),
                "crawl_url": config.source_url if config else site.get("preferred_url"),
                "status": site.get("status", "unknown"),
                "status_raw": site.get("status_raw"),
                "supported": config is not None,
                "parser": config.parser if config else None,
                "parser_backend": config.parser_backend if config else None,
                "selectors": config.selectors if config else {},
                "render_js": config.render_js if config else False,
                "default_category": config.default_category if config else "news",
                "category_mapping": _category_mapping_for(config),
                "data_since": _effective_data_since(config, resolved_site_key),
            }
        )
        seen_keys.add(site_key)
        if config is not None:
            seen_keys.add(resolved_site_key)

    for site_key, config in supported.items():
        if site_key in seen_keys:
            continue
        catalog.append(
            {
                "site_key": site_key,
                "name": config.name,
                "registry_url": config.source_url,
                "alternate_url": None,
                "preferred_url": config.source_url,
                "crawl_url": config.source_url,
                "status": "working",
                "status_raw": None,
                "supported": True,
                "parser": config.parser,
                "parser_backend": config.parser_backend,
                "selectors": config.selectors,
                "render_js": config.render_js,
                "default_category": config.default_category,
                "category_mapping": _category_mapping_for(config),
                "data_since": _effective_data_since(config),
            }
        )

    return sorted(catalog, key=lambda site: (not site["supported"], site["name"].lower()))


def site_catalog_payload() -> dict[str, Any]:
    sites = get_site_catalog()
    return {
        "sites": sites,
        "meta": {
            "total_sites": len(sites),
            "supported_sites": sum(1 for site in sites if site["supported"]),
            "unsupported_sites": sum(1 for site in sites if not site["supported"]),
            "cache_ttl_seconds": int(CACHE_TTL.total_seconds()),
            "global_min_date": GLOBAL_MIN_DATE.strftime("%Y-%m-%d"),
        },
    }


def _classify_item(config: SiteConfig, item: ScrapedItem) -> str:
    haystack = _normalize_text(f"{item.title} {item.summary or ''}")
    mapping = _category_mapping_for(config)
    for category, keywords in mapping.items():
        if any(keyword.lower() in haystack for keyword in keywords):
            return category

    if item.is_pdf:
        return config.default_category or "circular"
    return config.default_category or "news"


def _shape_item(config: SiteConfig, item: ScrapedItem, *, crawl_time: str, previous_links: set[str]) -> dict[str, Any]:
    published_at = item.published_at
    if published_at and published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    return {
        "site_key": config.site_key,
        "source_website": config.name,
        "section_label": item.section_label or "",   # e.g. "Notifications", "Press Releases"
        "crawl_url": config.source_url or config.base_url,
        "title": _clean_title(item.title),
        "category": _classify_item(config, item),
        "description": item.summary,
        "publish_date": published_at.isoformat() if published_at else None,
        "end_date": item.end_date.isoformat() if item.end_date else None,
        "pdf_url": item.link if item.is_pdf else None,
        "external_link": None if item.is_pdf else item.link,
        "link": item.link,
        "is_pdf": item.is_pdf,
        "is_new": item.link not in previous_links,
        "crawl_time": crawl_time,
        "from_cache": False,
    }


def _cache_entry(site_key: str) -> dict[str, Any] | None:
    with SESSION_LOCK:
        return SESSION_CACHE.get(site_key)


def _is_cache_fresh(site_key: str) -> bool:
    entry = _cache_entry(site_key)
    if not entry:
        return False

    cached_at = datetime.fromisoformat(entry["cached_at"])
    return _now() - cached_at <= CACHE_TTL


def _store_cache(site_key: str, items: list[dict[str, Any]]) -> None:
    with SESSION_LOCK:
        SESSION_CACHE[site_key] = {
            "cached_at": _now_iso(),
            "items": items,
            "links": {item["link"] for item in items},
        }


def _cached_items(site_key: str) -> list[dict[str, Any]]:
    entry = _cache_entry(site_key)
    if not entry:
        return []
    return [{**item, "from_cache": True} for item in entry["items"]]


def _previous_links(site_key: str) -> set[str]:
    entry = _cache_entry(site_key)
    if not entry:
        return set()
    return set(entry["links"])


def _result_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (item.get("publish_date") or "", item.get("crawl_time") or "")


_GLOBAL_MIN_DATE_EXEMPT = {
    "department-of-bio-technology",
    "cbic-customs",
    "cochin-sez",
    "department-of-agriculture-and-farmers-welfare-whatsnew",
    "department-of-fisheries",
    "jerc-mizoram",
}


def _passes_global_min_date(item: dict[str, Any]) -> bool:
    """Return True if item's publish_date is on or after GLOBAL_MIN_DATE."""
    if item.get("site_key") in _GLOBAL_MIN_DATE_EXEMPT:
        return True
    publish_date = item.get("publish_date")
    if not publish_date:
        return True
    try:
        dt = datetime.fromisoformat(publish_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= GLOBAL_MIN_DATE
    except (ValueError, TypeError):
        return True


def _passes_date_filter(
    item: dict[str, Any],
    date_from: str | None,
    date_to: str | None,
) -> bool:
    """Apply explicit date range if provided, otherwise fall back to global min date.

    Items with no publish_date always pass through — we never silently drop
    content that simply has no parseable date.
    """
    if not date_from and not date_to:
        return _passes_global_min_date(item)

    if item.get("site_key") in _GLOBAL_MIN_DATE_EXEMPT:
        return True

    publish_date = item.get("publish_date")
    if not publish_date:
        return True

    try:
        dt = datetime.fromisoformat(publish_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if date_from:
            df = datetime.fromisoformat(date_from + "T00:00:00+00:00")
            if dt < df:
                return False
        if date_to:
            dt_end = datetime.fromisoformat(date_to + "T23:59:59+00:00")
            if dt > dt_end:
                return False
        return True
    except (ValueError, TypeError):
        return True


def _status_payload(
    *,
    site_key: str,
    site_name: str,
    ministry: str = "",
    state: str,
    message: str,
    item_count: int = 0,
    new_count: int = 0,
    from_cache: bool = False,
    data_since: str | None = None,
    crawl_time: str | None = None,
    ssl_bypassed: bool = False,
) -> dict[str, Any]:
    return {
        "site_key": site_key,
        "site_name": site_name,
        "ministry": ministry,
        "state": state,
        "message": message,
        "item_count": item_count,
        "new_count": new_count,
        "from_cache": from_cache,
        "data_since": data_since,
        "crawl_time": crawl_time,
        "ssl_bypassed": ssl_bypassed,
    }


def _unsupported_status(site: dict[str, Any], config: "SiteConfig | None" = None) -> dict[str, Any]:
    return _status_payload(
        site_key=site["site_key"],
        site_name=site["name"],
        ministry=config.ministry if config else "",
        state="unsupported",
        message="This site is in the inventory but does not have scraper selectors configured yet.",
    )


def _error_status(
    site_key: str,
    site_name: str,
    message: str,
    ministry: str = "",
    crawl_time: str | None = None,
) -> dict[str, Any]:
    return _status_payload(
        site_key=site_key,
        site_name=site_name,
        ministry=ministry,
        state="error",
        message=message,
        crawl_time=crawl_time,
    )

def _clean_title(title: str | None) -> str:
    if not title:
        return ""
    # Remove ♦ prefix
    title = title.lstrip("♦ \u25c6\u2666").strip()
    # Remove [Updated on:...], [Uploaded on:...], [Dated:...] suffixes
    title = _TITLE_SUFFIX_RE.sub("", title).strip()
    return title


async def _maybe_extract_pdf_dates(
    config: SiteConfig,
    shaped_items: list[dict[str, Any]],
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Post-shaping step: fill in publish_date for PDF items missing a date.

    Runs when config.extract_pdf_dates is True or force=True (per-request override).
    """
    if not config.extract_pdf_dates and not force:
        return shaped_items

    from gov_aggregator.scrapers.pdf_date_extractor import (
        extract_pdf_dates_batch,
        flush_cache,
    )

    # Collect URLs that need extraction
    urls_needed = [
        item["link"]
        for item in shaped_items
        if item.get("is_pdf") and not item.get("publish_date") and item.get("link")
    ]

    if not urls_needed:
        return shaped_items

    logger.info(
        "PDF date extraction: %d/%d items missing dates for %s",
        len(urls_needed), len(shaped_items), config.site_key,
    )

    date_map = await extract_pdf_dates_batch(urls_needed)
    flush_cache()

    for item in shaped_items:
        if item.get("is_pdf") and not item.get("publish_date") and item.get("link"):
            extracted = date_map.get(item["link"])
            if extracted:
                item["publish_date"] = extracted.isoformat() + "T00:00:00+00:00"
                item["date_source"] = "pdf_extracted"

    return shaped_items


async def crawl_site_keys(
    site_keys: list[str],
    *,
    use_cache: bool = True,
    date_from: str | None = None,
    date_to: str | None = None,
    pdf_date_sites: set[str] = frozenset(),
    _job_id: str | None = None,
) -> dict[str, Any]:
    from gov_aggregator.scrapers.custom import CUSTOM_CRAWLERS
    from gov_aggregator.scrapers.engine import ScraperEngine

    unique_keys = list(dict.fromkeys(site_keys))
    catalog = {site["site_key"]: site for site in get_site_catalog()}
    configs = _supported_config_map()
    crawl_time = _now_iso()

    items: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    to_crawl: list[tuple[str, dict[str, Any], SiteConfig]] = []

    for site_key in unique_keys:
        site = catalog.get(site_key)
        resolved_site_key = _resolve_site_key(site_key)
        if site is None:
            statuses.append(
                _status_payload(
                    site_key=site_key,
                    site_name=site_key,
                    state="missing",
                    message="The selected site key is not present in the site inventory.",
                )
            )
            continue

        if not site["supported"] or resolved_site_key not in configs:
            statuses.append(_unsupported_status(site, configs.get(resolved_site_key)))
            continue

        if use_cache and _is_cache_fresh(resolved_site_key):
            cached = _cached_items(resolved_site_key)
            items.extend(cached)
            _cfg = configs.get(resolved_site_key)
            statuses.append(
                _status_payload(
                    site_key=site_key,
                    site_name=site["name"],
                    ministry=(_cfg.ministry if _cfg else ""),
                    state="cached",
                    message="Returned cached crawl results from this session.",
                    item_count=len(cached),
                    new_count=sum(1 for item in cached if item.get("is_new")),
                    from_cache=True,
                    data_since=_effective_data_since(_cfg, resolved_site_key),
                    crawl_time=crawl_time,
                )
            )
            continue

        config = configs[resolved_site_key]
        if config.custom_crawler and config.custom_crawler in CUSTOM_CRAWLERS:
            try:
                previous_links = _previous_links(resolved_site_key)
                custom_items = await CUSTOM_CRAWLERS[config.custom_crawler](config)
                shaped_items = [
                    _shape_item(config, item, crawl_time=crawl_time, previous_links=previous_links)
                    for item in custom_items
                ]
                shaped_items = await _maybe_extract_pdf_dates(
                    config, shaped_items,
                    force=config.site_key in pdf_date_sites,
                )
                _store_cache(config.site_key, shaped_items)
                items.extend(shaped_items)
                statuses.append(
                    _status_payload(
                        site_key=site_key,
                        site_name=site["name"],
                        ministry=config.ministry,
                        state="completed",
                        message="Crawl completed successfully.",
                        item_count=len(shaped_items),
                        new_count=sum(1 for item in shaped_items if item["is_new"]),
                        data_since=_effective_data_since(config),
                        crawl_time=crawl_time,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                error_msg = str(exc)
                if is_ssl_error(exc):
                    error_msg = f"[SSL ERROR] {error_msg}"
                statuses.append(_error_status(site_key, site["name"], error_msg, ministry=config.ministry, crawl_time=crawl_time))
            continue

        to_crawl.append((site_key, site, configs[resolved_site_key]))

    if to_crawl:
        engine = ScraperEngine(
            site_configs=[config for _, _, config in to_crawl],
            concurrency=5,
            timeout_seconds=90.0,
        )
        results = await engine.scrape_all()
        result_map = {result.site_key: result for result in results}

        for requested_site_key, site, config in to_crawl:
            result = result_map.get(config.site_key)
            if result is None:
                statuses.append(_error_status(requested_site_key, site["name"], "No crawl result was returned.", ministry=config.ministry, crawl_time=crawl_time))
                continue

            if result.error:
                statuses.append(_error_status(requested_site_key, site["name"], result.error, ministry=config.ministry, crawl_time=crawl_time))
                continue

            previous_links = _previous_links(config.site_key)
            shaped_items = [
                _shape_item(config, item, crawl_time=crawl_time, previous_links=previous_links)
                for item in result.items
            ]
            shaped_items = await _maybe_extract_pdf_dates(
                config, shaped_items,
                force=config.site_key in pdf_date_sites,
            )
            _store_cache(config.site_key, shaped_items)
            items.extend(shaped_items)
            ssl_bypassed = getattr(result, "ssl_bypassed", False)
            statuses.append(
                _status_payload(
                    site_key=requested_site_key,
                    site_name=site["name"],
                    ministry=config.ministry,
                    state="completed",
                    message="Crawl completed successfully." if not ssl_bypassed else "Crawl completed (SSL verification bypassed).",
                    item_count=len(shaped_items),
                    new_count=sum(1 for item in shaped_items if item["is_new"]),
                    data_since=_effective_data_since(config),
                    crawl_time=crawl_time,
                    ssl_bypassed=ssl_bypassed,
                )
            )

    total_before_filter = len(items)
    items = [item for item in items if _passes_date_filter(item, date_from, date_to)]
    filtered_count = total_before_filter - len(items)
    if filtered_count > 0:
        filter_desc = f"{date_from} to {date_to}" if (date_from or date_to) else f"before {GLOBAL_MIN_DATE.strftime('%Y-%m-%d')}"
        logger.info("Date filter removed %d items (%s)", filtered_count, filter_desc)
    items.sort(key=_result_sort_key, reverse=True)
    return {
        "crawl_time": crawl_time,
        "items": items,
        "site_statuses": statuses,
        "meta": {
            "requested_sites": len(unique_keys),
            "returned_items": len(items),
            "errors": sum(1 for status in statuses if status["state"] in {"error", "missing"}),
            "cached_sites": sum(1 for status in statuses if status["from_cache"]),
        },
    }


async def crawl_all_supported_sites(*, use_cache: bool = True) -> dict[str, Any]:
    return await crawl_site_keys(list(_supported_config_map()), use_cache=use_cache)


async def run_bulk_crawl(
    job_id: str,
    *,
    use_cache: bool,
    date_from: str | None,
    date_to: str | None,
) -> None:
    """Background task: crawl all active sites in batches, tracking progress in ACTIVE_JOBS."""
    all_site_keys = list(_supported_config_map())
    total = len(all_site_keys)

    with JOBS_LOCK:
        ACTIVE_JOBS[job_id]["sites_total"] = total
        ACTIVE_JOBS[job_id]["status"] = "running"

    all_items: list[dict[str, Any]] = []
    all_statuses: list[dict[str, Any]] = []

    for i in range(0, total, BATCH_SIZE):
        with JOBS_LOCK:
            if ACTIVE_JOBS[job_id].get("status") == "cancelled":
                return

        batch = all_site_keys[i : i + BATCH_SIZE]
        try:
            result = await crawl_site_keys(
                batch,
                use_cache=use_cache,
                date_from=date_from,
                date_to=date_to,
                _job_id=job_id,
            )
            all_items.extend(result["items"])
            all_statuses.extend(result["site_statuses"])
        except Exception as exc:  # noqa: BLE001
            logger.error("Bulk crawl batch %d error: %s", i // BATCH_SIZE, exc)

        with JOBS_LOCK:
            ACTIVE_JOBS[job_id]["sites_done"] = min(i + BATCH_SIZE, total)

    all_items.sort(key=_result_sort_key, reverse=True)

    with JOBS_LOCK:
        ACTIVE_JOBS[job_id].update(
            {
                "status": "done",
                "finished_at": _now_iso(),
                "sites_done": total,
                "result": {
                    "crawl_time": _now_iso(),
                    "items": all_items,
                    "site_statuses": all_statuses,
                    "meta": {
                        "requested_sites": total,
                        "returned_items": len(all_items),
                        "errors": sum(1 for s in all_statuses if s["state"] in {"error", "missing"}),
                        "cached_sites": sum(1 for s in all_statuses if s["from_cache"]),
                    },
                },
            }
        )

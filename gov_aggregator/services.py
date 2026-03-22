from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from gov_aggregator.scrapers.config import load_site_configs
from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
KNOWN_SITES_PATH = DATA_DIR / "known_sites.json"
CACHE_TTL = timedelta(minutes=15)

DEFAULT_CATEGORY_MAPPING: dict[str, list[str]] = {
    "recruitment": ["recruitment", "vacancy", "apply", "application", "post of", "posts of", "appointment"],
    "tender": ["tender", "bid", "eoi", "rfp", "corrigendum"],
    "circular": ["circular", "guideline", "manual"],
    "notification": ["notification", "notice", "order", "quota", "allocation", "reallocation"],
    "news": ["news", "press release", "update", "celebrates", "portal", "committed"],
}

SESSION_CACHE: dict[str, dict[str, Any]] = {}
SESSION_LOCK = Lock()


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


def _supported_config_map() -> dict[str, SiteConfig]:
    return {site.site_key: site for site in load_site_configs()}


def load_known_sites() -> list[dict[str, Any]]:
    payload = _read_json(KNOWN_SITES_PATH, {"sites": []})
    return payload.get("sites", [])


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
        if site_key in seen_keys:
            continue
        config = supported.get(site_key)
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
            }
        )
        seen_keys.add(site_key)

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
        },
    }


def _parse_embedded_date(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", value)
    if not match:
        return None

    day, month, year = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


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
    if published_at is None:
        published_at = _parse_embedded_date(item.title)

    return {
        "site_key": config.site_key,
        "source_website": config.name,
        "section_label": item.section_label or "",   # e.g. "Notifications", "Press Releases"
        "crawl_url": config.source_url or config.base_url,
        "title": item.title,
        "category": _classify_item(config, item),
        "description": item.summary,
        "publish_date": published_at.isoformat() if published_at else None,
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


def _status_payload(
    *,
    site_key: str,
    site_name: str,
    state: str,
    message: str,
    item_count: int = 0,
    new_count: int = 0,
    from_cache: bool = False,
) -> dict[str, Any]:
    return {
        "site_key": site_key,
        "site_name": site_name,
        "state": state,
        "message": message,
        "item_count": item_count,
        "new_count": new_count,
        "from_cache": from_cache,
    }


def _unsupported_status(site: dict[str, Any]) -> dict[str, Any]:
    return _status_payload(
        site_key=site["site_key"],
        site_name=site["name"],
        state="unsupported",
        message="This site is in the inventory but does not have scraper selectors configured yet.",
    )


def _error_status(site_key: str, site_name: str, message: str) -> dict[str, Any]:
    return _status_payload(
        site_key=site_key,
        site_name=site_name,
        state="error",
        message=message,
    )


async def crawl_site_keys(site_keys: list[str], *, use_cache: bool = True) -> dict[str, Any]:
    from gov_aggregator.scrapers.custom import CUSTOM_CRAWLERS
    from gov_aggregator.scrapers.engine import ScraperEngine

    unique_keys = list(dict.fromkeys(site_keys))
    catalog = {site["site_key"]: site for site in get_site_catalog()}
    configs = _supported_config_map()
    crawl_time = _now_iso()

    items: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    to_crawl: list[SiteConfig] = []

    for site_key in unique_keys:
        site = catalog.get(site_key)
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

        if not site["supported"] or site_key not in configs:
            statuses.append(_unsupported_status(site))
            continue

        if use_cache and _is_cache_fresh(site_key):
            cached = _cached_items(site_key)
            items.extend(cached)
            statuses.append(
                _status_payload(
                    site_key=site_key,
                    site_name=site["name"],
                    state="cached",
                    message="Returned cached crawl results from this session.",
                    item_count=len(cached),
                    new_count=sum(1 for item in cached if item.get("is_new")),
                    from_cache=True,
                )
            )
            continue

        if site_key in CUSTOM_CRAWLERS:
            config = configs[site_key]
            try:
                previous_links = _previous_links(site_key)
                custom_items = await CUSTOM_CRAWLERS[site_key](config)
                shaped_items = [
                    _shape_item(config, item, crawl_time=crawl_time, previous_links=previous_links)
                    for item in custom_items
                ]
                _store_cache(config.site_key, shaped_items)
                items.extend(shaped_items)
                statuses.append(
                    _status_payload(
                        site_key=config.site_key,
                        site_name=config.name,
                        state="completed",
                        message="Crawl completed successfully.",
                        item_count=len(shaped_items),
                        new_count=sum(1 for item in shaped_items if item["is_new"]),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                statuses.append(_error_status(site_key, site["name"], str(exc)))
            continue

        to_crawl.append(configs[site_key])

    if to_crawl:
        engine = ScraperEngine(site_configs=to_crawl, timeout_seconds=90.0)
        results = await engine.scrape_all()
        result_map = {result.site_key: result for result in results}

        for config in to_crawl:
            result = result_map.get(config.site_key)
            if result is None:
                statuses.append(_error_status(config.site_key, config.name, "No crawl result was returned."))
                continue

            if result.error:
                statuses.append(_error_status(config.site_key, config.name, result.error))
                continue

            previous_links = _previous_links(config.site_key)
            shaped_items = [
                _shape_item(config, item, crawl_time=crawl_time, previous_links=previous_links)
                for item in result.items
            ]
            _store_cache(config.site_key, shaped_items)
            items.extend(shaped_items)
            statuses.append(
                _status_payload(
                    site_key=config.site_key,
                    site_name=config.name,
                    state="completed",
                    message="Crawl completed successfully.",
                    item_count=len(shaped_items),
                    new_count=sum(1 for item in shaped_items if item["is_new"]),
                )
            )

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

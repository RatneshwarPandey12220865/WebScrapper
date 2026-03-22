from __future__ import annotations

import json
import re
from pathlib import Path

from gov_aggregator.scrapers.schemas import SiteConfig, SiteSection


SCRAPER_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRAPER_DIR.parent / "data"
DEFAULT_CONFIG_PATH = DATA_DIR / "sites_config.json"
LEGACY_CONFIG_PATH = SCRAPER_DIR / "sites.json"

SELECTOR_KEYS = (
    "item_selector",
    "title_selector",
    "link_selector",
    "summary_selector",
    "date_selector",
    "row_selector",
    "wait_for_selector",
)

VALID_BACKENDS = {"bs4", "scrapy"}


def _config_path(config_path: str | Path | None = None) -> Path:
    if config_path:
        return Path(config_path)
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return LEGACY_CONFIG_PATH


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "site"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _parser_backend(site: dict) -> str:
    backend = (site.get("parser_backend") or site.get("parse_with") or "bs4").lower()
    if backend not in VALID_BACKENDS:
        raise ValueError(f"Unsupported parser backend: {backend}")
    return backend


def _selectors_from(site: dict) -> dict:
    selectors = dict(site.get("selectors", {}))
    for key in SELECTOR_KEYS:
        if key in site and key not in selectors:
            selectors[key] = site[key]
    return selectors


def _site_section(section: dict) -> SiteSection:
    """Parse a single section entry within a multi-section site config."""
    source_url = section.get("source_url") or section.get("url")
    if not source_url:
        raise ValueError(f"Section config missing source URL: {section}")
    return SiteSection(
        source_url=source_url,
        parser=section.get("parser") or section.get("parser_type", "list"),
        parser_backend=_parser_backend(section) if section.get("parser_backend") or section.get("parse_with") else "bs4",
        render_js=section.get("render_js", False),
        selectors=_selectors_from(section),
        default_category=section.get("default_category", "news"),
        section_label=section.get("section_label", ""),
        pagination_param=section.get("pagination_param"),
        start_page=section.get("start_page", 1),
        max_pages=section.get("max_pages", 1),
    )


def _site_config(site: dict) -> SiteConfig:
    ministry = site.get("ministry") or site.get("name") or "Unknown Ministry"
    name = site.get("name") or ministry
    site_key = site.get("site_key") or _slugify(name)

    # --- Multi-section support ---
    raw_sections = site.get("sections", [])
    sections = [_site_section(s) for s in raw_sections]

    # For single-section sites (no sections array), derive source_url from top-level
    source_url = site.get("source_url") or site.get("url") or ""
    if not source_url and not sections:
        raise ValueError(f"Site config missing source URL and sections: {site}")

    return SiteConfig(
        site_key=site_key,
        ministry=ministry,
        name=name,
        source_url=source_url,
        base_url=site.get("base_url", source_url),
        parser=site.get("parser") or site.get("parser_type", "list"),
        parser_backend=_parser_backend(site),
        render_js=site.get("render_js", False),
        active=site.get("active", True),
        selectors=_selectors_from(site),
        category_mapping=site.get("category_mapping", {}),
        default_category=site.get("default_category", "news"),
        pagination_param=site.get("pagination_param"),
        start_page=site.get("start_page", 1),
        max_pages=site.get("max_pages", 1),
        sections=sections,
    )


def load_site_configs(config_path: str | Path | None = None) -> list[SiteConfig]:
    source_path = _config_path(config_path)
    payload = _read_json(source_path)
    sites = payload.get("sites", [])
    return [_site_config(site) for site in sites if site.get("active", True)]


def config_metadata(config_path: str | Path | None = None) -> dict:
    source_path = _config_path(config_path)
    payload = _read_json(source_path)
    metadata = payload.get("metadata", {})
    metadata.setdefault("config_path", str(source_path))
    metadata.setdefault("site_count", len(payload.get("sites", [])))
    return metadata

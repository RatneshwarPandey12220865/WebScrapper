from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

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
    "pre_capture_js",
    "pre_capture_click",
    "allow_missing_link",
    "extra_links_selector",
    "exclude_title_pattern",
)

VALID_BACKENDS = {"bs4", "scrapy"}
logger = logging.getLogger("gov_aggregator.scrapers.config")


def _config_path(config_path: str | Path | None = None) -> Path:
    if config_path:
        return Path(config_path)
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return LEGACY_CONFIG_PATH


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "site"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sites_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        sites = payload.get("sites", [])
        if isinstance(sites, list):
            return sites
        raise ValueError("sites_config.json must contain a list under the 'sites' key.")
    raise ValueError("sites_config.json must be either a list of site configs or an object with a 'sites' list.")


def _metadata_from_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        metadata = payload.get("metadata", {})
        if isinstance(metadata, dict):
            return dict(metadata)
    return {}


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
        max_items=section.get("max_items"),
        verify_ssl=section.get("verify_ssl"),
        min_date=section.get("min_date"),
        date_format=section.get("date_format"),
    )


def _site_config(site: dict, metadata_defaults: dict | None = None) -> SiteConfig:
    metadata_defaults = metadata_defaults or {}
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
        max_items=site.get("max_items"),
        verify_ssl=site.get("verify_ssl", True),
        sections=sections,
        min_date=site.get("min_date"),
        date_format=site.get("date_format"),
        custom_crawler=site.get("custom_crawler"),
        extract_pdf_dates=bool(site.get("extract_pdf_dates", metadata_defaults.get("extract_pdf_dates_global", False))),
    )


def load_site_configs(config_path: str | Path | None = None) -> list[SiteConfig]:
    source_path = _config_path(config_path)
    payload = _read_json(source_path)
    sites = _sites_from_payload(payload)
    metadata = _metadata_from_payload(payload)
    configs = [_site_config(site, metadata) for site in sites if site.get("active", True)]

    counts: dict[str, int] = {}
    for config in configs:
        counts[config.site_key] = counts.get(config.site_key, 0) + 1

    duplicates = sorted(site_key for site_key, count in counts.items() if count > 1)
    if duplicates:
        logger.warning(
            "Duplicate site_key values found in sites_config.json; later lookups may overwrite earlier entries: %s",
            ", ".join(duplicates),
        )

    return configs


def config_metadata(config_path: str | Path | None = None) -> dict:
    source_path = _config_path(config_path)
    payload = _read_json(source_path)
    metadata = _metadata_from_payload(payload)
    metadata.setdefault("config_path", str(source_path))
    metadata.setdefault("site_count", len(_sites_from_payload(payload)))
    return metadata


def is_ssl_error(exception: Exception) -> bool:
    """Check if an exception is related to SSL/TLS errors."""
    error_msg = str(exception).lower()
    error_type = type(exception).__name__.lower()
    
    ssl_indicators = [
        "ssl",
        "tls",
        "certificate",
        "sslverify",
        "ssl_verification",
        "handshake",
        "wrapperexception",
        "endpointssl",
        "sslv3",
        "tlsv1",
        "certificate_verify_failed",
        "certificate verify failed",
        "ssl certificate",
        "ssl error",
        "unable to get local issuer certificate",
        "self signed certificate",
        "invalid certificate",
        "certificate has expired",
        "certificate not trusted",
        "root certificate",
        "httpsconnectionpool",
        "sslcertverificationerror",
        "sslerror",
    ]
    
    for indicator in ssl_indicators:
        if indicator in error_msg or indicator in error_type:
            return True
    
    ssl_exception_types = (
        "sslerror",
        "sslcertverificationerror",
        "sslopenssl",
        "ssl",
        "wrapperexception",
        "connecterror",
        "httpxconnecterror",
        "httpxsslcertverificationerror",
        "httpxsslerror",
        "urllib3sslerror",
        "requestexception",
    )
    
    if error_type in ssl_exception_types:
        return True
    
    return False


def auto_disable_ssl_verification(site_key: str, config_path: str | Path | None = None) -> bool:
    """
    Automatically disable SSL verification for a site that failed due to SSL errors.
    Returns True if the config was updated, False otherwise.
    """
    source_path = _config_path(config_path)

    try:
        payload = _read_json(source_path)
        sites = _sites_from_payload(payload)

        for site in sites:
            if site.get("site_key") != site_key:
                continue

            if site.get("verify_ssl") is False:
                return False

            site["verify_ssl"] = False

            if "sections" in site and site["sections"]:
                for section in site["sections"]:
                    section["verify_ssl"] = False

            payload_to_write: Any = sites
            if isinstance(payload, dict):
                updated_payload = dict(payload)
                metadata = _metadata_from_payload(payload)
                metadata["ssl_auto_fixed"] = True
                metadata["auto_fixed_sites"] = list(metadata.get("auto_fixed_sites", []))
                if site_key not in metadata["auto_fixed_sites"]:
                    metadata["auto_fixed_sites"].append(site_key)
                updated_payload["sites"] = sites
                updated_payload["metadata"] = metadata
                payload_to_write = updated_payload

            with open(source_path, "w", encoding="utf-8") as f:
                json.dump(payload_to_write, f, indent=2)

            return True

    except Exception:
        return False

    return False


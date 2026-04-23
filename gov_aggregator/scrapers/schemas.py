from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SiteSection:
    """Represents one crawlable URL section within a parent site."""
    source_url: str
    parser: str
    parser_backend: str = "bs4"
    render_js: bool = False
    selectors: dict[str, Any] = field(default_factory=dict)
    default_category: str = "news"
    section_label: str = ""  # e.g. "Notifications", "Press Releases"
    pagination_param: str | None = None
    start_page: int = 1
    max_pages: int = 1
    max_items: int | None = None
    verify_ssl: bool | None = None
    min_date: str | None = None


@dataclass(slots=True)
class SiteConfig:
    site_key: str
    ministry: str
    name: str
    source_url: str
    base_url: str
    parser: str
    parser_backend: str = "bs4"
    render_js: bool = False
    active: bool = True
    selectors: dict[str, Any] = field(default_factory=dict)
    category_mapping: dict[str, list[str]] = field(default_factory=dict)
    default_category: str = "news"
    pagination_param: str | None = None
    start_page: int = 1
    max_pages: int = 1
    max_items: int | None = None
    verify_ssl: bool = True
    # Optional multi-section support: when set, engine crawls all sections
    # and merges results under this single site_key.
    sections: list[SiteSection] = field(default_factory=list)
    min_date: str | None = None


@dataclass(slots=True)
class ScrapedItem:
    title: str
    link: str
    summary: str | None = None
    published_at: datetime | None = None
    is_pdf: bool = False
    section_label: str = ""  # filled in by engine when multi-section

"""
Validate a sites_config.json entry for common mistakes BEFORE running the scraper.

Usage:
    python -m gov_aggregator.validate_config <site_key>
    python -m gov_aggregator.validate_config --all
    python -m gov_aggregator.validate_config --json '<JSON config snippet>'

This tool checks for the most common configuration errors that cause silent
failures (0 items with no error). It catches issues BEFORE you run the scraper.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("validate_config")

# ANSI color codes for terminal output
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
SYMBOL_ERROR = "x"
SYMBOL_WARN = "!"
SYMBOL_OK = "+"

DATA_DIR = Path(__file__).resolve().parent / "data"
CONFIG_PATH = DATA_DIR / "sites_config.json"

VALID_PARSERS = {"list", "table", "pdf_index"}
VALID_BACKENDS = {"bs4", "scrapy"}

# Which selector keys each parser type expects for container selection
PARSER_CONTAINER_KEYS = {
    "list": "item_selector",
    "table": "row_selector",
    "pdf_index": "item_selector",
}


class ValidationResult:
    def __init__(self, site_key: str):
        self.site_key = site_key
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    def ok(self, msg: str):
        self.info.append(msg)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def print_report(self):
        header = f"\n{'='*60}\n  Validation: {self.site_key}\n{'='*60}"
        print(header)

        if self.errors:
            print(f"\n{RED}{BOLD}ERRORS ({len(self.errors)}):{RESET}")
            for e in self.errors:
                print(f"  {RED}✗ {e}{RESET}")

        if self.warnings:
            print(f"\n{YELLOW}{BOLD}WARNINGS ({len(self.warnings)}):{RESET}")
            for w in self.warnings:
                print(f"  {YELLOW}⚠ {w}{RESET}")

        if self.info:
            print(f"\n{GREEN}{BOLD}OK:{RESET}")
            for i in self.info:
                print(f"  {GREEN}✓ {i}{RESET}")

        if self.passed:
            print(f"\n{GREEN}{BOLD}Result: PASSED{RESET} — config looks valid")
        else:
            print(f"\n{RED}{BOLD}Result: FAILED{RESET} — fix the errors above before running")
        print()


def _print_report_safe(self: ValidationResult):
    header = f"\n{'='*60}\n  Validation: {self.site_key}\n{'='*60}"
    print(header)

    if self.errors:
        print(f"\n{RED}{BOLD}ERRORS ({len(self.errors)}):{RESET}")
        for error in self.errors:
            print(f"  {RED}{SYMBOL_ERROR} {error}{RESET}")

    if self.warnings:
        print(f"\n{YELLOW}{BOLD}WARNINGS ({len(self.warnings)}):{RESET}")
        for warning in self.warnings:
            print(f"  {YELLOW}{SYMBOL_WARN} {warning}{RESET}")

    if self.info:
        print(f"\n{GREEN}{BOLD}OK:{RESET}")
        for info in self.info:
            print(f"  {GREEN}{SYMBOL_OK} {info}{RESET}")

    if self.passed:
        print(f"\n{GREEN}{BOLD}Result: PASSED{RESET} - config looks valid")
    else:
        print(f"\n{RED}{BOLD}Result: FAILED{RESET} - fix the errors above before running")
    print()


ValidationResult.print_report = _print_report_safe


def _site_key_counts(sites: list[dict]) -> Counter[str]:
    return Counter(site.get("site_key", "") for site in sites if site.get("site_key"))


def _duplicate_site_keys(sites: list[dict]) -> dict[str, int]:
    counts = _site_key_counts(sites)
    return {site_key: count for site_key, count in counts.items() if count > 1}


def _config_integrity_result(sites: list[dict]) -> ValidationResult | None:
    duplicates = _duplicate_site_keys(sites)
    if not duplicates:
        return None

    result = ValidationResult("config-integrity")
    for site_key, count in sorted(duplicates.items()):
        result.error(
            f"Duplicate site_key '{site_key}' appears {count} times in sites_config.json - "
            "the runtime map silently keeps only one entry."
        )
    return result


def validate_site_config(site: dict, live_check: bool = False) -> ValidationResult:
    """Validate a single site config dict for common mistakes."""
    site_key = site.get("site_key") or site.get("name", "unknown")
    result = ValidationResult(site_key)

    # ── Check basic fields ──
    if not site.get("site_key"):
        result.error("Missing 'site_key' field")

    if not site.get("name") and not site.get("ministry"):
        result.error("Missing both 'name' and 'ministry' fields — at least one is required")

    # ── Check URL ──
    source_url = site.get("source_url") or site.get("url") or ""
    sections = site.get("sections", [])

    if not source_url and not sections:
        result.error("Missing 'url' (or 'source_url') and no 'sections' defined — nowhere to crawl")
    elif source_url:
        parsed = urlparse(source_url)
        if not parsed.scheme or not parsed.netloc:
            result.error(f"Invalid source URL: '{source_url}' — must start with http:// or https://")
        else:
            result.ok(f"Source URL: {source_url}")

    # ── Check base_url ──
    base_url = site.get("base_url", source_url)
    if not base_url:
        result.warn("No 'base_url' set — relative links in extracted items may not resolve correctly")
    elif not base_url.startswith("http"):
        result.error(f"Invalid 'base_url': '{base_url}' — must start with http:// or https://")

    # ── Check parser type ──
    parser_type = site.get("parser") or site.get("parser_type", "list")
    if parser_type not in VALID_PARSERS:
        result.error(f"Invalid parser type: '{parser_type}' — must be one of {VALID_PARSERS}")
    else:
        result.ok(f"Parser type: {parser_type}")

    # ── Check parser backend ──
    backend = (site.get("parser_backend") or site.get("parse_with") or "bs4").lower()
    if backend not in VALID_BACKENDS:
        result.error(f"Invalid parser_backend: '{backend}' — must be one of {VALID_BACKENDS}")
    else:
        result.ok(f"Parser backend: {backend}")

    # ── Check selectors (the most common source of failures) ──
    selectors = site.get("selectors", {})

    if not sections:
        # Non-section site must have selectors (unless it's a custom crawler)
        if not selectors:
            result.warn(
                "No 'selectors' defined — this will only work if the site has a custom crawler "
                "in scrapers/custom/. Otherwise, the parser will use defaults which likely won't match."
            )
        else:
            _validate_selectors(selectors, parser_type, result)

    # ── Check sections ──
    if sections:
        for i, section in enumerate(sections):
            sec_url = section.get("source_url") or section.get("url")
            sec_label = section.get("section_label", f"Section {i+1}")
            if not sec_url:
                result.error(f"Section '{sec_label}' is missing 'url' (or 'source_url')")
            
            sec_parser = section.get("parser") or section.get("parser_type", "list")
            sec_selectors = section.get("selectors", {})
            if sec_selectors:
                _validate_selectors(sec_selectors, sec_parser, result, prefix=f"[{sec_label}] ")
            else:
                result.warn(f"Section '{sec_label}' has no selectors — will use defaults")

            # Check for common section field name mistakes
            if "parser_type" in section and "parser" not in section:
                result.ok(f"Section '{sec_label}' uses 'parser_type': '{section['parser_type']}'")
            if "url" in section and "source_url" not in section:
                result.ok(f"Section '{sec_label}' uses 'url': {section['url']}")

    # ── Check render_js ──
    render_js = site.get("render_js", False)
    if render_js:
        wait_sel = selectors.get("wait_for_selector") or site.get("wait_for_selector")
        if not wait_sel:
            result.warn(
                "render_js=true but no 'wait_for_selector' — the page will be captured after "
                "a 2-second delay, which may not be enough for dynamic content"
            )
        else:
            result.ok(f"wait_for_selector: '{wait_sel}'")

    # ── Live check (optional) ──
    if live_check and source_url and not sections:
        _live_check(source_url, selectors, parser_type, render_js, result)
    elif live_check and sections:
        for i, section in enumerate(sections):
            sec_url = section.get("source_url") or section.get("url")
            sec_label = section.get("section_label", f"Section {i+1}")
            if sec_url:
                sec_selectors = section.get("selectors", {})
                sec_parser = section.get("parser") or section.get("parser_type", "list")
                sec_render_js = section.get("render_js", False)
                result.ok(f"  --- Live check for {sec_label} ---")
                _live_check(sec_url, sec_selectors, sec_parser, sec_render_js, result)

    return result


def _validate_selectors(selectors: dict, parser_type: str, result: ValidationResult, prefix: str = ""):
    """Check selector keys for common mistakes."""
    expected_key = PARSER_CONTAINER_KEYS.get(parser_type, "item_selector")
    wrong_key = "item_selector" if expected_key == "row_selector" else "row_selector"

    has_expected = expected_key in selectors and selectors[expected_key]
    has_wrong = wrong_key in selectors and selectors[wrong_key]

    # Bug #1: Wrong selector key for parser type
    if has_wrong and not has_expected:
        result.error(
            f"{prefix}parser_type='{parser_type}' expects '{expected_key}' but you used '{wrong_key}'. "
            f"RENAME '{wrong_key}' to '{expected_key}' in your config! "
            f"(The code will auto-remap at runtime, but fix your config to be correct.)"
        )
    elif has_expected:
        result.ok(f"{prefix}Container selector: {expected_key}='{selectors[expected_key]}'")
    else:
        defaults = {"item_selector": "li", "row_selector": "table tr"}
        result.warn(
            f"{prefix}No '{expected_key}' specified — will use default '{defaults.get(expected_key, '?')}'"
        )

    # Check link_selector
    link_sel = selectors.get("link_selector")
    if link_sel is None:
        # Check if the container selector points to an <a> tag
        container_sel = selectors.get(expected_key, "")
        if container_sel and (" a" in container_sel or container_sel.endswith("a") or container_sel.startswith("a")):
            result.ok(f"{prefix}link_selector is null — container IS an <a> tag, should work")
        else:
            result.warn(
                f"{prefix}link_selector is null/missing — the parser will try to get 'href' from "
                f"the container element itself. If the container is not an <a> tag, links will fail. "
                f"The code now has a fallback to find the first child <a>, but setting an explicit "
                f"link_selector is recommended."
            )
    elif link_sel:
        result.ok(f"{prefix}link_selector: '{link_sel}'")

    # Check title_selector
    title_sel = selectors.get("title_selector")
    if not title_sel:
        result.warn(
            f"{prefix}No 'title_selector' — will use the entire container text as title, "
            f"which typically includes unwanted text (dates, link labels, etc.)"
        )
    else:
        result.ok(f"{prefix}title_selector: '{title_sel}'")


def _live_check(url: str, selectors: dict, parser_type: str, render_js: bool, result: ValidationResult):
    """Fetch the URL and check if selectors actually match elements."""
    if render_js:
        result.warn(f"Live check skipped for {url} — render_js=true requires Playwright (use full scraper)")
        return

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        response = httpx.get(url, follow_redirects=True, timeout=20, headers=headers, verify=False)
        response.raise_for_status()
        html = response.text
        result.ok(f"HTTP {response.status_code} from {url} ({len(html)} bytes)")
    except Exception as exc:
        result.error(f"Live check failed — could not fetch {url}: {exc}")
        return

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Check container selector
    expected_key = PARSER_CONTAINER_KEYS.get(parser_type, "item_selector")
    container_sel = selectors.get(expected_key)
    # Also check wrong key as fallback
    if not container_sel:
        wrong_key = "item_selector" if expected_key == "row_selector" else "row_selector"
        container_sel = selectors.get(wrong_key)

    if not container_sel:
        defaults = {"item_selector": "li", "row_selector": "table tr"}
        container_sel = defaults.get(expected_key, "li")

    containers = soup.select(container_sel)
    if len(containers) == 0:
        result.error(
            f"LIVE CHECK: Selector '{container_sel}' matched 0 elements in the fetched HTML! "
            f"The selector is wrong, OR the page needs render_js:true."
        )
        # Show some of the html structure to help debug
        body = soup.find("body")
        if body:
            children = [f"<{tag.name}>" for tag in body.children if hasattr(tag, "name") and tag.name][:10]
            result.warn(f"  HTML body top-level children: {' '.join(children)}")
    else:
        result.ok(f"LIVE CHECK: '{container_sel}' matched {len(containers)} elements ✓")

        # Check child selectors on the first few containers
        if selectors.get("title_selector"):
            matched_titles = sum(1 for c in containers[:5] if c.select_one(selectors["title_selector"]))
            if matched_titles == 0:
                result.error(
                    f"LIVE CHECK: title_selector '{selectors['title_selector']}' matched 0 of first "
                    f"{min(5, len(containers))} containers!"
                )
            else:
                result.ok(f"LIVE CHECK: title_selector matched {matched_titles}/{min(5, len(containers))} containers")

        link_sel = selectors.get("link_selector")
        if link_sel:
            matched_links = sum(1 for c in containers[:5] if c.select_one(link_sel))
            if matched_links == 0:
                result.error(
                    f"LIVE CHECK: link_selector '{link_sel}' matched 0 of first "
                    f"{min(5, len(containers))} containers!"
                )
            else:
                result.ok(f"LIVE CHECK: link_selector matched {matched_links}/{min(5, len(containers))} containers")


def load_and_validate(site_key: str | None = None, validate_all: bool = False, 
                      json_snippet: str | None = None, live: bool = False) -> list[ValidationResult]:
    """Load config and validate one or all sites."""
    results = []

    if json_snippet:
        try:
            site = json.loads(json_snippet)
        except json.JSONDecodeError as exc:
            r = ValidationResult("json-input")
            r.error(f"Invalid JSON: {exc}")
            r.print_report()
            return [r]
        r = validate_site_config(site, live_check=live)
        r.print_report()
        return [r]

    if not CONFIG_PATH.exists():
        r = ValidationResult("config")
        r.error(f"Config file not found: {CONFIG_PATH}")
        r.print_report()
        return [r]

    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    sites = payload.get("sites", [])
    integrity_result = _config_integrity_result(sites)

    if validate_all:
        if integrity_result:
            results.append(integrity_result)
            integrity_result.print_report()
        for site in sites:
            if site.get("active", True):
                r = validate_site_config(site, live_check=live)
                results.append(r)
                r.print_report()
    elif site_key:
        duplicate_count = _site_key_counts(sites).get(site_key, 0)
        if duplicate_count > 1:
            r = ValidationResult(site_key)
            r.error(
                f"site_key '{site_key}' appears {duplicate_count} times in sites_config.json - "
                "runtime lookup may use the wrong definition."
            )
            results.append(r)
            r.print_report()
        found = False
        for site in sites:
            sk = site.get("site_key", "")
            if sk == site_key:
                r = validate_site_config(site, live_check=live)
                results.append(r)
                r.print_report()
                found = True
                break
        if not found:
            r = ValidationResult(site_key)
            r.error(f"Site key '{site_key}' not found in {CONFIG_PATH}")
            r.print_report()
            results.append(r)
    else:
        print("Usage:")
        print(f"  python -m gov_aggregator.validate_config <site_key>")
        print(f"  python -m gov_aggregator.validate_config --all")
        print(f"  python -m gov_aggregator.validate_config --json '<JSON>'")
        print(f"  Add --live to also fetch the URL and test selectors against real HTML")

    # Summary
    if len(results) > 1:
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        warned = sum(1 for r in results if r.warnings and r.passed)
        print(f"\n{'='*60}")
        print(f"  {BOLD}SUMMARY: {passed} passed, {failed} failed, {warned} with warnings{RESET}")
        print(f"{'='*60}\n")

    return results


def main():
    args = sys.argv[1:]
    
    if not args:
        load_and_validate()
        return

    validate_all = "--all" in args
    live = "--live" in args
    json_snippet = None
    site_key = None

    if "--json" in args:
        idx = args.index("--json")
        if idx + 1 < len(args):
            json_snippet = args[idx + 1]
        else:
            print("ERROR: --json requires a JSON string argument")
            sys.exit(1)
    else:
        # First non-flag arg is the site key
        for arg in args:
            if not arg.startswith("--"):
                site_key = arg
                break

    results = load_and_validate(
        site_key=site_key,
        validate_all=validate_all,
        json_snippet=json_snippet,
        live=live,
    )
    
    if results and any(not r.passed for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()

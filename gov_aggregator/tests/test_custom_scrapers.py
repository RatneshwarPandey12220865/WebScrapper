from gov_aggregator.scrapers.config import load_site_configs
from gov_aggregator.scrapers.custom import CUSTOM_CRAWLERS
from gov_aggregator.scrapers.custom.income_tax import _fallback_link, _parse_date
from gov_aggregator.services import get_site_catalog


def test_income_tax_custom_scraper_is_registered():
    sites = {site.site_key: site for site in load_site_configs()}

    assert "income-tax" in sites
    assert "income-tax" in CUSTOM_CRAWLERS


def test_income_tax_date_parser_handles_ordinal_dates():
    parsed = _parse_date("March 21st, 2026")

    assert parsed is not None
    assert parsed.isoformat() == "2026-03-21T00:00:00+00:00"


def test_income_tax_fallback_link_is_stable_and_unique_per_title():
    assert _fallback_link("CBDT Notification No. 12", 0) == (
        "https://incometaxindia.gov.in/Pages/communications/whats-new.aspx#cbdt-notification-no-12"
    )


def test_income_tax_appears_once_in_site_catalog():
    income_tax_sites = [site for site in get_site_catalog() if site["site_key"] == "income-tax"]

    assert len(income_tax_sites) == 1

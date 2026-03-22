from gov_aggregator.scrapers.config import load_site_configs
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS


def test_spices_board_uses_correct_subpath_urls():
    sites = {site.site_key: site for site in load_site_configs()}

    spices_board = sites["spices-board"]

    assert spices_board.base_url == "https://www.indianspices.com/indianspices"
    assert [section.source_url for section in spices_board.sections] == [
        "https://www.indianspices.com/indianspices/whats-new",
        "https://www.indianspices.com/indianspices/spice-news",
        "https://www.indianspices.com/indianspices/spice-news?page=2",
    ]


def test_default_headers_include_browser_like_accept_headers():
    assert DEFAULT_HEADERS["Accept"].startswith("text/html")
    assert DEFAULT_HEADERS["Accept-Language"] == "en-US,en;q=0.5"
    assert DEFAULT_HEADERS["Upgrade-Insecure-Requests"] == "1"

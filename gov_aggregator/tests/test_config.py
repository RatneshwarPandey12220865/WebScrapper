import json
from pathlib import Path

from gov_aggregator.scrapers.config import config_metadata, load_site_configs
from gov_aggregator.scrapers.engine import DEFAULT_HEADERS


def _sites_from_payload(payload):
    if isinstance(payload, list):
        return payload
    return payload["sites"]


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


def test_icar_config_disables_ssl_verification():
    sites = {site.site_key: site for site in load_site_configs()}

    assert sites["department-of-agricultural-research-and-education"].verify_ssl is False


def test_nhb_config_disables_ssl_verification():
    sites = {site.site_key: site for site in load_site_configs()}

    nhb = sites["national-housing-bank"]

    assert nhb.verify_ssl is False
    assert nhb.sections
    assert all(section.verify_ssl is False for section in nhb.sections)




def test_all_active_sites_have_url_or_sections():
    payload = json.loads(Path("gov_aggregator/data/sites_config.json").read_text(encoding="utf-8-sig"))

    invalid_sites = [
        site["site_key"]
        for site in _sites_from_payload(payload)
        if site.get("active", True) and not (site.get("source_url") or site.get("url") or site.get("sections"))
    ]

    assert invalid_sites == []


def test_load_site_configs_accepts_list_payload(tmp_path):
    config_path = tmp_path / "sites_config.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "site_key": "sample-site",
                    "name": "Sample Site",
                    "url": "https://example.gov.in",
                    "parser_type": "list",
                    "parser_backend": "bs4",
                    "selectors": {"item_selector": ".item"},
                }
            ]
        ),
        encoding="utf-8",
    )

    sites = load_site_configs(config_path)

    assert [site.site_key for site in sites] == ["sample-site"]
    assert config_metadata(config_path)["site_count"] == 1


def test_load_site_configs_accepts_object_payload(tmp_path):
    config_path = tmp_path / "sites_config.json"
    config_path.write_text(
        json.dumps(
            {
                "metadata": {"description": "test"},
                "sites": [
                    {
                        "site_key": "sample-site",
                        "name": "Sample Site",
                        "url": "https://example.gov.in",
                        "parser_type": "list",
                        "parser_backend": "bs4",
                        "selectors": {"item_selector": ".item"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    sites = load_site_configs(config_path)

    assert [site.site_key for site in sites] == ["sample-site"]
    assert config_metadata(config_path)["description"] == "test"


def test_agriculture_recent_page_has_pre_capture_js():
    sites = {site.site_key: site for site in load_site_configs()}

    section = next(
        section
        for section in sites["department-of-agriculture-and-farmers-welfare"].sections
        if section.section_label == "Recent Initiatives"
    )
    script = section.selectors["pre_capture_js"]

    assert "select[name='tblRecruitment_length']" in script
    assert "dispatchEvent(new Event('change'" in script
    assert "'100'" in script
    assert "tbody#tbodyRecruitment tr" in script


def test_fertilizers_row_selector_matches_live_table_shape():
    sites = {site.site_key: site for site in load_site_configs()}

    assert sites["department-of-fertilizers"].selectors["row_selector"] == "table.cols-8 tbody tr"


def test_ifsca_uses_static_html_and_caps_items():
    sites = {site.site_key: site for site in load_site_configs()}

    ifsca = sites["international-financial-services-centres-authority"]

    assert ifsca.render_js is False
    assert ifsca.max_items == 100
    assert ifsca.selectors["row_selector"] == "table#tblNewSec tbody tr"


def test_dgfscdhg_config_points_at_homepage_latest_updates():
    sites = {site.site_key: site for site in load_site_configs()}

    dgfscdhg = sites["directorate-general-fire-services-civil-defence-home-guards"]

    assert dgfscdhg.source_url == "https://dgfscdhg.gov.in/"
    assert dgfscdhg.parser == "list"
    assert dgfscdhg.selectors["item_selector"] == "div.region-latest-updatation li.news-item"


def test_dccd_config_disables_ssl_and_uses_aside_date():
    sites = {site.site_key: site for site in load_site_configs()}

    dccd = sites["directorate-of-cashewnut-cocoa-development"]

    assert dccd.verify_ssl is False
    assert dccd.render_js is False
    assert dccd.selectors["date_selector"] == "aside"


def test_midh_archive_section_has_pre_capture_click():
    sites = {site.site_key: site for site in load_site_configs()}

    archive = next(
        section
        for section in sites["mission-for-integrated-development-of-horticulture-schemes-guidelines"].sections
        if section.section_label == "Archive"
    )

    assert archive.selectors["pre_capture_click"] == "a.nav-link:has-text('Archive')"

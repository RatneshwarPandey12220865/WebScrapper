from gov_aggregator.scrapers.config import load_site_configs
from gov_aggregator.scrapers.schemas import ScrapedItem
from gov_aggregator.services import _clean_title, _shape_item


def test_clean_title_removes_esic_pdf_size_suffix():
    title = "Corrigendum-06 in DG ESIC RC NO. 161 (English)- PDF size:(435.68 KB) ."

    assert _clean_title(title) == "Corrigendum-06 in DG ESIC RC NO. 161 (English)"


def test_esic_config_includes_expected_archive_sections():
    sites = {site.site_key: site for site in load_site_configs()}

    esic = sites["esic"]

    assert esic.source_url == "https://esic.gov.in/circulars"
    assert len(esic.sections) == 36
    assert esic.sections[0].source_url == "https://esic.gov.in/newsevents"
    assert esic.sections[14].source_url == "https://esic.gov.in/NewsEvents/index/page:15"
    assert esic.sections[15].source_url == "https://esic.gov.in/circulars"
    assert esic.sections[-1].source_url == "https://esic.gov.in/circulars/index/page:21"


def test_shape_item_does_not_extract_publish_date_from_title_text():
    config = load_site_configs()[0]
    item = ScrapedItem(
        title="Reference to office order dated 07/04/2024 in background note",
        link="https://example.gov.in/item",
        summary=None,
        published_at=None,
        is_pdf=False,
    )

    shaped = _shape_item(config, item, crawl_time="2026-04-08T00:00:00+00:00", previous_links=set())

    assert shaped["publish_date"] is None


def test_shape_item_does_not_extract_publish_date_from_filename():
    config = load_site_configs()[0]
    item = ScrapedItem(
        title="Fresh circular",
        link="https://example.gov.in/files/fc_notice_07042025.pdf",
        summary=None,
        published_at=None,
        is_pdf=True,
    )

    shaped = _shape_item(config, item, crawl_time="2026-04-08T00:00:00+00:00", previous_links=set())

    assert shaped["publish_date"] is None

from gov_aggregator.scrapers.config import load_site_configs
from gov_aggregator.scrapers.custom import CUSTOM_CRAWLERS
from gov_aggregator.scrapers.custom.dgft import _parse_regulatory_updates_html
from gov_aggregator.scrapers.custom.dot import _extract_announcementbox_items
from gov_aggregator.scrapers.custom.fssai import _parse_recent_whatnew
from gov_aggregator.scrapers.custom.income_tax import _fallback_link, _parse_date
from gov_aggregator.scrapers.custom.meity import _extract_nic_announcementbox_items, _page_url
from gov_aggregator.services import get_site_catalog


def test_income_tax_custom_scraper_is_registered():
    sites = {site.site_key: site for site in load_site_configs()}

    assert "income-tax" in sites
    assert "income-tax" in CUSTOM_CRAWLERS


def test_fssai_custom_scraper_is_registered():
    sites = {site.site_key: site for site in load_site_configs()}

    assert "fssai" in sites
    assert "fssai" in CUSTOM_CRAWLERS


def test_fssai_recent_parser_handles_both_patterns_and_prefers_english_pdf():
    html = """
    <div class="drax">
      <h4 id="title10"><b>Press Note</b></h4>
      <p class="notifi_h5">Sample Press Note [Updated on:17-03-2026]</p>
      <ul>
        <li><a href="/upload/sample-hi.pdf" title="Hindi">Hindi</a></li>
        <li><a href="/upload/sample-en.pdf" title="English">English</a></li>
      </ul>
      <p class="mb10">
        <a href="https://fssai.gov.in/upload/orders/sample.pdf">
          Sample Advisory [Updated on:27-03-2026] <span class="fontsize">[0.12 MB]</span>
        </a>
      </p>
    </div>
    """

    items = _parse_recent_whatnew(html)

    assert len(items) == 2
    assert items[0].title == "Sample Press Note"
    assert items[0].link == "https://fssai.gov.in/upload/sample-en.pdf"
    assert items[0].section_label == "Press Note"
    assert items[1].title == "Sample Advisory"
    assert items[1].is_pdf is True


def test_fssai_recent_parser_skips_placeholder_links():
    html = """
    <div class="drax">
      <h4 id="title24"><b>Public Comments</b></h4>
      <p class="notifi_h5">Placeholder Item [Updated on:21-03-2026]</p>
      <ul>
        <li><a href="#" title="English">English</a></li>
      </ul>
    </div>
    """

    assert _parse_recent_whatnew(html) == []


def test_dot_custom_scraper_is_registered():
    sites = {site.site_key: site for site in load_site_configs()}

    assert "dot" in sites
    assert "dot" in CUSTOM_CRAWLERS
    assert sites["dot"].source_url == "https://www.dot.gov.in/whats-new"


def test_dot_announcementbox_parser_handles_dates_and_relative_pdf_links():
    html = """
    <div role="row" class="announcementbox">
      <div role="cell"><p class="mb-0">Spectrum Allocation Order</p></div>
      <div role="cell"><small class="ptype mb-0" aria-label="03.04.2026">03.04.2026</small></div>
      <div role="cell"><a class="download-btn" href="/sites/default/files/order.pdf">Download</a></div>
    </div>
    <div role="row" class="announcementbox">
      <div role="cell"><p class="mb-0">What's New Item</p></div>
      <div role="cell"><small class="ptype mb-0">1.2 MB</small></div>
      <div role="cell"><a class="download-btn" href="https://www.dot.gov.in/node/123">Open</a></div>
    </div>
    """

    items = _extract_announcementbox_items(
        html,
        base_url="https://www.dot.gov.in",
        section_label="Orders and Notices",
    )

    assert len(items) == 2
    assert items[0].title == "Spectrum Allocation Order"
    assert items[0].link == "https://www.dot.gov.in/sites/default/files/order.pdf"
    assert items[0].published_at is not None
    assert items[0].published_at.isoformat() == "2026-04-03T00:00:00+00:00"
    assert items[0].is_pdf is True
    assert items[1].published_at is None
    assert items[1].is_pdf is False


def test_meity_custom_scraper_is_registered():
    sites = {site.site_key: site for site in load_site_configs()}

    assert "meity" in sites
    assert "meity" in CUSTOM_CRAWLERS


def test_meity_page_url_includes_first_page_parameter():
    sites = {site.site_key: site for site in load_site_configs()}
    meity = sites["meity"]
    press = next(section for section in meity.sections if section.section_label == "Press Releases")

    assert _page_url(press, 1) == "https://www.meity.gov.in/documents/press-release?page=1"
    assert _page_url(press, 2) == "https://www.meity.gov.in/documents/press-release?page=2"


def test_meity_parser_scopes_whats_new_container_only():
    html = """
    <div class="whats-new-announcements">
      <div role="row" class="announcementbox row">
        <div role="cell"><p class="mb-0">Guidelines for the Cloud Selection Framework</p></div>
        <div role="cell"><small class="ptype mb-0">253.71 KB</small></div>
        <div role="cell">
          <a class="download-btn" type="pdf" href="https://www.meity.gov.in/static/uploads/2026/03/sample.pdf">View</a>
        </div>
      </div>
    </div>
    <div role="table" aria-label="careers_post data">
      <div role="row" class="announcementbox row">
        <div role="cell"><div class="mb-0 text-break">STQC Directorate is inviting applications</div></div>
        <div role="cell"><small class="ptype mb-0">08.10.2024</small></div>
        <div role="cell"><a class="link-btn" href="/offerings/vacancies/details/sample">Open</a></div>
      </div>
    </div>
    """

    items = _extract_nic_announcementbox_items(
        html,
        base_url="https://www.meity.gov.in",
        section_label="What's New",
        scope_selector="div.whats-new-announcements",
    )

    assert len(items) == 1
    assert items[0].title == "Guidelines for the Cloud Selection Framework"
    assert items[0].link == "https://www.meity.gov.in/static/uploads/2026/03/sample.pdf"
    assert items[0].published_at is None
    assert items[0].is_pdf is True


def test_meity_parser_extracts_dates_from_orders_rows():
    html = """
    <div role="row" class="announcementbox row">
      <div role="cell"><p class="mb-0">Delegation of Powers</p></div>
      <div role="cell"><small class="ptype mb-0" aria-label="11.03.2026">11.03.2026</small></div>
      <div role="cell">
        <a class="download-btn" type="pdf" href="https://www.meity.gov.in/static/uploads/2026/03/order.pdf">View</a>
      </div>
    </div>
    """

    items = _extract_nic_announcementbox_items(
        html,
        base_url="https://www.meity.gov.in",
        section_label="Orders and Notices",
    )

    assert len(items) == 1
    assert items[0].title == "Delegation of Powers"
    assert items[0].published_at is not None
    assert items[0].published_at.isoformat() == "2026-03-11T00:00:00+00:00"


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


def test_dgft_legacy_alias_maps_to_supported_scraper():
    dgft_sites = [site for site in get_site_catalog() if site["site_key"] == "dgft"]

    assert len(dgft_sites) == 1
    assert dgft_sites[0]["supported"] is True
    assert dgft_sites[0]["crawl_url"] == "https://www.dgft.gov.in/CP/"

    canonical_dgft = [
        site for site in get_site_catalog() if site["site_key"] == "directorate-general-of-foreign-trade"
    ]
    assert canonical_dgft == []


def test_dgft_custom_scraper_is_registered():
    sites = {site.site_key: site for site in load_site_configs()}

    assert "directorate-general-of-foreign-trade" in sites
    assert "directorate-general-of-foreign-trade" in CUSTOM_CRAWLERS


def test_dgft_regulatory_updates_parser_keeps_current_year_rows():
    html = """
    <table id="metadataTable">
      <tbody>
        <tr>
          <td>1</td>
          <td>05/2026-27</td>
          <td>2026-27</td>
          <td>Amendments to Para 2.62 of Foreign Trade Policy 2023</td>
          <td>07/04/2026</td>
          <td><a href="https://content.dgft.gov.in/doc1.pdf">Download</a></td>
        </tr>
        <tr>
          <td>2</td>
          <td>74/2025-26</td>
          <td>2025-26</td>
          <td>Continuation of RoDTEP Scheme beyond March 31, 2026</td>
          <td>31/03/2026</td>
          <td><a href="https://content.dgft.gov.in/doc2.pdf">Download</a></td>
        </tr>
        <tr>
          <td>3</td>
          <td>61/2024-25</td>
          <td>2024-25</td>
          <td>Old notice</td>
          <td>01/12/2024</td>
          <td><a href="https://content.dgft.gov.in/doc3.pdf">Download</a></td>
        </tr>
      </tbody>
    </table>
    """

    items = _parse_regulatory_updates_html(html, current_year=2026)

    assert len(items) == 2
    assert items[0].title == "Amendments to Para 2.62 of Foreign Trade Policy 2023"
    assert items[0].summary == "05/2026-27"
    assert items[0].link == "https://content.dgft.gov.in/doc1.pdf"
    assert items[0].published_at.isoformat() == "2026-04-07T00:00:00+00:00"
    assert items[0].section_label == "Regulatory Updates"
    assert items[1].title == "Continuation of RoDTEP Scheme beyond March 31, 2026"

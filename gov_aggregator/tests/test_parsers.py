import importlib.util
from datetime import datetime

from gov_aggregator.scrapers.parsers import _parse_date, extract_items
from gov_aggregator.scrapers.schemas import SiteConfig


HAS_SCRAPY = importlib.util.find_spec("scrapy") is not None


def make_config(parser: str, selectors: dict[str, str], parser_backend: str = "bs4") -> SiteConfig:
    return SiteConfig(
        site_key="test-site",
        ministry="Test Ministry",
        name="Test Site",
        source_url="https://example.gov.in/page",
        base_url="https://example.gov.in",
        parser=parser,
        parser_backend=parser_backend,
        selectors=selectors,
    )


def test_parse_list_items_bs4():
    html = """
    <ul>
      <li class="news-item"><a href="/notice-1.pdf">Notice One</a></li>
      <li class="news-item"><a href="/notice-2">Notice Two</a></li>
    </ul>
    """
    config = make_config(
        "list",
        {
            "item_selector": ".news-item",
            "title_selector": "a",
            "link_selector": "a",
        },
    )

    items = extract_items(config, html)

    assert len(items) == 2
    assert items[0].title == "Notice One"
    assert items[0].link == "https://example.gov.in/notice-1.pdf"
    assert items[0].is_pdf is True


def test_parse_table_items_bs4():
    html = """
    <table>
      <tbody>
        <tr>
          <td class="title"><a href="/circular-1">Circular One</a></td>
          <td class="date">21/03/2026</td>
        </tr>
      </tbody>
    </table>
    """
    config = make_config(
        "table",
        {
            "row_selector": "table tbody tr",
            "title_selector": ".title a",
            "link_selector": ".title a",
            "date_selector": ".date",
        },
    )

    items = extract_items(config, html)

    assert len(items) == 1
    assert items[0].title == "Circular One"
    assert items[0].link == "https://example.gov.in/circular-1"
    assert items[0].published_at is not None


def test_parse_table_items_bs4_with_plain_text_title_and_separate_document_link():
    html = """
    <table>
      <tbody>
        <tr>
          <td class="views-field-title">Administrative Approval Order</td>
          <td class="views-field-field-upload-notifications-docum-1">
            <a href="/files/approval-order.pdf">Download</a>
          </td>
          <td class="views-field-field-notifications-d">21-03-2026</td>
        </tr>
      </tbody>
    </table>
    """
    config = make_config(
        "table",
        {
            "row_selector": "table tbody tr",
            "title_selector": ".views-field-title",
            "link_selector": ".views-field-field-upload-notifications-docum-1 a, .views-field-title a",
            "date_selector": ".views-field-field-notifications-d",
        },
    )

    items = extract_items(config, html)

    assert len(items) == 1
    assert items[0].title == "Administrative Approval Order"
    assert items[0].link == "https://example.gov.in/files/approval-order.pdf"
    assert items[0].is_pdf is True
    assert items[0].published_at is not None


def test_parse_table_items_bs4_without_href_can_fall_back_to_source_url():
    html = """
    <table>
      <tbody>
        <tr>
          <td class="date">23rd April, 2024</td>
          <td class="title"><a>Allocation of funds to NLAs under MIDH for the FY 2024-25.</a></td>
        </tr>
      </tbody>
    </table>
    """
    config = make_config(
        "table",
        {
            "row_selector": "table tbody tr",
            "title_selector": ".title",
            "link_selector": ".title a",
            "date_selector": ".date",
            "allow_missing_link": True,
        },
    )

    items = extract_items(config, html)

    assert len(items) == 1
    assert items[0].title == "Allocation of funds to NLAs under MIDH for the FY 2024-25."
    assert items[0].link == "https://example.gov.in/page#allocation-of-funds-to-nlas-under-midh-for-the-fy-2024-25"


def test_parse_list_items_bs4_with_plain_h2_title_and_separate_download_link():
    html = """
    <div class="staffcat">
      <div class="staffrightcat1">
        <h2>Grant of All India First Licence for Hand-Held Motor-Operated Electric Tools Safety Part 2 Particular requirements Section 6 Hammers</h2>
        <h3>Type: pdf</h3>
        <h3>Published On: 24 Mar, 2026</h3>
        <a href="/files/bis-hammers.pdf">Download</a>
      </div>
    </div>
    """
    config = make_config(
        "list",
        {
            "item_selector": ".staffcat .staffrightcat1",
            "title_selector": "h2, h2 a",
            "link_selector": "h2 a, a[href]",
            "date_selector": "h3:last-of-type",
        },
    )

    items = extract_items(config, html)

    assert len(items) == 1
    assert items[0].title.startswith("Grant of All India First Licence")
    assert items[0].link == "https://example.gov.in/files/bis-hammers.pdf"
    assert items[0].published_at is not None


def test_parse_pdf_index_items_bs4():
    html = """
    <div>
      <a href="/files/press-release.pdf">Press Release PDF</a>
    </div>
    """
    config = make_config("pdf_index", {"item_selector": "a[href$='.pdf']"})

    items = extract_items(config, html)

    assert len(items) == 1
    assert items[0].title == "Press Release PDF"
    assert items[0].link == "https://example.gov.in/files/press-release.pdf"
    assert items[0].is_pdf is True


def test_parse_date_rolls_yearless_future_dates_back_one_year():
    parsed = _parse_date("30 Apr", reference_date=datetime(2026, 3, 30))

    assert parsed == datetime(2025, 4, 30)


def test_parse_date_keeps_explicit_year_unchanged():
    parsed = _parse_date("30 Apr 2022", reference_date=datetime(2026, 3, 30))

    assert parsed == datetime(2022, 4, 30)


def test_parse_table_items_scrapy():
    if not HAS_SCRAPY:
        return

    html = """
    <table>
      <tbody>
        <tr>
          <td class="title"><a href="/notification-1.pdf">Notification One</a></td>
          <td class="date">21/03/2026</td>
        </tr>
      </tbody>
    </table>
    """
    config = make_config(
        "table",
        {
            "row_selector": "table tbody tr",
            "title_selector": ".title a",
            "link_selector": ".title a",
            "date_selector": ".date",
        },
        parser_backend="scrapy",
    )

    items = extract_items(config, html)

    assert len(items) == 1
    assert items[0].title == "Notification One"
    assert items[0].link == "https://example.gov.in/notification-1.pdf"
    assert items[0].is_pdf is True

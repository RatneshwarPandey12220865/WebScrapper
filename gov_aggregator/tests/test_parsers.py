import importlib.util

from gov_aggregator.scrapers.parsers import extract_items
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

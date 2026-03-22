# Simple Scraping Tutorial

This project should stay simple.

Use `gov_aggregator` as the main codebase.
Ignore `govcrawler` for new work.
Ignore `database.py` and `models.py` for now because this workflow is local-file based.

## Files You Actually Need

- `gov_aggregator/data/known_sites.json`: master inventory of all sites you want to track
- `gov_aggregator/data/sites_config.json`: only the sites or sections that are currently supported and ready to crawl
- `gov_aggregator/scrapers/config.py`: loads site configs
- `gov_aggregator/scrapers/engine.py`: fetches HTML with `httpx` or `playwright`
- `gov_aggregator/scrapers/parsers.py`: extracts items from HTML
- `gov_aggregator/run_scraper.py`: quick local run

## Keep The Model Simple

Do not think in terms of one whole website.
Think in terms of one website section at a time.

Examples:

- `ministry-of-labour-notifications`
- `ministry-of-labour-circulars`
- `ministry-of-labour-press-releases`

Each section can have its own URL, parser type, parser backend, selectors, and default category.

## What We Use Now

This app is not using Scrapy spiders.

It uses:

- `httpx` to fetch static pages
- `playwright` to fetch JS pages
- `bs4` or `scrapy` selector parsing depending on `parser_backend`

Use this rule:

- `parser_backend: "bs4"` for simple HTML pages
- `parser_backend: "scrapy"` for selector-heavy pages where Scrapy-style CSS extraction is easier

## Supported Parser Types Right Now

- `list`
- `table`
- `pdf_index`

Use them like this:

- `list`: repeated links, cards, bullet items, notice lists
- `table`: rows inside an HTML table
- `pdf_index`: page is mostly a list of PDF links

## What To Look For On A Government Website

When you open a site, try to find these sections first:

- Notifications
- Circulars
- Notices
- Press Releases
- What's New
- Tenders
- Recruitment
- Orders
- Gazette
- Archive
- News / Media / Updates

## How To Inspect A Section

### 1. Open the section page

Example:

- `https://dfpd.gov.in/Home/WhatsNewViewAllList?languageId=1`
- `https://heavyindustries.gov.in/en/notifications`

### 2. Decide whether the page is static or JS-rendered

Use browser dev tools.

Signs that plain `httpx` is enough:

- you can see the items in `View Page Source`
- the HTML already contains the list or table rows
- items are present without scrolling or clicking filters

Signs that `playwright` is needed:

- page source is mostly empty but the browser shows items
- items appear only after scripts run
- page waits for a table to load
- links are inserted dynamically by JavaScript

In config:

- use `"render_js": false` for static pages
- use `"render_js": true` for JS-heavy pages

### 3. Decide whether to parse with bs4 or scrapy

Use `bs4` when:

- the page structure is simple
- you only need a few selectors
- the repeated items are easy to inspect

Use `scrapy` when:

- the page is table-heavy or selector-heavy
- Scrapy-style CSS handling works more cleanly
- `bs4` selectors become awkward

In config:

- use `"parser_backend": "bs4"`
- use `"parser_backend": "scrapy"`

## How To Choose A Parser Type

### Use `list`

If the page looks like:

- links in a list
- cards
- repeated blocks
- notice board entries

### Use `table`

If the page has rows in a table.

### Use `pdf_index`

If the page is mainly a list of PDF links.

## How To Add A New Site Section

1. Add the site to `known_sites.json`
2. Add one supported section to `sites_config.json`
3. Set `parser_type`
4. Set `parser_backend`
5. Add selectors
6. Run locally
7. Verify output
8. Then add the next section

## Example Config: bs4 list page

```json
{
  "site_key": "department-of-food-and-public-distribution",
  "ministry": "Department of Food and Public Distribution",
  "name": "Department of Food and Public Distribution",
  "url": "https://dfpd.gov.in/Home/WhatsNewViewAllList?languageId=1",
  "base_url": "https://dfpd.gov.in",
  "parser_type": "list",
  "parser_backend": "bs4",
  "render_js": false,
  "default_category": "notification",
  "selectors": {
    "item_selector": "#dataTable tbody tr td a"
  }
}
```

## Example Config: scrapy table page

```json
{
  "site_key": "department-of-heavy-industries",
  "ministry": "Ministry of Heavy Industries",
  "name": "Ministry of Heavy Industries",
  "url": "https://heavyindustries.gov.in/en/notifications",
  "base_url": "https://heavyindustries.gov.in",
  "parser_type": "table",
  "parser_backend": "scrapy",
  "render_js": false,
  "default_category": "notification",
  "selectors": {
    "row_selector": "table tbody tr",
    "title_selector": ".views-field-title a",
    "link_selector": ".views-field-field-upload-notifications-docum-1 a, .views-field-title a",
    "date_selector": ".views-field-field-notifications-d, .views-field-field-date"
  }
}
```

## Run Locally

```powershell
.\gov_env\Scripts\activate
uvicorn gov_aggregator.main:app --reload
```

## Simple Testing Workflow

Install test dependency:

```powershell
pip install -r gov_aggregator/requirements.txt -r gov_aggregator/requirements-dev.txt
```

Run tests:

```powershell
pytest gov_aggregator/tests -q
```

## Practical Rule For This Project

If a section can be scraped with one URL plus one parser type plus one parser backend plus a few selectors, include it.
If it needs a lot of custom logic, postpone it.

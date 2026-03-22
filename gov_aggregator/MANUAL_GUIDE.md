# Government Aggregator: Manual Guide

This guide provides both the **Setup & Run** instructions and the **Manual Website Integration Strategy** for the project.

---

## Part 1: Setup & Run Instructions

### Prerequisites
1.  **Python 3.11+**: Ensure you have Python installed.
2.  **Virtual Environment**: Use the pre-existing `venv` folder in the root directory.

### Quick Start
1.  **Activate Virtual Environment**:
    *   **CMD**: `venv\Scripts\activate`
    *   **PowerShell**: `.\venv\Scripts\activate`

2.  **Install Dependencies**:
    ```bash
    pip install -r gov_aggregator/requirements.txt
    ```

3.  **Install Browser (Playwright)**:
    ```bash
    playwright install msedge
    ```

4.  **Launch the Dashboard**:
    ```bash
    uvicorn gov_aggregator.main:app --reload
    ```
    Access at: `http://127.0.0.1:8000/static/index.html`

5.  **Manual Scraper Refresh**:
    ```bash
    python -m gov_aggregator.run_scraper
    ```

---

## Part 2: Manual Website Integration Strategy

### Workflow Steps

#### 1. Target Selection
Identify the specific URL for updates (e.g., "Notifications", "What's New"). Focus on archival or "View All" pages.

#### 2. Technical Inspection (F12)
*   **Source Check**: Use "View Page Source". If news items are present, use `render_js: false`. If missing, use `render_js: true`.
*   **Structure Identification**: Determine if the site uses a `<table>`, a `<ul>/<li>` list, or individual cards.

#### 3. Map CSS Selectors
Identify selectors for:
*   **Row/Item**: The repeating container for each entry.
*   **Title/Link/Date**: The specific fields within that container.

#### 4. Configure in `sites_config.json`
Add a new entry to the `sites` array in `gov_aggregator/data/sites_config.json`.

**Example Pattern:**
```json
{
  "site_key": "ministry-of-example",
  "ministry": "Ministry of Example",
  "url": "https://example.gov.in/notifications",
  "parser_type": "table",
  "parser_backend": "bs4",
  "render_js": false,
  "selectors": {
    "row_selector": "table tbody tr",
    "title_selector": "td.title",
    "link_selector": "td.title a",
    "date_selector": "td.date"
  }
}
```

#### 5. Verify & Refine
Run the scraper and check `data/latest_news.json`. Adjust selectors if data is truncated or missing.

# Government Aggregator

FastAPI + local JSON cache project for ministry updates, notifications, and PDF indexes.

## Stack

- FastAPI backend and static dashboard
- Local JSON storage in `data/latest_news.json`
- HTTPX for static pages
- Playwright for JS-heavy sites
- BeautifulSoup parsers for list, table, and PDF index layouts
- Background refresh via `multiprocessing`

## Layout

- `main.py`: FastAPI entrypoint and API routes
- `run_scraper.py`: manual cache refresh runner
- `services.py`: JSON cache and refresh status helpers
- `data/sites_config.json`: central JSON site inventory and selector map
- `scrapers/engine.py`: async scraper orchestration
- `scrapers/parsers.py`: parser implementations
- `static/`: responsive dashboard assets

## Configure Sites

Populate `data/sites_config.json` with your full 178-site ministry list before production use.

Supported parser types:

- `list`
- `table`
- `pdf_index`

Useful selector keys:

- `item_selector`
- `row_selector`
- `title_selector`
- `link_selector`
- `summary_selector`
- `date_selector`
- `wait_for_selector`

For DFPD-style tables, the parser also supports anchors that use `websiteurl` instead of `href`.

## Run

```bash
pip install -r gov_aggregator/requirements.txt
playwright install msedge
uvicorn gov_aggregator.main:app --reload
```

Manual refresh:

```bash
python -m gov_aggregator.run_scraper
```

## API

- `GET /api/health`
- `GET /api/news?ministry=...&limit=500`
- `GET /api/refresh/status`
- `POST /api/refresh`


## OCR for scanned PDFs (optional)

Digital (text-based) PDFs need no extra setup. **Scanned/image-only PDFs** use an
OCR fallback that requires two *system* tools (not pip packages):

- **Poppler** — used by `pdf2image` to render PDF pages.
  - Windows: download the Poppler build, unzip, add its `bin/` folder to `PATH`.
  - macOS: `brew install poppler`  • Debian/Ubuntu: `sudo apt install poppler-utils`
- **Tesseract OCR** — the OCR engine behind `pytesseract`.
  - Windows: install the Tesseract binary, add it to `PATH`.
  - macOS: `brew install tesseract`  • Debian/Ubuntu: `sudo apt install tesseract-ocr`

Then: `pip install pdf2image pytesseract opencv-python`

Without Poppler/Tesseract the extractor logs `ocr_render_failed` for scanned PDFs
and skips them gracefully — text PDFs are unaffected.

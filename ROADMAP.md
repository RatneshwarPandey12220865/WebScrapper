# KSyder 2.0 — Excel Export & Bulk Crawl Feature Roadmap

**Project:** KSyder 2.0 — Government Intelligence Platform  
**Goal:** Add bulk crawl-all capability, date-filtered result extraction, and Excel export (summary + per-site detail files) with PDF date extraction for items missing publish dates.  
**Safe Rollback Point:** Commit `fff7dc8` (pushed 2026-05-26)

---

## Table of Contents

1. [Phase 1 — Bulk "Crawl All" Trigger](#phase-1--bulk-crawl-all-trigger)
2. [Phase 2 — PDF Date Extraction](#phase-2--pdf-date-extraction)
3. [Phase 3 — Summary Excel File](#phase-3--summary-excel-file)
4. [Phase 4 — Per-Site Detailed Excel Files](#phase-4--per-site-detailed-excel-files)
5. [Phase 5 — Custom Date Range UI Controls](#phase-5--custom-date-range-ui-controls)
6. [Phase 6 — Job Persistence & History](#phase-6--job-persistence--history)
7. [Dependencies](#dependencies)
8. [Implementation Order](#implementation-order)
9. [Rollback Instructions](#rollback-instructions)

---

## Current State (Before This Roadmap)

| Area | Current Behavior | Gap |
|---|---|---|
| Crawl trigger | User manually selects site_keys in UI | No "crawl all active sites" button |
| Date filtering | `GLOBAL_MIN_DATE = 2026-01-01` hardcoded | No per-request date range control |
| PDF items | `published_at = None` for many PDF-only items | No date extraction from PDF content |
| Export | None | No Excel, no download |
| Job tracking | Crawl runs synchronously in HTTP request | No background jobs, no progress tracking |
| Result persistence | In-memory `SESSION_CACHE` (15 min TTL, lost on restart) | No durable job storage |

---

## Phase 1 — Bulk "Crawl All" Trigger

**Goal:** Add a single button/endpoint that crawls every active site without manual selection, with progress tracking since a full crawl can take 10–30 minutes.

---

### 1.1 — New Backend Endpoint: `POST /api/crawl/all`

**File:** `gov_aggregator/main.py`

- Reads all `site_key` values from `load_site_configs()` where `active=True`
- Passes the complete list into `crawl_site_keys()` (same function used by `/api/crawl`)
- Accepts optional request body:
  ```json
  {
    "use_cache": false,
    "date_from": "2026-05-26",
    "date_to": "2026-05-26"
  }
  ```
- Starts crawl as a **background task** (FastAPI `BackgroundTasks`) so the HTTP response returns immediately with a `job_id`
- Returns:
  ```json
  {
    "job_id": "abc123",
    "status": "started",
    "total_sites": 178,
    "message": "Crawl started. Poll /api/crawl/status/abc123 for progress."
  }
  ```

---

### 1.2 — Job Status Tracking

**File:** `gov_aggregator/services.py`

Add an in-memory (later DB-backed in Phase 6) job registry:

```python
ACTIVE_JOBS = {}
# Structure:
# {
#   "abc123": {
#     "status": "running",          # running | done | failed
#     "sites_total": 178,
#     "sites_done": 45,
#     "started_at": "2026-05-26T10:00:00",
#     "finished_at": None,
#     "date_from": "2026-05-26",
#     "date_to": "2026-05-26",
#     "error": None,
#     "result": None                # filled when done
#   }
# }
```

New function `run_bulk_crawl(job_id, site_keys, date_from, date_to)`:
- Loops through site_keys in batches of 10
- After each batch, updates `ACTIVE_JOBS[job_id]["sites_done"]`
- On completion, sets `status = "done"` and stores result
- On exception, sets `status = "failed"` and stores error message

---

### 1.3 — New Endpoint: `GET /api/crawl/status/{job_id}`

**File:** `gov_aggregator/main.py`

Returns current job state:
```json
{
  "job_id": "abc123",
  "status": "running",
  "sites_total": 178,
  "sites_done": 67,
  "percent_complete": 37.6,
  "started_at": "2026-05-26T10:00:00",
  "finished_at": null,
  "elapsed_seconds": 240
}
```

---

### 1.4 — Date Range Parameter Threading

**Files:** `gov_aggregator/services.py`, `gov_aggregator/scrapers/engine.py`, `gov_aggregator/scrapers/parsers.py`

Currently `crawl_site_keys()` applies only `GLOBAL_MIN_DATE` (hardcoded `2026-01-01`).

Changes:
- Add `date_from: str | None` and `date_to: str | None` parameters to `crawl_site_keys()`
- If provided, these override the global filter for this request only
- Thread the values down through `ScraperEngine` → `_apply_min_date()` in parsers
- `date_to` support (upper bound filtering) is new — add it to `_apply_min_date()`

```python
# New signature
async def crawl_site_keys(
    site_keys: list[str],
    use_cache: bool = True,
    date_from: str | None = None,   # overrides GLOBAL_MIN_DATE
    date_to: str | None = None      # new upper bound filter
) -> dict
```

---

### 1.5 — Frontend: "Crawl All Sites" Button

**Files:** `gov_aggregator/static/index.html`, `gov_aggregator/static/script.js`, `gov_aggregator/static/style.css`

UI additions:
- "Crawl All Sites" button in the navbar alongside existing "Crawl Selected"
- Progress bar modal that appears on click:
  - Shows: `Crawling 67 / 178 sites (37%)`
  - Live-updates by polling `GET /api/crawl/status/{job_id}` every 3 seconds
  - "Cancel" button (calls `POST /api/crawl/cancel/{job_id}`)
  - On completion: shows summary counts + "Download Summary Excel" button

---

### Phase 1 — Files to Create/Modify

| File | Action | What Changes |
|---|---|---|
| `gov_aggregator/main.py` | Modify | Add `/api/crawl/all`, `/api/crawl/status/{job_id}`, `/api/crawl/cancel/{job_id}` |
| `gov_aggregator/services.py` | Modify | `ACTIVE_JOBS` dict, `run_bulk_crawl()`, date_from/date_to params in `crawl_site_keys()` |
| `gov_aggregator/scrapers/parsers.py` | Modify | `_apply_min_date()` — add `date_to` upper bound |
| `gov_aggregator/static/index.html` | Modify | "Crawl All" button, progress bar modal HTML |
| `gov_aggregator/static/script.js` | Modify | `crawlAll()`, `pollJobStatus()`, progress bar update logic |
| `gov_aggregator/static/style.css` | Modify | Progress modal styles |

**Estimated effort:** 2–3 hours

---

## Phase 2 — PDF Date Extraction

**Goal:** For items where `published_at` is `None` (common for PDF-only items), automatically extract the publication date from the PDF document itself.

**Why this is needed:** Many Indian government websites list circulars and orders as bare PDF links with no date visible in the HTML. The date only exists inside the document itself — either as selectable text or as a scanned/printed image.

---

### 2.0 — PDF Types & What Tool Handles Each

Government PDFs fall into three tiers. The extractor handles all three:

| Tier | Description | How common | Tool used |
|---|---|---|---|
| 1 | Digital PDF — selectable text | ~60–70% | `pypdf` text extraction + regex |
| 2 | Scanned PDF — image only, clean print | ~25–30% | `pdf2image` → `pytesseract` OCR |
| 3 | Scanned PDF — skewed, noisy, low quality | ~5% | OpenCV preprocessing → `pytesseract` OCR |

**Why not CNN / deep learning for Tier 2–3?**
CNNs are designed for recognizing objects in natural images (faces, vehicles). A printed date like `15 May 2026` on a government letterhead is structured, typed text — Tesseract OCR handles this with 95%+ accuracy and has done so for decades. CNN would only be justified for handwritten dates or severely degraded documents, which are rare in official circulars. OpenCV is still used here but only as an image **pre-processor** (deskew, denoise, binarize) before passing to Tesseract — not for date recognition itself.

---

### 2.1 — Extraction Pipeline (Tier-by-Tier)

**File:** `gov_aggregator/scrapers/pdf_date_extractor.py` (new)

The extractor runs each tier in sequence, stopping at the first success:

---

#### Tier 1 — PDF Metadata (fastest, no download needed beyond header)

```
PDF files store XMP/DocInfo metadata in the file header.
Fields checked: /CreationDate, /ModDate
Format:         "D:20260515120000+05'30'" → parsed to 2026-05-15
Library:        pypdf (pure Python, zero binary dependencies)
Download:       first 8 KB only — metadata lives in the PDF header
```

```python
from pypdf import PdfReader
import io

async def _extract_from_metadata(pdf_bytes: bytes) -> date | None:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    meta = reader.metadata
    for field in ["/CreationDate", "/ModDate"]:
        raw = meta.get(field)
        if raw:
            # Format: D:YYYYMMDDHHmmSS or D:YYYYMMDD
            m = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw)
            if m:
                return date(int(m[1]), int(m[2]), int(m[3]))
    return None
```

---

#### Tier 1b — First-Page Text Extraction (digital PDFs with selectable text)

```
Extract raw text from page 1 only (first 800 characters).
Run regex patterns against the extracted text.
Library: pypdf (same dependency, no extra install)
```

Date patterns matched (in priority order):
```
DD Month YYYY     →  "15 May 2026", "15th May, 2026"
DD/MM/YYYY        →  "15/05/2026"
DD-MM-YYYY        →  "15-05-2026"
DD.MM.YYYY        →  "15.05.2026"
YYYY-MM-DD        →  "2026-05-15"
Month DD, YYYY    →  "May 15, 2026"
DD Mon YYYY       →  "15 May 26" (2-digit year, resolved to 20xx)
```

Take the **earliest valid date** found in the header region — circular/order dates are typically at the top of page 1, not embedded in body text.

---

#### Tier 2 — OCR on Scanned PDF (image-only, clean quality)

Triggered only when Tier 1 / 1b return no text (i.e. `pypdf` extracts 0 characters from page 1).

```
Step 1: Convert page 1 to a PIL image at 200 DPI
        Library: pdf2image (wraps poppler — system install needed)

Step 2: Crop top 25% of the image
        Most govt circulars have the date in the header/letterhead

Step 3: Pass cropped image directly to Tesseract OCR
        Library: pytesseract
        Config:  --psm 6 (assume uniform block of text)
                 lang=eng

Step 4: Run same regex patterns against OCR output text
```

```python
from pdf2image import convert_from_bytes
import pytesseract

async def _extract_via_ocr(pdf_bytes: bytes) -> date | None:
    images = convert_from_bytes(pdf_bytes, dpi=200, first_page=1, last_page=1)
    if not images:
        return None
    page = images[0]
    # Crop top 25% — date is almost always in the header
    w, h = page.size
    header = page.crop((0, 0, w, h // 4))
    text = pytesseract.image_to_string(header, config="--psm 6")
    return _parse_date_from_text(text)
```

---

#### Tier 3 — OpenCV Pre-processing + OCR (noisy/skewed scans)

Triggered when Tier 2 OCR returns no date (OCR produced text but no date matched).

```
Step 1: Convert PIL image to OpenCV numpy array (BGR)

Step 2: Pre-processing pipeline:
  a. Convert to grayscale
  b. Adaptive thresholding (binarize — black text on white bg)
  c. Deskew: detect skew angle via Hough line transform, rotate to correct
  d. Denoise: cv2.fastNlMeansDenoising()
  e. Upscale 1.5× if image resolution < 150 DPI

Step 3: Convert back to PIL, pass to Tesseract (same as Tier 2)

Step 4: Run regex patterns against output text
```

```python
import cv2
import numpy as np

def _preprocess_image(pil_img) -> "PIL.Image":
    img = np.array(pil_img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Adaptive threshold — handles uneven lighting in scans
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 10
    )
    # Deskew
    coords = np.column_stack(np.where(binary < 128))
    if len(coords) > 100:
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45: angle += 90
        (h, w) = binary.shape
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        binary = cv2.warpAffine(binary, M, (w, h), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
    denoised = cv2.fastNlMeansDenoising(binary, h=10)
    return Image.fromarray(denoised)
```

---

#### Strategy 4 — Filename Date (no download needed)

```
Run regex against the last segment of the PDF URL (filename).
Many govt PDFs embed dates in their filename:
  circular_15052026.pdf        → 15 May 2026
  notification_2026-05-15.pdf  → 15 May 2026
  order_May_15_2026.pdf        → 15 May 2026
  SO_1234_dt_15_5_2026.pdf     → 15 May 2026
```

---

#### Strategy 5 — URL Path Date (no download needed)

```
Run regex against the full URL path segments.
Many govt portals embed the upload date in the directory:
  /uploads/2026/05/15/circular.pdf  → 2026-05-15
  /docs/2026-05/notification.pdf    → 2026-05-01 (day unknown → 1st)
  /files/26052026/order.pdf         → 2026-05-26
```

Strategies 4 and 5 run **before** any PDF download — they cost zero network requests.

---

#### Strategy 6 — Fallback

```
Return None. Do not fabricate or guess a date.
Log at WARNING level: "PDF date extraction failed: {url} — all strategies exhausted"
Item publish_date stays None and passes through the date filter unchanged.
```

---

### 2.2 — Full Extraction Order (Optimised for Speed)

```
1. Strategy 4 — Filename regex        (0ms, no download)
2. Strategy 5 — URL path regex        (0ms, no download)
3. Download first 8 KB of PDF         (~100–300ms)
4. Tier 1  — PDF metadata             (parse in memory)
5. Tier 1b — pypdf text extraction    (parse in memory)
   → If text found AND date matched: done
   → If text found BUT no date: skip Tier 2/3 (it's a digital PDF with no date in header)
   → If NO text found: PDF is scanned → proceed to Tier 2
6. Download full page 1 as image      (~300–800ms additional)
7. Tier 2  — Direct OCR on header crop
   → If date found: done
   → If not: proceed to Tier 3
8. Tier 3  — OpenCV preprocessing → OCR
9. Strategy 6 — Fallback (return None)
```

---

### 2.3 — Caching Extracted Dates

**File:** `gov_aggregator/data/pdf_date_cache.json` (auto-created on first run)

```json
{
  "https://example.gov.in/docs/circular.pdf": {
    "date": "2026-05-15",
    "extracted_at": "2026-05-26T10:30:00",
    "strategy_used": "ocr_tier2",
    "confidence": "high"
  }
}
```

- Check cache **before** any download — zero cost on repeat crawls
- Cache is persistent across server restarts (JSON file on disk)
- PDF dates never change — entries never expire
- Cache written to disk every 10 new extractions (batched writes)
- `strategy_used` field: `metadata` / `text_tier1` / `filename` / `url_path` / `ocr_tier2` / `ocr_tier3`

---

### 2.4 — Integration Point

**File:** `gov_aggregator/services.py` → `_shape_item()`

```python
# After shaping item, if publish_date is still None:
if item.published_at is None and item.is_pdf and item.link:
    if config.extract_pdf_dates:  # opt-in per site config
        extracted = await extract_pdf_date(item.link)
        if extracted:
            item_dict["publish_date"] = extracted.isoformat()
            item_dict["date_source"] = "pdf_extracted"
```

- New field `date_source` added to shaped item: `"html"` (normal) vs `"pdf_extracted"`
- Only triggers when `published_at is None` AND `is_pdf=True` AND site has opted in
- Async — does not block other items being shaped in parallel

---

### 2.5 — Config Flag (Opt-In Per Site)

**File:** `gov_aggregator/data/sites_config.json`

```json
{
  "site_key": "rbi",
  "extract_pdf_dates": true
}
```

Set globally in the `metadata` block to enable for all sites:
```json
{
  "metadata": {
    "extract_pdf_dates_global": true
  }
}
```

Default is `false` — not forced on all 178 sites since PDF downloads add latency.

---

### 2.6 — System Dependencies (One-Time Setup)

```bash
# Windows
winget install oschwartz10612.poppler    # for pdf2image
# OR download poppler binaries and add to PATH

# pip packages
pip install pypdf>=4.0.0
pip install pdf2image>=1.17.0
pip install pytesseract>=0.3.13
pip install opencv-python>=4.9.0
pip install Pillow>=10.0.0              # already likely installed

# Tesseract OCR engine (separate install)
# Windows: https://github.com/UB-Mannheim/tesseract/wiki
# Add Tesseract to PATH after install
```

---

### Phase 2 — Files to Create/Modify

| File | Action | What Changes |
|---|---|---|
| `gov_aggregator/scrapers/pdf_date_extractor.py` | **Create** | Full 3-tier extractor: metadata → text → OCR → OpenCV+OCR → filename → URL |
| `gov_aggregator/data/pdf_date_cache.json` | **Create** | Auto-created on first run, persists across restarts |
| `gov_aggregator/services.py` | Modify | Call extractor in `_shape_item()`, add `date_source` field |
| `gov_aggregator/scrapers/schemas.py` | Modify | Add `extract_pdf_dates: bool = False` to `SiteConfig` |
| `gov_aggregator/scrapers/config.py` | Modify | Parse `extract_pdf_dates` from JSON config |
| `gov_aggregator/requirements.txt` | Modify | Add `pypdf`, `pdf2image`, `pytesseract`, `opencv-python` |

**Estimated effort:** 6–8 hours (increased from original due to 3-tier OCR pipeline)

---

## Phase 3 — Summary Excel File

**Goal:** After a crawl completes, generate one master Excel file showing per-site counts of updates within the selected date range, with ministry grouping, category breakdown, and color coding.

---

### 3.1 — Summary Excel Structure

**Filename:** `KSyder_Summary_{date_from}_to_{date_to}_{timestamp}.xlsx`

**Sheet 1: "Summary"**

| Col | Header | Description |
|---|---|---|
| A | Ministry | e.g. "Ministry of Finance" |
| B | Site Name | e.g. "Income Tax Department" |
| C | Site Key | e.g. "income-tax" |
| D | Total Items Found | All items returned by crawl |
| E | Items in Date Range | Items filtered to date_from–date_to |
| F | Circular | Count of category=circular |
| G | Tender | Count of category=tender |
| H | Recruitment | Count of category=recruitment |
| I | Notification | Count of category=notification |
| J | News | Count of category=news |
| K | Has PDF | "Yes" / "No" |
| L | Crawl Status | success / failed / cached / unsupported |
| M | Error Message | If status=failed, the error string |
| N | Crawl Time | Timestamp when site was crawled |

**Sheet 2: "Meta"**

| Field | Value |
|---|---|
| Generated At | 2026-05-26 10:30:00 |
| Date Range | 2026-05-26 to 2026-05-26 |
| Total Sites Crawled | 178 |
| Sites With Updates | 67 |
| Sites With No Updates | 98 |
| Sites Failed | 13 |
| Total Items Found | 1,245 |
| Total Items in Range | 312 |

---

### 3.2 — Excel Formatting Rules

- **Row 1:** Frozen header row, bold, dark navy background (`#1e3a5f`), white text
- **Column widths:** Auto-fitted to content, minimum 12, maximum 50
- **Ministry grouping:** Rows grouped by ministry, bold ministry name in first row of each group, light gray background for ministry header rows
- **Conditional formatting on column E (Items in Date Range):**
  - `0` → red background (`#ffcccc`)
  - `1–5` → yellow background (`#fff3cc`)
  - `>5` → green background (`#ccffcc`)
- **Status column:** "failed" → red text, "success" → dark green text, "cached" → blue text
- **Footer row:** Total counts across all columns, bold, gray background
- **Tab color:** Summary sheet = navy, Meta sheet = gray

---

### 3.3 — New Exporter Module

**File:** `gov_aggregator/exporters/excel_summary.py` (new)

Key function:
```python
async def generate_summary_excel(
    crawl_result: dict,         # output of crawl_site_keys()
    date_from: str,
    date_to: str,
    output_path: str            # where to save the file
) -> str:                       # returns the saved file path
```

Internal steps:
1. Group items by `site_key`
2. For each site: count total items, count items within date range, count per category
3. Merge with `site_statuses` from crawl result for status/error info
4. Build worksheet rows
5. Apply formatting (openpyxl styles)
6. Save to `output_path`

---

### 3.4 — New Export Endpoint

**File:** `gov_aggregator/main.py`

```
POST /api/export/summary
Body: { "job_id": "abc123", "date_from": "2026-05-26", "date_to": "2026-05-26" }
Response: streams the .xlsx file as a download
```

- Reads crawl result from `ACTIVE_JOBS[job_id]["result"]`
- Calls `generate_summary_excel()`
- Returns `FileResponse` with `Content-Disposition: attachment; filename=KSyder_Summary_...xlsx`

---

### Phase 3 — Files to Create/Modify

| File | Action | What Changes |
|---|---|---|
| `gov_aggregator/exporters/__init__.py` | **Create** | Empty init |
| `gov_aggregator/exporters/excel_summary.py` | **Create** | Summary Excel generator |
| `gov_aggregator/main.py` | Modify | `POST /api/export/summary` endpoint |
| `gov_aggregator/requirements.txt` | Modify | Add `openpyxl>=3.1.0` |
| `exports/` | **Create folder** | Output directory for generated files (gitignored) |

**Estimated effort:** 3–4 hours

---

## Phase 4 — Per-Site Detailed Excel Files

**Goal:** For every site that has at least one item in the selected date range, generate a separate Excel file containing full item details — title, date, description, document link, crawl time, section.

---

### 4.1 — Per-Site Excel Structure

**Filename:** `{Ministry}_{site_key}_{date_from}.xlsx`  
Example: `MinistryOfFinance_income-tax_2026-05-26.xlsx`

**Header Block (rows 1–4):**
```
Row 1: Ministry:    Ministry of Finance
Row 2: Site:        Income Tax Department
Row 3: Date Range:  2026-05-26 to 2026-05-26
Row 4: Total Items: 12  |  Crawl Time: 2026-05-26 10:30:45
Row 5: (blank separator)
Row 6: Column headers
```

**Column Layout (one sheet per section label, or "All" if no sections):**

| Col | Header | Description |
|---|---|---|
| A | # | Row number |
| B | Title | Full item title (word-wrapped) |
| C | Category | circular / tender / recruitment / notification / news |
| D | Published Date | `published_at` formatted as DD-Mon-YYYY |
| E | Crawl Time | When this item was scraped |
| F | Section | Section label (e.g. "Circulars", "Press Releases") |
| G | Description | `summary` field (word-wrapped, max 3 lines) |
| H | Document Link | Hyperlink: `=HYPERLINK("url","Open Document")` — blue underlined |
| I | Source Page | URL of the page where item was found |
| J | Is PDF | Yes / No |

---

### 4.2 — Multi-Section Handling

Sites like "Civil Aviation" have sections: Circulars, Orders, Notifications.

Options:
- **One sheet per section** — `Sheet: Circulars`, `Sheet: Orders`, etc.
- **One flat sheet** with Section column — simpler, chosen as default
- Config flag `split_by_section: true` on site config to override to multi-sheet

---

### 4.3 — Excel Formatting Rules

- Header block: merged cells, bold, navy background
- Column headers: bold, light blue background (`#d6e4f7`)
- Title column (B): width=50, word-wrap enabled
- Description column (G): width=60, word-wrap, max row height=60px
- Document Link column (H): blue underlined hyperlink style
- Category column (C): conditional color fill:
  - circular → light blue
  - tender → light orange
  - recruitment → light green
  - notification → light yellow
  - news → light gray
- Alternating row colors: white / very light gray (`#f8f8f8`)
- Freeze rows 1–6 (header block + column headers)
- Print area set to used range, landscape orientation

---

### 4.4 — Batch Generation & ZIP

**File:** `gov_aggregator/exporters/zip_builder.py` (new)

```python
async def generate_all_site_files(
    crawl_result: dict,
    date_from: str,
    date_to: str,
    output_dir: str
) -> str:  # returns path to ZIP file
```

- Loops all sites in crawl result that have `items_in_range > 0`
- Calls `generate_site_detail_excel()` for each
- Compresses all files + summary Excel into one ZIP
- ZIP filename: `KSyder_Export_{date_from}_to_{date_to}_{timestamp}.zip`

---

### 4.5 — New Download Endpoints

**File:** `gov_aggregator/main.py`

```
GET /api/export/site/{site_key}?job_id=abc123
  → streams single site Excel file

GET /api/export/all?job_id=abc123
  → streams ZIP of all site files + summary Excel
```

---

### Phase 4 — Files to Create/Modify

| File | Action | What Changes |
|---|---|---|
| `gov_aggregator/exporters/excel_site_detail.py` | **Create** | Per-site Excel generator |
| `gov_aggregator/exporters/zip_builder.py` | **Create** | ZIP bundler for all site files |
| `gov_aggregator/main.py` | Modify | `GET /api/export/site/{site_key}`, `GET /api/export/all` |
| `.gitignore` | Modify | Add `exports/` folder |

**Estimated effort:** 3–4 hours

---

## Phase 5 — Custom Date Range UI Controls

**Goal:** Give users full control over the date range used for filtering crawl results and exports, with quick presets and a custom date picker.

---

### 5.1 — Date Range Selector Widget

**Location:** Top of the dashboard, between the navbar and results area.

**Quick preset buttons:**
| Button | Behavior |
|---|---|
| Today | `date_from = date_to = today` |
| Yesterday | `date_from = date_to = yesterday` |
| Last 7 Days | `date_from = today-7, date_to = today` |
| This Month | `date_from = first of current month, date_to = today` |
| Custom Range | Shows two date pickers |

**Custom Range inputs:**
- `date_from` date picker (HTML `<input type="date">`)
- `date_to` date picker
- Validation: `date_from <= date_to`, neither can be in the future
- "Apply" button confirms the range

**State management:**
- Selected date range stored in `window.kspyder.dateRange = { from, to }`
- Persisted to `localStorage` so it survives page refresh
- Sent with every crawl request and export request

---

### 5.2 — "Updates Today" Count Badge

In the results table, add a badge per site row:
```
[Income Tax]  ████████  12 updates today
[RBI]         ████      4 updates today
[SEBI]        ░░░░      0 updates today  ← grayed out
```

- Badge shows count of items within selected date range
- Clicking the badge triggers download of that site's Excel file
- Sites with 0 updates in range shown at bottom, slightly grayed

---

### 5.3 — Export Controls Panel

Add a collapsible "Export" panel below the results table:

```
┌─────────────────────────────────────────────────┐
│  EXPORT OPTIONS                            [▼]  │
│                                                  │
│  Date Range: 2026-05-26  to  2026-05-26         │
│                                                  │
│  [Download Summary Excel]  [Download All (ZIP)] │
│                                                  │
│  Per-Site Downloads:                            │
│  Income Tax      12 items  [Download Excel]     │
│  RBI              4 items  [Download Excel]     │
└─────────────────────────────────────────────────┘
```

---

### 5.4 — IST Timezone Handling

All date comparisons must use IST (`+05:30`) not UTC.

- Frontend: all date inputs treated as IST, sent as `YYYY-MM-DD` strings (no time)
- Backend: when filtering by `date_from`/`date_to`, convert item `published_at` to IST before comparing date part
- Applies in: `_apply_min_date()` in parsers + export filter in `excel_summary.py`

---

### Phase 5 — Files to Create/Modify

| File | Action | What Changes |
|---|---|---|
| `gov_aggregator/static/index.html` | Modify | Date range widget HTML, export panel HTML |
| `gov_aggregator/static/script.js` | Modify | Date preset logic, `localStorage` persistence, export button handlers, badge rendering |
| `gov_aggregator/static/style.css` | Modify | Date picker styles, badge styles, export panel styles |
| `gov_aggregator/services.py` | Modify | IST timezone conversion in date filter |

**Estimated effort:** 2–3 hours

---

## Phase 6 — Job Persistence & History

**Goal:** Store crawl results durably on disk so they survive server restarts, and provide a job history screen where past crawls can be re-exported without re-crawling.

---

### 6.1 — Jobs Table in SQLite

**File:** `gov_aggregator/models.py`

New ORM model:
```python
class Job(Base):
    __tablename__ = "jobs"

    id          = Column(String, primary_key=True)     # UUID
    started_at  = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status      = Column(String)                       # running | done | failed
    date_from   = Column(String, nullable=True)        # YYYY-MM-DD
    date_to     = Column(String, nullable=True)        # YYYY-MM-DD
    sites_total = Column(Integer)
    sites_done  = Column(Integer, default=0)
    result_path = Column(String, nullable=True)        # path to JSON result file
    error       = Column(Text, nullable=True)
```

---

### 6.2 — Result File Storage

**Directory:** `gov_aggregator/data/jobs/`

After each bulk crawl completes:
- Serialize shaped items as JSON to `gov_aggregator/data/jobs/{job_id}.json`
- Update `Job.result_path` in DB
- Export endpoints read from this file (not in-memory dict)

File structure:
```json
{
  "job_id": "abc123",
  "crawl_time": "2026-05-26T10:30:45",
  "date_from": "2026-05-26",
  "date_to": "2026-05-26",
  "items": [...],
  "site_statuses": [...]
}
```

---

### 6.3 — Job History Endpoint

**File:** `gov_aggregator/main.py`

```
GET /api/jobs
  → list of past jobs, newest first
  → { jobs: [{ id, status, started_at, finished_at, sites_total, date_from, date_to, item_count }] }

GET /api/jobs/{job_id}
  → full job detail including site_statuses

DELETE /api/jobs/{job_id}
  → deletes job record + result JSON file
```

---

### 6.4 — Auto-Cleanup

**File:** `gov_aggregator/services.py`

- On server startup, delete `data/jobs/*.json` files older than 7 days
- Delete corresponding `Job` DB records
- Delete generated Excel files in `exports/` older than 7 days

---

### 6.5 — Job History UI

**File:** `gov_aggregator/static/index.html` + `script.js`

Add a "History" tab in the sidebar:
```
Recent Crawls
─────────────────────────────────
2026-05-26 10:30  Today  178 sites  312 items  [Re-Export]
2026-05-25 09:15  Yesterday  178 sites  289 items  [Re-Export]
2026-05-24 11:00  Custom Range  178 sites  401 items  [Re-Export]
```

"Re-Export" button: downloads Excel from the stored result without re-crawling.

---

### Phase 6 — Files to Create/Modify

| File | Action | What Changes |
|---|---|---|
| `gov_aggregator/models.py` | Modify | Add `Job` ORM model |
| `gov_aggregator/database.py` | Modify | `init_db()` creates jobs table |
| `gov_aggregator/services.py` | Modify | Persist job to DB + JSON, auto-cleanup on startup |
| `gov_aggregator/main.py` | Modify | `/api/jobs`, `/api/jobs/{id}`, `DELETE /api/jobs/{id}` |
| `gov_aggregator/static/index.html` | Modify | History tab HTML |
| `gov_aggregator/static/script.js` | Modify | History tab rendering, re-export logic |
| `gov_aggregator/data/jobs/` | **Create folder** | Job result storage (gitignored) |

**Estimated effort:** 3–4 hours

---

## Dependencies

Add to `gov_aggregator/requirements.txt`:

```
openpyxl>=3.1.0       # Excel file creation and formatting
pypdf>=4.0.0          # PDF metadata reading and text extraction
```

No other new dependencies required. All other functionality uses existing packages (`httpx`, `BeautifulSoup4`, `FastAPI`, `SQLAlchemy`).

---

## Implementation Order

```
Phase 1 (Bulk Crawl)
      ↓
Phase 3 (Summary Excel)
      ↓
Phase 4 (Per-Site Excel)
      ↓
Phase 5 (Date Range UI)
      ↓
Phase 2 (PDF Date Extraction)
      ↓
Phase 6 (Job Persistence)
```

**Why this order:**

| Phase | Why This Position |
|---|---|
| Phase 1 first | Foundation — all export phases need a job_id and bulk crawl result |
| Phase 3 before 4 | Summary is simpler; validates the openpyxl setup before detailed files |
| Phase 4 before 5 | Build the export first, then wire the UI controls to it |
| Phase 5 before 2 | Date range UI needed to properly test PDF date extraction results |
| Phase 2 near end | Most complex; safe to add after pipeline is stable |
| Phase 6 last | Polish / production-readiness; system works fine without it |

---

## Effort Summary

| Phase | New Files | Modified Files | Complexity | Estimated Hours |
|---|---|---|---|---|
| 1 — Bulk Crawl & Job Tracking | 0 | 6 | Medium | 2–3 hrs |
| 2 — PDF Date Extraction | 2 | 4 | High | 4–6 hrs |
| 3 — Summary Excel | 2 | 2 | Medium | 3–4 hrs |
| 4 — Per-Site Excel + ZIP | 2 | 2 | Medium | 3–4 hrs |
| 5 — Date Range UI | 0 | 4 | Low–Medium | 2–3 hrs |
| 6 — Job Persistence | 1 folder | 6 | Medium | 3–4 hrs |
| **Total** | **7 new files** | **~20 touches** | — | **17–24 hrs** |

---

## Rollback Instructions

If anything goes wrong at any phase, use these commands:

```bash
# View all commits (find the safe point)
git log --oneline

# Undo the last commit only (keeps file changes staged)
git revert HEAD

# Hard reset to the snapshot commit (DESTRUCTIVE — discards all uncommitted changes)
git reset --hard fff7dc8

# Reset to snapshot AND force-push (use only if remote is also broken)
git reset --hard fff7dc8
git push --force origin main

# Restore a single file to its state at the snapshot commit
git checkout fff7dc8 -- gov_aggregator/services.py
```

**Safe snapshot commit:** `fff7dc8`  
**Committed:** 2026-05-26  
**Contains:** All 58 custom scrapers, full config, no Excel/bulk-crawl code yet.

"""Document date extraction — universal pipeline.

Supports: PDF, DOCX, TXT, HTML, and raw text strings.
Format is auto-detected from magic bytes; filename hint improves accuracy.

Extraction pipeline (PDFs):

  Stage 0 — FREE (no download):
    • Filename regex
    • URL path regex

  Stage 1 — PDF text via PyMuPDF (fitz) [preferred] or pypdf [fallback]:
    • Metadata: CreationDate / ModDate
    • Full first-page text extraction (much better than pypdf for govt docs)

  Stage 2 — OCR for scanned PDFs:
    • Tesseract on top-30% header crop
    • OpenCV preprocessing + Tesseract (noisy/skewed scans)

Date scoring strategy:
  Every candidate date gets a score:
    +10  — found near a publication keyword ("dated", "issued on", etc.)
    +8   — found in the top header region (first ~300 chars of page text)
    +5   — appears more than once in the document
    +3   — from PDF metadata
    +2   — from filename / URL path
    -10  — future date (> today)
    -5   — very old date (< 2020)

  The highest-scoring candidate is returned.

Date extraction:
  Primary  — datefinder (NLP-based, handles almost any natural-language format)
  Fallback — custom regex patterns (handles compact / ambiguous Indian formats
             like "12052026" that datefinder misses)

Public API (unchanged):
  extract_date_from_text(text)          → date | None
  extract_date_from_bytes(content, ...)  → date | None
  extract_pdf_date(url)                 → date | None  (async, cached)
  extract_pdf_dates_batch(urls)         → dict         (async, concurrent)
  flush_cache()                         → None
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from gov_aggregator.scrapers.throttle import HOST_THROTTLE

logger = logging.getLogger("gov_aggregator.pdf_date_extractor")

# ── Optional heavy dependencies ────────────────────────────────────────────

try:
    import fitz as _fitz          # PyMuPDF
    _FITZ_OK = True
except ImportError:
    _FITZ_OK = False
    logger.debug("PyMuPDF (fitz) not installed — falling back to pypdf")

try:
    import datefinder as _datefinder
    _DATEFINDER_OK = True
except ImportError:
    _DATEFINDER_OK = False
    logger.debug("datefinder not installed — using regex-only date extraction")

try:
    from pypdf import PdfReader as _PdfReader
    import io as _io
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False
    logger.debug("pypdf not installed — PDF metadata/text extraction disabled")

try:
    from pdf2image import convert_from_bytes as _convert_from_bytes
    _PDF2IMAGE_OK = True
except ImportError:
    _PDF2IMAGE_OK = False
    logger.debug("pdf2image not installed — OCR tier disabled")

try:
    import pytesseract as _tesseract
    from PIL import Image as _PILImage
    _TESSERACT_OK = True
except ImportError:
    _TESSERACT_OK = False
    logger.debug("pytesseract not installed — OCR tier disabled")

try:
    import cv2 as _cv2
    import numpy as _np
    _CV2_OK = True
except ImportError:
    _CV2_OK = False
    logger.debug("opencv-python not installed — preprocessing tier disabled")

try:
    from ultralytics import YOLO as _YOLO
    _YOLO_OK = True
except ImportError:
    _YOLO_OK = False
    logger.debug("ultralytics not installed — YOLO date-region detection disabled")


# Path to the YOLO model weights used for text-region detection.
# Defaults to yolov8n.pt (auto-downloaded by ultralytics on first use).
# Override with a custom document-layout model for better accuracy, e.g.:
#   YOLO_MODEL_PATH = "/path/to/doc_layout_yolov8.pt"
YOLO_MODEL_PATH: str = "yolov8n.pt"

# Cached YOLO model instance — loaded once on first use.
_yolo_model: Any = None


def _get_yolo_model() -> Any | None:
    global _yolo_model
    if not _YOLO_OK:
        return None
    if _yolo_model is None:
        try:
            _yolo_model = _YOLO(YOLO_MODEL_PATH)
            logger.info("YOLO model loaded: %s", YOLO_MODEL_PATH)
        except Exception as exc:
            logger.warning("Failed to load YOLO model (%s): %s", YOLO_MODEL_PATH, exc)
            return None
    return _yolo_model


# Surface missing *primary* extractors loudly — these are listed in
# requirements.txt but are easy to forget when re-creating a venv. Without them
# the pipeline silently degrades to the weaker pypdf + regex path.
if not _FITZ_OK:
    logger.warning(
        "PyMuPDF (pymupdf/fitz) is NOT installed — PDF text extraction is "
        "degraded. Install with: pip install pymupdf"
    )
if not _DATEFINDER_OK:
    logger.warning(
        "datefinder is NOT installed — natural-language date parsing is "
        "degraded (regex-only). Install with: pip install datefinder"
    )
if not _YOLO_OK:
    logger.debug(
        "ultralytics not installed — YOLO region detection disabled "
        "(optional). Install with: pip install ultralytics"
    )

# ── Cache ──────────────────────────────────────────────────────────────────
_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "pdf_date_cache.json"
_cache: dict[str, Any] = {}
_cache_dirty = 0


def _load_cache() -> None:
    global _cache
    if _CACHE_PATH.exists():
        try:
            _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}


def _save_cache() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not save pdf_date_cache.json: %s", exc)


# Strategies that represent a *transient* failure (the server was down, the
# download stalled, rendering crashed). These are NOT cached, so the next run
# retries them — unlike a genuine "document has no date" result, which is cached
# permanently to avoid pointlessly re-downloading the same file.
_TRANSIENT_FAILURE_STRATEGIES = {
    "download_failed",
    "ocr_download_failed",
    "ocr_render_failed",
}


def _cache_put(url: str, result_date: str | None, strategy: str) -> None:
    global _cache_dirty
    if result_date is None and strategy in _TRANSIENT_FAILURE_STRATEGIES:
        # Don't persist transient failures — let the next crawl retry the URL.
        return
    _cache[url] = {
        "date": result_date,
        "strategy_used": strategy,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_dirty += 1
    if _cache_dirty >= 10:
        _save_cache()
        _cache_dirty = 0


_load_cache()

# ── Scoring ────────────────────────────────────────────────────────────────

@dataclass
class _Candidate:
    """A date candidate with an accumulated relevance score."""
    d: date
    score: int = 0
    sources: list[str] = field(default_factory=list)

    def add(self, points: int, label: str) -> "_Candidate":
        self.score += points
        self.sources.append(f"{label}({points:+d})")
        return self


# Publication-keyword scoring: lines that contain these words are very likely
# to carry the publication date.
_PUB_KEYWORDS = [
    "dated", "date:", "date -", "date–", "dt.", "dtd.",
    "published on", "publish date", "publication date",
    "issued on", "issue date",
    "notification date", "gazette date",
    "release date", "released on",
    "effective date", "circular date",
    "signed on", "order date",
    # Hindi / Devanagari keywords
    "दिनांक",       # dinaank — date (most common in govt docs)
    "तारीख",        # taareekh — date
    "प्रकाशन तिथि", # prakashan tithi — publication date
    "जारी दिनांक",  # jaari dinaank — issue date
    "दिनाँक",       # alternate spelling
]


def _keyword_score(line: str) -> int:
    """Return +10 if the line contains any publication keyword, else 0."""
    lo = line.lower()
    return 10 if any(k in lo for k in _PUB_KEYWORDS) else 0


def _is_future(d: date) -> bool:
    return d > date.today()


def _is_very_old(d: date) -> bool:
    return d.year < 2020


# ── Regex patterns (fallback when datefinder unavailable / misses compact) ─

_MONTHS = (
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
)

_DATE_LABEL = (
    r"(?:"
    r"(?:issue[d]?|publication|circular|order|notification|gazette|"
    r"office\s+order|letter|publish(?:ed)?|release[d]?|"
    r"sign(?:ed)?|effective|ref(?:erence)?|circular)\s+"
    r")?"
    r"(?:dat(?:ed?|\.)|dtd?\.?)"
    r"(?:\s+this)?"
    r"\s*[-:/–—.]*\s*"
)

_PLACE_LABEL = r"(?:[A-Z][a-zA-Z\s]{2,25},\s*(?:the\s+)?)"

_LABELLED_DATE_PATTERNS: list[tuple[str, str]] = [
    (_DATE_LABEL + r"(\d{1,2})(?:st|nd|rd|th)?(?:\s+day\s+of)?\s+" + _MONTHS + r",?\s+(20\d{2})\b", "lbl_dmy_text"),
    (_DATE_LABEL + _MONTHS + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",                        "lbl_mdy_text"),
    (_DATE_LABEL + r"(\d{1,2})[/\-.](\d{1,2})[/\-.](20\d{2})\b",                                     "lbl_numeric"),
    (_DATE_LABEL + r"(20\d{2})[/\-.](\d{2})[/\-.](\d{2})\b",                                         "lbl_iso"),
    (_PLACE_LABEL + r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTHS + r",?\s+(20\d{2})\b",                 "lbl_dmy_text"),
    (_PLACE_LABEL + _MONTHS + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",                       "lbl_mdy_text"),
]

_BARE_DATE_PATTERNS: list[tuple[str, str]] = [
    (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTHS + r",?\s+(20\d{2})\b", "dmy_text"),
    (r"\b" + _MONTHS + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", "mdy_text"),
    (r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b",                               "dmy_slash"),
    (r"(?<!\d)(\d{1,2})-(\d{1,2})-(20\d{2})\b",                          "dmy_dash"),
    (r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b",                             "dmy_dot"),
    (r"\b(20\d{2})-(\d{2})-(\d{2})\b",                                   "iso"),
    (r"\b(20\d{2})/(\d{2})/(\d{2})\b",                                   "iso_slash"),
    (r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)",                            "compact"),
]

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


def _try_date(year: int, month: int, day: int) -> date | None:
    try:
        d = date(year, month, day)
        today = date.today()
        if d.year < 2000 or d > date(today.year + 1, today.month, today.day):
            return None
        return d
    except ValueError:
        return None


def _parse_one_match(m: re.Match, kind: str) -> date | None:
    try:
        g = m.groups()
        if kind in ("dmy_text", "lbl_dmy_text"):
            return _try_date(int(g[-1]), _MONTH_MAP.get(str(g[-2]).lower()[:3], 0), int(g[-3]))
        if kind in ("mdy_text", "lbl_mdy_text"):
            return _try_date(int(g[-1]), _MONTH_MAP.get(str(g[-3]).lower()[:3], 0), int(g[-2]))
        if kind in ("dmy_slash", "dmy_dash", "dmy_dot"):
            return _try_date(int(g[-1]), int(g[-2]), int(g[-3]))
        if kind == "lbl_numeric":
            a, b, c = int(g[-3]), int(g[-2]), int(g[-1])
            return _try_date(a, b, c) if a >= 2000 else _try_date(c, b, a)
        if kind in ("iso", "iso_slash", "lbl_iso"):
            return _try_date(int(g[-3]), int(g[-2]), int(g[-1]))
        if kind == "compact":
            dd, mm, yyyy = int(g[0]), int(g[1]), int(g[2])
            if 1 <= dd <= 31 and 1 <= mm <= 12:
                return _try_date(yyyy, mm, dd)
    except (IndexError, ValueError, AttributeError, TypeError):
        pass
    return None


def _regex_dates_from_text(text: str) -> list[date]:
    """Extract dates using regex patterns — labelled patterns tried first."""
    labelled: list[date] = []
    for pattern, kind in _LABELLED_DATE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE | re.UNICODE):
            d = _parse_one_match(m, kind)
            if d:
                labelled.append(d)
    if labelled:
        return labelled

    found: list[date] = []
    for pattern, kind in _BARE_DATE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE | re.UNICODE):
            d = _parse_one_match(m, kind)
            if d:
                found.append(d)
    return found


def _datefinder_dates_from_text(text: str) -> list[date]:
    """
    Use datefinder (NLP-based) to extract dates from text.
    Returns a deduplicated list; filters invalid / far-future dates.
    """
    if not _DATEFINDER_OK:
        return []
    try:
        today = date.today()
        cutoff_future = date(today.year + 1, today.month, today.day)
        seen: set[date] = set()
        results: list[date] = []
        for dt in _datefinder.find_dates(text, source=False, index=False):
            d = dt.date() if hasattr(dt, "date") else dt
            if d.year < 2000 or d > cutoff_future:
                continue
            if d not in seen:
                seen.add(d)
                results.append(d)
        return results
    except Exception as exc:
        logger.debug("datefinder error: %s", exc)
        return []


_DEVANAGARI_DIGIT_MAP = str.maketrans("०१२३४५६७८९", "0123456789")

_HINDI_MONTH_MAP = {
    "जनवरी": "January", "फरवरी": "February", "मार्च": "March",
    "अप्रैल": "April",  "मई": "May",         "जून": "June",
    "जुलाई": "July",    "अगस्त": "August",   "सितंबर": "September",
    "सितम्बर": "September", "अक्टूबर": "October", "नवंबर": "November",
    "नवम्बर": "November",   "दिसंबर": "December", "दिसम्बर": "December",
}


def _normalise_hindi(text: str) -> str:
    """
    Convert Devanagari digits → ASCII and Hindi month names → English.
    Enables the existing regex + datefinder pipeline to parse Hindi-script dates
    like "२७ जनवरी २०२६" → "27 January 2026".
    """
    text = text.translate(_DEVANAGARI_DIGIT_MAP)
    for hindi, english in _HINDI_MONTH_MAP.items():
        text = text.replace(hindi, english)
    return text


def _clean_text(text: str) -> str:
    """Normalise whitespace and transliterate Hindi digits/months."""
    text = _normalise_hindi(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Core scoring engine ────────────────────────────────────────────────────

def _score_candidates_from_text(
    text: str,
    base_score: int = 0,
    source_label: str = "text",
) -> list[_Candidate]:
    """
    Extract all date candidates from text and assign scores.

    Scoring per candidate:
      base_score    — caller-supplied (e.g. +3 for metadata, +2 for filename)
      +10           — line containing the date has a publication keyword
      +8            — date found in the header region (first 300 chars)
      +5 per extra  — date appears more than once
      -10           — future date
      -5            — pre-2020 date
    """
    if not text:
        return []

    text = _clean_text(text)
    header_text = text[:300]

    # Gather raw dates from datefinder first, then regex for compact formats
    df_dates = _datefinder_dates_from_text(text)
    rx_dates = _regex_dates_from_text(text)

    # Merge — datefinder is primary; regex fills gaps (compact / ambiguous)
    all_dates: list[date] = []
    seen: set[date] = set()
    for d in df_dates + rx_dates:
        if d not in seen:
            seen.add(d)
            all_dates.append(d)

    if not all_dates:
        return []

    # Count occurrences of each date in the full text
    occurrence: dict[date, int] = {}
    for d in all_dates:
        # Check how many times the date's ISO form or natural forms appear
        count = len(re.findall(
            re.escape(d.strftime("%d/%m/%Y")) + "|" +
            re.escape(d.strftime("%d-%m-%Y")) + "|" +
            re.escape(d.strftime("%Y-%m-%d")),
            text
        ))
        occurrence[d] = max(count, 1)

    # Build candidates with scores
    candidates: list[_Candidate] = []
    lines = text.splitlines()

    for d in all_dates:
        c = _Candidate(d=d, score=base_score, sources=[source_label])

        # Future / old penalty
        if _is_future(d):
            c.add(-10, "future")
        elif _is_very_old(d):
            c.add(-5, "pre2020")

        # Header bonus -- only if this specific date appears in the header region
        header_variants = [
            d.strftime("%d/%m/%Y"), d.strftime("%d-%m-%Y"), d.strftime("%Y-%m-%d"),
            d.strftime("%d.%m.%Y"), f"{d.day} ", f" {d.year}",
        ]
        if any(v and v in header_text for v in header_variants):
            c.add(+8, "header_region")

        # Keyword bonus — scan lines for the date + keyword
        d_str_variants = [
            d.strftime("%d/%m/%Y"), d.strftime("%d-%m-%Y"),
            d.strftime("%Y-%m-%d"), d.strftime("%d %B %Y"),
            f"{d.day} {d.strftime('%B %Y')}",  # no leading zero, e.g. "9 June 2026"
            str(d.day), str(d.year),
        ]
        for line in lines:
            line_lo = line.lower()
            date_in_line = any(v and v in line for v in d_str_variants)
            if date_in_line:
                kw_pts = _keyword_score(line)
                if kw_pts:
                    c.add(kw_pts, "keyword")
                    break

        # Repetition bonus
        if occurrence.get(d, 1) > 1:
            c.add(+5, f"repeated×{occurrence[d]}")

        candidates.append(c)

    return candidates


def _best_date(candidates: list[_Candidate]) -> date | None:
    """Return the highest-scoring candidate's date, or None."""
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c.score)
    logger.debug(
        "Best date candidate: %s  score=%d  sources=%s",
        best.d, best.score, best.sources,
    )
    return best.d


# ── Free strategies (no download) ─────────────────────────────────────────

def _date_from_filename(url: str) -> date | None:
    try:
        filename = urlparse(url).path.split("/")[-1]
        filename = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
        candidates = _score_candidates_from_text(
            filename.replace("_", " ").replace("-", " "),
            base_score=2,
            source_label="filename",
        )
        return _best_date(candidates)
    except Exception:
        return None


def _date_from_url_path(url: str) -> date | None:
    try:
        path = urlparse(url).path
        m = re.search(r"/(20\d{2})/(\d{1,2})(?:/(\d{1,2}))?", path)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            day = int(m.group(3)) if m.group(3) else 1
            return _try_date(year, month, day)
        readable = path.replace("/", " ").replace("_", " ").replace("-", " ")
        candidates = _score_candidates_from_text(readable, base_score=2, source_label="url_path")
        return _best_date(candidates)
    except Exception:
        return None


# ── PDF text extraction — PyMuPDF (fitz) primary, pypdf fallback ───────────

def _fitz_extract_text_and_flag(content: bytes) -> tuple[str, bool]:
    """
    Extract text from page 1 using PyMuPDF.

    PyMuPDF is significantly better than pypdf for:
    - Multi-column government documents
    - Hindi + English mixed text
    - Tables and structured layouts
    - Headers / footers

    Returns (text, has_text).
    """
    if not _FITZ_OK:
        return "", False
    try:
        doc = _fitz.open(stream=content, filetype="pdf")
        if doc.page_count == 0:
            return "", False
        page = doc[0]
        text = page.get_text("text")          # plain text, layout-aware
        doc.close()
        return text[:3000], len(text.strip()) > 20
    except Exception as exc:
        logger.debug("PyMuPDF text extraction failed: %s", exc)
        return "", False


def _fitz_metadata_date(content: bytes) -> date | None:
    """Read creation/modification date from PDF metadata via PyMuPDF."""
    if not _FITZ_OK:
        return None
    try:
        doc = _fitz.open(stream=content, filetype="pdf")
        meta = doc.metadata or {}
        doc.close()
        for field in ("creationDate", "modDate"):
            raw = meta.get(field, "")
            if not raw:
                continue
            m = re.match(r"D?:?(\d{4})(\d{2})(\d{2})", raw)
            if m:
                d = _try_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if d:
                    return d
    except Exception:
        pass
    return None


def _pypdf_extract_text_and_flag(content: bytes) -> tuple[str, bool]:
    """Fallback text extraction using pypdf."""
    if not _PYPDF_OK:
        return "", False
    try:
        reader = _PdfReader(_io.BytesIO(content))
        if not reader.pages:
            return "", False
        text = reader.pages[0].extract_text() or ""
        return text[:2000], len(text.strip()) > 20
    except Exception:
        return "", False


def _pypdf_metadata_date(content: bytes) -> date | None:
    """Fallback: read PDF metadata via pypdf."""
    if not _PYPDF_OK:
        return None
    try:
        reader = _PdfReader(_io.BytesIO(content))
        meta = reader.metadata or {}
        for field in ("/CreationDate", "/ModDate", "CreationDate", "ModDate"):
            raw = meta.get(field)
            if not raw:
                continue
            m = re.match(r"D?:?(\d{4})(\d{2})(\d{2})", str(raw))
            if m:
                d = _try_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if d:
                    return d
    except Exception:
        pass
    return None


def _extract_text_and_flag(content: bytes) -> tuple[str, bool]:
    """Try PyMuPDF first, fall back to pypdf."""
    if _FITZ_OK:
        return _fitz_extract_text_and_flag(content)
    return _pypdf_extract_text_and_flag(content)


def _extract_metadata_date(content: bytes) -> date | None:
    """Try PyMuPDF first, fall back to pypdf."""
    if _FITZ_OK:
        return _fitz_metadata_date(content)
    return _pypdf_metadata_date(content)


# ── OCR pipeline ───────────────────────────────────────────────────────────

def _ocr_image(pil_image: Any) -> str:
    if not _TESSERACT_OK:
        return ""
    try:
        return _tesseract.image_to_string(pil_image, config="--psm 6 --oem 3")
    except Exception:
        return ""


def _crop_header(pil_image: Any) -> Any:
    """Crop top 30% — publication dates live in the header of govt documents."""
    w, h = pil_image.size
    return pil_image.crop((0, 0, w, int(h * 0.30)))


def _preprocess_image(pil_image: Any) -> Any:
    """Deskew + binarize + denoise for noisy scanned documents."""
    if not _CV2_OK:
        return pil_image
    try:
        img = _np.array(pil_image.convert("RGB"))
        gray = _cv2.cvtColor(img, _cv2.COLOR_RGB2GRAY)
        binary = _cv2.adaptiveThreshold(
            gray, 255,
            _cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            _cv2.THRESH_BINARY, 31, 10,
        )
        coords = _np.column_stack(_np.where(binary < 128))
        if len(coords) > 100:
            rect = _cv2.minAreaRect(coords)
            angle = rect[-1]
            if angle < -45:
                angle += 90
            h2, w2 = binary.shape
            M = _cv2.getRotationMatrix2D((w2 / 2, h2 / 2), angle, 1.0)
            binary = _cv2.warpAffine(
                binary, M, (w2, h2),
                flags=_cv2.INTER_CUBIC,
                borderMode=_cv2.BORDER_REPLICATE,
            )
        denoised = _cv2.fastNlMeansDenoising(binary, h=10)
        return _PILImage.fromarray(denoised)
    except Exception:
        return pil_image


def _date_from_ocr(page_image: Any, preprocess: bool = False) -> date | None:
    """Run OCR on the header crop and extract the best-scored date."""
    header = _crop_header(page_image)
    if preprocess and _CV2_OK:
        header = _preprocess_image(header)
    text = _ocr_image(header)
    if not text:
        return None
    candidates = _score_candidates_from_text(text, base_score=0, source_label="ocr")
    return _best_date(candidates)


# ── YOLO region detection ──────────────────────────────────────────────────

def _yolo_detect_regions(pil_image: Any, conf_threshold: float = 0.25) -> list[tuple[int, int, int, int]]:
    """
    Run YOLO on a PIL image and return bounding boxes of detected regions.

    Returns a list of (x1, y1, x2, y2) tuples sorted top-to-bottom.
    Boxes in the bottom 50% of the page are discarded — publication dates
    appear in headers / top sections of government documents.
    """
    model = _get_yolo_model()
    if model is None or not _CV2_OK:
        return []

    try:
        img_w, img_h = pil_image.size
        half_h = img_h // 2

        # Convert PIL → numpy for ultralytics
        img_np = _np.array(pil_image.convert("RGB"))
        results = model(img_np, conf=conf_threshold, verbose=False)

        boxes: list[tuple[int, int, int, int]] = []
        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes.xyxy.tolist():
                x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
                # Skip regions entirely in the bottom half
                if y1 > half_h:
                    continue
                # Clamp to image bounds
                x1 = max(0, x1)
                y1 = max(0, y1)
                x2 = min(img_w, x2)
                y2 = min(img_h, y2)
                if x2 > x1 and y2 > y1:
                    boxes.append((x1, y1, x2, y2))

        # Sort top-to-bottom so header regions are tried first
        boxes.sort(key=lambda b: b[1])
        logger.debug("YOLO detected %d regions in top half of page", len(boxes))
        return boxes

    except Exception as exc:
        logger.debug("YOLO detection failed: %s", exc)
        return []


def _date_from_yolo_ocr(pil_image: Any) -> date | None:
    """
    Use YOLO to detect text regions, crop each, OCR it, and score for dates.

    This is Stage 2c — runs after direct-OCR and preprocessed-OCR both fail.
    It is especially effective on:
      - Multi-column layouts where the date is in a right-side header box
      - Scanned PDFs where the date stamp is a separate visual element
      - Documents with a top-right "Dated:" box alongside a large logo
    """
    if not (_YOLO_OK and _TESSERACT_OK and _CV2_OK):
        return None

    regions = _yolo_detect_regions(pil_image)
    if not regions:
        # Fall back to full top-30% crop if YOLO found nothing
        regions = [(0, 0, pil_image.size[0], int(pil_image.size[1] * 0.30))]

    all_candidates: list[_Candidate] = []

    for x1, y1, x2, y2 in regions:
        try:
            crop = pil_image.crop((x1, y1, x2, y2))
            # Upscale small crops — tesseract accuracy drops below ~30px height
            w, h = crop.size
            if h < 60:
                scale = max(2, 60 // h)
                crop = crop.resize((w * scale, h * scale))

            # Try raw OCR first, then preprocessed
            for preprocess in (False, True):
                img = _preprocess_image(crop) if preprocess else crop
                text = _ocr_image(img)
                if text:
                    candidates = _score_candidates_from_text(
                        text,
                        base_score=1,
                        source_label=f"yolo_ocr{'_pp' if preprocess else ''}",
                    )
                    all_candidates.extend(candidates)
                    if candidates:
                        break  # preprocessed not needed if raw OCR found dates
        except Exception as exc:
            logger.debug("YOLO region OCR failed for box %s: %s", (x1, y1, x2, y2), exc)

    result = _best_date(all_candidates)
    if result:
        logger.info("YOLO OCR extracted date: %s (%d regions tried)", result, len(regions))
    return result


# ── PDF download helpers ───────────────────────────────────────────────────

async def _download_bytes(url: str, max_bytes: int = 10_485_760) -> bytes | None:
    """Download a document for parsing.

    IMPORTANT: we must NOT fetch only a leading byte-range. A PDF's
    cross-reference table and EOF marker live at the *end* of the file, so a
    truncated download (e.g. the first 64 KB) cannot be parsed by PyMuPDF or
    pypdf -- it fails with "EOF marker not found". We therefore stream the whole
    file, stopping only if it exceeds ``max_bytes`` (default 10 MB), in which
    case we return None rather than a corrupt partial document.
    """
    try:
        # Politeness: space out downloads to the same host. A single ministry
        # can have dozens of dateless PDFs — without this they'd all be fetched
        # in a rapid burst, the most likely trigger for an IP ban.
        await HOST_THROTTLE.wait(url)
        async with httpx.AsyncClient(verify=False, timeout=30.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in r.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        logger.debug("PDF exceeds %d bytes, skipping: %s", max_bytes, url)
                        return None
                return b"".join(chunks)
    except Exception as exc:
        logger.debug("PDF download failed (%s): %s", url, exc)
    return None


async def _download_full_first_page(url: str, max_bytes: int = 10_485_760) -> bytes | None:
    """Download the full document for OCR rendering.

    Like ``_download_bytes``, this must return a complete file -- pdf2image /
    poppler also need a valid trailer to render any page.
    """
    return await _download_bytes(url, max_bytes=max_bytes)


def _render_page1(content: bytes) -> Any | None:
    if not _PDF2IMAGE_OK:
        return None
    try:
        images = _convert_from_bytes(content, dpi=200, first_page=1, last_page=1)
        return images[0] if images else None
    except Exception as exc:
        logger.debug("pdf2image render failed: %s", exc)
        return None


# ── Main public API ────────────────────────────────────────────────────────

async def extract_pdf_date(url: str) -> date | None:
    """
    Extract a publication date from any government document URL.

    Works for PDFs, DOCX, HTML pages, and plain text. Format is auto-detected
    from magic bytes after download.

    Priority order (first success wins):
      1. Filename regex (free, no download)
      2. URL path regex (free, no download)
      3. For non-PDF content (HTML, DOCX, TXT): universal text scorer
      4. PDF metadata (fitz > pypdf)
      5. Page-1 text extraction (fitz > pypdf) + scored date selection
      6. OCR on header crop (scanned PDFs)
      7. OCR with OpenCV preprocessing (noisy/skewed scans)
      8. YOLO region detection + per-region OCR

    Results are cached to data/pdf_date_cache.json.
    """
    if not url:
        return None

    cached = _cache.get(url)
    if cached is not None:
        raw = cached.get("date")
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                pass
        return None

    result: date | None = None
    strategy = "none"

    # Stage 0 — free strategies (work for any URL format)
    result = _date_from_filename(url)
    if result:
        strategy = "filename"
        _cache_put(url, result.isoformat(), strategy)
        logger.debug("Doc date via %s: %s → %s", strategy, url, result)
        return result

    result = _date_from_url_path(url)
    if result:
        strategy = "url_path"
        _cache_put(url, result.isoformat(), strategy)
        logger.debug("Doc date via %s: %s → %s", strategy, url, result)
        return result

    # Stage 1 — download the document.
    # For PDFs: must download the WHOLE file — xref table and EOF marker live
    # at the end, so a truncated download fails with "EOF marker not found".
    content = await _download_bytes(url)
    if not content:
        _cache_put(url, None, "download_failed")
        return None

    # Auto-detect format from magic bytes
    filename = urlparse(url).path.split("/")[-1]
    fmt = _sniff_format(content, filename)

    # Stage 1 (non-PDF) — universal text extractor for HTML, DOCX, TXT
    if fmt != "pdf":
        result = extract_date_from_bytes(content, filename)
        strategy = f"{fmt}_text_scored" if result else f"{fmt}_no_date"
        _cache_put(url, result.isoformat() if result else None, strategy)
        if result:
            logger.debug("Doc date via %s: %s → %s", strategy, url, result)
        return result

    # Stage 1a — PDF metadata
    result = _extract_metadata_date(content)
    if result:
        strategy = "pdf_metadata"
        _cache_put(url, result.isoformat(), strategy)
        logger.debug("Doc date via %s: %s → %s", strategy, url, result)
        return result

    # Stage 1b — page-1 text with full scoring
    text, has_text = _extract_text_and_flag(content)
    if has_text:
        candidates = _score_candidates_from_text(text, base_score=0, source_label="pdf_text")
        result = _best_date(candidates)
        if result:
            strategy = "pdf_text_scored"
            _cache_put(url, result.isoformat(), strategy)
            logger.debug("Doc date via %s: %s → %s", strategy, url, result)
            return result
        # Digital PDF but no date in header text — OCR will not help
        _cache_put(url, None, "pdf_text_no_date")
        return None

    # Stage 2 — scanned PDF → OCR
    if not (_PDF2IMAGE_OK and _TESSERACT_OK):
        logger.debug("Scanned PDF but OCR deps not installed, skipping: %s", url)
        _cache_put(url, None, "ocr_unavailable")
        return None

    full_content = await _download_full_first_page(url)
    if not full_content:
        _cache_put(url, None, "ocr_download_failed")
        return None

    page_image = _render_page1(full_content)
    if not page_image:
        _cache_put(url, None, "ocr_render_failed")
        return None

    # Stage 2a — direct OCR on header crop
    result = _date_from_ocr(page_image, preprocess=False)
    if result:
        strategy = "ocr_direct"
        _cache_put(url, result.isoformat(), strategy)
        logger.debug("Doc date via %s: %s → %s", strategy, url, result)
        return result

    # Stage 2b — OpenCV preprocessing + OCR
    if _CV2_OK:
        result = _date_from_ocr(page_image, preprocess=True)
        if result:
            strategy = "ocr_preprocessed"
            _cache_put(url, result.isoformat(), strategy)
            logger.debug("Doc date via %s: %s → %s", strategy, url, result)
            return result

    # Stage 2c — YOLO region detection + per-region OCR
    if _YOLO_OK:
        result = _date_from_yolo_ocr(page_image)
        if result:
            strategy = "yolo_ocr"
            _cache_put(url, result.isoformat(), strategy)
            logger.debug("Doc date via %s: %s → %s", strategy, url, result)
            return result

    logger.debug("Doc date extraction failed (all strategies): %s", url)
    _cache_put(url, None, "exhausted")
    return None


async def extract_pdf_dates_batch(urls: list[str]) -> dict[str, date | None]:
    """Extract dates from multiple PDF URLs concurrently (max 5 at a time)."""
    semaphore = asyncio.Semaphore(5)

    async def _bounded(url: str) -> tuple[str, date | None]:
        async with semaphore:
            return url, await extract_pdf_date(url)

    raw = await asyncio.gather(*[_bounded(u) for u in urls], return_exceptions=True)
    out: dict[str, date | None] = {}
    for item in raw:
        if isinstance(item, Exception):
            logger.warning("PDF date batch extraction error: %s", item)
        else:
            url, res = item
            out[url] = res
    return out


# ── Universal bytes extractor (PDF / DOCX / TXT / HTML) ───────────────────

def _sniff_format(content: bytes, filename: str = "") -> str:
    if content[:4] == b"%PDF":
        return "pdf"
    if content[:2] == b"PK":
        fname_lower = filename.lower()
        if fname_lower.endswith(".docx"):
            return "docx"
        if fname_lower.endswith(".xlsx"):
            return "xlsx"
        return "zip"
    if content[:4] == b"\xd0\xcf\x11\xe0":
        return "doc"
    stripped = content[:512].lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<html") or stripped.startswith(b"<!DOCTYPE"):
        return "html"
    return "text"


def _text_from_docx(content: bytes) -> str:
    try:
        import io
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
            texts: list[str] = []
            targets = [n for n in names if re.match(r"word/document\d*\.xml", n)]
            if not targets:
                targets = ["word/document.xml"] if "word/document.xml" in names else []
            for target in targets:
                try:
                    xml_bytes = zf.read(target)
                    root = ET.fromstring(xml_bytes)
                    parts = [
                        node.text or ""
                        for node in root.iter(
                            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                        )
                    ]
                    texts.append(" ".join(parts))
                except Exception:
                    pass
            return "\n".join(texts)
    except Exception as exc:
        logger.debug("DOCX text extraction failed: %s", exc)
        return ""


def _text_from_html(content: bytes) -> str:
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(content, "html.parser").get_text(separator=" ")
    except ImportError:
        text = content.decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", text)


def extract_date_from_bytes(content: bytes, filename: str = "") -> date | None:
    """
    Extract a publication date from raw file bytes.

    Supports PDF, DOCX, DOC, TXT, HTML — format is auto-detected.
    Uses the full scoring pipeline for PDFs; regex + datefinder for others.
    """
    if not content:
        return None

    fmt = _sniff_format(content, filename)
    logger.debug("extract_date_from_bytes: format=%s filename=%r len=%d", fmt, filename, len(content))

    if fmt == "pdf":
        d = _extract_metadata_date(content)
        if d:
            return d
        text, has_text = _extract_text_and_flag(content)
        if has_text:
            candidates = _score_candidates_from_text(text, base_score=0, source_label="bytes_pdf")
            return _best_date(candidates)
        return None

    if fmt == "docx":
        text = _text_from_docx(content)
        if text:
            candidates = _score_candidates_from_text(text, base_score=0, source_label="docx")
            return _best_date(candidates)
        return None

    if fmt == "html":
        text = _text_from_html(content)
        if text:
            candidates = _score_candidates_from_text(text[:4000], base_score=0, source_label="html")
            return _best_date(candidates)
        return None

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            text = ""
    if text:
        candidates = _score_candidates_from_text(text[:8000], base_score=0, source_label="plaintext")
        return _best_date(candidates)

    return None


def extract_date_from_text(text: str) -> date | None:
    """
    Extract the most likely publication date from an arbitrary string.

    Uses datefinder (NLP-based) + regex patterns + keyword scoring to return
    the highest-confidence date candidate. Returns None if no date found.
    """
    if not text:
        return None
    candidates = _score_candidates_from_text(text, base_score=0, source_label="text")
    return _best_date(candidates)


def flush_cache() -> None:
    """Force-write the cache to disk immediately."""
    _save_cache()

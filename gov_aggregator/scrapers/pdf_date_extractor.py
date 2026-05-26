"""PDF date extraction — 3-tier pipeline.

Tier 1  : PDF metadata + pypdf text extraction (digital PDFs)
Tier 2  : Tesseract OCR on page-1 header crop (scanned PDFs, clean)
Tier 3  : OpenCV preprocessing + Tesseract OCR (noisy/skewed scans)
Free    : Filename regex + URL path regex (zero network cost, run first)

All tiers are optional-dependency-guarded — the module works with only
pypdf installed; OCR tiers activate when pdf2image / pytesseract / cv2
are also present.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("gov_aggregator.pdf_date_extractor")

# ── Optional heavy dependencies ────────────────────────────────────────────
try:
    from pypdf import PdfReader
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

# ── Cache ──────────────────────────────────────────────────────────────────
_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "pdf_date_cache.json"
_cache: dict[str, Any] = {}
_cache_dirty = 0  # count of unsaved writes

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

def _cache_put(url: str, result_date: str | None, strategy: str) -> None:
    global _cache_dirty
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

# ── Date regex patterns ────────────────────────────────────────────────────
_MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
)

_DATE_PATTERNS: list[tuple[str, str]] = [
    # DD Month YYYY  — "15 May 2026", "15th May, 2026", "15 May, 2026"
    (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTHS + r",?\s+(20\d{2})\b", "dmy_text"),
    # Month DD, YYYY — "May 15, 2026"
    (r"\b" + _MONTHS + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", "mdy_text"),
    # DD/MM/YYYY
    (r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", "dmy_slash"),
    # DD-MM-YYYY
    (r"\b(\d{1,2})-(\d{1,2})-(20\d{2})\b", "dmy_dash"),
    # DD.MM.YYYY
    (r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b", "dmy_dot"),
    # YYYY-MM-DD
    (r"\b(20\d{2})-(\d{2})-(\d{2})\b", "iso"),
    # YYYY/MM/DD
    (r"\b(20\d{2})/(\d{2})/(\d{2})\b", "iso_slash"),
    # DDMMYYYY (compact — common in filenames)
    (r"\b(\d{2})(\d{2})(20\d{2})\b", "compact"),
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
        # Reject obviously wrong dates (future > 1 year, or before 2000)
        today = date.today()
        if d.year < 2000 or d > date(today.year + 1, today.month, today.day):
            return None
        return d
    except ValueError:
        return None


def _parse_dates_from_text(text: str) -> list[date]:
    """Extract all plausible dates from a text string."""
    found: list[date] = []
    text_lower = text.lower()

    for pattern, kind in _DATE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            d = None
            try:
                if kind == "dmy_text":
                    day, month_str, year = int(m.group(1)), m.group(2).lower()[:3], int(m.group(3))
                    d = _try_date(year, _MONTH_MAP.get(month_str, 0), day)
                elif kind == "mdy_text":
                    month_str, day, year = m.group(1).lower()[:3], int(m.group(2)), int(m.group(3))
                    d = _try_date(year, _MONTH_MAP.get(month_str, 0), day)
                elif kind in ("dmy_slash", "dmy_dash", "dmy_dot"):
                    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    d = _try_date(year, month, day)
                elif kind in ("iso", "iso_slash"):
                    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    d = _try_date(year, month, day)
                elif kind == "compact":
                    # DDMMYYYY — only accept if DD<=31 and MM<=12
                    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if 1 <= dd <= 31 and 1 <= mm <= 12:
                        d = _try_date(yyyy, mm, dd)
            except (IndexError, ValueError):
                pass
            if d:
                found.append(d)

    return found


def _earliest_valid(dates: list[date]) -> date | None:
    return min(dates) if dates else None


# ── Free strategies (no download) ─────────────────────────────────────────

def _date_from_filename(url: str) -> date | None:
    """Extract date from the PDF filename segment of the URL."""
    try:
        filename = urlparse(url).path.split("/")[-1]
        filename = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
        dates = _parse_dates_from_text(filename)
        return _earliest_valid(dates)
    except Exception:
        return None


def _date_from_url_path(url: str) -> date | None:
    """Extract date from directory segments of the URL path."""
    try:
        path = urlparse(url).path
        # Match /YYYY/MM/DD/ or /YYYY/MM/ patterns
        m = re.search(r"/(20\d{2})/(\d{1,2})(?:/(\d{1,2}))?", path)
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
            day = int(m.group(3)) if m.group(3) else 1
            return _try_date(year, month, day)
        # Also run general date patterns against the full path
        dates = _parse_dates_from_text(path.replace("/", " ").replace("_", " ").replace("-", " "))
        return _earliest_valid(dates)
    except Exception:
        return None


# ── Tier 1: PDF metadata + text (pypdf) ───────────────────────────────────

def _date_from_pdf_metadata(content: bytes) -> date | None:
    """Read /CreationDate or /ModDate from PDF XMP metadata."""
    if not _PYPDF_OK:
        return None
    try:
        reader = PdfReader(_io.BytesIO(content))
        meta = reader.metadata or {}
        for field in ("/CreationDate", "/ModDate", "CreationDate", "ModDate"):
            raw = meta.get(field)
            if not raw:
                continue
            raw = str(raw)
            # PDF date format: D:YYYYMMDDHHmmSSOHH'mm'
            m = re.match(r"D?:?(\d{4})(\d{2})(\d{2})", raw)
            if m:
                d = _try_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if d:
                    return d
    except Exception:
        pass
    return None


def _extract_pdf_text_and_flag(content: bytes) -> tuple[str, bool]:
    """Return (text_from_page1, has_text). has_text=False means scanned PDF."""
    if not _PYPDF_OK:
        return "", False
    try:
        reader = PdfReader(_io.BytesIO(content))
        if not reader.pages:
            return "", False
        text = reader.pages[0].extract_text() or ""
        return text[:2000], len(text.strip()) > 20
    except Exception:
        return "", False


# ── Tier 2: OCR — direct Tesseract on header crop ────────────────────────

def _ocr_image(pil_image: Any) -> str:
    """Run Tesseract on a PIL image and return extracted text."""
    if not _TESSERACT_OK:
        return ""
    try:
        return _tesseract.image_to_string(pil_image, config="--psm 6 --oem 3")
    except Exception:
        return ""


def _crop_header(pil_image: Any) -> Any:
    """Crop the top 30% of an image (where dates usually live in govt docs)."""
    w, h = pil_image.size
    return pil_image.crop((0, 0, w, int(h * 0.30)))


def _date_from_ocr_direct(page_image: Any) -> date | None:
    """Tier 2: OCR on cropped header, no preprocessing."""
    header = _crop_header(page_image)
    text = _ocr_image(header)
    if not text:
        return None
    dates = _parse_dates_from_text(text)
    return _earliest_valid(dates)


# ── Tier 3: OpenCV preprocessing + OCR ───────────────────────────────────

def _preprocess_image(pil_image: Any) -> Any:
    """Deskew, binarize, and denoise a PIL image using OpenCV."""
    if not _CV2_OK:
        return pil_image
    try:
        img = _np.array(pil_image.convert("RGB"))
        gray = _cv2.cvtColor(img, _cv2.COLOR_RGB2GRAY)

        # Adaptive threshold — handles uneven lighting from scanner
        binary = _cv2.adaptiveThreshold(
            gray, 255,
            _cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            _cv2.THRESH_BINARY, 31, 10,
        )

        # Deskew via minimum bounding rect of dark pixels
        coords = _np.column_stack(_np.where(binary < 128))
        if len(coords) > 100:
            rect = _cv2.minAreaRect(coords)
            angle = rect[-1]
            if angle < -45:
                angle += 90
            (h, w) = binary.shape
            M = _cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            binary = _cv2.warpAffine(
                binary, M, (w, h),
                flags=_cv2.INTER_CUBIC,
                borderMode=_cv2.BORDER_REPLICATE,
            )

        # Denoise
        denoised = _cv2.fastNlMeansDenoising(binary, h=10)

        return _PILImage.fromarray(denoised)
    except Exception:
        return pil_image


def _date_from_ocr_preprocessed(page_image: Any) -> date | None:
    """Tier 3: OpenCV preprocess header, then OCR."""
    header = _crop_header(page_image)
    cleaned = _preprocess_image(header)
    text = _ocr_image(cleaned)
    if not text:
        return None
    dates = _parse_dates_from_text(text)
    return _earliest_valid(dates)


# ── PDF download helpers ───────────────────────────────────────────────────

async def _download_bytes(url: str, max_bytes: int = 65_536) -> bytes | None:
    """Download the first max_bytes of a URL. Returns None on failure."""
    try:
        headers = {"Range": f"bytes=0-{max_bytes - 1}"}
        async with httpx.AsyncClient(verify=False, timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            if r.status_code in (200, 206):
                return r.content
    except Exception as exc:
        logger.debug("PDF download failed (%s): %s", url, exc)
    return None


async def _download_full_first_page(url: str) -> bytes | None:
    """Download enough of the PDF to render page 1 (up to 2 MB)."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200:
                return r.content[:2_097_152]  # 2 MB cap
    except Exception as exc:
        logger.debug("Full PDF download failed (%s): %s", url, exc)
    return None


def _render_page1(content: bytes) -> Any | None:
    """Convert page 1 of PDF bytes to a PIL image."""
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
    Try to extract a publication date from a government PDF.
    Returns a date object or None if all strategies fail.
    Results are cached to data/pdf_date_cache.json.
    """
    if not url:
        return None

    # Check cache first
    cached = _cache.get(url)
    if cached is not None:
        raw = cached.get("date")
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError:
                pass
        return None  # cached as "no date found"

    result: date | None = None
    strategy = "none"

    # ── Free strategies (no network cost) ─────────────────────────────────
    result = _date_from_filename(url)
    if result:
        strategy = "filename"
        _cache_put(url, result.isoformat(), strategy)
        logger.debug("PDF date via %s: %s → %s", strategy, url, result)
        return result

    result = _date_from_url_path(url)
    if result:
        strategy = "url_path"
        _cache_put(url, result.isoformat(), strategy)
        logger.debug("PDF date via %s: %s → %s", strategy, url, result)
        return result

    # ── Download PDF header (first 64 KB) ─────────────────────────────────
    content = await _download_bytes(url, max_bytes=65_536)
    if not content:
        _cache_put(url, None, "download_failed")
        return None

    # ── Tier 1a: PDF metadata ──────────────────────────────────────────────
    result = _date_from_pdf_metadata(content)
    if result:
        strategy = "pdf_metadata"
        _cache_put(url, result.isoformat(), strategy)
        logger.debug("PDF date via %s: %s → %s", strategy, url, result)
        return result

    # ── Tier 1b: pypdf text extraction ────────────────────────────────────
    text, has_text = _extract_pdf_text_and_flag(content)
    if has_text:
        dates = _parse_dates_from_text(text)
        result = _earliest_valid(dates)
        if result:
            strategy = "pdf_text"
            _cache_put(url, result.isoformat(), strategy)
            logger.debug("PDF date via %s: %s → %s", strategy, url, result)
            return result
        # Digital PDF but no date in header text — skip OCR (won't help)
        _cache_put(url, None, "pdf_text_no_date")
        return None

    # ── Scanned PDF — need OCR ─────────────────────────────────────────────
    if not (_PDF2IMAGE_OK and _TESSERACT_OK):
        logger.debug("Scanned PDF but OCR deps not installed, skipping: %s", url)
        _cache_put(url, None, "ocr_unavailable")
        return None

    # Download more content for image rendering
    full_content = await _download_full_first_page(url)
    if not full_content:
        _cache_put(url, None, "ocr_download_failed")
        return None

    page_image = _render_page1(full_content)
    if not page_image:
        _cache_put(url, None, "ocr_render_failed")
        return None

    # ── Tier 2: Direct OCR on header crop ─────────────────────────────────
    result = _date_from_ocr_direct(page_image)
    if result:
        strategy = "ocr_tier2"
        _cache_put(url, result.isoformat(), strategy)
        logger.debug("PDF date via %s: %s → %s", strategy, url, result)
        return result

    # ── Tier 3: OpenCV preprocessing + OCR ───────────────────────────────
    if _CV2_OK:
        result = _date_from_ocr_preprocessed(page_image)
        if result:
            strategy = "ocr_tier3_cv2"
            _cache_put(url, result.isoformat(), strategy)
            logger.debug("PDF date via %s: %s → %s", strategy, url, result)
            return result

    # ── All strategies exhausted ───────────────────────────────────────────
    logger.debug("PDF date extraction failed (all strategies): %s", url)
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
            url, result = item
            out[url] = result
    return out


def flush_cache() -> None:
    """Force-write the cache to disk immediately."""
    _save_cache()

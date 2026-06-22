"""Shared robust date parser for all custom scrapers.

Handles in priority order:
  1. ISO datetime attribute: 2022-12-05  or  2022-12-05T00:00:00+05:30
  2. Human-readable text:    05 Dec 2022  /  December 5, 2022  /  5th Dec 2022
  3. DD/MM/YYYY separators:  05/12/2022  |  05-12-2022  |  05.12.2022
  4. YYYY/MM/DD (rare):      2022/12/05

Returns None if no date can be parsed. Never raises.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_ISO_RE   = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_RE   = re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b")
_YMD_RE   = re.compile(r"\b(\d{4})[./\-](\d{2})[./\-](\d{2})\b")
_TEXT_RE  = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\s+(\d{4})\b")
_TEXT2_RE = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")  # Dec 5, 2022

_MONTH_MAP = {
    m: i for i, m in enumerate([
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    ], 1)
}
# Also map full names
_MONTH_MAP.update({
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
})


def _dt(y: int, m: int, d: int) -> datetime | None:
    try:
        return datetime(y, m, d, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_date(raw: str | None) -> datetime | None:
    """Parse a date string in any common Indian government website format."""
    if not raw:
        return None
    s = raw.strip()

    # 1. ISO: 2022-12-05 or 2022-12-05T...
    m = _ISO_RE.search(s)
    if m:
        result = _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if result:
            return result

    # 2a. Text: 05 Dec 2022 / 5th December 2022
    m = _TEXT_RE.search(s)
    if m:
        mon = _MONTH_MAP.get(m.group(2).lower()[:3])
        if mon:
            result = _dt(int(m.group(3)), mon, int(m.group(1)))
            if result:
                return result

    # 2b. Text: Dec 5, 2022 / December 05 2022
    m = _TEXT2_RE.search(s)
    if m:
        mon = _MONTH_MAP.get(m.group(1).lower()[:3])
        if mon:
            result = _dt(int(m.group(3)), mon, int(m.group(2)))
            if result:
                return result

    # 3. DD/MM/YYYY  (most common on Indian sites)
    m = _DMY_RE.search(s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Sanity: day <= 31, month <= 12
        if d <= 31 and mo <= 12:
            result = _dt(y, mo, d)
            if result:
                return result

    # 4. YYYY/MM/DD fallback (rare)
    m = _YMD_RE.search(s)
    if m:
        result = _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if result:
            return result

    return None

"""Resolve the most trustworthy creation date of a file.

Order of preference (first valid wins):
  1. exif-original   EXIF DateTimeOriginal / DateTimeDigitized (camera, phone, many apps)
  2. xmp             XMP CreateDate / photoshop:DateCreated (Photoshop, Affinity...)
     document        creation date stored inside the document (Krita .kra)
     sidecar         creation date in an .xmp sidecar next to the file (Kurator, Lightroom...)
  3. png             PNG "Creation Time" text chunk
  4. exif-modified   EXIF DateTime (date the file was last saved by software)
  5. filename        2019-04-12, 20190412_153000, IMG_20190412 ... in the file name
  6. file-created    file system birth time (Windows / macOS)
  7. file-modified   file system modification time

The source is stored next to the date, so the UI can show how reliable it is
and the user can filter "dates that are only guesses".
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime

MIN_YEAR = 1980

# Ordered by trust; used by the UI for labels.
SOURCES = (
    "exif-original",
    "xmp",
    "document",
    "png",
    "sidecar",
    "exif-modified",
    "filename",
    "file-created",
    "file-modified",
    "manual",
)

_EXIF_RE = re.compile(r"^\s*(\d{4})[:\-](\d{2})[:\-](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
_ISO_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?"
)
# Year 1980-2099, optional separators, month, day, optional time HHMMSS.
_NAME_RE = re.compile(
    r"(?<!\d)((?:19[89]|20\d)\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_. T]?([01]\d|2[0-3])[-_.:h]?([0-5]\d)[-_.:m]?([0-5]\d)?)?(?!\d)"
)
_XMP_DATE_RE = re.compile(
    r"(?:xmp:CreateDate|photoshop:DateCreated|exif:DateTimeOriginal)\s*(?:=\s*[\"']|>)\s*([0-9][0-9:\-T \.+Z]+)"
)


def _valid(ts: float | None) -> bool:
    if ts is None:
        return False
    try:
        year = datetime.fromtimestamp(ts).year
    except (OverflowError, OSError, ValueError):
        return False
    return year >= MIN_YEAR and ts <= time.time() + 86400


def _local_ts(y, mo, d, h=0, mi=0, s=0) -> float | None:
    try:
        return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0)).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def parse_exif_datetime(value) -> float | None:
    """'2019:04:12 15:30:00' -> local timestamp."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    m = _EXIF_RE.match(str(value))
    if not m or m.group(1) == "0000":
        return None
    ts = _local_ts(*m.groups())
    return ts if _valid(ts) else None


def parse_iso_datetime(value: str) -> float | None:
    m = _ISO_RE.search(value or "")
    if not m:
        return None
    y, mo, d, h, mi, s, tz = m.groups()
    if tz:
        txt = f"{y}-{mo}-{d}T{h or '00'}:{mi or '00'}:{s or '00'}{'+00:00' if tz == 'Z' else tz}"
        if len(tz) == 5:  # +0100 -> +01:00
            txt = txt[: -5] + tz[:3] + ":" + tz[3:]
        try:
            ts = datetime.fromisoformat(txt).timestamp()
        except ValueError:
            ts = None
    else:
        ts = _local_ts(y, mo, d, h, mi, s)
    return ts if _valid(ts) else None


def parse_xmp_date(xmp: str) -> float | None:
    m = _XMP_DATE_RE.search(xmp or "")
    if not m:
        return None
    value = m.group(1).strip()
    if re.match(r"\d{4}:\d{2}:\d{2}", value):
        return parse_exif_datetime(value)
    return parse_iso_datetime(value)


def parse_filename_date(name: str) -> float | None:
    stem = os.path.splitext(os.path.basename(name))[0]
    for m in _NAME_RE.finditer(stem):
        ts = _local_ts(*m.groups())
        if _valid(ts):
            return ts
    return None


def file_times(st: os.stat_result) -> tuple[float | None, float]:
    """Return (birth time or None, modification time)."""
    birth = getattr(st, "st_birthtime", None)  # Windows (3.12+), macOS, BSD
    if birth is None and os.name == "nt":
        birth = st.st_ctime  # on older Pythons st_ctime is creation time on Windows
    return birth, st.st_mtime


def resolve_date(embedded: dict, filename: str, st: os.stat_result) -> tuple[float, str]:
    """embedded: {'exif-original': ts, 'xmp': ts, 'png': ts, 'exif-modified': ts} (any subset)."""
    for src in ("exif-original", "xmp", "document", "png", "sidecar", "exif-modified"):
        ts = embedded.get(src)
        if _valid(ts):
            return ts, src
    ts = parse_filename_date(filename)
    if ts is not None:
        return ts, "filename"
    birth, mtime = file_times(st)
    # Copies often reset the birth time to "now" while keeping mtime; trust the older one.
    if _valid(birth) and birth <= mtime:
        return birth, "file-created"
    return mtime, "file-modified"

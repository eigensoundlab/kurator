"""Krita (.kra) and OpenRaster (.ora): zip files with a flattened mergedimage.png inside."""

from __future__ import annotations

import io
import re
import zipfile

from PIL import Image

from .. import dates
from . import Handler, Loaded, register

MAX_MEMBER = 512 * 1024 * 1024  # refuse absurdly large members (zip-bomb guard)


def _read_member(z: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_MEMBER:
        raise ValueError(f"{name} too large ({info.file_size} bytes)")
    return z.read(info)


def load(data: bytes, path: str, target: int | None) -> Loaded:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        png = _read_member(z, "mergedimage.png")
        if png is None:  # fall back to the small embedded preview
            png = _read_member(z, "Thumbnails/thumbnail.png") or _read_member(z, "preview.png")
        if png is None:
            raise ValueError("no merged image inside")
        meta: dict = {"format": "KRA" if path.lower().endswith(".kra") else "ORA"}
        found: dict = {}
        info = _read_member(z, "documentinfo.xml")
        if info:
            txt = info.decode("utf-8", "ignore")
            m = re.search(r"<creation-date>([^<]+)</creation-date>", txt)
            if m:
                ts = dates.parse_iso_datetime(m.group(1))
                if ts:
                    found["document"] = ts
                    meta["document_created"] = m.group(1)
            for tag in ("title", "description", "creator-name", "editing-time"):
                m = re.search(rf"<{tag}>([^<]*)</{tag}>", txt)
                if m and m.group(1).strip():
                    meta.setdefault("document", {})[tag] = m.group(1).strip()[:2000]
    img = Image.open(io.BytesIO(png))
    meta["mode"] = img.mode
    return Loaded(img, meta, found)


register(Handler("image", (".kra", ".ora"), load, browser_viewable=False, group="work"))

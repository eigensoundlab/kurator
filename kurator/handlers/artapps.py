"""Files from art applications that carry their own preview image.

  .procreate  ZIP with QuickLook/Thumbnail.png
  .clip       Clip Studio Paint: chunked file with an embedded SQLite database (table CanvasPreview)
  .cdr        CorelDRAW X4+: ZIP with metadata/thumbnails/*.bmp; older versions -> Windows thumbnail
"""

from __future__ import annotations

import io
import os
import sqlite3
import tempfile

from PIL import Image

from . import Handler, Loaded, register
from .common import placeholder, shell_thumbnail, zip_image


def _done(img, meta, path, ext, note) -> Loaded:
    if img is None:
        img = shell_thumbnail(path)
        if img is not None:
            meta["preview"] = "windows"
    if img is None:
        img = placeholder(ext, note)
        meta["placeholder"] = True
    return Loaded(img, meta, {}, None if meta.get("placeholder") else img.size)


def load_procreate(data: bytes, path: str, target: int | None) -> Loaded:
    img = zip_image(data, names=["QuickLook/Thumbnail.png"])
    return _done(img, {"format": "PROCREATE", "preview": "embedded"}, path, ".procreate", "no preview")


def _clip_sqlite(data: bytes) -> bytes | None:
    i = data.find(b"CHNKSQLi")
    if i >= 0 and len(data) >= i + 16:
        size = int.from_bytes(data[i + 8:i + 16], "big")
        db = data[i + 16:i + 16 + size]
        if db.startswith(b"SQLite format 3\x00"):
            return db
    j = data.find(b"SQLite format 3\x00")
    return data[j:] if j >= 0 else None


def load_clip(data: bytes, path: str, target: int | None) -> Loaded:
    meta = {"format": "CLIP", "preview": "embedded"}
    img = None
    db = _clip_sqlite(data)
    if db:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "clip.sqlite")
            with open(p, "wb") as f:
                f.write(db)
            try:
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                try:
                    row = con.execute("SELECT ImageData FROM CanvasPreview ORDER BY ImageWidth * ImageHeight DESC LIMIT 1").fetchone()
                    if row and row[0]:
                        img = Image.open(io.BytesIO(row[0]))
                        img.load()
                    w = con.execute("SELECT CanvasWidth, CanvasHeight FROM Canvas LIMIT 1").fetchone()
                    if w:
                        meta["canvas"] = f"{int(w[0])} × {int(w[1])}"
                finally:
                    con.close()
            except sqlite3.Error:
                img = None
    return _done(img, meta, path, ".clip", "no preview")


def load_cdr(data: bytes, path: str, target: int | None) -> Loaded:
    meta = {"format": "CDR"}
    img = zip_image(data, prefix="metadata/thumbnails/")
    if img is None:
        img = zip_image(data, prefix="previews/")
    if img is not None:
        meta["preview"] = "embedded"
    elif data[:4] == b"RIFF":
        meta["note"] = "CorelDRAW before X4"
    return _done(img, meta, path, ".cdr", "install CorelDRAW for previews" if os.name == "nt" else "")


register(Handler("image", (".procreate",), load_procreate, group="work"))
register(Handler("image", (".clip",), load_clip, group="work"))
register(Handler("image", (".cdr",), load_cdr, group="vector"))

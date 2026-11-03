"""Per-file work: hash, decode, metadata, date, thumbnails.

Runs in worker processes (background indexing) and in threads (on-demand
thumbnails for what is on screen right now). Must stay free of DB access.
"""

from __future__ import annotations

import hashlib
import io
import os
import traceback

from PIL import Image, ImageOps

from . import dates
from .handlers import handler_for

GRID_SIZE = 256
THUMB_SIZES = (256, 512, 1600)
WEBP_QUALITY = {256: 78, 512: 80, 1600: 84}
_PLACEHOLDER_BG = (128, 128, 128)


def file_hash(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _normalise_mode(img: Image.Image) -> Image.Image:
    if img.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I"):
        return img.point(lambda v: v * (1 / 256)).convert("L")
    if img.mode == "F":
        return img.convert("L")
    has_alpha = img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
        img.mode == "P" and "transparency" in img.info
    )
    if has_alpha:
        return img.convert("RGBA")
    if img.mode not in ("RGB", "L"):
        return img.convert("RGB")
    return img


def _encode(img: Image.Image, size: int) -> bytes:
    t = img.copy()
    t.thumbnail((size, size), Image.Resampling.LANCZOS, reducing_gap=3.0)
    buf = io.BytesIO()
    t.save(buf, "WEBP", quality=WEBP_QUALITY.get(size, 80), method=4)
    return buf.getvalue()


def _avg_color(img: Image.Image) -> int:
    t = img.copy()
    t.thumbnail((16, 16), Image.Resampling.BILINEAR)
    if t.mode == "RGBA":
        bg = Image.new("RGBA", t.size, _PLACEHOLDER_BG + (255,))
        t = Image.alpha_composite(bg, t)
    r, g, b = t.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    return (r << 16) | (g << 8) | b


def dhash(img: Image.Image) -> int:
    """64-bit difference hash: survives resizing, re-saving and small edits (for "similar images").
    Signed, so it fits an SQLite INTEGER."""
    if img.mode == "RGBA":
        bg = Image.new("RGBA", img.size, _PLACEHOLDER_BG + (255,))
        img = Image.alpha_composite(bg, img)
    g = img.convert("L").resize((9, 8), Image.Resampling.BOX)
    px = list(g.tobytes())   # 8-bit grey: one byte per pixel
    v = 0
    for row in range(8):
        for col in range(8):
            v = (v << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return v - (1 << 64) if v >= 1 << 63 else v


def process_file(path: str, sizes: tuple[int, ...] = (GRID_SIZE,)) -> dict:
    """Return a result dict; never raises (errors are reported in result['error'])."""
    out: dict = {"path": path}
    try:
        st = os.stat(path)
        out["size"] = st.st_size
        out["mtime_ns"] = st.st_mtime_ns
        ext = os.path.splitext(path)[1].lower()
        handler = handler_for(ext)
        if handler is None:
            raise ValueError(f"unsupported type {ext}")
        if handler.streams:   # videos: hash in chunks, never hold the whole file in memory
            h = hashlib.blake2b(digest_size=16)
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8 << 20), b""):
                    h.update(chunk)
            out["hash"] = h.hexdigest()
            data = None
        else:
            with open(path, "rb") as f:
                data = f.read()
            out["hash"] = file_hash(data)
        loaded = handler.load(data, path, max(sizes) if sizes else None)
        del data
        out["kind"] = handler.kind
        out["meta"] = loaded.meta
        if not loaded.dates.get("exif-original"):
            from .xmp import sidecar_date  # a date preserved in a sidecar survives copying
            sts = sidecar_date(path)
            if sts:
                loaded.dates["sidecar"] = sts
        ts, src = dates.resolve_date(loaded.dates, path, st)
        out["date_ts"], out["date_source"] = ts, src
        img = loaded.image
        if img is not None:
            # Real dimensions of the original (draft() may have shrunk the decode size).
            full_w, full_h = loaded.size or img.size
            orientation = 1
            try:
                orientation = img.getexif().get(274, 1)
            except Exception:
                pass
            if orientation in (5, 6, 7, 8):
                full_w, full_h = full_h, full_w
            out["width"], out["height"] = full_w, full_h
            if (loaded.meta.get("audio") or (loaded.meta.get("placeholder") and not loaded.size) or "model" in loaded.meta
                    or (loaded.size is None and loaded.meta.get("preview"))):
                out["width"] = out["height"] = None  # the thumbnail is a waveform / a card, not the content
            img = ImageOps.exif_transpose(img) if orientation != 1 else img
            img.load()
            img = _normalise_mode(img)
            # waveforms: the cell behind the 2:1 picture gets the waveform's background, not its average
            out["color"] = _avg_color(img.crop((0, 0, img.width, 4))) if loaded.meta.get("audio") else _avg_color(img)
            out["thumbs"] = {s: _encode(img, s) for s in sizes}
            for key, extra in (loaded.extra or {}).items():   # e.g. the video hover strip, stored as it is
                buf = io.BytesIO()
                extra.convert("RGB").save(buf, "WEBP", quality=72, method=4)
                out["thumbs"][key] = buf.getvalue()
            out["alpha"] = img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 255
            if handler.kind in ("image", "video") and not loaded.meta.get("placeholder"):
                out["phash"] = dhash(img)
    except Exception as e:  # report, don't crash the pool
        out["error"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc(limit=3)
    return out


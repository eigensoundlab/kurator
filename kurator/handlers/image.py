"""Raster images through Pillow: jpg/jpeg/jfif/jpe, png, webp, gif, bmp, tiff, avif."""

from __future__ import annotations

import io
import json
import warnings
from email.utils import parsedate_to_datetime

from PIL import ExifTags, Image, features

from .. import dates
from . import Handler, Loaded, register

# Artists have big canvases; allow up to ~400 MP, refuse beyond (protects memory).
Image.MAX_IMAGE_PIXELS = 400_000_000
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

MAX_TEXT = 64 * 1024        # cap per text chunk kept in the catalog
MAX_EXIF_VALUE = 500

_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_SKIP_TAGS = {"MakerNote", "UserComment", "PrintImageMatching", "ExifOffset", "GPSInfo",
              "JPEGInterchangeFormat", "JPEGInterchangeFormatLength", "StripOffsets",
              "StripByteCounts", "TileOffsets", "TileByteCounts", "XMLPacket", "InterColorProfile"}


def _scalar(v):
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8").strip("\x00 ")
        except UnicodeDecodeError:
            return None
    if isinstance(v, (int, str)):
        return v if not isinstance(v, str) else v[:MAX_EXIF_VALUE]
    if isinstance(v, float):
        return round(v, 6)
    try:  # IFDRational and friends
        f = float(v)
        return round(f, 6)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    if isinstance(v, tuple) and len(v) <= 8:
        out = [_scalar(x) for x in v]
        return out if all(x is not None for x in out) else None
    return None


def decode_xp(value) -> str | None:
    """Windows Explorer tags/titles (EXIF XPKeywords, XPTitle...) are UTF-16LE byte arrays."""
    if isinstance(value, tuple):
        try:
            value = bytes(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, bytes):
        return value.decode("utf-16-le", "ignore").rstrip("\x00").strip() or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _decode_user_comment(raw) -> str | None:
    """EXIF UserComment: 8-byte charset prefix + payload. A1111 stores prompts here in JPEGs."""
    if not isinstance(raw, bytes) or len(raw) < 8:
        return raw if isinstance(raw, str) else None
    head, body = raw[:8], raw[8:]
    try:
        if head.startswith(b"UNICODE"):
            # Most writers use big-endian; try both and keep the one that looks like text.
            be, le = body.decode("utf-16-be", "ignore"), body.decode("utf-16-le", "ignore")
            txt = be if sum(c.isascii() for c in be) >= sum(c.isascii() for c in le) else le
        else:
            txt = body.decode("utf-8", "ignore")
    except Exception:
        return None
    txt = txt.strip("\x00 ")
    return txt or None


def _read_exif(img: Image.Image, meta: dict, found: dict) -> None:
    try:
        exif = img.getexif()
    except Exception:
        return
    if not exif:
        return
    out = {}
    for tag, val in exif.items():
        name = ExifTags.TAGS.get(tag, f"0x{tag:04x}")
        if name in _SKIP_TAGS:
            continue
        if name.startswith("XP"):  # Windows Explorer title/comment/keywords
            txt = decode_xp(val)
            if txt:
                out[name] = txt[:MAX_EXIF_VALUE]
            continue
        s = _scalar(val)
        if s is not None:
            out[name] = s
    try:
        sub = exif.get_ifd(_EXIF_IFD)
    except Exception:
        sub = {}
    for tag, val in sub.items():
        name = ExifTags.TAGS.get(tag, f"0x{tag:04x}")
        if name == "UserComment":
            txt = _decode_user_comment(val)
            if txt:
                out["UserComment"] = txt[:MAX_TEXT]
            continue
        if name in _SKIP_TAGS:
            continue
        s = _scalar(val)
        if s is not None:
            out[name] = s
    try:
        if exif.get_ifd(_GPS_IFD):
            out["GPS"] = "present"
    except Exception:
        pass
    if out:
        meta["exif"] = out
    ts = dates.parse_exif_datetime(sub.get(36867)) or dates.parse_exif_datetime(sub.get(36868))
    if ts:
        found["exif-original"] = ts
    ts = dates.parse_exif_datetime(exif.get(306))
    if ts:
        found["exif-modified"] = ts


def _detect_generation(texts: dict, exif: dict | None) -> dict | None:
    """Recognise AI-generation parameters (A1111/Forge/Fooocus, ComfyUI, InvokeAI, NovelAI...)."""
    if "parameters" in texts:
        return {"tool": "A1111-compatible", "parameters": texts["parameters"]}
    if "workflow" in texts or "prompt" in texts:
        g = {"tool": "ComfyUI"}
        for k in ("prompt", "workflow"):
            if k in texts:
                g[k] = texts[k]
        return g
    if "invokeai_metadata" in texts:
        return {"tool": "InvokeAI", "parameters": texts["invokeai_metadata"]}
    if texts.get("Software", "").startswith("NovelAI") or "Comment" in texts and '"sampler"' in texts.get("Comment", ""):
        return {"tool": "NovelAI", "parameters": texts.get("Comment", ""), "description": texts.get("Description", "")}
    uc = (exif or {}).get("UserComment")
    if isinstance(uc, str) and ("Steps:" in uc or "Sampler:" in uc):
        return {"tool": "A1111-compatible", "parameters": uc}
    return None


def load(data: bytes, path: str, target: int | None) -> Loaded:
    img = Image.open(io.BytesIO(data))
    meta: dict = {"format": img.format, "mode": img.mode}
    found: dict = {}

    n_frames = getattr(img, "n_frames", 1)
    if n_frames > 1:
        meta["frames"] = n_frames
    if "dpi" in img.info:
        meta["dpi"] = _scalar(img.info["dpi"])
    if img.info.get("icc_profile"):
        meta["icc_profile"] = True
    bits = {"I;16": 16, "I;16B": 16, "I;16L": 16, "I": 32, "F": 32, "1": 1}.get(img.mode)
    if img.format == "TIFF":
        bps = img.tag_v2.get(258) if hasattr(img, "tag_v2") else None
        if bps:
            bits = int(bps[0] if isinstance(bps, tuple) else bps)
    if bits:
        meta["bits"] = bits

    _read_exif(img, meta, found)

    # PNG / WebP / GIF text chunks (AI generation info lives here)
    texts = {}
    raw_text = getattr(img, "text", None) if img.format == "PNG" else None
    for k, v in (raw_text or {}).items():
        if isinstance(v, str) and k != "XML:com.adobe.xmp":
            texts[k] = v[:MAX_TEXT]
    for k in ("comment", "Comment"):
        v = img.info.get(k)
        if isinstance(v, bytes):
            v = v.decode("utf-8", "ignore")
        if isinstance(v, str) and v.strip():
            texts.setdefault("Comment", v[:MAX_TEXT])
    if texts:
        meta["text"] = texts
        ct = texts.get("Creation Time") or texts.get("creation_time")
        if ct:
            ts = dates.parse_iso_datetime(ct) or dates.parse_exif_datetime(ct)
            if not ts:
                try:
                    ts = parsedate_to_datetime(ct).timestamp()
                except (TypeError, ValueError, IndexError):
                    ts = None
            if ts:
                found["png"] = ts

    xmp = img.info.get("xmp") or (raw_text or {}).get("XML:com.adobe.xmp")
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", "ignore")
    if isinstance(xmp, str) and xmp:
        meta["xmp"] = xmp[:MAX_TEXT]
        ts = dates.parse_xmp_date(xmp)
        if ts:
            found["xmp"] = ts

    gen = _detect_generation(texts, meta.get("exif"))
    if gen:
        for k, v in list(gen.items()):
            if isinstance(v, str) and k in ("prompt", "workflow"):
                try:
                    gen[k] = json.loads(v)
                except ValueError:
                    pass
        meta["generation"] = gen

    orig_size = img.size
    # JPEG: let libjpeg decode at 1/2, 1/4 or 1/8 scale - much faster for thumbnails.
    if target and img.format == "JPEG":
        try:
            img.draft("RGB", (target, target))
        except Exception:
            pass
    # Embedded colour profiles (CMYK print files, Adobe RGB, ProPhoto...): previews in sRGB, the way they
    # print or show in Photoshop. Big pictures are reduced first - the preview needs no more.
    if n_frames == 1 and (img.mode == "CMYK" or img.info.get("icc_profile")):
        from ..printinfo import to_srgb
        try:
            if target and max(img.size) > 2 * target:
                img.thumbnail((2 * target, 2 * target), Image.Resampling.LANCZOS)
            img, colour = to_srgb(img)
            meta["colour"] = colour
        except (OSError, ValueError):
            pass
    return Loaded(img, meta, found, orig_size)


try:  # HEIC/HEIF (iPhone photos): decode-only build without GPL encoders
    import pi_heif
    pi_heif.register_heif_opener()
    _HEIF = True
except Exception:
    _HEIF = False

Image.init()
_have = {e for e, fmt in Image.registered_extensions().items()}

_viewable = {".jpg", ".jpeg", ".jfif", ".jpe", ".png", ".webp", ".gif", ".bmp", ".avif", ".svg"}
_groups = {
    "image": [".jpg", ".jpeg", ".jfif", ".jpe", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif",
              ".heic", ".heif", ".jp2", ".j2k", ".jpx", ".qoi", ".ico", ".icns", ".pcx", ".sgi", ".rgb",
              ".ppm", ".pgm", ".pbm", ".pnm"],
    "work": [".psd"],             # Pillow reads the flattened (merged) image
    "texture": [".tga", ".dds", ".blp"],
}
for group, exts in _groups.items():
    ok = [e for e in exts if e in _have or (e in (".heic", ".heif") and _HEIF)]
    register(Handler("image", tuple(e for e in ok if e in _viewable), load, browser_viewable=True, group=group))
    register(Handler("image", tuple(e for e in ok if e not in _viewable), load, browser_viewable=False, group=group))

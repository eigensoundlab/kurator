"""Vector and document formats.

  .svg/.svgz  rendered with resvg (no scripts, no network) - the browser shows the original crisp
  .pdf .ai    first page rendered with PDFium (pypdfium2, Apache-2.0/BSD) - modern .ai files are
              PDF-compatible; otherwise the embedded XMP preview is used
  .eps        DOS-EPS TIFF preview -> XMP preview -> Ghostscript (only if you installed it)
  .indd       InDesign: embedded XMP preview
"""

from __future__ import annotations

import gzip
import io
import re
import threading

from PIL import Image

from . import Handler, Loaded, register
from .common import placeholder, shell_thumbnail, xmp_thumbnail

MAX_SVG = 50 * 1024 * 1024
_pdfium_lock = threading.Lock()  # PDFium is not thread-safe (on-demand thumbnails run in threads)


def _dim(v: str | None) -> float | None:
    if not v:
        return None
    m = re.match(r"\s*([0-9.]+)\s*(px|pt|mm|cm|in)?", v)
    if not m:
        return None
    n = float(m.group(1))
    return n * {"pt": 4 / 3, "mm": 3.7795, "cm": 37.795, "in": 96}.get(m.group(2) or "px", 1)


def load_svg(data: bytes, path: str, target: int | None) -> Loaded:
    import resvg_py
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            data = g.read(MAX_SVG + 1)
        if len(data) > MAX_SVG:
            raise ValueError("SVG too large")
    text = data.decode("utf-8", "replace")
    head = text[:4000]
    w = _dim((re.search(r'<svg[^>]*\swidth="([^"]+)"', head) or [None, None])[1])
    h = _dim((re.search(r'<svg[^>]*\sheight="([^"]+)"', head) or [None, None])[1])
    vb = re.search(r'viewBox="\s*[-0-9.]+[\s,]+[-0-9.]+[\s,]+([0-9.]+)[\s,]+([0-9.]+)', head)
    if (not w or not h) and vb:
        w, h = float(vb.group(1)), float(vb.group(2))
    meta = {"format": "SVG", "vector": True}
    if "inkscape" in head.lower():
        meta["editor"] = "Inkscape"
    px = max(256, min(2048, (target or 512) * 2))
    wide = (w or 1) >= (h or 1)
    png = bytes(resvg_py.svg_to_bytes(svg_string=text, **({"width": px} if wide else {"height": px})))
    img = Image.open(io.BytesIO(png))
    img.load()
    size = (round(w), round(h)) if w and h else img.size
    return Loaded(img, meta, {}, size)


def _pdf_render(data: bytes, target: int | None) -> tuple[Image.Image, dict, tuple]:
    import pypdfium2 as pdfium
    with _pdfium_lock:
        doc = pdfium.PdfDocument(data)
        try:
            meta = {"pages": len(doc)}
            try:
                info = {k: v for k, v in doc.get_metadata_dict().items() if v}
                for k in ("Title", "Author", "Creator", "Producer"):
                    if info.get(k):
                        meta[k.lower()] = str(info[k])[:300]
            except Exception:
                pass
            page = doc[0]
            pw, ph = page.get_size()
            scale = max(0.1, min(6.0, ((target or 512) * 1.5) / max(pw, ph, 1)))
            img = page.render(scale=scale).to_pil()
            page.close()
        finally:
            doc.close()
    return img, meta, (round(pw * 96 / 72), round(ph * 96 / 72))


def load_pdf(data: bytes, path: str, target: int | None) -> Loaded:
    img, meta, size = _pdf_render(data, target)
    meta["format"] = "PDF"
    return Loaded(img, meta, {}, size)


def load_ai(data: bytes, path: str, target: int | None) -> Loaded:
    meta = {"format": "AI", "vector": True}
    if data.lstrip()[:5] == b"%PDF-":
        try:
            img, m, size = _pdf_render(data, target)
            meta.update(m)
            return Loaded(img, meta, {}, size)
        except Exception:
            pass
    img = xmp_thumbnail(data)
    if img is not None:
        meta["preview"] = "embedded"
        return Loaded(img, meta, {}, None)
    return _fallback(path, ".ai", meta, "no PDF content")


def load_eps(data: bytes, path: str, target: int | None) -> Loaded:
    meta = {"format": "EPS", "vector": True}
    if data[:4] == b"\xc5\xd0\xd3\xc6":  # DOS EPS binary header with an embedded TIFF preview
        off, ln = int.from_bytes(data[20:24], "little"), int.from_bytes(data[24:28], "little")
        if off and ln and off + ln <= len(data):
            try:
                img = Image.open(io.BytesIO(data[off:off + ln]))
                img.load()
                meta["preview"] = "embedded TIFF"
                return Loaded(img, meta, {}, None)
            except Exception:
                pass
    img = xmp_thumbnail(data)
    if img is not None:
        meta["preview"] = "embedded"
        return Loaded(img, meta, {}, None)
    from PIL import EpsImagePlugin
    if EpsImagePlugin.has_ghostscript():  # only when the user installed Ghostscript (AGPL - not bundled)
        img = Image.open(io.BytesIO(data))
        img.load(scale=2)
        return Loaded(img, meta, {}, img.size)
    return _fallback(path, ".eps", meta, "install Ghostscript")


def load_indd(data: bytes, path: str, target: int | None) -> Loaded:
    meta = {"format": "INDD"}
    img = xmp_thumbnail(data)
    if img is not None:
        meta["preview"] = "embedded"
        return Loaded(img, meta, {}, None)
    return _fallback(path, ".indd", meta, "no preview")


def _fallback(path: str, ext: str, meta: dict, note: str) -> Loaded:
    img = shell_thumbnail(path)
    if img is not None:
        meta["preview"] = "windows"
        return Loaded(img, meta, {}, None)
    meta["placeholder"] = True
    return Loaded(placeholder(ext, note), meta, {}, None)


register(Handler("image", (".svg",), load_svg, browser_viewable=True, group="vector"))
register(Handler("image", (".svgz",), load_svg, group="vector"))
register(Handler("image", (".ai",), load_ai, group="vector"))
register(Handler("image", (".eps", ".epsf"), load_eps, group="vector"))
register(Handler("image", (".pdf",), load_pdf, group="document"))
register(Handler("image", (".indd",), load_indd, group="document"))

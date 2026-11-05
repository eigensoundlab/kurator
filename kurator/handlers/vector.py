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
import os
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


_ROOT = re.compile(r"<svg\b[^>]*>", re.S)
_ROOT_ATTRS = re.compile(r'\s(?:width|height|viewBox|preserveAspectRatio)\s*=\s*("[^"]*"|\'[^\']*\')', re.S)


def show_all() -> bool:
    """Setting: render everything in an SVG, also objects outside the page (set by the app)."""
    return os.environ.get("KURATOR_SVG_ALL") == "1"


def _page_box(head: str) -> tuple[float, float, float, float] | None:
    """The page in user units: the viewBox, else width/height; None when it is zero or missing."""
    vb = re.search(r'<svg[^>]*\sviewBox\s*=\s*["\']\s*([-0-9.eE+]+)[\s,]+([-0-9.eE+]+)[\s,]+([-0-9.eE+]+)[\s,]+([-0-9.eE+]+)', head)
    if vb:
        x, y, w, h = (float(g) for g in vb.groups())
        return (x, y, w, h) if w > 0 and h > 0 else None   # a zero viewBox can't be drawn
    w = _dim((re.search(r'<svg[^>]*\swidth="([^"]+)"', head) or [None, None])[1])
    h = _dim((re.search(r'<svg[^>]*\sheight="([^"]+)"', head) or [None, None])[1])
    if w and h and w >= 1 and h >= 1:
        return 0.0, 0.0, w, h
    return None


def with_box(text: str, box: tuple, px: tuple[int, int] | None = None) -> str:
    """The SVG with its page replaced by `box` (user units), at px pixels (or the box size)."""
    m = _ROOT.search(text)
    if not m:
        raise ValueError("no <svg> element")
    x, y, w, h = box
    pw, ph = px or (max(1, round(w)), max(1, round(h)))
    tag = _ROOT_ATTRS.sub("", m.group(0))
    tag = tag[:4] + f' width="{pw}" height="{ph}" viewBox="{x:.6g} {y:.6g} {w:.6g} {h:.6g}" preserveAspectRatio="xMidYMid meet"' + tag[4:]
    return text[:m.start()] + tag + text[m.end():]


def _alpha_box(text: str, box: tuple, n: int = 1024) -> tuple[int, int, int, int] | None:
    import resvg_py
    x, y, w, h = box
    k = n / max(w, h)
    px = (max(1, round(w * k)), max(1, round(h * k)))
    png = bytes(resvg_py.svg_to_bytes(dpi=96, svg_string=with_box(text, box, px)))
    img = Image.open(io.BytesIO(png))
    return img.getchannel("A").getbbox() if img.mode == "RGBA" else img.getbbox(), px


def content_box(text: str, page: tuple | None) -> tuple[float, float, float, float] | None:
    """Bounding box of everything drawn (user units). A first render of a wide area finds where
    things are (anti-aliasing keeps even sub-pixel shapes visible), two more home in on them."""
    if page:
        cx, cy, ext = page[0] + page[2] / 2, page[1] + page[3] / 2, max(page[2], page[3])
        sizes = [ext * 50, ext * 5000]
    else:
        cx = cy = 0.0
        sizes = [5000, 5_000_000]
    box = None
    for k, half in enumerate(sizes):
        wide = (cx - half, cy - half, 2 * half, 2 * half)
        found, (pw, ph) = _alpha_box(text, wide, 2048)
        if found is None:
            continue
        x0, y0, x1, y1 = found
        if (x0 <= 0 or y0 <= 0 or x1 >= pw or y1 >= ph) and k < len(sizes) - 1:
            continue   # it reaches the edge: something lies further out
        box = wide
        break
    if box is None:
        return None
    for _ in range(3):
        found, (pw, ph) = _alpha_box(text, box)
        if found is None:
            return box
        x0, y0, x1, y1 = found
        sx, sy = box[2] / pw, box[3] / ph
        new = (box[0] + (x0 - 1) * sx, box[1] + (y0 - 1) * sy, (x1 - x0 + 2) * sx, (y1 - y0 + 2) * sy)
        if new[2] >= box[2] * 0.98 and new[3] >= box[3] * 0.98:
            break
        box = new
    x0, y0, x1, y1 = found
    sx, sy = box[2] / pw, box[3] / ph
    return box[0] + x0 * sx, box[1] + y0 * sy, max((x1 - x0) * sx, 1e-6), max((y1 - y0) * sy, 1e-6)


def _svg_text(data: bytes) -> str:
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            data = g.read(MAX_SVG + 1)
        if len(data) > MAX_SVG:
            raise ValueError("SVG too large")
    return data.decode("utf-8", "replace")


def _inside(b: tuple, page: tuple) -> bool:
    return b[0] >= page[0] and b[1] >= page[1] and b[0] + b[2] <= page[0] + page[2] and b[1] + b[3] <= page[1] + page[3]


def _union(a: tuple, b: tuple) -> tuple:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    return x0, y0, max(a[0] + a[2], b[0] + b[2]) - x0, max(a[1] + a[3], b[1] + b[3]) - y0


def svg_for_view(data: bytes) -> bytes | None:
    """For the viewer: the SVG with its page set around all of its content (show-all setting, or a
    page of zero size). None when the original is fine as it is."""
    text = _svg_text(data)
    page = _page_box(text[:4000])
    if page is not None and not show_all():
        return None
    box = content_box(text, page)
    if box is None:
        return None
    if page is not None:
        if _inside(box, page):
            return None
        box = _union(box, page)
    else:
        pad = max(box[2], box[3]) * 0.02
        box = (box[0] - pad, box[1] - pad, box[2] + 2 * pad, box[3] + 2 * pad)
    k = 1600 / max(box[2], box[3]) if max(box[2], box[3]) < 400 else 1
    return with_box(text, box, (max(1, round(box[2] * k)), max(1, round(box[3] * k)))).encode("utf-8")


def load_svg(data: bytes, path: str, target: int | None) -> Loaded:
    import resvg_py
    text = _svg_text(data)
    head = text[:4000]
    page = _page_box(head)
    meta = {"format": "SVG", "vector": True}
    if "inkscape" in head.lower():
        meta["editor"] = "Inkscape"
    px = max(256, min(2048, (target or 512) * 2))
    if page is not None and not show_all():
        w = _dim((re.search(r'<svg[^>]*\swidth="([^"]+)"', head) or [None, None])[1]) or page[2]
        h = _dim((re.search(r'<svg[^>]*\sheight="([^"]+)"', head) or [None, None])[1]) or page[3]
        try:
            png = bytes(resvg_py.svg_to_bytes(dpi=96, svg_string=text, **({"width": px} if w >= h else {"height": px})))
        except ValueError as e:
            if "size" not in str(e):
                raise
            page = None   # e.g. a tiny page resvg won't draw: show the content instead
        else:
            img = Image.open(io.BytesIO(png))
            img.load()
            return Loaded(img, meta, {}, (round(w), round(h)))
    # everything that is drawn (setting "show all objects", or a page without a usable size)
    box = content_box(text, page)
    if box is None:
        if page is None:
            meta["note"] = "empty drawing (nothing visible, and the page has no size)"
            meta["placeholder"] = True
            return Loaded(placeholder(".svg", "empty / no size"), meta, {}, None)
        box = page
    else:
        if page is None:
            meta["note"] = "the page has no size: showing everything in the drawing"
            pad = max(box[2], box[3]) * 0.02
            box = (box[0] - pad, box[1] - pad, box[2] + 2 * pad, box[3] + 2 * pad)
        else:
            if not _inside(box, page):
                meta["outside_page"] = True
            box = _union(box, page)   # the page, grown to take in whatever lies outside it
        meta["shown"] = "all objects"
    k = px / max(box[2], box[3])
    png = bytes(resvg_py.svg_to_bytes(dpi=96, svg_string=with_box(text, box, (max(1, round(box[2] * k)), max(1, round(box[3] * k))))))
    img = Image.open(io.BytesIO(png))
    img.load()
    unit = 1.0   # pixels per user unit (a page of 210mm with a viewBox of 0 0 210 297: 3.78)
    if page is not None:
        pw = _dim((re.search(r'<svg[^>]*\swidth="([^"]+)"', head) or [None, None])[1])
        if pw and page[2] > 0:
            unit = pw / page[2]
    return Loaded(img, meta, {}, (max(1, round(box[2] * unit)), max(1, round(box[3] * unit))))


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

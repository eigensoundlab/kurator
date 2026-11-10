"""Pages of PDF documents (and PDF-based Illustrator files) for the viewer.

The thumbnails show the first page; the viewer pages through the rest. Pages are rendered on
request with PDFium at the size the viewer needs (sharp when zoomed in), white behind them.
"""

from __future__ import annotations

import io
import os

from .handlers.vector import _pdfium_lock

MAX_SIDE = 8000          # longest side of a rendered page, in pixels


class PageError(ValueError):
    pass


def _open(path: str):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise PageError("pages need pypdfium2, which has no build for this system (the installer said so)") from None
    if not os.path.isfile(path):
        raise PageError("the file is missing on disk")
    with open(path, "rb") as f:
        head = f.read(1024)
    if b"%PDF-" not in head:
        raise PageError("no PDF content in this file")
    try:
        return pdfium.PdfDocument(path)
    except Exception as e:    # pdfium raises its own PdfiumError
        raise PageError(f"can't open the PDF: {e}") from None


def pages(path: str) -> dict:
    """{"count", "pages": [[width_px, height_px] at 96 dpi], "labels": [...] or None}."""
    with _pdfium_lock:
        doc = _open(path)
        try:
            n = len(doc)
            sizes = []
            for i in range(n):
                w, h = doc.get_page_size(i)
                sizes.append([max(1, round(w * 96 / 72)), max(1, round(h * 96 / 72))])
            labels = None
            try:
                labels = [doc.get_page_label(i) for i in range(n)]
                if not any(labels) or labels == [str(i + 1) for i in range(n)]:
                    labels = None
            except Exception:
                labels = None
            return {"count": n, "pages": sizes, "labels": labels}
        finally:
            doc.close()


def render(path: str, index: int, size: int, fmt: str = "webp") -> bytes:
    """Page `index` (0-based) with its longest side `size` pixels."""
    size = max(16, min(MAX_SIDE, int(size)))
    with _pdfium_lock:
        doc = _open(path)
        try:
            if not 0 <= index < len(doc):
                raise PageError("no such page")
            page = doc[index]
            try:
                w, h = page.get_size()
                scale = size / max(w, h, 1)
                if w * h * scale * scale > 64_000_000:           # about 8000 x 8000
                    scale = (64_000_000 / max(w * h, 1)) ** 0.5
                img = page.render(scale=scale, fill_color=(255, 255, 255, 255)).to_pil().convert("RGB")
            finally:
                page.close()
        finally:
            doc.close()
    buf = io.BytesIO()
    if fmt == "png":
        img.save(buf, "PNG", optimize=False)
    else:
        img.save(buf, "WEBP", quality=90, method=2)
    return buf.getvalue()


def info(path: str, index: int) -> dict:
    """Size and page boxes (trim, bleed) of one page."""
    from .printinfo import page_boxes
    with _pdfium_lock:
        doc = _open(path)
        try:
            if not 0 <= index < len(doc):
                raise PageError("no such page")
            page = doc[index]
            try:
                w, h = page.get_size()
                out = {"index": index, "width": round(w * 96 / 72), "height": round(h * 96 / 72),
                       "size_mm": [round(w * 25.4 / 72, 1), round(h * 25.4 / 72, 1)]}
                out.update(page_boxes(page))
                return out
            finally:
                page.close()
        finally:
            doc.close()

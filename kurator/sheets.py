"""Contact sheets and joined images, made from a selection.

* Contact sheet: pages (A4, A3, Letter or any pixel size) with a grid of images, names, dates or
  tags under them, a title and page numbers. Saved as one PDF or as PNG/JPEG pages.
* Join: images side by side (horizontal), stacked (vertical) or in a grid, scaled to one height or
  width, with a gap and background, to a target size if you like. Saved as PNG, JPEG or WebP.
Originals are never changed; results are new files.
"""

from __future__ import annotations

import io
import os
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .db import Database
from .handlers import handler_for
from .processing import process_file

PAGES_MM = {"A4": (210, 297), "A3": (297, 420), "A5": (148, 210), "Letter": (216, 279), "Tabloid": (279, 432)}
BACKGROUNDS = {"white": (255, 255, 255), "light": (236, 238, 242), "gray": (128, 130, 135), "dark": (24, 26, 31),
               "black": (0, 0, 0)}
MAX_PIXELS = 30_000 * 30_000 // 4   # a joined image bigger than ~225 MP is refused (memory)


class SheetError(ValueError):
    pass


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _rows(db: Database, ids: list[int]) -> list[dict]:
    out = []
    for i in ids:
        r = db.cat.execute("SELECT id, path, name, ext, hash, status, date_ts FROM files WHERE id = ?", (int(i),)).fetchone()
        if r:
            out.append(dict(r))
    if not out:
        raise SheetError("Select some images first")
    return out


def picture(db: Database, row: dict, max_side: int | None) -> Image.Image | None:
    """The image to place: a cached preview when that is big enough, else the original decoded."""
    want = 1600 if max_side is None or max_side > 512 else 512
    if max_side is not None and max_side <= 1600 and row["hash"] and row["status"] == 1:
        data = db.get_thumb(row["hash"], want) or (db.get_thumb(row["hash"], 1600) if want == 512 else None)
        if data:
            return Image.open(io.BytesIO(data)).convert("RGBA")
    h = handler_for(row["ext"])
    if h is None or not os.path.isfile(row["path"]):
        return None
    if max_side is not None and max_side <= 1600:
        r = process_file(row["path"], (want,))
        if "error" in r or want not in r.get("thumbs", {}):
            return None
        db.put_thumbs(r["hash"], r["thumbs"])
        return Image.open(io.BytesIO(r["thumbs"][want])).convert("RGBA")
    try:   # full resolution (joins): read the original
        if h.streams:
            loaded = h.load(None, row["path"], None)
        else:
            with open(row["path"], "rb") as f:
                loaded = h.load(f.read(), row["path"], None)
    except Exception:
        return None
    img = loaded.image
    if img is None:
        return None
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img.convert("RGBA")


def _flatten(img: Image.Image, bg) -> Image.Image:
    if bg is None:
        return img
    base = Image.new("RGBA", img.size, tuple(bg) + (255,))
    return Image.alpha_composite(base, img).convert("RGB")


def _unique(path: str) -> str:
    stem, ext = os.path.splitext(path)
    cand, k = path, 2
    while os.path.exists(cand):
        cand = f"{stem}_{k}{ext}"
        k += 1
    return cand


def _out_dir(dest: str) -> str:
    dest = os.path.normpath(os.path.abspath(os.path.expanduser(dest or "")))
    if not os.path.isdir(dest):
        if not (dest and os.path.isdir(os.path.dirname(dest))):
            raise SheetError("Choose an existing folder to save into")
        os.makedirs(dest, exist_ok=True)
    return dest


# ---------------------------------------------------------------- contact sheet
def contact_sheet(db: Database, ids: list[int], *, page: str = "A4", landscape: bool = False, dpi: int = 150,
                  width: int | None = None, height: int | None = None, columns: int = 5, caption: str = "name",
                  title: str = "", background: str = "white", page_numbers: bool = True) -> list[Image.Image]:
    rows = _rows(db, ids)
    if page == "custom":
        if not width or not height:
            raise SheetError("Give the page width and height in pixels")
        W, H = int(width), int(height)
    else:
        mm = PAGES_MM.get(page)
        if not mm:
            raise SheetError("Unknown page size")
        W, H = (round(mm[0] / 25.4 * dpi), round(mm[1] / 25.4 * dpi))
    if landscape:
        W, H = max(W, H), min(W, H)
    if W * H > 12000 * 12000:
        raise SheetError("That page is too big")
    columns = max(1, min(int(columns), 20))
    bg = BACKGROUNDS.get(background, BACKGROUNDS["white"])
    fg = (20, 22, 26) if sum(bg) > 380 else (232, 234, 240)
    muted = tuple((a + b) // 2 for a, b in zip(fg, bg))
    margin = round(W * 0.045)
    gap = round(W * 0.018)
    tsize = max(12, round(W * 0.026))
    csize = max(9, round(W * 0.0125))
    head = tsize + gap if title else 0
    foot = csize + gap // 2 if page_numbers else 0
    cell_w = (W - 2 * margin - (columns - 1) * gap) / columns
    cap_lines = {"none": 0, "name": 1, "name_date": 2, "name_tags": 2}.get(caption, 1)
    cap_h = cap_lines * (csize + 3) + (4 if cap_lines else 0)
    cell_h = cell_w + cap_h
    per_col = max(1, int((H - 2 * margin - head - foot + gap) // (cell_h + gap)))
    per_page = columns * per_col
    pages = []
    names = {}
    if caption == "name_tags":
        tn = {r[0]: r[1] for r in db.cat.execute("SELECT id, name FROM tags")}
        for fid, tid in db.cat.execute("SELECT file_id, tag_id FROM file_tags"):
            if tid in tn:
                names.setdefault(fid, []).append(tn[tid].split("/")[-1])
    total_pages = (len(rows) + per_page - 1) // per_page
    if total_pages > 200:
        raise SheetError("More than 200 pages - select fewer images or more columns")
    fcap, ftitle = _font(csize), _font(tsize)
    side = int(cell_w)
    for p in range(total_pages):
        page_img = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(page_img)
        if title:
            d.text((margin, margin), title, fill=fg, font=ftitle)
        for k, row in enumerate(rows[p * per_page:(p + 1) * per_page]):
            cx, cy = k % columns, k // columns
            x = round(margin + cx * (cell_w + gap))
            y = round(margin + head + cy * (cell_h + gap))
            img = picture(db, row, side)
            if img is not None:   # fit the cell (small images are enlarged up to 4x, not more)
                f = min(side / img.width, side / img.height, 4.0)
                img = img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.Resampling.LANCZOS)
                img = _flatten(img, bg)
                page_img.paste(img, (x + (side - img.width) // 2, y + (side - img.height) // 2))
            else:
                d.rectangle((x, y, x + side, y + side), outline=muted)
            ty = y + side + 4
            if cap_lines:
                d.text((x, ty), _fit(d, row["name"], fcap, side), fill=fg, font=fcap)
            if caption == "name_date" and row["date_ts"]:
                d.text((x, ty + csize + 3), datetime.fromtimestamp(row["date_ts"]).strftime("%Y-%m-%d"), fill=muted, font=fcap)
            elif caption == "name_tags" and names.get(row["id"]):
                d.text((x, ty + csize + 3), _fit(d, ", ".join(sorted(names[row["id"]])), fcap, side), fill=muted, font=fcap)
        if page_numbers:
            label = f"{p + 1} / {total_pages}"
            d.text((W - margin - d.textlength(label, font=fcap), H - margin // 2 - csize), label, fill=muted, font=fcap)
        pages.append(page_img)
    return pages


def _fit(d: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


# ---------------------------------------------------------------- join
def join(db: Database, ids: list[int], *, layout: str = "horizontal", columns: int = 3, gap: int = 0,
         background: str = "white", transparent: bool = False, size: str = "smallest",
         target_width: int | None = None, target_height: int | None = None) -> Image.Image:
    """layout: horizontal | vertical | grid. size: smallest | largest (the common height/width the
    images are scaled to). target_width/height: scale the result to fit."""
    rows = _rows(db, ids)
    if len(rows) > 400:
        raise SheetError("Join at most 400 images")
    imgs = [picture(db, r, None) for r in rows]
    imgs = [i for i in imgs if i is not None]
    if not imgs:
        raise SheetError("None of these files can be read as an image")
    gap = max(0, min(int(gap or 0), 2000))
    pick = min if size != "largest" else max
    if layout == "horizontal":
        h = pick(i.height for i in imgs)
        imgs = [i.resize((max(1, round(i.width * h / i.height)), h), Image.Resampling.LANCZOS) if i.height != h else i for i in imgs]
        W, H = sum(i.width for i in imgs) + gap * (len(imgs) - 1), h
        places, x = [], 0
        for i in imgs:
            places.append((x, 0)); x += i.width + gap
    elif layout == "vertical":
        w = pick(i.width for i in imgs)
        imgs = [i.resize((w, max(1, round(i.height * w / i.width))), Image.Resampling.LANCZOS) if i.width != w else i for i in imgs]
        W, H = w, sum(i.height for i in imgs) + gap * (len(imgs) - 1)
        places, y = [], 0
        for i in imgs:
            places.append((0, y)); y += i.height + gap
    elif layout == "grid":
        cols = max(1, min(int(columns), len(imgs)))
        w = pick(i.width for i in imgs)
        imgs = [i.resize((w, max(1, round(i.height * w / i.width))), Image.Resampling.LANCZOS) if i.width != w else i for i in imgs]
        rows_h = [max(i.height for i in imgs[r:r + cols]) for r in range(0, len(imgs), cols)]
        W = cols * w + gap * (cols - 1)
        H = sum(rows_h) + gap * (len(rows_h) - 1)
        places, y = [], 0
        for r, rh in enumerate(rows_h):
            for c, i in enumerate(imgs[r * cols:(r + 1) * cols]):
                places.append((c * (w + gap), y + (rh - i.height) // 2))
            y += rh + gap
    else:
        raise SheetError("Layout must be horizontal, vertical or grid")
    if W * H > MAX_PIXELS:
        raise SheetError(f"The result would be {W} × {H} px - too big. Use 'smallest' or a target size")
    bg = (0, 0, 0, 0) if transparent else BACKGROUNDS.get(background, BACKGROUNDS["white"]) + (255,)
    out = Image.new("RGBA", (W, H), bg)
    for i, (x, y) in zip(imgs, places):
        out.alpha_composite(i, (x, y))
    if target_width or target_height:
        tw = int(target_width) if target_width else None
        th = int(target_height) if target_height else None
        s = min(tw / W if tw else float("inf"), th / H if th else float("inf"))
        out = out.resize((max(1, round(W * s)), max(1, round(H * s))), Image.Resampling.LANCZOS)
    return out


# ---------------------------------------------------------------- saving
def save_pages(pages: list[Image.Image], dest: str, name: str, fmt: str = "pdf", dpi: int = 150) -> list[str]:
    folder = _out_dir(dest)
    stem = _safe_name(name) or f"contact_sheet_{time.strftime('%Y-%m-%d')}"
    if fmt == "pdf":
        path = _unique(os.path.join(folder, stem + ".pdf"))
        pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=float(dpi))
        return [path]
    ext = {"png": ".png", "jpg": ".jpg"}.get(fmt)
    if not ext:
        raise SheetError("Format must be pdf, png or jpg")
    out = []
    for k, p in enumerate(pages):
        path = _unique(os.path.join(folder, stem + (f"_{k + 1:02d}" if len(pages) > 1 else "") + ext))
        p.save(path, dpi=(dpi, dpi), **({"quality": 92} if ext == ".jpg" else {}))
        out.append(path)
    return out


def save_image(img: Image.Image, dest: str, name: str, fmt: str = "png") -> str:
    folder = _out_dir(dest)
    stem = _safe_name(name) or f"joined_{time.strftime('%Y-%m-%d')}"
    ext = {"png": ".png", "jpg": ".jpg", "webp": ".webp"}.get(fmt)
    if not ext:
        raise SheetError("Format must be png, jpg or webp")
    path = _unique(os.path.join(folder, stem + ext))
    if ext == ".jpg":
        _flatten(img, (255, 255, 255)).save(path, quality=92)
    elif ext == ".webp":
        img.save(path, quality=90)
    else:
        img.save(path, optimize=True)
    return path


def _safe_name(name: str) -> str:
    import re
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip()).strip(" .")[:120]


def preview_png(img: Image.Image, max_side: int = 900) -> bytes:
    p = img.copy()
    p.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    p.save(buf, "PNG")
    return buf.getvalue()

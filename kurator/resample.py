"""Resampled copies: a smaller (or larger) version of each selected picture, saved as a new file
next to the original, in a subfolder next to it, or in one folder. Originals are never changed.

* Size: a percentage, the longest side, the width, the height, to fit inside a box, or an exact
  size (the proportions may change - e.g. 512 x 512 textures). By default pictures are only made
  smaller: one that is already small enough is left out, unless "enlarge" is on.
* Filter: Lanczos (sharp, the default), bicubic, bilinear, area average (smooth reductions) or
  nearest neighbour (pixel art: whole pixels - 200 %, 300 % scale it up cleanly).
* Format: as the original where Pillow writes it (PNG, JPEG, WebP, TIFF, BMP, GIF, TGA), or PNG,
  JPEG or WebP. Layered and other work files (PSD, Krita, SVG, HEIC, EXR...) become PNG.
* Animated GIF, WebP and PNG keep every frame and its timing (when saved as GIF, WebP or PNG).
* The colour profile and EXIF (with the original date) are kept, and the copy gets the original's
  modified time, so it sorts next to it.
* In the library the copies are indexed at once, with the original's tags, source, author and
  licence. Undo deletes the copies that are still unchanged.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from PIL import Image, ImageOps, ImageSequence

from . import fileops
from .db import Database
from .handlers import handler_for

FILTERS = {"lanczos": Image.Resampling.LANCZOS, "bicubic": Image.Resampling.BICUBIC,
           "bilinear": Image.Resampling.BILINEAR, "box": Image.Resampling.BOX, "nearest": Image.Resampling.NEAREST}
MODES = ("percent", "long", "width", "height", "fit", "exact")
FORMATS = {"keep": None, "png": ".png", "jpg": ".jpg", "webp": ".webp"}
WHERE = ("beside", "subfolder", "folder")
# what Pillow writes, by extension: the format kept for "as the original"
WRITABLE = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".jfif": "JPEG", ".jpe": "JPEG", ".webp": "WEBP",
            ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP", ".gif": "GIF", ".tga": "TGA"}
ANIMATED_OUT = {"GIF", "WEBP", "PNG"}
PICTURE_GROUPS = {"image", "work", "vector", "document", "texture"}
MAX_PIXELS = 30_000 * 30_000 // 4    # like joined images: bigger results are refused (memory)
MAX_FILES = 20_000


class ResampleError(ValueError):
    pass


def target_size(w: int, h: int, mode: str, a: float, b: float | None = None) -> tuple[int, int]:
    """The new pixel size of a w x h picture."""
    if mode == "exact":
        return max(1, round(a)), max(1, round(b or a))
    if mode == "percent":
        s = a / 100.0
    elif mode == "long":
        s = a / max(w, h)
    elif mode == "width":
        s = a / w
    elif mode == "height":
        s = a / h
    elif mode == "fit":
        s = min(a / w, (b or a) / h)
    else:
        raise ResampleError(f"Unknown size mode {mode!r}")
    return max(1, round(w * s)), max(1, round(h * s))


def _suffix(template: str, w: int, h: int, pct: float) -> str:
    s = (template or "").replace("{w}", str(w)).replace("{h}", str(h)).replace("{pct}", f"{pct:g}")
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)[:60]


def _out_ext(ext: str, fmt: str) -> str:
    if fmt != "keep":
        return FORMATS[fmt]
    return ext if ext in WRITABLE else ".png"


def plan(db: Database, ids: list[int], *, mode: str = "long", value: float = 1024, value2: float | None = None,
         enlarge: bool = False, fmt: str = "keep", where: str = "beside", folder: str = "",
         suffix: str = "_{w}x{h}") -> dict:
    """What would be made: [{'id', 'from', 'to', 'size': [w, h], 'new': [w, h]}] and what is left out."""
    if mode not in MODES:
        raise ResampleError(f"Unknown size mode {mode!r}")
    if fmt not in FORMATS:
        raise ResampleError("Format must be keep, png, jpg or webp")
    if where not in WHERE:
        raise ResampleError("Save next to the originals, in a subfolder or in one folder")
    try:
        value = float(value)
        value2 = float(value2) if value2 not in (None, "", 0) else None
    except (TypeError, ValueError):
        raise ResampleError("The size must be a number") from None
    if value <= 0 or (mode == "percent" and value > 1000) or (mode != "percent" and value > 30000):
        raise ResampleError("The size is out of range")
    if mode in ("fit", "exact") and not value2:
        raise ResampleError("Give a width and a height")
    folder = (folder or "").strip().strip('"')
    if where == "folder":
        if not folder:
            raise ResampleError("Choose a folder to save into")
        folder = os.path.normpath(os.path.abspath(os.path.expanduser(folder)))
    elif where == "subfolder":
        folder = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", folder or "resampled").strip(" .") or "resampled"
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids:
        raise ResampleError("Select some pictures first")
    if len(ids) > MAX_FILES:
        raise ResampleError(f"Resample at most {MAX_FILES:,} files at once")
    items, skipped = [], []
    taken: set[str] = set()
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        rows = {r[0]: r for r in db.cat.execute(
            f"SELECT id, path, name, ext, width, height, status FROM files WHERE id IN ({q})", chunk)}
        for fid in chunk:
            r = rows.get(fid)
            if r is None:
                skipped.append({"id": fid, "reason": "not in catalog"})
                continue
            _, path, name, ext, w, h, status = r
            hd = handler_for(ext)
            if hd is None or hd.kind != "image" or hd.streams or hd.group not in PICTURE_GROUPS:
                skipped.append({"id": fid, "from": path, "reason": "not a picture"})
                continue
            if not os.path.isfile(path):
                skipped.append({"id": fid, "from": path, "reason": "missing on disk"})
                continue
            if not w or not h:
                skipped.append({"id": fid, "from": path, "reason": "size unknown (not indexed yet)"
                                if status != 2 else "can't be read"})
                continue
            nw, nh = target_size(w, h, mode, value, value2)
            if not enlarge and mode != "exact" and nw * nh > w * h:
                skipped.append({"id": fid, "from": path, "reason": "already smaller than that"})
                continue
            if not enlarge and mode == "exact" and (nw > w or nh > h):
                skipped.append({"id": fid, "from": path, "reason": "already smaller than that"})
                continue
            if nw * nh > MAX_PIXELS:
                skipped.append({"id": fid, "from": path, "reason": "the result would be too big"})
                continue
            out_dir = os.path.dirname(path) if where == "beside" else (
                os.path.join(os.path.dirname(path), folder) if where == "subfolder" else folder)
            stem = os.path.splitext(name)[0] + _suffix(suffix, nw, nh, round(100 * nw / w, 1))
            dst = fileops._free_name(os.path.join(out_dir, stem + _out_ext(ext, fmt)), taken)
            taken.add(os.path.normcase(dst))
            items.append({"id": fid, "from": path, "to": dst, "size": [w, h], "new": [nw, nh]})
    return {"items": items, "skipped": skipped, "count": len(items), "mode": mode, "value": value, "value2": value2,
            "enlarge": enlarge, "fmt": fmt}


# ---------------------------------------------------------------- one picture
def _open(path: str, ext: str, need_px: int) -> tuple[list[Image.Image], list[int], dict]:
    """Frames (one for still pictures), frame durations, and the info to keep (ICC, EXIF, loop)."""
    if ext in WRITABLE:
        try:
            with Image.open(path) as im:
                info = {k: im.info[k] for k in ("icc_profile", "exif", "loop", "transparency", "dpi") if k in im.info}
                if getattr(im, "n_frames", 1) > 1 and im.format in ANIMATED_OUT:
                    frames, durations = [], []
                    for fr in ImageSequence.Iterator(im):
                        durations.append(int(fr.info.get("duration", im.info.get("duration", 100)) or 100))
                        frames.append(fr.convert("RGBA"))
                    return frames, durations, info
                im.load()
                img = ImageOps.exif_transpose(im) or im.copy()
                if "exif" in img.info:
                    info["exif"] = img.info["exif"]
                return [img], [], info
        except (OSError, ValueError, Image.DecompressionBombError):
            pass                     # e.g. a TIFF with an unusual compression: Kurator's handler next
    h = handler_for(ext)
    if h is None:
        raise ResampleError("can't read this picture")
    with open(path, "rb") as f:
        data = f.read()
    loaded = h.load(data, path, need_px if h.group in ("vector", "document") else None)
    if loaded.image is None or loaded.meta.get("placeholder"):
        raise ResampleError("no picture in this file")
    img = loaded.image
    try:
        img = ImageOps.exif_transpose(img) or img
    except Exception:
        pass
    return [img], [], {}


def _prepare(img: Image.Image, fmt: str) -> Image.Image:
    """A mode the filter works in (no palettes: they would be resized pixel by pixel) and the
    output format can take. 16-bit greys (height maps) stay 16-bit in PNG and TIFF."""
    m = img.mode
    if m in ("1", "P", "PA"):
        alpha = m == "PA" or "transparency" in img.info
        return img.convert("RGBA" if alpha else ("L" if m == "1" else "RGB"))
    if m == "I" or m.startswith("I;16"):
        if fmt in ("PNG", "TIFF"):
            return img if m in ("I", "I;16") else img.convert("I")
        return img.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    if m == "F":
        return img.convert("L")
    if m == "CMYK" and fmt not in ("JPEG", "TIFF"):
        return img.convert("RGB")
    if m in ("L", "LA", "RGB", "RGBA", "CMYK"):
        return img
    return img.convert("RGBA" if "A" in img.getbands() else "RGB")


def _fit_mode(img: Image.Image, fmt: str) -> Image.Image:
    """After resizing: what the format can store (JPEG has no alpha: flattened onto white)."""
    if fmt == "JPEG" and img.mode in ("RGBA", "LA"):
        base = Image.new("RGB", img.size, (255, 255, 255))
        base.paste(img.convert("RGBA"), mask=img.getchannel("A"))
        return base
    if fmt in ("BMP", "TGA") and img.mode in ("LA", "CMYK"):
        return img.convert("RGBA" if img.mode == "LA" else "RGB")
    return img


def resample_file(src: str, dst: str, new: tuple[int, int], filt: str = "lanczos", quality: int = 90,
                  keep_date: bool = True) -> None:
    """Write a resampled copy of src at dst (which must not exist yet)."""
    ext = os.path.splitext(src)[1].lower()
    out_ext = os.path.splitext(dst)[1].lower()
    fmt = WRITABLE[out_ext]
    frames, durations, info = _open(src, ext, max(new))
    if fmt not in ANIMATED_OUT:
        frames = frames[:1]
    rs = FILTERS.get(filt, Image.Resampling.LANCZOS)
    out = []
    for fr in frames:
        fr = _prepare(fr, fmt)
        fr = fr.resize(tuple(new), rs) if fr.size != tuple(new) else fr
        out.append(_fit_mode(fr, fmt))
    first = out[0]
    kw: dict = {}
    icc = info.get("icc_profile")
    if icc and (frames[0].mode != "CMYK" or first.mode == "CMYK"):
        kw["icc_profile"] = icc            # (a CMYK profile only stays with CMYK pixels)
    exif = info.get("exif")
    if exif and fmt in ("JPEG", "WEBP", "PNG"):
        try:
            ex = Image.Exif()
            ex.load(exif)
            ifd = ex.get_ifd(0x8769)
            if 0xA002 in ifd or 0xA003 in ifd:   # pixel size recorded in the EXIF
                ifd[0xA002], ifd[0xA003] = first.width, first.height
            kw["exif"] = ex.tobytes()
        except Exception:
            kw["exif"] = exif
    if "dpi" in info and fmt in ("JPEG", "PNG", "TIFF"):
        kw["dpi"] = info["dpi"]
    if fmt == "JPEG":
        kw.update(quality=max(1, min(int(quality), 100)), subsampling=0 if quality >= 90 else 2, optimize=True)
    elif fmt == "WEBP":
        kw.update(quality=max(1, min(int(quality), 100)), method=4, lossless=quality >= 100)
    elif fmt == "PNG":
        kw.update(optimize=len(out) == 1 and first.width * first.height <= 4_000_000)
    elif fmt == "TIFF":
        kw.update(compression="tiff_lzw")
    if len(out) > 1:
        kw.update(save_all=True, append_images=out[1:], duration=durations, loop=info.get("loop", 0))
        if fmt == "GIF":
            kw["disposal"] = 2
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".kurator-part"
    try:
        first.save(tmp, fmt, **kw)
        if os.path.lexists(dst):
            raise ResampleError(f"Target exists: {dst}")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    if keep_date:
        st = os.stat(src)
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
        birth = getattr(st, "st_birthtime", None)
        if birth and os.name == "nt":
            fileops.set_creation_time(dst, birth)


# ---------------------------------------------------------------- the job
class ResampleJob:
    """Runs in the background: the dialog shows progress, Cancel stops after the current file."""

    def __init__(self, db: Database, scanner, on_done=None):
        self.db = db
        self.scanner = scanner
        self.on_done = on_done            # (new catalog ids, keep_tags) -> None, e.g. XMP sidecars
        self.state: dict = {"running": False}
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    def start(self, p: dict, filt: str = "lanczos", quality: int = 90, keep_date: bool = True,
              keep_tags: bool = True) -> dict:
        if filt not in FILTERS:
            raise ResampleError(f"Unknown filter {filt!r}")
        with self._lock:
            if self.state.get("running"):
                raise ResampleError("Resampling is already running")
            if not p["items"]:
                raise ResampleError("Nothing to resample")
            self._cancel.clear()
            self.state = {"running": True, "done": 0, "total": p["count"], "errors": [], "result": None}
        threading.Thread(target=self._run, args=(p, filt, quality, keep_date, keep_tags),
                         name="kurator-resample", daemon=True).start()
        return dict(self.state)

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, p: dict, filt: str, quality: int, keep_date: bool, keep_tags: bool) -> None:
        st = self.state
        done: list[dict] = []
        roots: dict[str, int | None] = {}
        op_id = None
        try:
            for it in p["items"]:
                if self._cancel.is_set():
                    break
                dst = it["to"]
                try:
                    with self.scanner.fs_lock:
                        if os.path.lexists(dst):
                            dst = fileops._free_name(dst, set())
                        resample_file(it["from"], dst, tuple(it["new"]), filt, quality, keep_date)
                        s = os.stat(dst)
                        new_id = None
                        rid = fileops.indexed_root(self.db, os.path.dirname(dst), roots)
                        if rid is not None:
                            new_id = fileops.add_derived_file(self.db, dst, rid, it["id"], keep_tags)
                    done.append({"id": new_id, "src_id": it["id"], "from": it["from"], "to": dst,
                                 "size": s.st_size, "mtime_ns": s.st_mtime_ns, "sidecars": [],
                                 "own_xmp": not os.path.lexists(dst + ".xmp")})
                except Exception as e:  # noqa: BLE001 - one unreadable picture doesn't stop the rest
                    st["errors"].append(f"{os.path.basename(it['from'])}: {e}")
                st["done"] += 1
        finally:
            if done:
                with self.db.write_lock:
                    op_id = self.db.cat.execute("INSERT INTO operations(ts, kind, data) VALUES (?, ?, ?)",
                                                (time.time(), "resample", json.dumps(done))).lastrowid
            new_ids = [d["id"] for d in done if d["id"] is not None]
            if new_ids:
                self.scanner.request_process()         # index the new files (previews, metadata)
                if self.on_done:
                    try:
                        self.on_done(new_ids, keep_tags)
                    except Exception:  # noqa: BLE001 - sidecars are a convenience
                        pass
            self.scanner._bump()
            st["result"] = {"written": len(done), "failed": len(st["errors"]), "cancelled": self._cancel.is_set(),
                            "op_id": op_id, "new_ids": new_ids, "files": [d["to"] for d in done[:20]]}
            st["running"] = False

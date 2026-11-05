"""Sprite sheets and texture atlases for games.

* Sprite sheet (frames): every frame gets the same cell, in a grid - for animations. Animated
  GIF/WebP/APNG files are split into their frames.
* Atlas (packed): images packed tightly (MaxRects, best short side fit) with transparent borders
  trimmed, padding and edge extrusion against bleeding, optionally power-of-two sized.

Both save a PNG and a JSON file in the TexturePacker "JSON Hash" layout, which Phaser, PixiJS,
Godot and Unity importers read. Originals are never changed.
"""

from __future__ import annotations

import json
import math
import os
import time

import numpy as np
from PIL import Image, ImageSequence

from .handlers import handler_for
from .sheets import SheetError, _out_dir, _rows, _safe_name, _unique, picture

MAX_SPRITES = 2000
MAX_SIDE = 16384
ANIMATED = {".gif", ".webp", ".png", ".apng"}


class Sprite:
    __slots__ = ("name", "img", "src_w", "src_h", "off_x", "off_y", "x", "y", "group")

    def __init__(self, name: str, img: Image.Image, group: str):
        self.name, self.img, self.group = name, img, group
        self.src_w, self.src_h = img.size
        self.off_x = self.off_y = 0
        self.x = self.y = 0


def _frames(row: dict) -> list[Image.Image] | None:
    """All frames of an animated picture, or None when it is not animated."""
    if row["ext"] not in ANIMATED or not os.path.isfile(row["path"]):
        return None
    try:
        with Image.open(row["path"]) as im:
            if getattr(im, "n_frames", 1) < 2:
                return None
            out = []
            for f in ImageSequence.Iterator(im):
                out.append(f.convert("RGBA"))
                if len(out) >= MAX_SPRITES:
                    break
            return out
    except Exception:
        return None


def _load(db, ids: list[int], expand: bool, scale: float) -> list[Sprite]:
    rows = [r for r in _rows(db, ids) if (h := handler_for(r["ext"])) is not None and h.kind == "image"]
    sprites: list[Sprite] = []
    seen: dict[str, int] = {}

    def add(name: str, img: Image.Image, group: str):
        if scale != 1:
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.Resampling.LANCZOS)
        k = seen.get(name.lower(), 0)
        seen[name.lower()] = k + 1
        if k:
            stem, ext = os.path.splitext(name)
            name = f"{stem}_{k + 1}{ext}"
        sprites.append(Sprite(name, img, group))

    for r in rows:
        stem = os.path.splitext(r["name"])[0]
        frames = _frames(r) if expand else None
        if frames:
            for k, f in enumerate(frames):
                add(f"{stem}_{k:03d}.png", f, stem)
        else:
            img = picture(db, r, None)
            if img is not None:
                add(r["name"], img, "")
        if len(sprites) > MAX_SPRITES:
            raise SheetError(f"At most {MAX_SPRITES} sprites in one sheet")
    if not sprites:
        raise SheetError("None of these files can be read as an image")
    return sprites


def _trim(s: Sprite) -> None:
    box = s.img.getchannel("A").getbbox()
    if box is None:      # fully transparent: keep one pixel
        box = (0, 0, 1, 1)
    if box != (0, 0, s.img.width, s.img.height):
        s.off_x, s.off_y = box[0], box[1]
        s.img = s.img.crop(box)


# ---------------------------------------------------------------- packing
def maxrects(sizes: list[tuple[int, int]], W: int, H: int) -> list[tuple[int, int]] | None:
    """Place rectangles (already including padding) in a W × H bin. None when they do not fit."""
    free = [(0, 0, W, H)]
    out: list[tuple[int, int] | None] = [None] * len(sizes)
    order = sorted(range(len(sizes)), key=lambda i: (-max(sizes[i]), -min(sizes[i]), i))
    for i in order:
        w, h = sizes[i]
        best = None
        for fx, fy, fw, fh in free:
            if w <= fw and h <= fh:
                score = (min(fw - w, fh - h), max(fw - w, fh - h), fy, fx)
                if best is None or score < best[0]:
                    best = (score, fx, fy)
        if best is None:
            return None
        _, x, y = best
        out[i] = (x, y)
        keep, new = [], []
        for f in free:
            fx, fy, fw, fh = f
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                keep.append(f)
                continue
            if x > fx:
                new.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                new.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                new.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                new.append((fx, y + h, fw, fy + fh - y - h))
        # drop free rectangles inside another one; the untouched ones were already pruned among themselves
        new.sort(key=lambda r: -(r[2] * r[3]))
        added: list[tuple[int, int, int, int]] = []
        for r in new:
            rx, ry, rr, rb = r[0], r[1], r[0] + r[2], r[1] + r[3]
            if any(rx >= k[0] and ry >= k[1] and rr <= k[0] + k[2] and rb <= k[1] + k[3] for k in added) or \
               any(rx >= k[0] and ry >= k[1] and rr <= k[0] + k[2] and rb <= k[1] + k[3] for k in keep):
                continue
            added.append(r)
        if added:
            keep = [k for k in keep if not any(k[0] >= r[0] and k[1] >= r[1] and k[0] + k[2] <= r[0] + r[2]
                                               and k[1] + k[3] <= r[1] + r[3] for r in added)]
        free = keep + added
    return out  # type: ignore[return-value]


def _pot(n: int) -> int:
    return 1 << max(0, math.ceil(math.log2(max(1, n))))


def _pack(sizes: list[tuple[int, int]], pot: bool, max_side: int) -> tuple[int, int, list[tuple[int, int]]]:
    area = sum(w * h for w, h in sizes)
    mw, mh = max(w for w, _ in sizes), max(h for _, h in sizes)
    if mw > max_side or mh > max_side:
        raise SheetError(f"A sprite is larger than the {max_side} px limit")
    if pot:
        cands = sorted({(a, b) for a in (1 << k for k in range(4, 15)) for b in (1 << k for k in range(4, 15))
                        if a >= mw and b >= mh and a <= max_side and b <= max_side and a * b >= area and max(a, b) <= 2 * min(a, b)},
                       key=lambda s: (s[0] * s[1], abs(s[0] - s[1]), -s[0]))
        for W, H in cands:
            places = maxrects(sizes, W, H)
            if places is not None:
                return W, H, places
    else:
        side = max(mw, mh, math.ceil(math.sqrt(area * 1.04)))
        while side <= max_side:
            for W, H in ((side, side), (max(mw, side * 2), max(mh, side // 2))) if side * 2 <= max_side else ((side, side),):
                places = maxrects(sizes, W, H)
                if places is not None:
                    used_w = max(x + w for (x, _), (w, _) in zip(places, sizes))
                    used_h = max(y + h for (_, y), (_, h) in zip(places, sizes))
                    return used_w, used_h, places
            side = math.ceil(side * 1.06) + 1
    raise SheetError(f"The sprites do not fit in {max_side} × {max_side} px - lower the scale or pick fewer")


def _extruded(img: Image.Image, e: int) -> Image.Image:
    if e <= 0:
        return img
    a = np.asarray(img)
    return Image.fromarray(np.pad(a, ((e, e), (e, e), (0, 0)), mode="edge"), "RGBA")


# ---------------------------------------------------------------- the two kinds
def build(db, ids: list[int], *, mode: str = "atlas", columns: int = 0, padding: int = 2, extrude: int = 0,
          trim: bool = True, pot: bool = False, max_side: int = 4096, scale: float = 1.0, expand: bool = True,
          cell_w: int | None = None, cell_h: int | None = None) -> tuple[Image.Image, list[Sprite], dict]:
    """mode: 'atlas' (packed) or 'frames' (grid of equal cells). Returns (image, sprites, stats)."""
    padding = max(0, min(int(padding or 0), 64))
    extrude = max(0, min(int(extrude or 0), 8))
    scale = max(0.05, min(float(scale or 1), 4.0))
    max_side = max(64, min(int(max_side or 4096), MAX_SIDE))
    sprites = _load(db, ids, expand, scale)
    if mode == "frames":
        if cell_w or cell_h:   # fit each frame into a fixed cell
            cw, ch = int(cell_w or cell_h), int(cell_h or cell_w)
            for s in sprites:
                k = min(cw / s.img.width, ch / s.img.height)
                if k != 1:
                    s.img = s.img.resize((max(1, round(s.img.width * k)), max(1, round(s.img.height * k))), Image.Resampling.LANCZOS)
                s.src_w, s.src_h = cw, ch
        else:
            cw, ch = max(s.img.width for s in sprites), max(s.img.height for s in sprites)
        n = len(sprites)
        cols = max(1, min(int(columns or 0) or math.ceil(math.sqrt(n * ch / max(1, cw))), n))
        rows = math.ceil(n / cols)
        step_w, step_h = cw + 2 * extrude + padding, ch + 2 * extrude + padding
        W, H = padding + cols * step_w, padding + rows * step_h
        if pot:
            W, H = _pot(W), _pot(H)
        if W > max_side or H > max_side:
            raise SheetError(f"The sheet would be {W} × {H} px, over the {max_side} px limit - use fewer columns, a smaller cell or scale")
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for k, s in enumerate(sprites):
            cx, cy = padding + (k % cols) * step_w + extrude, padding + (k // cols) * step_h + extrude
            # centre in the cell (frames of one animation are usually the same size anyway)
            ox, oy = (cw - s.img.width) // 2, (ch - s.img.height) // 2
            s.x, s.y = cx, cy
            s.off_x, s.off_y = ox, oy
            s.src_w, s.src_h = cw, ch
            full = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            full.alpha_composite(s.img, (ox, oy))
            s.img = full
            out.alpha_composite(_extruded(full, extrude), (cx - extrude, cy - extrude))
        stats = {"columns": cols, "rows": rows, "cell": [cw, ch]}
    elif mode == "atlas":
        if trim:
            for s in sprites:
                _trim(s)
        sizes = [(s.img.width + 2 * extrude + padding, s.img.height + 2 * extrude + padding) for s in sprites]
        W, H, places = _pack(sizes, pot, max_side - padding)
        W, H = W + padding, H + padding
        if pot:
            W, H = _pot(W), _pot(H)
            if W > max_side or H > max_side:
                raise SheetError(f"The sprites do not fit in {max_side} × {max_side} px with this padding")
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for s, (x, y) in zip(sprites, places):
            s.x, s.y = padding + x + extrude, padding + y + extrude
            out.alpha_composite(_extruded(s.img, extrude), (s.x - extrude, s.y - extrude))
        used = sum(s.img.width * s.img.height for s in sprites)
        stats = {"fill": round(used / (W * H), 3)}
    else:
        raise SheetError("Mode must be atlas or frames")
    stats.update({"sprites": len(sprites), "width": out.width, "height": out.height})
    return out, sprites, stats


def describe(sprites: list[Sprite], image_name: str, size: tuple[int, int], mode: str) -> dict:
    """TexturePacker JSON Hash (+ 'animations' for frame groups, as PixiJS reads them)."""
    frames = {}
    groups: dict[str, list[str]] = {}
    for s in sprites:
        w, h = s.img.size
        trimmed = (w, h) != (s.src_w, s.src_h)
        frames[s.name] = {
            "frame": {"x": s.x, "y": s.y, "w": w, "h": h}, "rotated": False, "trimmed": trimmed and mode == "atlas",
            "spriteSourceSize": {"x": s.off_x if mode == "atlas" else 0, "y": s.off_y if mode == "atlas" else 0, "w": w, "h": h},
            "sourceSize": {"w": s.src_w if mode == "atlas" else w, "h": s.src_h if mode == "atlas" else h},
            "pivot": {"x": 0.5, "y": 0.5},
        }
        groups.setdefault(s.group or ("frames" if mode == "frames" else ""), []).append(s.name)
    data = {"frames": frames}
    anims = {k: v for k, v in groups.items() if k and len(v) > 1}
    if anims:
        data["animations"] = anims
    from . import __version__
    data["meta"] = {"app": "https://github.com/eigensoundlab/kurator", "version": __version__, "image": image_name,
                    "format": "RGBA8888", "size": {"w": size[0], "h": size[1]}, "scale": "1"}
    return data


def _godot_path(png: str) -> str:
    """res:// path inside a Godot project, else the plain file name (Godot resolves it next to the .tres)."""
    from .projectdirs import engine_root
    root = engine_root(png)
    if root and root[0] == "Godot":
        return "res://" + os.path.relpath(png, root[1]).replace(os.sep, "/")
    return os.path.basename(png)


def _tres_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _region(s: Sprite) -> str:
    return f"Rect2({s.x}, {s.y}, {s.img.width}, {s.img.height})"


def _margin(s: Sprite, mode: str) -> str | None:
    if mode != "atlas" or (s.img.size == (s.src_w, s.src_h) and not (s.off_x or s.off_y)):
        return None
    return f"Rect2({s.off_x}, {s.off_y}, {s.src_w - s.img.width}, {s.src_h - s.img.height})"


def godot_resources(sprites: list[Sprite], png: str, mode: str, fps: float = 12.0) -> list[str]:
    """Godot 4 resources: a SpriteFrames .tres with the animations (sprite sheets, split GIFs), and
    for atlases one AtlasTexture .tres per sprite in a folder next to the PNG."""
    out = []
    folder, stem = os.path.dirname(png), os.path.splitext(os.path.basename(png))[0]
    path = _godot_path(png)
    groups: dict[str, list[Sprite]] = {}
    for s in sprites:
        groups.setdefault(s.group or "default", []).append(s)
    anims = {k: v for k, v in groups.items() if mode == "frames" or (k != "default" and len(v) > 1)}
    if anims:
        subs, lines, n = [], [], 0
        ids: dict[int, str] = {}
        for frames in anims.values():
            for s in frames:
                if id(s) in ids:
                    continue
                ids[id(s)] = f"AtlasTexture_{n}"
                n += 1
                sub = [f'[sub_resource type="AtlasTexture" id="{ids[id(s)]}"]', 'atlas = ExtResource("1_sheet")', f"region = {_region(s)}"]
                m = _margin(s, mode)
                if m:
                    sub.append(f"margin = {m}")
                subs.append("\n".join(sub))
        for name, frames in anims.items():
            fr = ",\n".join('{\n"duration": 1.0,\n"texture": SubResource("' + ids[id(s)] + '")\n}' for s in frames)
            lines.append('{\n"frames": [' + fr + '],\n"loop": true,\n"name": &' + _tres_str(name) + f',\n"speed": {float(fps):g}\n}}')
        text = (f'[gd_resource type="SpriteFrames" load_steps={2 + len(subs)} format=3]\n\n'
                f'[ext_resource type="Texture2D" path={_tres_str(path)} id="1_sheet"]\n\n' + "\n\n".join(subs)
                + "\n\n[resource]\nanimations = [" + ", ".join(lines) + "]\n")
        f = _unique(os.path.join(folder, stem + "_frames.tres"))
        with open(f, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        out.append(f)
    if mode == "atlas":
        sub = os.path.join(folder, stem + "_sprites")
        os.makedirs(sub, exist_ok=True)
        rel = path if path.startswith("res://") else "../" + path
        for s in sprites:
            body = [f'[gd_resource type="AtlasTexture" load_steps=2 format=3]', "",
                    f'[ext_resource type="Texture2D" path={_tres_str(rel)} id="1_atlas"]', "", "[resource]",
                    'atlas = ExtResource("1_atlas")', f"region = {_region(s)}"]
            m = _margin(s, mode)
            if m:
                body.append(f"margin = {m}")
            f = _unique(os.path.join(sub, _safe_name(os.path.splitext(s.name)[0]) + ".tres"))
            with open(f, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(body) + "\n")
            out.append(f)
    return out


def save(img: Image.Image, sprites: list[Sprite], mode: str, dest: str, name: str, godot: bool = False, fps: float = 12.0,
         unity: bool | None = None, ppu: float = 100, pixel_art: bool = False) -> list[str]:
    """unity: write Unity's .meta with the sprites sliced (None: when saved inside a Unity project)."""
    folder = _out_dir(dest)
    stem = _safe_name(name) or f"{'spritesheet' if mode == 'frames' else 'atlas'}_{time.strftime('%Y-%m-%d')}"
    png = _unique(os.path.join(folder, stem + ".png"))
    stem = os.path.splitext(os.path.basename(png))[0]
    js = os.path.join(folder, stem + ".json")
    img.save(png, optimize=True)
    with open(js, "w", encoding="utf-8") as f:
        json.dump(describe(sprites, os.path.basename(png), img.size, mode), f, indent=1)
    out = [png, js]
    if unity is None:
        from .projectdirs import engine_root
        root = engine_root(png)
        unity = bool(root and root[0] == "Unity")
    if unity:
        from .unity import write_sheet_meta
        out.append(write_sheet_meta(png, img.width, img.height, sprites, mode, ppu, "point" if pixel_art else "bilinear"))
    if godot:
        out += godot_resources(sprites, png, mode, fps)
    return out

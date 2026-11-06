"""Training datasets from library pictures: image + caption .txt pairs.

The layout most trainers read (kohya-ss sd-scripts, OneTrainer, ai-toolkit, SimpleTuner,
diffusers' imagefolder): each picture next to a .txt with its caption, plus a metadata.jsonl
({"file_name", "text"} per line). With a validation share, two folders: train/ and val/.

* captions from the AI captions Kurator made (vision models), from tags, or both; an optional
  trigger word first (e.g. "mystyle, ...")
* identical files once; near-duplicates (perceptual hash) once, keeping the largest picture
* optional downscaling (longest side), format (keep PNG/JPEG/WebP, or convert all), transparency
  kept or flattened onto white
* a seeded random split, so the same settings give the same split again

Nothing in the library changes; the export only writes into the chosen folder.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import threading
import time

import numpy as np

from .db import Database

CAPTIONS = {"caption": "AI caption", "tags": "Tags", "both": "AI caption, then tags", "none": "No captions"}
FORMATS = {"keep": "Keep (others as PNG)", "png": "PNG", "jpg": "JPEG (quality 95)", "webp": "WebP (lossless)"}
TRAINABLE = {".png", ".jpg", ".jpeg", ".webp"}
NEAR = 4                      # dHash bits that may differ for "the same picture"


class DatasetError(ValueError):
    pass


def _safe(s: str) -> str:
    s = re.sub(r"[^\w\-. ]+", "_", s, flags=re.UNICODE).strip(" .")
    return s[:80] or "dataset"


def _near_groups(phashes: list[int | None]) -> list[int]:
    """Union-find over perceptual hashes: index of the group each picture belongs to."""
    n = len(phashes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    idx = [i for i, p in enumerate(phashes) if p is not None]
    if len(idx) > 1:
        hv = np.array([phashes[i] & 0xFFFFFFFFFFFFFFFF for i in idx], dtype=np.uint64)
        step = max(16, min(1024, 4_000_000 // len(idx)))     # bounded memory for big libraries
        for start in range(0, len(idx), step):
            block = hv[start:start + step]
            dist = np.bitwise_count(block[:, None] ^ hv[None, :])
            for a, b in zip(*np.nonzero(dist <= NEAR)):
                i, j = idx[start + a], idx[b]
                if i < j:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[rj] = ri
    return [find(i) for i in range(n)]


def plan(db: Database, ids: list[int], dest: str, folder: str = "dataset", captions: str = "both",
         trigger: str = "", tag_facets: list[str] | None = None, near_dupes: bool = True, max_side: int = 0,
         fmt: str = "keep", flatten: bool = False, val: float = 0.0, seed: int = 1, names: str = "numbers") -> dict:
    if captions not in CAPTIONS:
        raise DatasetError("unknown caption source")
    if fmt not in FORMATS:
        raise DatasetError("unknown format")
    dest = (dest or "").strip()
    if not dest or not os.path.isabs(dest):
        raise DatasetError("Choose a folder for the dataset (a full path)")
    folder = _safe(folder or "dataset")
    val = min(0.5, max(0.0, float(val or 0)))
    max_side = max(0, int(max_side or 0))
    facets = tag_facets or ["subject"]
    if not ids:
        raise DatasetError("No pictures chosen")
    q = ",".join("?" * len(ids))
    rows = db.cat.execute(
        f"SELECT id, path, name, ext, hash, width, height, phash, meta FROM files WHERE id IN ({q}) AND kind = 'image' "
        "AND status = 1", ids).fetchall()
    skipped = {"not_picture": len(ids) - len(rows), "identical": 0, "near": 0, "missing": 0, "no_caption": 0}
    good = []
    for r in rows:
        meta = json.loads(r[8]) if r[8] else {}
        if meta.get("placeholder") or not os.path.isfile(r[1]):
            skipped["missing" if not os.path.isfile(r[1]) else "not_picture"] += 1
            continue
        good.append({"id": r[0], "path": r[1], "name": r[2], "ext": r[3].lower(), "hash": r[4],
                     "w": r[5] or 0, "h": r[6] or 0, "phash": r[7]})
    # captions and tags (from the chosen tag groups) of every candidate
    cap: dict[int, str] = {}
    tagtxt: dict[int, list[str]] = {}
    fids = [g["id"] for g in good]
    fq = ",".join("?" * len(facets))
    for start in range(0, len(fids), 900):
        part = fids[start:start + 900]
        qq = ",".join("?" * len(part))
        for fid, text in db.cat.execute(f"SELECT file_id, text FROM captions WHERE file_id IN ({qq}) ORDER BY created", part):
            cap[fid] = text.strip()                    # the newest wins
        for fid, name in db.cat.execute(
                f"SELECT ft.file_id, t.name FROM file_tags ft JOIN tags t ON t.id = ft.tag_id WHERE ft.file_id IN ({qq}) "
                f"AND t.facet IN ({fq}) ORDER BY t.name", part + facets):
            tagtxt.setdefault(fid, []).append(name.split("/")[-1].strip().lower())
    for g in good:
        g["caption"] = cap.get(g["id"], "")
        g["tags"] = tagtxt.get(g["id"], [])

    def better(a: dict, b: dict) -> bool:
        """Which copy of the same picture goes into the dataset: the bigger one, then the described one."""
        ka = (a["w"] * a["h"], bool(a["caption"]), len(a["tags"]), -len(a["name"]), a["name"])
        kb = (b["w"] * b["h"], bool(b["caption"]), len(b["tags"]), -len(b["name"]), b["name"])
        return ka > kb

    def merge(groups: dict) -> list[dict]:
        """One picture per group; it gets the group's best caption and all of its tags."""
        out = []
        for members in groups.values():
            best = members[0]
            for m in members[1:]:
                if better(m, best):
                    best = m
            if not best["caption"]:
                best["caption"] = next((m["caption"] for m in members if m["caption"]), "")
            best["tags"] = sorted({t for m in members for t in m["tags"]})
            out.append(best)
        return out

    # identical files once
    by_hash: dict = {}
    for g in good:
        by_hash.setdefault(g["hash"] or ("file", g["id"]), []).append(g)
    uniq = merge(by_hash)
    skipped["identical"] = len(good) - len(uniq)
    # near-duplicates: the biggest picture of each group
    if near_dupes and len(uniq) > 1:
        groups = _near_groups([g["phash"] for g in uniq])
        by_group: dict = {}
        for g, gid in zip(uniq, groups):
            by_group.setdefault(gid, []).append(g)
        n = len(uniq)
        uniq = merge(by_group)
        skipped["near"] = n - len(uniq)
    uniq.sort(key=lambda g: g["path"].lower())
    from .fileinfo import get_many
    infos = get_many(db, [g["id"] for g in uniq])
    for g in uniq:
        g["info"] = infos.get(g["id"], {})

    trigger = (trigger or "").strip().strip(",")
    for g in uniq:
        parts = []
        if captions in ("caption", "both") and g["caption"]:
            parts.append(g["caption"].rstrip("."))
        if captions in ("tags", "both") and g["tags"]:
            parts.append(", ".join(dict.fromkeys(g["tags"])))
        text = ", ".join(parts)
        if captions != "none" and not text:
            skipped["no_caption"] += 1
        if trigger:
            text = f"{trigger}, {text}" if text else trigger
        g["text"] = text

    # split and names
    order = list(range(len(uniq)))
    random.Random(seed).shuffle(order)
    n_val = int(round(len(uniq) * val)) if len(uniq) > 1 else 0
    val_set = set(order[:n_val])
    used: set = set()
    root = os.path.join(dest, folder)
    for k, g in enumerate(uniq):
        split = "val" if k in val_set else ("train" if n_val else "")
        out_ext = g["ext"] if fmt == "keep" and g["ext"] in TRAINABLE else "." + ("png" if fmt == "keep" else fmt)
        if out_ext == ".jpeg":
            out_ext = ".jpg"
        stem = f"{k + 1:05d}" if names == "numbers" else _safe(os.path.splitext(g["name"])[0])
        base, i = stem, 2
        while (split, stem.lower()) in used:
            stem, i = f"{base}_{i}", i + 1
        used.add((split, stem.lower()))
        g["split"] = split
        g["out"] = os.path.join(split, stem + out_ext) if split else stem + out_ext
        g["convert"] = (out_ext.lstrip(".") != g["ext"].lstrip(".").replace("jpeg", "jpg") or bool(max_side and max(g["w"], g["h"]) > max_side)
                        or (flatten and out_ext != ".jpg"))
    return {"dest": dest, "root": root, "folder": folder, "items": uniq, "skipped": skipped, "max_side": max_side,
            "fmt": fmt, "flatten": bool(flatten), "captions": captions, "val": n_val, "seed": seed,
            "count": len(uniq)}


def summary(p: dict) -> dict:
    items = p["items"]
    exists = os.path.isdir(p["root"]) and bool(os.listdir(p["root"]))
    return {"count": p["count"], "train": p["count"] - p["val"], "val": p["val"], "skipped": p["skipped"],
            "no_licence": sum(1 for g in items if not g["info"].get("licence")),
            "root": p["root"], "folder_not_empty": exists, "convert": sum(1 for g in items if g["convert"]),
            "examples": [{"file": g["out"], "text": g["text"]} for g in items[:6]]}


class DatasetJob:
    def __init__(self, db: Database):
        self.db = db
        self.state: dict = {"running": False}
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    def start(self, p: dict) -> dict:
        with self._lock:
            if self.state.get("running"):
                raise DatasetError("A dataset export is already running")
            if not p["items"]:
                raise DatasetError("Nothing to export")
            self._cancel.clear()
            self.state = {"running": True, "done": 0, "total": p["count"], "root": p["root"], "errors": [], "result": None}
        threading.Thread(target=self._run, args=(p,), name="kurator-dataset", daemon=True).start()
        return dict(self.state)

    def cancel(self):
        self._cancel.set()

    def _picture(self, g: dict, p: dict, out: str) -> None:
        from PIL import Image

        from .handlers import handler_for
        img = None
        if g["ext"] in TRAINABLE or g["ext"] in (".bmp", ".tif", ".tiff", ".tga", ".gif"):
            try:
                with Image.open(g["path"]) as im:
                    im.load()
                    img = im.copy()
            except Exception:
                img = None
        if img is None:                                  # PSD, Krita, SVG, HEIC, ... through Kurator's handlers
            h = handler_for(g["ext"])
            if h is None:
                raise DatasetError("can't read this picture")
            with open(g["path"], "rb") as f:
                data = f.read()
            loaded = h.load(data, g["path"], max(p["max_side"], 4096) if p["max_side"] else 8192)
            if loaded.image is None or loaded.meta.get("placeholder"):
                raise DatasetError("no picture to export")
            img = loaded.image
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
        alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if alpha else "RGB")
        ext = os.path.splitext(out)[1]
        if alpha and (p["flatten"] or ext == ".jpg"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
        m = p["max_side"]
        if m and max(img.size) > m:
            img.thumbnail((m, m), Image.Resampling.LANCZOS)
        tmp = out + ".kurator-part"
        if ext == ".jpg":
            img.save(tmp, "JPEG", quality=95, subsampling=0)
        elif ext == ".webp":
            img.save(tmp, "WEBP", lossless=True)
        else:
            img.save(tmp, "PNG")
        os.replace(tmp, out)

    def _run(self, p: dict) -> None:
        st = self.state
        written = 0
        meta: dict[str, list] = {}
        manifest = []
        try:
            for g in p["items"]:
                if self._cancel.is_set():
                    break
                out = os.path.join(p["root"], g["out"])
                try:
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    if g["convert"]:
                        self._picture(g, p, out)
                    else:
                        tmp = out + ".kurator-part"
                        shutil.copyfile(g["path"], tmp)
                        os.replace(tmp, out)
                    if p["captions"] != "none" or g["text"]:
                        with open(os.path.splitext(out)[0] + ".txt", "w", encoding="utf-8", newline="\n") as f:
                            f.write(g["text"] + "\n")
                    meta.setdefault(g["split"], []).append({"file_name": os.path.basename(g["out"]), "text": g["text"]})
                    manifest.append({"file": g["out"].replace(os.sep, "/"), "original": g["path"], "text": g["text"],
                                     **{k: v for k, v in g["info"].items() if v}})
                    written += 1
                except Exception as e:
                    st["errors"].append(f"{g['path']}: {e}")
                st["done"] += 1
            try:
                for split, rows in meta.items():
                    with open(os.path.join(p["root"], split, "metadata.jsonl"), "w", encoding="utf-8", newline="\n") as f:
                        for r in rows:
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
                with open(os.path.join(p["root"], "kurator_dataset.json"), "w", encoding="utf-8") as f:
                    json.dump({"exported": time.strftime("%Y-%m-%dT%H:%M:%S"), "captions": p["captions"],
                               "max_side": p["max_side"], "format": p["fmt"], "seed": p["seed"],
                               "skipped": p["skipped"], "files": manifest}, f, ensure_ascii=False, indent=1)
            except OSError as e:
                st["errors"].append(f"metadata: {e}")
        finally:
            st["result"] = {"written": written, "failed": len(st["errors"]), "cancelled": self._cancel.is_set(),
                            "folder": p["root"]}
            st["running"] = False

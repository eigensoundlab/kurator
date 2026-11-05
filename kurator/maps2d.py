"""Companion maps of 2D art: hero.png with hero_normal.png, hero_glow.png, hero_ao.png,
hero_depth.png, hero_sky.png ... (lighting and parallax effects in 2D engines: Godot CanvasTexture,
Unity 2D lights' secondary textures, Sprite DLight / Laigter output).

A map belongs to a picture when it sits in the same folder, is named <picture>_<suffix> (also
"-" or "."), and has the same proportions. Known suffixes get a role the lit preview uses; other
suffixes (_sky, _bg...) are layers you can look at, as long as a known map is there as well, so
that versions like hero_v2 or hero_final are never taken for maps.
"""

from __future__ import annotations

import os
import re

from .db import Database
from .stacks import version_key

ROLE_WORDS = {
    "base": ["diffuse", "albedo", "color", "colour", "basecolor", "base", "diff", "col", "c", "d"],
    "normal": ["normal", "normals", "normalmap", "nrm", "nor", "norm", "nml", "n"],
    "emissive": ["glow", "emission", "emissive", "emit", "illum", "illumination", "light", "lights", "lum", "e", "g"],
    "ao": ["ao", "occlusion", "ambientocclusion", "o"],
    "depth": ["depth", "height", "heightmap", "parallax", "displacement", "disp", "bump", "h", "p", "z"],
    "specular": ["specular", "spec", "gloss", "glossiness", "shine", "s"],
    "mask": ["mask", "maskmap", "m"],
    "roughness": ["roughness", "rough", "r"],
    "background": ["sky", "bg", "background", "back", "backdrop"],
}
WORD_ROLE = {w: r for r, ws in ROLE_WORDS.items() for w in ws}
LABEL = {"base": "Base", "normal": "Normal", "emissive": "Glow", "ao": "AO", "depth": "Depth", "specular": "Specular",
         "mask": "Mask", "roughness": "Roughness", "background": "Background"}
_SPLIT = re.compile(r"^(?P<base>.+?)[_\-.](?P<suffix>[A-Za-z][A-Za-z0-9]{0,15})$")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tga", ".tif", ".tiff", ".bmp", ".psd", ".dds", ".exr", ".gif", ".kra", ".ora"}


def _split(stem: str) -> tuple[str, str] | None:
    m = _SPLIT.match(stem)
    return (m.group("base"), m.group("suffix")) if m else None


def _same_shape(a: dict, b: dict) -> bool:
    if not (a.get("width") and a.get("height") and b.get("width") and b.get("height")):
        return True   # not indexed yet: trust the name
    return abs(a["width"] / a["height"] - b["width"] / b["height"]) < 0.01


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def group(db: Database, file_id: int) -> dict | None:
    """{'main': {...}, 'maps': [{id, name, role, label, ...}], 'role': role of the asked file} or None."""
    row = db.cat.execute("SELECT id, dir, name, ext, width, height FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row or row[3] not in IMAGE_EXTS:
        return None
    me = dict(zip(("id", "dir", "name", "ext", "width", "height"), row))
    stem = os.path.splitext(me["name"])[0]
    bases = [stem]
    sp = _split(stem)
    if sp:
        bases.insert(0, sp[0])   # this may itself be a map (hero_normal): its picture is "hero"
    for base in bases:
        rows = db.cat.execute(
            "SELECT id, name, ext, width, height, status, kind FROM files WHERE dir = ? AND kind = 'image' "
            "AND (name LIKE ? ESCAPE '\\' )", (me["dir"], _escape(base) + "%")).fetchall()
        files = [dict(zip(("id", "name", "ext", "width", "height", "status", "kind"), r)) for r in rows]
        files = [f for f in files if f["ext"] in IMAGE_EXTS]
        main = None
        maps = []
        for f in files:
            fs = os.path.splitext(f["name"])[0]
            if fs.lower() == base.lower():
                main = main or f
                continue
            s = _split(fs)
            if not s or s[0].lower() != base.lower():
                continue
            role = WORD_ROLE.get(s[1].lower())
            if role == "base":
                main = main or f   # hero_albedo.png when there is no plain hero.png
                continue
            maps.append({**f, "suffix": s[1], "role": role})
        if main is None or not maps:
            continue
        maps = [m for m in maps if _same_shape(main, m)]
        known = [m for m in maps if m["role"] and (len(m["suffix"]) > 1 or _same_shape(main, m))]
        if not known:
            continue
        out = []
        for m in maps:
            if m["role"] is None and (len(m["suffix"]) < 2 or version_key(m["name"]).lower() == base.lower()
                                      or not (m.get("width") == main.get("width") and m.get("height") == main.get("height"))):
                continue   # unknown suffix: a layer only when it isn't a version name and matches the picture exactly
            role = m["role"] or "layer"
            out.append({"id": m["id"], "name": m["name"], "role": role,
                        "label": LABEL.get(role, m["suffix"].capitalize()), "ext": m["ext"]})
        if not out:
            continue
        order = list(LABEL) + ["layer"]
        out.sort(key=lambda m: (order.index(m["role"]) if m["role"] in order else 99, m["name"].lower()))
        roles = {m["id"]: m["role"] for m in out}
        if me["id"] != main["id"] and me["id"] not in roles:
            continue
        return {"main": {"id": main["id"], "name": main["name"], "ext": main["ext"]}, "maps": out,
                "role": "base" if me["id"] == main["id"] else roles[me["id"]]}
    return None

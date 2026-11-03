"""Game assets: texture sets, the files a 3D model needs, and exporting a project to a game folder.

Texture sets are recognised by name: rock_albedo.png, rock_normal.png, rock_roughness_4k.png...
share the base "rock". A model's companions are the files it references (OBJ .mtl and its maps, glTF
buffers and images, textures named in FBX/3DS/DAE files) plus a texture set with the model's name.
Exports copy (never move) and keep those relative paths, so models still find their textures.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from urllib.parse import unquote

from . import projects
from .db import Database
from .fileops import set_creation_time
from .handlers import handler_for

ROLE_WORDS = {
    "color": ["albedo", "basecolor", "base_color", "basecolour", "diffuse", "diff", "color", "colour", "col", "alb", "bc"],
    "normal": ["normal", "normalgl", "normaldx", "normal_gl", "normal_dx", "nrm", "nor", "norm", "nml"],
    "roughness": ["roughness", "rough", "rgh"],
    "metallic": ["metallic", "metalness", "metal", "mtl"],
    "ao": ["ambientocclusion", "ambient_occlusion", "occlusion", "ao"],
    "height": ["height", "displacement", "disp", "bump"],
    "emissive": ["emissive", "emission", "emit", "glow"],
    "opacity": ["opacity", "alpha", "mask", "transparency"],
    "packed": ["orm", "arm", "mra", "rma", "maskmap"],
    "specular": ["specular", "spec", "gloss", "glossiness"],
}
_WORD_ROLE = {w: r for r, ws in ROLE_WORDS.items() for w in ws}
_ROLE_RE = re.compile(r"^(?P<base>.+?)[ _.\-]+(?P<role>" + "|".join(sorted(map(re.escape, _WORD_ROLE), key=len, reverse=True))
                      + r")(?:[ _.\-]*(?:\d+k|\d{3,4}(?:px)?))?$", re.I)
_RES_RE = re.compile(r"[ _.\-]*(\d+k|\d{3,4}px)$", re.I)
TEXTURE_EXTS = {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".dds", ".bmp", ".webp", ".ktx2", ".psd"}
ROLE_ORDER = ["color", "normal", "roughness", "metallic", "ao", "packed", "height", "emissive", "opacity", "specular"]


def map_role(name: str) -> tuple[str, str] | None:
    """('rock', 'normal') for rock_normal_4k.png; None if the name doesn't look like a texture map."""
    stem, ext = os.path.splitext(name)
    if ext.lower() not in TEXTURE_EXTS:
        return None
    m = _ROLE_RE.match(stem)
    if not m:
        return None
    return _RES_RE.sub("", m.group("base")).lower(), _WORD_ROLE[m.group("role").lower()]


def _base_of(name: str) -> str:
    return _RES_RE.sub("", os.path.splitext(name)[0]).lower()


def texture_set(db: Database, file_id: int) -> dict | None:
    row = db.cat.execute("SELECT dir, name FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        return None
    mr = map_role(row[1])
    if not mr:
        return None
    maps = []
    for fid, name in db.cat.execute("SELECT id, name FROM files WHERE dir = ?", (row[0],)):
        r = map_role(name)
        if r and r[0] == mr[0]:
            maps.append({"id": fid, "name": name, "role": r[1]})
    if len(maps) < 2:
        return None
    maps.sort(key=lambda m: (ROLE_ORDER.index(m["role"]) if m["role"] in ROLE_ORDER else 99, m["name"].lower()))
    return {"base": mr[0], "maps": maps}


def model_companions(path: str, meta: dict | None = None) -> list[str]:
    """Files next to a model that it needs: materials, buffers, textures (absolute paths that exist)."""
    base = os.path.dirname(os.path.abspath(path))
    ext = os.path.splitext(path)[1].lower()
    out: list[str] = []
    seen: set = set()

    def ident(p: str):
        """Same file, even when a case-insensitive disk (Windows, macOS) spells it differently."""
        try:
            st = os.stat(p)
            return (st.st_dev, st.st_ino) if st.st_ino else os.path.normcase(os.path.abspath(p))
        except OSError:
            return os.path.normcase(os.path.abspath(p))

    me = ident(path)

    def add(rel: str):
        rel = unquote(rel.strip().strip('"')).replace("\\", "/")
        if not rel or rel.startswith("data:"):
            return
        p = os.path.normpath(os.path.join(base, rel)) if not os.path.isabs(rel) else os.path.normpath(rel)
        if not os.path.isfile(p):   # textures referenced with an absolute path from the artist's PC: look nearby
            p = _find_nearby(base, os.path.basename(rel))
        if p and os.path.isfile(p):
            k = ident(p)
            if k not in seen and k != me:
                seen.add(k)
                out.append(p)

    try:
        if ext == ".obj":
            with open(path, "rb") as f:
                head = f.read(4 * 1024 * 1024)
            for m in re.finditer(rb"^mtllib\s+(.+?)\s*$", head, re.M):
                lib = m.group(1).decode("utf-8", "replace")
                add(lib)
                mp = os.path.join(base, lib)
                if os.path.isfile(mp):
                    for line in open(mp, encoding="utf-8", errors="replace"):
                        p = line.strip().split()
                        if p and (p[0].lower().startswith("map_") or p[0].lower() in ("bump", "disp", "decal", "refl", "norm")):
                            add(p[-1])
        elif ext == ".gltf":
            with open(path, "rb") as f:
                doc = json.loads(f.read())
            for b in doc.get("buffers", []) + doc.get("images", []):
                if b.get("uri"):
                    add(b["uri"])
        else:
            m = (meta or {}).get("model") or {}
            for t in (m.get("textures") or []) if isinstance(m.get("textures"), list) else []:
                add(t)
            for t in m.get("images") or []:
                add(t)
    except (OSError, ValueError):
        pass
    # a texture set named like the model (sword.fbx + sword_albedo.png ...)
    stem = _base_of(os.path.basename(path))
    dirs_seen: set = set()
    for d in (base, os.path.join(base, "textures"), os.path.join(base, "Textures")):
        if os.path.isdir(d) and ident(d) not in dirs_seen:
            dirs_seen.add(ident(d))
            for n in os.listdir(d):
                r = map_role(n)
                if r and r[0] == stem:
                    add(os.path.relpath(os.path.join(d, n), base))
    return out


def _find_nearby(base: str, name: str) -> str | None:
    for d in (base, os.path.join(base, "textures"), os.path.join(base, "Textures"), os.path.join(base, "tex")):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    for root, dirs, files in os.walk(base):
        if name in files:
            return os.path.join(root, name)
        if root.count(os.sep) - base.count(os.sep) >= 2:
            dirs.clear()
    return None


def related(db: Database, file_id: int) -> dict:
    """For the side panel: the texture set of a map, the textures a model uses, models using a texture."""
    row = db.cat.execute("SELECT id, path, dir, name, kind, meta FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        return {}
    out: dict = {}
    ts = texture_set(db, file_id)
    if ts:
        out["set"] = ts
    if row[4] == "model":
        meta = json.loads(row[5]) if row[5] else {}
        comps = model_companions(row[1], meta)
        if comps:
            ids = _ids_for_paths(db, comps)
            out["uses"] = [{"id": ids.get(p), "name": os.path.relpath(p, row[2]), "role": (map_role(os.path.basename(p)) or (None, None))[1]}
                           for p in comps]
    elif map_role(row[3]) or os.path.splitext(row[3])[1].lower() in TEXTURE_EXTS:
        base = (map_role(row[3]) or (_base_of(row[3]),))[0]
        parent = os.path.dirname(row[2])
        models = []
        for fid, path, name in db.cat.execute(
                "SELECT id, path, name FROM files WHERE kind = 'model' AND (dir = ? OR dir = ?) LIMIT 200", (row[2], parent)):
            if _base_of(name) == base:
                models.append({"id": fid, "name": name})
        if models:
            out["models"] = models
    return out


def _ids_for_paths(db: Database, paths: list[str]) -> dict[str, int]:
    out = {}
    for p in paths:
        r = db.cat.execute("SELECT id FROM files WHERE path = ?", (p,)).fetchone()
        if r:
            out[p] = r[0]
    return out


# ---------------------------------------------------------------- project export
CATEGORY = {"image": "image", "work": "source", "vector": "source", "document": "docs", "texture": "texture",
            "3d": "model", "audio": "audio", "video": "video"}
PRESETS = {
    "folders": {"label": "By type (Images, Models, Audio…)", "root": "{project}",
                "dirs": {"image": "Images", "texture": "Textures", "model": "Models", "audio": "Audio", "video": "Video",
                         "source": "Source", "docs": "Documents"}},
    "unity": {"label": "Unity (Assets/<project>/…)", "root": "Assets/{project}",
              "dirs": {"image": "Sprites", "texture": "Textures", "model": "Models", "audio": "Audio", "video": "Video",
                       "source": "_Source", "docs": "_Docs"}},
    "godot": {"label": "Godot (assets/<project>/…, lower-case names)", "root": "assets/{project_snake}", "snake": True,
              "dirs": {"image": "sprites", "texture": "textures", "model": "models", "audio": "audio", "video": "video",
                       "source": "_source", "docs": "_docs"}},
    "unreal": {"label": "Unreal (SourceArt/<project>/… to import from)", "root": "SourceArt/{project}",
               "dirs": {"image": "Textures", "texture": "Textures", "model": "Meshes", "audio": "Audio", "video": "Movies",
                        "source": "Source", "docs": "Docs"}},
}
_UNSAFE = re.compile(r'[<>:"|?*\x00-\x1f]')


def _snake(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "asset"


def _safe(s: str) -> str:
    return _UNSAFE.sub("_", s).strip(" .") or "_"


class ExportError(ValueError):
    pass


def plan_export(db: Database, project_id: int, dest: str, preset: str = "folders", by_subject: bool = False,
                companions: bool = True, ids: list[int] | None = None) -> dict:
    if preset not in PRESETS:
        raise ExportError("Unknown layout")
    pr = db.cat.execute("SELECT name, facet FROM tags WHERE id = ?", (project_id,)).fetchone()
    if not pr or pr[1] != "project":
        raise ExportError("Choose a project")
    dest = os.path.normpath(os.path.abspath(os.path.expanduser(dest or "")))
    if not dest or dest == os.sep:
        raise ExportError("Choose a destination folder")
    for (root,) in db.cat.execute("SELECT path FROM roots WHERE kind = 'library'"):
        r = os.path.normpath(root)
        if dest == r or dest.startswith(r.rstrip(os.sep) + os.sep):
            raise ExportError("The destination is inside a library folder - choose a folder outside the library "
                              "(e.g. your game project), so exported copies aren't catalogued twice")
    P = PRESETS[preset]
    snake = P.get("snake", False)
    root = P["root"].format(project=_safe(pr[0].replace("/", "_")), project_snake=_snake(pr[0]))
    sql, args = projects.member_clause(db, project_id)
    rows = db.cat.execute(f"SELECT id, path, name, ext, hash, size, mtime_ns, date_ts, meta FROM files WHERE status != 2 AND {sql} "
                          "AND root_id IN (SELECT id FROM roots WHERE kind = 'library') ORDER BY path", args).fetchall()
    if ids is not None:
        keep = set(ids)
        rows = [r for r in rows if r[0] in keep]
    subj = {}
    if by_subject:
        names = {r[0]: r[1] for r in db.cat.execute("SELECT id, name FROM tags WHERE facet = 'subject'")}
        for fid, tid in db.cat.execute("SELECT file_id, tag_id FROM file_tags"):
            if tid in names:
                subj.setdefault(fid, []).append(names[tid])
    ops, taken, by_hash, comp_done = [], set(), {}, set()
    counts: dict[str, int] = {}

    def name_for(n: str) -> str:
        stem, ext = os.path.splitext(n)
        return (_snake(stem) + ext.lower()) if snake else _safe(n)

    def unique(rel: str) -> str:
        cand, k = rel, 2
        stem, ext = os.path.splitext(rel)
        while cand.lower() in taken:
            cand = f"{stem}_{k}{ext}"
            k += 1
        taken.add(cand.lower())
        return cand

    # textures and materials that travel with a model aren't exported a second time on their own
    comp_paths: dict[int, list[str]] = {}
    if companions:
        for fid, path, name, ext, h, size, mtime, date_ts, meta in rows:
            hd = handler_for(ext)
            if hd and hd.group == "3d":
                comp_paths[fid] = model_companions(path, json.loads(meta) if meta else {})
    travelling = {os.path.normcase(p) for ps in comp_paths.values() for p in ps}
    for fid, path, name, ext, h, size, mtime, date_ts, meta in rows:
        if os.path.normcase(path) in travelling:
            continue
        hd = handler_for(ext)
        cat = CATEGORY.get(hd.group if hd else "", "source")
        if cat == "image" and map_role(name):
            cat = "texture"
        if h and h in by_hash:          # the same file twice in the project: copy once
            continue
        folder = f"{root}/{P['dirs'][cat]}"
        if by_subject and subj.get(fid):
            t = sorted(subj[fid], key=str.lower)[0]
            folder += "/" + "/".join((_snake(x) if snake else _safe(x)) for x in t.split("/"))
        if cat == "model":   # each model in its own folder, with its textures at the same relative paths
            folder += "/" + (_snake(os.path.splitext(name)[0]) if snake else _safe(os.path.splitext(name)[0]))
        rel = unique(f"{folder}/{name_for(name)}")
        by_hash[h] = rel
        ops.append({"id": fid, "src": path, "rel": rel, "size": size or 0, "kind": cat, "date_ts": date_ts})
        counts[cat] = counts.get(cat, 0) + 1
        if cat == "model" and companions:
            mdir = os.path.dirname(path)
            for cpath in comp_paths.get(fid, []):
                crel = os.path.relpath(cpath, mdir).replace("\\", "/")
                if crel.startswith(".."):
                    crel = "textures/" + os.path.basename(cpath)
                key = (rel, crel)
                if key in comp_done:
                    continue
                comp_done.add(key)
                ops.append({"id": None, "src": cpath, "rel": unique(f"{os.path.dirname(rel)}/{crel}"),
                            "size": os.path.getsize(cpath), "kind": "companion", "date_ts": None})
                counts["companion"] = counts.get("companion", 0) + 1
    unchanged = 0
    for o in ops:
        dp = os.path.join(dest, o["rel"])
        try:
            st = os.stat(dp)
            o["skip"] = st.st_size == o["size"] and int(st.st_mtime) == int(os.stat(o["src"]).st_mtime)
        except OSError:
            o["skip"] = False
        unchanged += o["skip"]
    todo = [o for o in ops if not o["skip"]]
    return {"project": pr[0], "dest": dest, "root": root, "preset": preset, "ops": ops, "counts": counts,
            "files": len(ops), "copy": len(todo), "unchanged": unchanged, "bytes": sum(o["size"] for o in todo)}


def summary(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "ops"}
    out["examples"] = [{"rel": o["rel"], "kind": o["kind"], "skip": o["skip"]} for o in p["ops"][:400]]
    return out


class ExportJob:
    def __init__(self, db: Database):
        self.db = db
        self.state: dict = {"running": False}
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    def start(self, plan: dict) -> dict:
        with self._lock:
            if self.state.get("running"):
                raise ExportError("An export is already running")
            self._cancel.clear()
            self.state = {"running": True, "done": 0, "total": plan["copy"], "bytes_done": 0, "bytes_total": plan["bytes"],
                          "dest": plan["dest"], "root": plan["root"], "errors": [], "result": None}
        threading.Thread(target=self._run, args=(plan,), name="kurator-export", daemon=True).start()
        return dict(self.state)

    def cancel(self):
        self._cancel.set()

    def _run(self, plan: dict) -> None:
        st = self.state
        copied = 0
        manifest = []
        try:
            for o in plan["ops"]:
                dp = os.path.join(plan["dest"], o["rel"])
                manifest.append({"file": o["rel"], "original": o["src"], "kind": o["kind"]})
                if o["skip"]:
                    continue
                if self._cancel.is_set():
                    break
                try:
                    os.makedirs(os.path.dirname(dp), exist_ok=True)
                    tmp = dp + ".kurator-part"
                    shutil.copy2(o["src"], tmp)
                    os.replace(tmp, dp)
                    if o.get("date_ts"):
                        set_creation_time(dp, o["date_ts"])
                    copied += 1
                except OSError as e:
                    st["errors"].append(f"{o['src']}: {e}")
                st["done"] += 1
                st["bytes_done"] += o["size"]
            try:
                mp = os.path.join(plan["dest"], plan["root"], "kurator_export.json")
                os.makedirs(os.path.dirname(mp), exist_ok=True)
                with open(mp, "w", encoding="utf-8") as f:
                    json.dump({"project": plan["project"], "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "layout": plan["preset"], "files": manifest}, f, ensure_ascii=False, indent=1)
            except OSError as e:
                st["errors"].append(f"manifest: {e}")
        finally:
            st["result"] = {"copied": copied, "unchanged": plan["unchanged"], "failed": len(st["errors"]),
                            "cancelled": self._cancel.is_set(), "folder": os.path.join(plan["dest"], plan["root"])}
            st["running"] = False


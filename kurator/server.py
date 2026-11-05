"""HTTP API + static UI. Listens on loopback only."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.gzip import GZipMiddleware

from . import __version__, assets, backup, dates, fileops, projects, rclone, rules, tags, xmp
from . import similar as similar_mod
from .sheets import SheetError
from . import stacks as stacks_mod
from .backup import BackupError
from .backup_targets import TargetError
from .db import Database
from .fileops import OpError
from .rules import RuleError
from .ai.embed import AIUnavailable
from .ai.vlm import VLMError
from .ai.service import AIError
from .tags import TagError
from .handlers import handler_for, supported_extensions, supported_types
from .processing import THUMB_SIZES, process_file
from .scanner import Scanner

ITEM_COLS = "id, hash, color, date_ts, name, width, height, date_source, alpha, status, frames, tver, stack_id"
SORTS = {
    "date": "date_ts {o}, id {o}",
    "length": "json_extract(meta, '$.duration') {o}, name COLLATE NOCASE",
    "bpm": "json_extract(meta, '$.bpm') {o}, name COLLATE NOCASE",
    "loudness": "json_extract(meta, '$.lufs') {o}, name COLLATE NOCASE",
    "name": "name COLLATE NOCASE {o}, id {o}",
    "size": "size {o}, id {o}",
    "folder": "dir COLLATE NOCASE {o}, name COLLATE NOCASE {o}",
    "type": "ext {o}, name COLLATE NOCASE {o}",
    "dimensions": "(COALESCE(width,0) * COALESCE(height,0)) {o}, id {o}",
}
SOURCE_INDEX = {s: i for i, s in enumerate(dates.SOURCES)}
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg", ".jpe": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".avif": "image/avif", ".svg": "image/svg+xml",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
        ".flac": "audio/flac", ".m4a": "audio/mp4", ".aac": "audio/aac",
        # .mov is usually H.264 inside: browsers play it when it is announced as MP4
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg"}


class RootIn(BaseModel):
    path: str


class PinsIn(BaseModel):
    paths: list[str]


class TagIn(BaseModel):
    name: str
    color: str | None = None
    hotkey: int | None = None
    auto_hotkey: bool = True
    facet: str | None = None


class TagPatch(BaseModel):
    name: str | None = None
    color: str | None = None
    hotkey: int | None = None
    clear_hotkey: bool = False
    facet: str | None = None
    archived: bool | None = None


class ApplyIn(BaseModel):
    ids: list[int]
    op: str = "toggle"


class MoveIn(BaseModel):
    ids: list[int]
    dest: str
    by_year: bool = False
    date_prefix: bool = False


class IdIn(BaseModel):
    id: int


class IdsIn(BaseModel):
    ids: list[int]
    include_untagged: bool = False


class RulesIn(BaseModel):
    rules: list[dict]


class BackupSetIn(BaseModel):
    name: str = "Backup"
    layout: str = "original"
    tag_template: str = "{tagpath}"
    policy: str = "fill"
    mode: str = "copy"
    verify: bool = True
    scope: dict = {}
    targets: list[dict] = []


class RestoreIn(BaseModel):
    mode: str = "missing"
    folder: str | None = None


class SettingsIn(BaseModel):
    xmp_auto: bool | None = None
    svg_all: bool | None = None
    rclone_path: str | None = None


class ConnectIn(BaseModel):
    name: str


def _types() -> dict:
    t = supported_types()
    from . import blender
    t["blender"] = bool(blender.find())
    if t["blender"]:   # Blender installed: .blend files open in the 3D viewer too
        t["meshes"] = sorted(set(t["meshes"]) | {"blend", "blend1", "blend2"})
    return t


def _tv(h: str, tver: int | None) -> str:
    """Version tag in thumbnail URLs: content hash, plus the preview version if it was remade."""
    return h[:12] + (f".{tver}" if tver else "")


def _json(data, status: int = 200) -> Response:
    return Response(json.dumps(data, separators=(",", ":"), ensure_ascii=False),
                    status_code=status, media_type="application/json")


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def create_app(db: Database, scanner: Scanner, port: int, watcher=None, ai=None) -> FastAPI:
    # On-demand thumbnails for what is visible now; limited so the UI stays responsive.
    ondemand = ThreadPoolExecutor(max_workers=max(2, min(6, os.cpu_count() or 2)), thread_name_prefix="thumb")

    @asynccontextmanager
    async def lifespan(_app):
        yield
        ondemand.shutdown(wait=False, cancel_futures=True)
        jobs.cancel()
        rc.stop()
        if watcher:
            watcher.stop()
        scanner.stop()

    jobs = backup.Jobs(db, scanner)
    rc = rclone.shared(db, os.path.join(db.data_dir, "logs"))
    app = FastAPI(title="Kurator", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=2048, compresslevel=5)

    allowed_hosts = {f"{h}:{port}" for h in LOOPBACK} | LOOPBACK
    allowed_origins = {f"http://{h}:{port}" for h in LOOPBACK}

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Blocks DNS-rebinding (foreign Host header) and cross-site requests from web pages.
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin not in allowed_origins:
                return JSONResponse({"detail": "forbidden origin"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "cross-site request blocked"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        return resp

    inflight: dict[tuple[int, int], object] = {}
    inflight_lock = threading.Lock()

    static_dir = str(resources.files("kurator") / "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.exception_handler(OpError)
    @app.exception_handler(TagError)
    @app.exception_handler(RuleError)
    @app.exception_handler(BackupError)
    @app.exception_handler(TargetError)
    @app.exception_handler(rclone.RcloneError)
    @app.exception_handler(projects.ProjectError)
    @app.exception_handler(SheetError)
    @app.exception_handler(stacks_mod.StackError)
    @app.exception_handler(assets.ExportError)
    @app.exception_handler(AIError)
    @app.exception_handler(AIUnavailable)
    @app.exception_handler(VLMError)
    async def _user_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(os.path.join(static_dir, "index.html"), headers={"Cache-Control": "no-cache"})

    # -- state / roots -----------------------------------------------------
    @app.get("/api/state")
    def state():
        return _json({
            "version": __version__,
            "roots": db.roots(),
            "scan": scanner.status(),
            "watch": watcher.status() if watcher else {"enabled": False},
            "job": jobs.status(),
            "ai_job": dict(ai.state) if ai else {"running": False},
            "extensions": supported_extensions(),
            "types": _types(),
            "date_sources": list(dates.SOURCES),
            "sep": os.sep,
        })

    def _norm_path(p: str) -> str:
        return os.path.normpath(os.path.abspath(os.path.expanduser(p.strip().strip('"'))))

    @app.post("/api/roots")
    def add_root(body: RootIn):
        p = _norm_path(body.path)
        if not os.path.isdir(p):
            raise HTTPException(400, f"Not a folder: {p}")
        inside = db.root_for(p, "library")
        if inside and inside["path"] != p:
            raise HTTPException(400, f"Already inside library folder {inside['path']}")
        rid = db.add_root(p, "library")   # absorbs library/browse folders inside it
        scanner.request_scan()
        if watcher:
            watcher.sync()
        return _json({"id": rid, "path": p})

    @app.delete("/api/roots/{root_id}")
    def remove_root(root_id: int):
        db.remove_root(root_id)
        if watcher:
            watcher.sync()
        return _json({"ok": True})

    @app.post("/api/scan")
    def scan():
        scanner.request_scan()
        return _json({"ok": True})

    # -- folders -----------------------------------------------------------
    @app.get("/api/folders")
    def folders(project: int | None = None):
        if project:   # project mode: only folders that hold files of the project, with those counts
            ps, pa = projects.member_clause(db, project)
            rows = db.cat.execute(f"SELECT root_id, dir, COUNT(*) FROM files WHERE {ps} GROUP BY root_id, dir", pa).fetchall()
        else:
            rows = db.cat.execute("SELECT root_id, dir, COUNT(*) FROM files GROUP BY root_id, dir").fetchall()
        roots = {r["id"]: r["path"] for r in db.roots("library")}
        nodes: dict[str, dict] = {}
        for rp in roots.values():
            nodes[rp] = {"path": rp, "name": rp, "parent": None, "count": 0, "total": 0}
        for root_id, d, n in rows:
            rp = roots.get(root_id)
            if rp is None:
                continue
            cur = d
            first = True
            while True:
                node = nodes.get(cur)
                if node is None:
                    parent = os.path.dirname(cur)
                    node = nodes[cur] = {"path": cur, "name": os.path.basename(cur) or cur,
                                         "parent": parent if cur != rp else None, "count": 0, "total": 0}
                if first:
                    node["count"] += n
                    first = False
                node["total"] += n
                if cur == rp or len(cur) <= len(rp):
                    break
                cur = os.path.dirname(cur)
        return _json(sorted(nodes.values(), key=lambda x: x["path"].lower()))

    # -- items -------------------------------------------------------------
    @app.get("/api/items")
    def items(
        folder: str | None = None,
        recursive: bool = True,
        q: str | None = None,
        ext: str | None = None,
        sort: str = "date",
        order: str = "desc",
        date_from: float | None = None,
        date_to: float | None = None,
        guessed: bool = False,
        tag: str | None = None,
        untagged: bool = False,
        missing: str | None = None,
        suggested: bool = False,
        profile: int | None = None,
        exclude: str | None = None,
        project: int | None = None,
        member_of: int | None = None,
        dupes: bool = False,
        similar: int = 0,
        stacks: str = "collapse",
        stack: int | None = None,
        hues: int = 0,
        shape: str = "",
        alpha: bool = False,
        minside: int = 0,
        bpm_min: float | None = None,
        bpm_max: float | None = None,
        key: str = "",
        len_min: float | None = None,
        len_max: float | None = None,
        tex: str = "",
    ):
        where, args = [], []
        # the look of a picture / the character of a sound
        if hues:
            where.append("(hues & ?) != 0")
            args.append(int(hues))
        shapes = {"landscape": "width > height * 1.1", "portrait": "height > width * 1.1",
                  "square": "width BETWEEN height * 0.9 AND height * 1.1",
                  "panorama": "width >= height * 2", "tall": "height >= width * 2"}
        if shape in shapes:
            where.append(f"width > 0 AND height > 0 AND {shapes[shape]}")
        if alpha:
            where.append("alpha = 1")
        if tex == "yes":
            where.append("kind = 'model' AND json_extract(meta, '$.model.textured') = 1")
        elif tex == "no":
            where.append("kind = 'model' AND json_extract(meta, '$.mesh') = 1 AND COALESCE(json_extract(meta, '$.model.textured'), 0) = 0")
        elif tex == "missing":
            where.append("kind = 'model' AND json_extract(meta, '$.model.missing_textures') IS NOT NULL")
        if minside:
            where.append("MAX(width, height) >= ?")
            args.append(int(minside))
        for col, lo, hi in (("bpm", bpm_min, bpm_max), ("duration", len_min, len_max)):
            if lo is not None:
                where.append(f"json_extract(meta, '$.{col}') >= ?")
                args.append(lo)
            if hi is not None:
                where.append(f"json_extract(meta, '$.{col}') <= ?")
                args.append(hi)
        if key:
            where.append("json_extract(meta, '$.key') = ?")
            args.append(key)
        if stack:   # the versions in one stack, oldest first
            where.append("stack_id = ?")
            args.append(stack)
            sort, order = "date", "asc"
        if dupes:   # the same content in more than one place (exact copies), shown next to each other
            where.append("hash IN (SELECT hash FROM files WHERE hash IS NOT NULL AND status = 1 AND "
                         "root_id IN (SELECT id FROM roots WHERE kind = 'library') GROUP BY hash HAVING COUNT(*) > 1)")
        # tag/project membership includes a project's live sources (tags, folders, other projects)
        if project:   # the active project: the top filter, everything else filters inside it
            sql_, a_ = projects.member_clause(db, project)
            where.append(sql_); args += a_
        for t in (exclude or "").split(","):   # "everything except ..." (Alt+click a tag)
            if t.strip().isdigit():
                sql_, a_ = projects.member_clause(db, int(t))
                where.append(f"NOT {sql_}"); args += a_
        for t in (tag or "").split(","):
            if t.strip().isdigit():
                sql_, a_ = projects.member_clause(db, int(t))
                where.append(sql_); args += a_
        if untagged:
            where.append("id NOT IN (SELECT file_id FROM file_tags)")
        for fct in (missing or "").split(","):
            fct = fct.strip()
            if fct == "project" and projects.has_sources(db):
                sql_, a_ = projects.any_project_clause(db)
                where.append(f"NOT {sql_}"); args += a_
            elif fct in tags.FACET:  # e.g. "no project yet"
                where.append("id NOT IN (SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id = ft.tag_id WHERE t.facet = ?)")
                args.append(fct)
        if folder:
            f = os.path.normpath(folder)
            if recursive:
                lo = f + os.sep
                hi = f + chr(ord(os.sep) + 1)
                where.append("(dir = ? OR (dir >= ? AND dir < ?))")
                args += [f, lo, hi]
            else:
                where.append("dir = ?")
                args.append(f)
        else:  # "All library items": browsed (temporary) folders are not part of the library
            where.append("root_id IN (SELECT id FROM roots WHERE kind = 'library')")
        if q:
            for term in q.split():
                where.append("name LIKE ? ESCAPE '\\'")
                args.append(f"%{_like_escape(term)}%")
        if ext:
            exts = [("." + e.strip().lower().lstrip(".")) for e in ext.split(",") if e.strip()]
            if exts:
                where.append(f"ext IN ({','.join('?' * len(exts))})")
                args += exts
        if date_from is not None:
            where.append("date_ts >= ?"); args.append(date_from)
        if date_to is not None:
            where.append("date_ts < ?"); args.append(date_to)
        if guessed:
            where.append("date_source IN ('file-created','file-modified')")
        o = "ASC" if order.lower() == "asc" else "DESC"
        order_sql = SORTS.get(sort, SORTS["date"]).format(o=o)
        if dupes:
            order_sql = "hash, " + order_sql
        if suggested:
            where.append("id IN (SELECT file_id FROM suggestions" + (" WHERE profile_id = ?)" if profile else ")"))
            if profile:
                args.append(profile)
        sql = (f"SELECT {ITEM_COLS} FROM files" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order_sql}")
        if similar:   # groups of near-identical pictures, group after group (biggest first)
            rows = db.cat.execute(sql, args).fetchall()
            byid = {r[0]: r for r in rows}
            ids = list(byid)
            ph = []
            for k in range(0, len(ids), 900):
                chunk = ids[k:k + 900]
                ph += db.cat.execute(f"SELECT id, phash FROM files WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            gs = similar_mod.groups(ph, max(0, min(int(similar), 20)))
            order = []
            for gi, g in enumerate(gs):
                g.sort(key=lambda i: -(byid[i][3] or 0))
                order += [(i, gi) for i in g]
            out = _payload([byid[i] for i, _ in order])
            out["g"] = [gi for _, gi in order]
            out["groups"] = len(gs)
        elif stacks == "collapse" and not stack and not dupes:   # a version stack shows as one item
            rows, sizes = stacks_mod.collapse(db, db.cat.execute(sql, args).fetchall(), 12)
            out = _payload(rows)
            if sizes:
                out["k"] = [sizes.get(i, 0) for i in out["ids"]]
        else:
            out = _payload(db.cat.execute(sql, args))
        if member_of:   # "add from library" mode: which items are already in the active project
            mem = projects.member_ids(db, member_of)
            out["m"] = [1 if i in mem else 0 for i in out["ids"]]
        return _json(out)

    def _payload(rows, scores: dict | None = None) -> dict:
        ids, v, c, d, n, w, h, ds, a, t, f, sg = [], [], [], [], [], [], [], [], [], [], [], []
        by_file = tags.tags_by_file(db)
        sugg = ai.suggestions_by_file() if ai else {}
        for row in rows:
            t.append(by_file.get(row[0], 0))
            sg.append(sugg.get(row[0], 0))
            f.append(1 if (row[10] or 0) > 1 else 0)
            ids.append(row[0])
            v.append(_tv(row[1], row[11]) if row[1] else ("e" if row[9] == 2 else ""))
            c.append(row[2] if row[2] is not None else -1)
            d.append(int(row[3]) if row[3] is not None else 0)
            n.append(row[4])
            w.append(row[5] or 0)
            h.append(row[6] or 0)
            ds.append(SOURCE_INDEX.get(row[7], -1))
            a.append(row[8] or 0)
        out = {"count": len(ids), "ids": ids, "v": v, "c": c, "d": d, "n": n, "w": w, "h": h,
               "ds": ds, "a": a, "t": t, "f": f, "s": sg}
        if scores is not None:
            out["score"] = [round(scores.get(i, 0), 4) for i in ids]
        return out

    def _payload_for_ranked(ranked: list[tuple[int, float]]) -> dict:
        """Items in the given order (search results, similar images)."""
        order = [i for i, _ in ranked]
        rows = {}
        for k in range(0, len(order), 900):
            chunk = order[k:k + 900]
            for r in db.cat.execute(f"SELECT {ITEM_COLS} FROM files WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                rows[r[0]] = r
        return _payload([rows[i] for i in order if i in rows], dict(ranked))

    @app.get("/api/items/{item_id}")
    def item(item_id: int):
        row = db.cat.execute("SELECT * FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        out = dict(row)
        out["meta"] = json.loads(out["meta"]) if out["meta"] else {}
        out.pop("seen", None)
        h = handler_for(out["ext"])
        out["viewable"] = bool(h and h.browser_viewable)
        out["tags"] = [r[0] for r in db.cat.execute("SELECT tag_id FROM file_tags WHERE file_id = ?", (item_id,))]
        out["stack"] = stacks_mod.info(db, item_id)
        out["twins"] = [{"id": r[0], "path": r[1]} for r in db.cat.execute(   # the same file elsewhere
            "SELECT id, path FROM files WHERE hash = ? AND id != ? ORDER BY path LIMIT 50", (out["hash"], item_id))] if out["hash"] else []
        out["tag_sources"] = {str(r[0]): [r[1], r[2]] for r in db.cat.execute(
            "SELECT tag_id, source, confidence FROM file_tags WHERE file_id = ?", (item_id,))}
        out["captions"] = [{"model": m, "text": tx} for m, tx in db.cat.execute(
            "SELECT model, text FROM captions WHERE file_id = ? ORDER BY created DESC", (item_id,))]
        return _json(out)

    # -- browsing & pins ---------------------------------------------------
    @app.get("/api/fs/places")
    def fs_places():
        return _json(fileops.places())

    @app.get("/api/fs/list")
    def fs_list(path: str):
        out = fileops.list_dir(path)
        lib = db.root_for(out["path"], "library")
        out["library"] = lib["path"] if lib else None
        return _json(out)

    @app.post("/api/browse")
    def browse(body: RootIn):
        """Open any folder. Inside the library it's just a filter; elsewhere the folder (not its
        subfolders) is catalogued as a temporary 'browse' folder - forgotten after 14 days unused."""
        p = _norm_path(body.path)
        if not os.path.isdir(p):
            raise HTTPException(400, f"Not a folder: {p}")
        lib = db.root_for(p, "library")
        if lib:
            return _json({"folder": p, "library": lib["path"]})
        rid = db.add_root(p, "browse")
        scanner.walk_root(rid, p, recursive=False)
        scanner.request_process()
        if watcher:
            watcher.sync()
        return _json({"folder": p, "library": None, "root_id": rid})

    @app.get("/api/pins")
    def get_pins():
        return _json(db.get_setting("pins", []))

    @app.put("/api/pins")
    def put_pins(body: PinsIn):
        pins = list(dict.fromkeys(_norm_path(p) for p in body.paths if p.strip()))[:200]
        db.set_setting("pins", pins)
        return _json(pins)

    # -- XMP sidecars (optional, automatic) -------------------------------------
    def _auto_sidecars(file_ids) -> None:
        if not file_ids or not db.get_setting("xmp_auto", False):
            return
        ids = list(file_ids)
        threading.Thread(target=xmp.write_sidecars, args=(db, ids), daemon=True).start()

    def _files_with_tags(tag_ids) -> list[int]:
        if not tag_ids:
            return []
        q = ",".join("?" * len(tag_ids))
        return [r[0] for r in db.cat.execute(f"SELECT DISTINCT file_id FROM file_tags WHERE tag_id IN ({q})", tag_ids)]

    # -- tags ----------------------------------------------------------------
    @app.get("/api/tags")
    def get_tags(project: int | None = None):
        """Tags with counts. With ?project=, counts are inside that project (the sidebar in project mode)."""
        lib = "root_id IN (SELECT id FROM roots WHERE kind = 'library')"
        scope, sargs = lib, []
        if project:
            ps, pa = projects.member_clause(db, project)
            scope, sargs = f"{lib} AND {ps}", pa
        untagged = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE id NOT IN (SELECT file_id FROM file_tags) AND {scope}", sargs).fetchone()[0]
        missing = {}
        live = projects.has_sources(db)
        for f in tags.FACETS:
            if f["key"] == "project" and live:
                q_, a_ = projects.any_project_clause(db)
                missing["project"] = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE NOT {q_} AND {scope}", a_ + sargs).fetchone()[0]
                continue
            missing[f["key"]] = db.cat.execute(
                "SELECT COUNT(*) FROM files WHERE id NOT IN (SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id = ft.tag_id "
                f"WHERE t.facet = ?) AND {scope}", (f["key"], *sargs)).fetchone()[0]
        lst = tags.list_tags(db)
        if project:
            counts = dict(db.cat.execute(f"SELECT ft.tag_id, COUNT(*) FROM file_tags ft JOIN files ON files.id = ft.file_id "
                                         f"WHERE {scope} GROUP BY ft.tag_id", sargs).fetchall())
            for t in lst:
                t["count"] = counts.get(t["id"], 0)
        if live:   # projects that include things automatically: count what is really in them
            with_src = {r[0] for r in db.cat.execute("SELECT DISTINCT tag_id FROM project_sources")}
            for t in lst:
                if t["id"] in with_src:
                    q_, a_ = projects.member_clause(db, t["id"])
                    t["count"] = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE {q_} AND {scope}", a_ + sargs).fetchone()[0]
                    t["live"] = True
        dupes = db.cat.execute(f"SELECT COALESCE(SUM(n), 0) FROM (SELECT COUNT(*) AS n FROM files WHERE hash IS NOT NULL AND status = 1 "
                               f"AND {scope} GROUP BY hash HAVING COUNT(*) > 1)", sargs).fetchone()[0]
        return _json({"tags": lst, "untagged": untagged, "facets": tags.FACETS, "missing": missing, "duplicates": dupes})

    @app.get("/api/projects")
    def get_projects():
        return _json(projects.overview(db))

    @app.put("/api/projects/{tag_id}/sources")
    def put_project_sources(tag_id: int, body: dict):
        return _json(projects.set_sources(db, tag_id, list(body.get("sources") or [])))

    @app.post("/api/tags")
    def create_tag(body: TagIn):
        hk = body.hotkey if body.hotkey is not None else ("auto" if body.auto_hotkey else None)
        return _json(tags.create_tag(db, body.name, body.color, hk, body.facet))

    @app.patch("/api/tags/{tag_id}")
    def patch_tag(tag_id: int, body: TagPatch):
        hk = None if body.clear_hotkey else (body.hotkey if body.hotkey is not None else "keep")
        out = tags.update_tag(db, tag_id, body.name, body.color, hk, body.facet, body.archived)
        if body.name is not None or body.facet is not None:
            _auto_sidecars(_files_with_tags(tags.descendant_ids(db, tag_id)))
        return _json(out)

    @app.delete("/api/tags/{tag_id}")
    def remove_tag(tag_id: int):
        affected = _files_with_tags(tags.descendant_ids(db, tag_id))
        out = tags.delete_tag(db, tag_id)
        _auto_sidecars(affected)
        return _json(out)

    @app.post("/api/tags/{tag_id}/apply")
    def apply_tag(tag_id: int, body: ApplyIn):
        res = tags.apply(db, body.ids, tag_id, body.op)
        _auto_sidecars(res["added"] + res["removed"] + [i for a in res["also_removed"] for i in a["ids"]])
        return _json(res)

    # -- settings, sidecars, rules -----------------------------------------
    @app.get("/api/settings")
    def get_settings():
        return _json({"xmp_auto": db.get_setting("xmp_auto", False), "svg_all": db.get_setting("svg_all", False), "data_dir": db.data_dir,
                      "rclone_path": db.get_setting("rclone_path", None)})

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        if body.xmp_auto is not None:
            db.set_setting("xmp_auto", body.xmp_auto)
        if body.svg_all is not None and body.svg_all != db.get_setting("svg_all", False):
            db.set_setting("svg_all", body.svg_all)
            os.environ["KURATOR_SVG_ALL"] = "1" if body.svg_all else "0"   # read by the SVG renderer (also in workers)
            if db.remake_previews("ext IN ('.svg', '.svgz')"):
                scanner.request_scan()
        if body.rclone_path is not None:
            p = body.rclone_path.strip().strip('"')
            if p and not os.path.isfile(p):
                raise HTTPException(400, f"Not a file: {p}")
            db.set_setting("rclone_path", p or None)
        return get_settings()

    @app.post("/api/xmp/write")
    def xmp_write(body: IdsIn):
        return _json(xmp.write_sidecars(db, body.ids, body.include_untagged))

    @app.post("/api/xmp/import")
    def xmp_import(body: IdsIn):
        return _json(xmp.import_keywords(db, body.ids))

    # -- game assets: texture sets, model companions, project export ----------------------
    exporter = assets.ExportJob(db)

    @app.get("/api/items/{item_id}/related")
    def item_related(item_id: int):
        return _json(assets.related(db, item_id))

    @app.get("/api/items/{item_id}/maps2d")
    def item_maps2d(item_id: int):
        """Companion maps of a 2D picture (normal, glow, AO, depth, layers) for the lit preview."""
        from . import maps2d
        g = maps2d.group(db, item_id)
        if g is None:
            return _json({"maps": []})

        def url(fid: int, ext: str) -> str:
            h = handler_for(ext)
            return f"/f/{fid}" if h and h.browser_viewable and ext != ".gif" else f"/t/{fid}?s=1600"
        g["main"]["url"] = url(g["main"]["id"], g["main"]["ext"])
        for m in g["maps"]:
            m["url"] = url(m["id"], m["ext"])
        return _json(g)

    @app.get("/api/export/presets")
    def export_presets():
        return _json({k: v["label"] for k, v in assets.PRESETS.items()})

    def _export_plan(body: dict) -> dict:
        return assets.plan_export(db, int(body.get("project") or 0), str(body.get("dest") or ""),
                                  str(body.get("preset") or "folders"), bool(body.get("by_subject")),
                                  bool(body.get("companions", True)), body.get("ids"))

    @app.post("/api/export/plan")
    def export_plan(body: dict):
        return _json(assets.summary(_export_plan(body)))

    @app.post("/api/export/run")
    def export_run(body: dict):
        return _json(exporter.start(_export_plan(body)))

    @app.get("/api/export/job")
    def export_job():
        return _json(exporter.state)

    @app.post("/api/export/cancel")
    def export_cancel():
        exporter.cancel()
        return _json({"ok": True})

    # -- contact sheets and joined images -------------------------------------------------
    from . import atlas, sheets

    def _num(v):
        try:
            return int(v) if v not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            return None

    @app.post("/api/sheets/contact")
    def sheet_contact(body: dict):
        pages = sheets.contact_sheet(
            db, list(body.get("ids") or []), page=str(body.get("page") or "A4"), landscape=bool(body.get("landscape")),
            dpi=max(72, min(_num(body.get("dpi")) or 150, 600)), width=_num(body.get("width")), height=_num(body.get("height")),
            columns=_num(body.get("columns")) or 5, caption=str(body.get("caption") or "name"), title=str(body.get("title") or ""),
            background=str(body.get("background") or "white"), page_numbers=body.get("page_numbers", True) is not False)
        if body.get("preview"):
            return Response(sheets.preview_png(pages[0]), media_type="image/png", headers={"X-Pages": str(len(pages)),
                            "X-Size": f"{pages[0].width}x{pages[0].height}", "Cache-Control": "no-store"})
        files = sheets.save_pages(pages, str(body.get("dest") or ""), str(body.get("name") or ""), str(body.get("format") or "pdf"),
                                  max(72, min(_num(body.get("dpi")) or 150, 600)))
        return _json({"files": files, "pages": len(pages)})

    @app.post("/api/sheets/join")
    def sheet_join(body: dict):
        img = sheets.join(db, list(body.get("ids") or []), layout=str(body.get("layout") or "horizontal"),
                          columns=_num(body.get("columns")) or 3, gap=_num(body.get("gap")) or 0,
                          background=str(body.get("background") or "white"), transparent=bool(body.get("transparent")),
                          size=str(body.get("size") or "smallest"), target_width=_num(body.get("target_width")),
                          target_height=_num(body.get("target_height")))
        if body.get("preview"):
            return Response(sheets.preview_png(img), media_type="image/png",
                            headers={"X-Size": f"{img.width}x{img.height}", "Cache-Control": "no-store"})
        path = sheets.save_image(img, str(body.get("dest") or ""), str(body.get("name") or ""), str(body.get("format") or "png"))
        return _json({"files": [path], "width": img.width, "height": img.height})

    @app.post("/api/sheets/atlas")
    def sheet_atlas(body: dict):
        mode = str(body.get("mode") or "atlas")
        try:
            scale = float(body.get("scale") or 1)
        except (TypeError, ValueError):
            scale = 1.0
        img, sprites, stats = atlas.build(
            db, list(body.get("ids") or []), mode=mode, columns=_num(body.get("columns")) or 0,
            padding=_num(body.get("padding")) or 0, extrude=_num(body.get("extrude")) or 0, trim=body.get("trim", True) is not False,
            pot=bool(body.get("pot")), max_side=_num(body.get("max_side")) or 4096, scale=scale, expand=body.get("expand", True) is not False,
            cell_w=_num(body.get("cell_w")), cell_h=_num(body.get("cell_h")))
        if body.get("preview"):
            return Response(sheets.preview_png(img), media_type="image/png",
                            headers={"X-Size": f"{img.width}x{img.height}", "X-Sprites": str(stats["sprites"]),
                                     "X-Fill": str(stats.get("fill", "")), "Cache-Control": "no-store"})
        try:
            fps = max(1.0, min(float(body.get("fps") or 12), 120.0))
        except (TypeError, ValueError):
            fps = 12.0
        files = atlas.save(img, sprites, mode, str(body.get("dest") or ""), str(body.get("name") or ""),
                           godot=bool(body.get("godot")), fps=fps)
        return _json({"files": files, **stats})

    @app.post("/api/audio/{item_id}/export")
    def audio_export(item_id: int, body: dict):
        """Save a region of a sound as a new file (fades, normalising, format)."""
        from . import audio_analysis
        row = db.cat.execute("SELECT path, kind, dir, name FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row or row[1] != "audio":
            raise HTTPException(404)
        if not os.path.isfile(row[0]):
            raise HTTPException(404, "file is missing on disk")
        try:
            start, end = float(body.get("start", 0)), float(body.get("end", 0))
            fade_in, fade_out = float(body.get("fade_in") or 0), float(body.get("fade_out") or 0)
            norm = body.get("normalize")
            norm = None if norm in (None, "", False) else float(norm)
        except (TypeError, ValueError):
            raise HTTPException(400, "bad numbers") from None
        if not 0 <= start < end or end - start > 4 * 3600:
            raise HTTPException(400, "Select a region first")
        dest = str(body.get("dest") or row[2]).strip()
        if not os.path.isdir(dest):
            raise HTTPException(400, "Choose an existing folder to save into")
        stem = os.path.splitext(row[3])[0]
        name = str(body.get("name") or f"{stem}_{int(start // 60)}m{start % 60:05.2f}s".replace(".", "_"))
        try:
            out = audio_analysis.export_region(row[0], start, end, dest, name, str(body.get("format") or "same"),
                                               fade_in, fade_out, norm)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from None
        root = db.root_for(out, None)
        if root is not None:
            scanner.walk_folder(root["id"], os.path.dirname(out), False)
            scanner.request_process()
        fid = db.cat.execute("SELECT id FROM files WHERE path = ?", (os.path.normpath(os.path.abspath(out)),)).fetchone()
        return _json({"file": out, "id": fid[0] if fid else None})

    # -- sound: detailed waveform and spectrogram for the waveform view ------------------------
    wave_cache: dict = {}

    def _sound(item_id: int) -> tuple:
        row = db.cat.execute("SELECT path, kind, size, mtime_ns FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row[1] != "audio":
            raise HTTPException(415, "not a sound")
        if not os.path.isfile(row[0]):
            raise HTTPException(404, "file is missing on disk")
        return row

    def _cached(key, make):
        v = wave_cache.get(key)
        if v is None:
            v = make()
            if len(wave_cache) >= 24:
                wave_cache.pop(next(iter(wave_cache)))
            wave_cache[key] = v
        return v

    @app.get("/api/audio/{item_id}/wave")
    def audio_wave(item_id: int, bins: int = 1600):
        from . import audio_analysis
        row = _sound(item_id)
        bins = max(100, min(int(bins), 8000))
        try:
            data = _cached(("w", row[0], row[3], bins), lambda: audio_analysis.wave_data(row[0], bins))
        except Exception as e:
            raise HTTPException(422, f"can't read this sound: {e}") from None
        return _json(data)

    @app.get("/api/audio/{item_id}/spectrogram")
    def audio_spectrogram(item_id: int, w: int = 1600, h: int = 256):
        from . import audio_analysis
        row = _sound(item_id)
        w, h = max(200, min(int(w), 4000)), max(64, min(int(h), 1024))
        try:
            png = _cached(("s", row[0], row[3], w, h), lambda: audio_analysis.spectrogram_png(row[0], w, h))
        except Exception as e:
            raise HTTPException(422, f"can't read this sound: {e}") from None
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-cache"})

    # -- version stacks --------------------------------------------------------------------
    @app.post("/api/stacks")
    def stack_make(body: dict):
        ids = list(body.get("ids") or [])
        if body.get("by_name"):
            made = stacks_mod.by_name(db, ids)
            return _json({"stacks": len(made)})
        return _json({"stack": stacks_mod.make(db, ids)})

    @app.post("/api/stacks/unstack")
    def stack_unstack(body: dict):
        return _json({"dissolved": stacks_mod.unstack(db, list(body.get("ids") or []))})

    @app.post("/api/stacks/cover")
    def stack_cover(body: dict):
        stacks_mod.set_cover(db, int(body.get("id") or 0))
        return _json({"ok": True})

    # -- similar images ------------------------------------------------------------------
    phash_fill = similar_mod.Backfill(db)
    app.state.backfill = phash_fill   # started by __main__ a little after start-up

    @app.get("/api/similar/status")
    def similar_status():
        """How many pictures still need a hash for "Similar images" (starts making them)."""
        running = phash_fill.start()
        return _json({"pending": phash_fill.pending(), "running": running, "done": phash_fill.done})

    # -- saved views ("smart folders"): a name for the grid's current filters -----------------
    VIEW_KEYS = {"folder", "recursive", "q", "ext", "sort", "order", "guessed", "tag", "exclude", "missing",
                 "hues", "shape", "alpha", "minside", "bpm_min", "bpm_max", "key", "len_min", "len_max", "tex",
                 "untagged", "project", "dupes", "similar", "suggested"}

    @app.get("/api/views")
    def get_views():
        return _json([{"id": r[0], "name": r[1], "query": json.loads(r[2])} for r in
                      db.cat.execute("SELECT id, name, query FROM saved_views ORDER BY position, name COLLATE NOCASE")])

    @app.post("/api/views")
    def add_view(body: dict):
        name = str(body.get("name") or "").strip()[:80]
        if not name:
            raise HTTPException(400, "Give the view a name")
        q = {k: v for k, v in (body.get("query") or {}).items() if k in VIEW_KEYS and v not in (None, "", False)}
        with db.write_lock:
            row = db.cat.execute("SELECT id FROM saved_views WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            if row:
                db.cat.execute("UPDATE saved_views SET query = ? WHERE id = ?", (json.dumps(q), row[0]))
            else:
                db.cat.execute("INSERT INTO saved_views(name, query, position, created) VALUES (?, ?, "
                               "(SELECT COALESCE(MAX(position), 0) + 1 FROM saved_views), ?)", (name, json.dumps(q), time.time()))
        return get_views()

    @app.delete("/api/views/{view_id}")
    def delete_view(view_id: int):
        with db.write_lock:
            db.cat.execute("DELETE FROM saved_views WHERE id = ?", (view_id,))
        return get_views()

    @app.get("/api/rules")
    def get_rules():
        return _json(rules.get_rules(db))

    @app.put("/api/rules")
    def put_rules(body: RulesIn):
        return _json(rules.set_rules(db, body.rules))

    @app.post("/api/rules/plan")
    def rules_plan(body: IdsIn):
        entries, skipped = rules.plan_entries(db, body.ids)
        plan = fileops.plan_moves(db, entries)
        plan["skipped"] += skipped
        plan["count"] = len(plan["moves"])
        per_rule: dict[int, int] = {}
        for m in plan["moves"]:
            per_rule[m["rule"]] = per_rule.get(m["rule"], 0) + 1
        plan["per_rule"] = per_rule
        plan["moves"] = plan["moves"][:500]
        return _json(plan)

    @app.post("/api/rules/apply")
    def rules_apply(body: IdsIn):
        entries, skipped = rules.plan_entries(db, body.ids)
        plan = fileops.plan_moves(db, entries)
        res = fileops.execute_moves(db, scanner, plan["moves"], None, kind="rules")
        res["skipped"] = plan["skipped"] + skipped
        return _json(res)

    # -- moving --------------------------------------------------------------
    @app.post("/api/move/plan")
    def move_plan(body: MoveIn):
        plan = fileops.plan_move(db, body.ids, body.dest, body.by_year, body.date_prefix)
        plan["count"] = len(plan["moves"])
        plan["moves"] = plan["moves"][:500]
        return _json(plan)

    @app.post("/api/move")
    def move(body: MoveIn):
        plan = fileops.plan_move(db, body.ids, body.dest, body.by_year, body.date_prefix)
        res = fileops.execute_moves(db, scanner, plan["moves"], plan["root_id"])
        res["skipped"] = plan["skipped"]
        return _json(res)

    # -- backups -------------------------------------------------------------
    @app.get("/api/backups")
    def backup_sets():
        return _json({"sets": backup.list_sets(db), "coverage": backup.coverage(db)})

    @app.post("/api/backups")
    def backup_create(body: BackupSetIn):
        return _json(backup.save_set(db, body.model_dump()))

    @app.put("/api/backups/{set_id}")
    def backup_update(set_id: int, body: BackupSetIn):
        return _json(backup.save_set(db, body.model_dump(), set_id))

    @app.delete("/api/backups/{set_id}")
    def backup_delete(set_id: int):
        backup.delete_set(db, set_id)
        return _json({"ok": True})

    @app.post("/api/backups/{set_id}/plan")
    def backup_plan(set_id: int):
        return _json(backup.plan_summary(backup.plan(db, set_id)))

    @app.post("/api/backups/{set_id}/run")
    def backup_run(set_id: int):
        return _json(jobs.start_backup(set_id))

    @app.post("/api/backups/{set_id}/restore/plan")
    def restore_plan(set_id: int, body: RestoreIn):
        p = backup.plan_restore(db, set_id, body.mode, body.folder)
        p["examples"] = p.pop("items")[:300]
        return _json(p)

    @app.post("/api/backups/{set_id}/restore")
    def restore_run(set_id: int, body: RestoreIn):
        return _json(jobs.start_restore(set_id, body.mode, body.folder))

    @app.get("/api/rclone")
    def rclone_info():
        return _json(rc.info())

    @app.post("/api/rclone/connect")
    def rclone_connect(body: ConnectIn):
        rc.connect_google_drive(body.name)
        return _json(rc.info())

    # -- AI (optional) ----------------------------------------------------------
    def _ai():
        if ai is None:
            raise HTTPException(503, "AI features are switched off")
        return ai

    @app.get("/api/ai")
    def ai_info():
        if ai is None:
            return _json({"status": {"installed": False, "error": "switched off"}})
        return _json(ai.info())

    @app.put("/api/ai/settings")
    def ai_settings(body: dict):
        return _json(_ai().save_settings(body))

    @app.post("/api/ai/embed")
    def ai_embed(body: dict):
        ids = body.get("ids")
        return _json(_ai().start_embed([int(i) for i in ids] if ids else None))

    @app.post("/api/ai/search")
    def ai_search(body: dict):
        q = str(body.get("q") or "").strip()
        if not q:
            raise HTTPException(400, "Type what you are looking for")
        return _json(_payload_for_ranked(_ai().search_text(q, int(body.get("limit") or 500))))

    @app.get("/api/ai/similar/{item_id}")
    def ai_similar(item_id: int, limit: int = 500):
        return _json(_payload_for_ranked(_ai().similar(item_id, limit)))

    @app.get("/api/ai/profiles")
    def ai_profiles():
        return _json(_ai().profiles())

    @app.post("/api/ai/profiles")
    def ai_profile_create(body: dict):
        return _json(_ai().save_profile(body))

    @app.put("/api/ai/profiles/{pid}")
    def ai_profile_update(pid: int, body: dict):
        return _json(_ai().save_profile(body, pid))

    @app.delete("/api/ai/profiles/{pid}")
    def ai_profile_delete(pid: int):
        _ai().delete_profile(pid)
        return _json({"ok": True})

    @app.post("/api/ai/profiles/{pid}/train")
    def ai_train(pid: int):
        return _json(_ai().train(pid))

    @app.post("/api/ai/profiles/{pid}/run")
    def ai_run(pid: int, body: dict):
        return _json(_ai().start_run(pid, [int(i) for i in body.get("ids") or []]))

    @app.put("/api/tags/{tag_id}/prompt")
    def tag_prompt(tag_id: int, body: dict):
        _ai().set_prompt(tag_id, body.get("prompt"))
        return _json(tags.get_tag(db, tag_id) | {"ai_prompt": body.get("prompt")})

    def _sel(body: dict):
        ids = body.get("ids")
        return dict(ids=[int(i) for i in ids] if ids is not None else None, tag_id=body.get("tag_id"),
                    min_score=body.get("min_score"), profile_id=body.get("profile_id"))

    @app.post("/api/ai/suggestions/accept")
    def ai_accept(body: dict):
        res = _ai().accept(**_sel(body))
        _auto_sidecars([i for ch in res["changes"] for i in ch["added"]])
        return _json(res)

    @app.post("/api/ai/suggestions/reject")
    def ai_reject(body: dict):
        return _json(_ai().reject(**_sel(body)))

    @app.get("/api/ai/job")
    def ai_job():
        return _json(dict(_ai().state))

    @app.post("/api/ai/cancel")
    def ai_cancel():
        _ai().cancel()
        return _json({"ok": True})

    @app.get("/api/ai/ollama")
    def ai_ollama():
        try:
            return _json({"ok": True, "models": _ai().ollama_models()})
        except Exception as e:
            return _json({"ok": False, "error": str(e), "models": []})

    @app.get("/api/job")
    def job_status():
        return _json(jobs.status())

    @app.post("/api/job/cancel")
    def job_cancel():
        jobs.cancel()
        return _json(jobs.status())

    @app.post("/api/operations/{op_id}/undo")
    def undo(op_id: int):
        return _json(fileops.undo_operation(db, scanner, op_id))

    @app.post("/api/open")
    def open_file(body: IdIn):
        """Open in the default application (Krita, Photoshop, an audio editor...)."""
        row = db.cat.execute("SELECT path FROM files WHERE id = ?", (body.id,)).fetchone()
        if not row:
            raise HTTPException(404)
        fileops.open_default(row[0])
        return _json({"ok": True})

    @app.post("/api/reveal")
    def reveal(body: IdIn):
        row = db.cat.execute("SELECT path FROM files WHERE id = ?", (body.id,)).fetchone()
        if not row:
            raise HTTPException(404)
        fileops.reveal(row[0])
        return _json({"ok": True})

    # -- thumbnails / originals --------------------------------------------
    def _generate(item_id: int, path: str, size: int, need_full: bool) -> dict:
        r = process_file(path, (size,) if not need_full or size == 256 else (256, size))
        if "error" not in r:
            if need_full:
                db.store_results([r])
            else:
                db.put_thumbs(r["hash"], r["thumbs"])
        elif need_full:
            db.store_results([r])
        return r

    @app.get("/t/{item_id}")
    def thumb(item_id: int, request: Request, s: int = Query(256), v: str | None = None):
        strip = s == 1   # the hover-scrub strip of a video (made together with its thumbnails)
        if s not in THUMB_SIZES and not strip:
            raise HTTPException(400, "bad size")
        row = db.cat.execute("SELECT path, hash, status, tver FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        path, h, status, tver = row
        data = db.get_thumb(h, s) if h and status == 1 else None
        if data is None and strip and status == 1:
            raise HTTPException(404, "no strip for this file")
        if data is None:
            if status == 2 and s in (1, 256):
                raise HTTPException(422, "cannot decode")
            gen = 256 if strip else s
            key = (item_id, gen)
            with inflight_lock:  # coalesce duplicate requests for the same thumbnail
                fut = inflight.get(key)
                if fut is None:
                    fut = inflight[key] = ondemand.submit(_generate, item_id, path, gen, status != 1)
            try:
                r = fut.result(timeout=120)
            finally:
                with inflight_lock:
                    inflight.pop(key, None)
            if "error" in r:
                raise HTTPException(422, r["error"])
            h = r["hash"]
            data = r["thumbs"].get(s)
            if data is None:
                raise HTTPException(404, "no strip for this file")
        etag = f'"{h[:16]}-{s}-{tver or 0}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        cache = "private, max-age=31536000, immutable" if v and v == _tv(h, tver) else "private, no-cache"
        return Response(data, media_type="image/webp", headers={"Cache-Control": cache, "ETag": etag})

    mesh_cache: dict = {}

    @app.get("/api/mesh/{item_id}")
    def mesh(item_id: int):
        """Geometry for the 3D viewer (see kurator/mesh3d/pack.py for the layout)."""
        body, _ = _mesh_entry(item_id)
        return Response(body, media_type="application/octet-stream",
                        headers={"Cache-Control": "private, no-cache", "X-Mesh-Bytes": str(len(body))})   # for progress (gzip changes the length)

    @app.get("/api/mesh/{item_id}/tex/{k}")
    def mesh_texture(item_id: int, k: int, s: int = 2048):
        from .mesh3d.pack import texture_bytes
        _, textures = _mesh_entry(item_id)
        if not 0 <= k < len(textures):
            raise HTTPException(404)
        ref, role = textures[k]
        out = texture_bytes(ref, role, max(64, min(int(s), 4096)))
        if out is None:
            raise HTTPException(415, "this texture can't be read (format not supported)")
        return Response(out[0], media_type=out[1], headers={"Cache-Control": "private, max-age=600"})

    def _mesh_entry(item_id: int):
        from .mesh3d import MeshError, read_mesh
        from .mesh3d.pack import pack
        row = db.cat.execute("SELECT path, ext, size, mtime_ns, hash FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        h = handler_for(row[1])
        is_blend = row[1] in (".blend", ".blend1", ".blend2")
        if not (h and (h.load.__name__ == "load_mesh_file" or is_blend)):
            raise HTTPException(415, "no 3D view for this format")
        if is_blend:
            from . import blender
            if not blender.find():
                raise HTTPException(415, "Install Blender to view .blend files in 3D (or set KURATOR_BLENDER to blender.exe)")
        if not os.path.isfile(row[0]):
            raise HTTPException(404, "file is missing on disk")
        key = (row[0], row[2], row[3])
        entry = mesh_cache.get(key)
        if entry is None:
            src = row[0]
            if is_blend:   # Blender (if installed) converts it once; the glTF is cached by content hash
                from . import blender
                try:
                    src = blender.to_glb(row[0], row[4] or f"id{item_id}", os.path.join(db.data_dir, "cache", "blend"))
                except blender.BlenderError as e:
                    raise HTTPException(422, str(e)) from None
            with open(src, "rb") as f:
                data = f.read()
            try:
                entry = pack(read_mesh(data, src, full=True))
            except MeshError as e:
                raise HTTPException(422, str(e)) from None
            except MemoryError:
                raise HTTPException(507, "not enough free memory to open this model") from None
            del data
            if len(mesh_cache) >= 2:
                mesh_cache.pop(next(iter(mesh_cache)))
            mesh_cache[key] = entry
        return entry

    @app.get("/f/{item_id}")
    def original(item_id: int):
        row = db.cat.execute("SELECT path, ext FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        h = handler_for(row[1])
        if not (h and h.browser_viewable):
            raise HTTPException(415, "not viewable in browser; use preview size")
        if not os.path.isfile(row[0]):
            raise HTTPException(404, "file is missing on disk")
        headers = {"Cache-Control": "private, no-cache"}
        if row[1] in (".svg", ".svgz"):
            from .handlers.vector import svg_for_view
            try:
                with open(row[0], "rb") as f:
                    fixed = svg_for_view(f.read(60 * 1024 * 1024))
            except (OSError, ValueError):
                fixed = None
            if fixed is not None:   # a page without size, or "show all objects": the page made to fit everything
                headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
                return Response(fixed, media_type="image/svg+xml", headers=headers)
        if row[1] == ".svg":
            # An SVG can contain scripts. Shown as <img> they never run; opened directly in a tab, this
            # policy keeps them from running in Kurator's origin.
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        return FileResponse(row[0], media_type=MIME.get(row[1], "application/octet-stream"),
                            headers=headers, content_disposition_type="inline")

    return app

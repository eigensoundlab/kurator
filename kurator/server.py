"""HTTP API + static UI. Listens on loopback only."""

from __future__ import annotations

import io
import json
import math
import mimetypes
import os
import threading
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from . import __version__, apps, assets, backup, dates, fileops, playhead_index, projects, rclone, rules, sfxlink, tags, xmp
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
from .handlers import extension_kinds, handler_for, supported_extensions, supported_types
from .processing import THUMB_SIZES, process_file
from .scanner import Scanner

mimetypes.add_type("text/javascript", ".js")    # module scripts need this type; the Windows registry
mimetypes.add_type("text/javascript", ".mjs")   # can map .js to something else


class _GZip(GZipMiddleware):
    """Gzip, except decoded samples (float audio hardly compresses and can be large) and spectrogram PNGs."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith(("/samples", "/spectrogram")):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

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
    create: bool = False   # make the folder first (where dropped files are to go)


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


class CopyIn(MoveIn):
    keep_tags: bool = True             # copies inside the library get the original's tags, source, author, licence
    clean: bool = False                # for delivery: GPS, serials, history, AI parameters... out (delivery.py)
    credit: bool = False               # for delivery: author and licence written into the files
    zip: str | None = None             # for delivery: one zip archive of this name instead of loose files


class IdIn(BaseModel):
    id: int


class IdsIn(BaseModel):
    ids: list[int]
    include_untagged: bool = False
    merge: bool | None = None          # also other programs' sidecars (None: the setting)


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
    every_days: float | None = None    # schedule: every N days (None: none)
    auto: bool = False                 # ... run by itself (else: a reminder)


class RestoreIn(BaseModel):
    mode: str = "missing"
    folder: str | None = None


class SettingsIn(BaseModel):
    delete_mode: str | None = None     # 'system' (Recycle Bin / Trash) | 'kurator' (Kurator's trash folder)
    trash_days: int | None = None      # Kurator's trash folder: empty after N days (0: never)
    xmp_auto: bool | None = None
    xmp_merge: bool | None = None
    svg_all: bool | None = None
    proof_profile: str | None = None
    rclone_path: str | None = None
    update_check: bool | None = None   # ask GitHub once a day whether there is a newer Kurator
    sfx_url: str | None = None         # where SFX Workbench is ("": the web app)


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
        sched = backup.Scheduler(db, jobs).start()      # automatic backups that are due

        def trash_cleanup():
            from . import trash
            try:
                trash.auto_empty(db)                     # Kurator's trash folder: batches older than N days
            except Exception:  # noqa: BLE001 - try again next start
                pass
        threading.Thread(target=trash_cleanup, name="kurator-trash", daemon=True).start()
        if not db.get_setting("ph_backfill") and os.path.isdir(ph_dir):   # Playhead documents stored by 0.16
            def ph_backfill():
                try:
                    playhead_index.backfill(db, ph_dir)
                    db.set_setting("ph_backfill", 1)
                except Exception:  # noqa: BLE001 - a summary is a convenience; try again next start
                    pass
            threading.Thread(target=ph_backfill, name="kurator-ph-backfill", daemon=True).start()
        yield
        ph_job.cancel()
        resampler.cancel()
        sched.stop()
        ondemand.shutdown(wait=False, cancel_futures=True)
        jobs.cancel()
        rc.stop()
        if watcher:
            watcher.stop()
        scanner.stop()

    jobs = backup.Jobs(db, scanner)
    ph_dir = os.path.join(db.data_dir, "cache", "playhead")   # Playhead documents (see playhead_index.py)
    ph_job = playhead_index.LibraryJob(db, ph_dir)
    rc = rclone.shared(db, os.path.join(db.data_dir, "logs"))
    app = FastAPI(title="Kurator", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.add_middleware(_GZip, minimum_size=2048, compresslevel=5)

    allowed_hosts = {f"{h}:{port}" for h in LOOPBACK} | LOOPBACK
    allowed_origins = {f"http://{h}:{port}" for h in LOOPBACK}

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Blocks DNS-rebinding (foreign Host header) and cross-site requests from web pages.
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if request.url.path.startswith("/api/sfx/link/"):
            return await _sfx_cors(request, call_next)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin not in allowed_origins:
                return JSONResponse({"detail": "forbidden origin"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site" and not (
                # opening Kurator's page itself from elsewhere (a bookmark, the desktop app's start
                # page) is harmless: it changes nothing, and everything it then asks is same-site
                request.method == "GET" and request.url.path == "/" and request.headers.get("sec-fetch-mode") == "navigate"):
            return JSONResponse({"detail": "cross-site request blocked"}, status_code=403)
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            # the UI's own files: always ask (a 304 when unchanged), so an update shows on the next
            # reload - without this, browsers guess how long to keep them and run old scripts
            resp.headers.setdefault("Cache-Control", "no-cache")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self' ipc: http://ipc.localhost; frame-ancestors 'none'",
        )
        return resp

    async def _sfx_cors(request: Request, call_next):
        """The SFX Workbench link: pages from SFX Workbench's address only (another site, even one
        with the token, gets nothing), and what the link allows is checked by its token."""
        origin = request.headers.get("origin")
        if not sfxlink.origin_ok(db, origin):
            return JSONResponse({"detail": "Only SFX Workbench can use this link (its address is in Settings)"}, status_code=403)
        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Expose-Headers": "Content-Length",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
        if request.method == "OPTIONS":
            return Response(status_code=204, headers={**cors, "Access-Control-Allow-Methods": "GET, POST",
                                                      "Access-Control-Allow-Headers": "Content-Type",
                                                      "Access-Control-Allow-Private-Network": "true",
                                                      "Access-Control-Max-Age": "600"})
        resp = await call_next(request)
        for k, v in cors.items():
            resp.headers[k] = v
        return resp

    inflight: dict[tuple[int, int], object] = {}
    inflight_lock = threading.Lock()

    static_dir = str(resources.files("kurator") / "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.exception_handler(OpError)
    @app.exception_handler(apps.AppError)
    @app.exception_handler(sfxlink.LinkError)
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
            "proof_profile": bool(db.get_setting("proof_profile", None)),
            "backup_due": backup.due(db),
            "sfx": sfxlink.inbox(),
        })

    def _norm_path(p: str) -> str:
        return os.path.normpath(os.path.abspath(os.path.expanduser(p.strip().strip('"'))))

    @app.post("/api/roots")
    def add_root(body: RootIn):
        p = _norm_path(body.path)
        if body.create and not os.path.exists(p):
            try:
                os.makedirs(p)
            except OSError as e:
                raise HTTPException(400, f"Can't make {p}: {e.strerror or e}") from None
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
        ph: str = "",
        phinfo: bool = False,
    ):
        where, args = [], []
        phc = playhead_index.filter_clause(db, ph)      # sound character from Playhead analysis
        if phc:
            where.append(phc[0])
            args += phc[1]
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
        elif not (tag and tag.strip(",")) and not project:  # "All library items": browsed (temporary) folders are not part of the library
            where.append("root_id IN (SELECT id FROM roots WHERE kind = 'library')")
        # a tag or project shows all of its files - also those in a folder you only browsed: you
        # tagged them, and the tag's count includes them
        if q:
            from . import fileinfo
            for term in q.split():
                special = fileinfo.search_clause(term)        # source:x author:x licence:x licence:none
                if special:
                    where.append(special[0])
                    args += special[1]
                    continue
                # a word in the name, or in the source / author / licence
                where.append("(name LIKE ? ESCAPE '\\' OR id IN (SELECT file_id FROM file_info WHERE source LIKE ? ESCAPE '\\' "
                             "OR author LIKE ? ESCAPE '\\' OR licence LIKE ? ESCAPE '\\'))")
                args += [f"%{_like_escape(term)}%"] * 4
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
        order_sql = (playhead_index.sort_sql(sort) or SORTS.get(sort, SORTS["date"])).format(o=o)
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
        if phinfo:      # Playhead strips in the grid: which sounds have a document (and since when)
            st = playhead_index.stamps(db)
            out["p"] = [st.get(i, 0) for i in out["ids"]]
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
        from .projectdirs import engine_root
        er = engine_root(out["path"])
        out["engine"] = er[0] if er else None
        out["twins"] = [{"id": r[0], "path": r[1]} for r in db.cat.execute(   # the same file elsewhere
            "SELECT id, path FROM files WHERE hash = ? AND id != ? ORDER BY path LIMIT 50", (out["hash"], item_id))] if out["hash"] else []
        out["tag_sources"] = {str(r[0]): [r[1], r[2]] for r in db.cat.execute(
            "SELECT tag_id, source, confidence FROM file_tags WHERE file_id = ?", (item_id,))}
        out["captions"] = [{"model": m, "text": tx} for m, tx in db.cat.execute(
            "SELECT model, text FROM captions WHERE file_id = ? ORDER BY created DESC", (item_id,))]
        from . import fileinfo
        out["info"] = fileinfo.get(db, item_id)
        return _json(out)

    @app.get("/api/info/options")
    def info_options():
        """Licence names (with links) and the values used so far, for suggestions while typing."""
        from . import fileinfo
        return _json({"licences": fileinfo.LICENCES, "used": {k: fileinfo.used_values(db, k) for k in fileinfo.FIELDS}})

    @app.post("/api/info")
    def set_info(body: dict):
        """Source / author / licence for one or many files: {"ids": [...], "source": ..., ...}."""
        from . import fileinfo
        ids = [int(i) for i in body.get("ids") or []]
        if not ids:
            raise HTTPException(400, "no files")
        r = fileinfo.set_many(db, ids, {k: body.get(k) for k in fileinfo.FIELDS})
        _auto_sidecars(ids)
        return _json(r)

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

    @app.put("/api/projects/{tag_id}/hotkeys")
    def put_project_hotkeys(tag_id: int, body: dict):
        """{"hotkeys": {"1": tag id, ...}}: the project's own keys (while it is open)."""
        return _json(projects.set_hotkeys(db, tag_id, dict(body.get("hotkeys") or {})))

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
        from . import trash
        return _json({"xmp_auto": db.get_setting("xmp_auto", False), "xmp_merge": db.get_setting("xmp_merge", False),
                      "delete_mode": trash.mode(db), "trash_days": trash.keep_days(db), "system_trash": trash.system_available(),
                      "svg_all": db.get_setting("svg_all", False), "data_dir": db.data_dir,
                      "proof_profile": db.get_setting("proof_profile", None),
                      "rclone_path": db.get_setting("rclone_path", None),
                      "update_check": db.get_setting("update_check", True),
                      "sfx_url": sfxlink.address(db), "sfx_default": sfxlink.DEFAULT_URL,
                      "sfx_links": len(sfxlink.active(db))})

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        if body.delete_mode is not None:
            from . import trash
            if body.delete_mode not in trash.MODES:
                raise HTTPException(400, "Delete to 'system' or 'kurator'")
            db.set_setting("delete_mode", body.delete_mode)
        if body.trash_days is not None:
            db.set_setting("trash_days", max(0, min(int(body.trash_days), 3650)))
        if body.xmp_auto is not None:
            db.set_setting("xmp_auto", body.xmp_auto)
        if body.xmp_merge is not None:
            db.set_setting("xmp_merge", body.xmp_merge)
        if body.proof_profile is not None:
            pp = body.proof_profile.strip().strip('"')
            if pp:
                from .printinfo import _open_profile
                if _open_profile(pp) is None:
                    raise HTTPException(400, "Not a CMYK ICC profile: " + pp)
            db.set_setting("proof_profile", pp or None)
        if body.svg_all is not None and body.svg_all != db.get_setting("svg_all", False):
            db.set_setting("svg_all", body.svg_all)
            os.environ["KURATOR_SVG_ALL"] = "1" if body.svg_all else "0"   # read by the SVG renderer (also in workers)
            if db.remake_previews("ext IN ('.svg', '.svgz')"):
                scanner.request_scan()
        if body.update_check is not None:
            db.set_setting("update_check", body.update_check)
        if body.sfx_url is not None:
            u = sfxlink.check_address(body.sfx_url)
            db.set_setting("sfx_url", None if u in ("", sfxlink.DEFAULT_URL) else u)
        if body.rclone_path is not None:
            p = body.rclone_path.strip().strip('"')
            if p and not os.path.isfile(p):
                raise HTTPException(400, f"Not a file: {p}")
            db.set_setting("rclone_path", p or None)
        return get_settings()

    @app.post("/api/xmp/write")
    def xmp_write(body: IdsIn):
        return _json(xmp.write_sidecars(db, body.ids, body.include_untagged, body.merge))

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

    @app.post("/api/unity/settings")
    def unity_settings(body: dict):
        """Sprite import settings for pictures inside Unity projects (edits / creates their .meta)."""
        from . import maps2d, unity
        from .projectdirs import engine_root
        files, outside = [], 0
        for fid in list(body.get("ids") or [])[:5000]:
            row = db.cat.execute("SELECT path, width, height, kind FROM files WHERE id = ?", (int(fid),)).fetchone()
            if not row or row[3] != "image" or not os.path.isfile(row[0]):
                continue
            root = engine_root(row[0])
            if not root or root[0] != "Unity":
                outside += 1
                continue
            maps = {}
            g = maps2d.group(db, int(fid))
            if g and g["role"] == "base":
                for m in g["maps"]:
                    if m["role"] in ("normal", "mask") and m["role"] not in maps:
                        p = db.cat.execute("SELECT path FROM files WHERE id = ?", (m["id"],)).fetchone()
                        if p:
                            maps[m["role"]] = p[0]
            files.append({"path": row[0], "width": row[1], "height": row[2], "maps": maps})
        if not files:
            raise HTTPException(400, "None of these pictures is inside a Unity project (a folder with Assets and ProjectSettings)")
        try:
            ppu = max(1.0, min(float(body.get("ppu") or 100), 10000.0))
        except (TypeError, ValueError):
            ppu = 100.0
        res = unity.apply_settings(files, db.data_dir, sprite=body.get("sprite", True) is not False, ppu=ppu,
                                   filter_mode=str(body.get("filter") or "bilinear"), compression=str(body.get("compression") or "normal"),
                                   mipmaps=bool(body.get("mipmaps")), link_maps=body.get("link_maps", True) is not False)
        res["outside"] = outside
        return _json(res)

    @app.post("/api/unity/undo")
    def unity_undo(body: dict):
        from . import unity
        try:
            return _json({"restored": unity.undo(db.data_dir, str(body.get("id") or ""))})
        except unity.UnityError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/icc/profiles")
    def icc_profiles():
        from .printinfo import cmyk_profiles
        return _json({"profiles": cmyk_profiles(), "current": db.get_setting("proof_profile", None)})

    def _picture_path(item_id: int) -> str:
        row = db.cat.execute("SELECT path, kind FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row or row[1] != "image":
            raise HTTPException(404)
        if not os.path.isfile(row[0]):
            raise HTTPException(404, "file is missing on disk")
        return row[0]

    @app.get("/api/items/{item_id}/inks")
    def item_inks(item_id: int):
        """Ink amounts for the separations view: 'KINK', u32 JSON length, JSON, then C,M,Y,K bytes per
        pixel (and one gamut byte per pixel when proofing an RGB picture)."""
        from . import printinfo
        path = _picture_path(item_id)
        try:
            r = printinfo.inks(path, db.get_setting("proof_profile", None))
        except (ValueError, OSError) as e:
            raise HTTPException(415, str(e)) from None
        cmyk = r["cmyk"]
        h, w = cmyk.shape[:2]
        head = json.dumps({"width": w, "height": h, "inks": ["Cyan", "Magenta", "Yellow", "Black"], "source": r["source"],
                           "profile": r["profile"], "gamut": r["gamut"] is not None}).encode()
        head += b" " * (-len(head) % 4)
        body = b"KINK" + len(head).to_bytes(4, "little") + head + cmyk.tobytes()
        if r["gamut"] is not None:
            body += r["gamut"].astype("uint8").tobytes()
        return Response(body, media_type="application/octet-stream", headers={"Cache-Control": "private, no-cache"})

    @app.get("/api/items/{item_id}/proof")
    def item_proof(item_id: int):
        from . import printinfo
        path = _picture_path(item_id)
        try:
            img = printinfo.proof_image(path, db.get_setting("proof_profile", None))
        except (ValueError, OSError) as e:
            raise HTTPException(415, str(e)) from None
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=92)
        return Response(buf.getvalue(), media_type="image/webp", headers={"Cache-Control": "private, no-cache"})

    def _pdf_path(item_id: int) -> str:
        row = db.cat.execute("SELECT path FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row or os.path.splitext(row[0])[1].lower() not in (".pdf", ".ai"):
            raise HTTPException(404)
        if not os.path.isfile(row[0]):
            raise HTTPException(404, "file is missing on disk")
        return row[0]

    @app.get("/api/items/{item_id}/pages")
    def pdf_pages(item_id: int):
        """Pages of a PDF (or PDF-based .ai) for paging in the viewer."""
        from . import pdfpages
        try:
            return _json(pdfpages.pages(_pdf_path(item_id)))
        except pdfpages.PageError as e:
            raise HTTPException(415, str(e)) from None

    @app.get("/api/items/{item_id}/page/{n}")
    def pdf_page(item_id: int, n: int, s: int = Query(1600, ge=16, le=8000)):
        from . import pdfpages
        path = _pdf_path(item_id)
        try:
            body = pdfpages.render(path, n, s)
        except pdfpages.PageError as e:
            raise HTTPException(404 if "no such page" in str(e) else 415, str(e)) from None
        st = os.stat(path)
        return Response(body, media_type="image/webp",
                        headers={"Cache-Control": "private, max-age=600", "ETag": f'"{st.st_mtime_ns}-{st.st_size}-{n}-{s}"'})

    @app.get("/api/items/{item_id}/page/{n}/info")
    def pdf_page_info(item_id: int, n: int):
        from . import pdfpages
        try:
            return _json(pdfpages.info(_pdf_path(item_id), n))
        except pdfpages.PageError as e:
            raise HTTPException(404 if "no such page" in str(e) else 415, str(e)) from None

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
        out = exporter.start(_export_plan(body))
        fileops.remember_folder(db, str(body.get("dest") or ""))
        return _json(out)

    @app.get("/api/export/job")
    def export_job():
        return _json(exporter.state)

    @app.post("/api/export/cancel")
    def export_cancel():
        exporter.cancel()
        return _json({"ok": True})

    # -- training datasets (image + caption .txt) ---------------------------------------
    from . import dataset as dataset_mod
    dataset_job = dataset_mod.DatasetJob(db)

    def _dataset_plan(body: dict) -> dict:
        try:
            return dataset_mod.plan(
                db, [int(i) for i in body.get("ids") or []], str(body.get("dest") or ""), str(body.get("folder") or "dataset"),
                str(body.get("captions") or "both"), str(body.get("trigger") or ""), body.get("facets") or None,
                bool(body.get("near_dupes", True)), int(body.get("max_side") or 0), str(body.get("format") or "keep"),
                bool(body.get("flatten")), float(body.get("val") or 0), int(body.get("seed") or 1),
                str(body.get("names") or "numbers"))
        except dataset_mod.DatasetError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/dataset/options")
    def dataset_options():
        return _json({"captions": dataset_mod.CAPTIONS, "formats": dataset_mod.FORMATS})

    @app.post("/api/dataset/plan")
    def dataset_plan(body: dict):
        return _json(dataset_mod.summary(_dataset_plan(body)))

    @app.post("/api/dataset/run")
    def dataset_run(body: dict):
        try:
            out = dataset_job.start(_dataset_plan(body))
        except dataset_mod.DatasetError as e:
            raise HTTPException(409, str(e)) from None
        fileops.remember_folder(db, str(body.get("dest") or ""))
        return _json(out)

    @app.get("/api/dataset/job")
    def dataset_status():
        return _json(dataset_job.state)

    @app.post("/api/dataset/cancel")
    def dataset_cancel():
        dataset_job.cancel()
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
        fileops.remember_folder(db, str(body.get("dest") or ""))
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
        fileops.remember_folder(db, str(body.get("dest") or ""))
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
        unity_opt = body.get("unity", "auto")
        try:
            ppu = max(1.0, min(float(body.get("ppu") or 100), 10000.0))
        except (TypeError, ValueError):
            ppu = 100.0
        files = atlas.save(img, sprites, mode, str(body.get("dest") or ""), str(body.get("name") or ""),
                           godot=bool(body.get("godot")), fps=fps, unity=None if unity_opt == "auto" else bool(unity_opt),
                           ppu=ppu, pixel_art=bool(body.get("pixel_art")))
        fileops.remember_folder(db, str(body.get("dest") or ""))
        return _json({"files": files, **stats})

    # -- resampled copies --------------------------------------------------------------
    from . import resample as resample_mod
    resampler = resample_mod.ResampleJob(db, scanner, on_done=lambda new_ids, keep_tags: _auto_sidecars(new_ids) if keep_tags else None)

    def _resample_plan(body: dict) -> dict:
        try:
            return resample_mod.plan(
                db, [int(i) for i in body.get("ids") or []], mode=str(body.get("mode") or "long"),
                value=body.get("value") or 0, value2=body.get("value2"), enlarge=bool(body.get("enlarge")),
                fmt=str(body.get("format") or "keep"), where=str(body.get("where") or "beside"),
                folder=str(body.get("folder") or ""), suffix=str(body.get("suffix") if body.get("suffix") is not None else "_{w}x{h}"))
        except resample_mod.ResampleError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/resample/options")
    def resample_options():
        return _json({"filters": list(resample_mod.FILTERS), "modes": list(resample_mod.MODES), "formats": list(resample_mod.FORMATS)})

    @app.post("/api/resample/plan")
    def resample_plan(body: dict):
        p = _resample_plan(body)
        reasons: dict[str, int] = {}
        for s in p["skipped"]:
            reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
        return _json({"count": p["count"], "items": p["items"][:300], "skipped": reasons})

    @app.post("/api/resample/run")
    def resample_run(body: dict):
        p = _resample_plan(body)
        try:
            quality = int(body.get("quality") or 90)
        except (TypeError, ValueError):
            quality = 90
        try:
            out = resampler.start(p, str(body.get("filter") or "lanczos"), quality,
                                  body.get("keep_date", True) is not False, body.get("keep_tags", True) is not False)
        except resample_mod.ResampleError as e:
            raise HTTPException(409, str(e)) from None
        if body.get("where") == "folder":
            fileops.remember_folder(db, str(body.get("folder") or ""))
        return _json(out)

    @app.get("/api/resample/job")
    def resample_job():
        return _json(resampler.state)

    @app.post("/api/resample/cancel")
    def resample_cancel():
        resampler.cancel()
        return _json({"ok": True})

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

    def _span(start, end) -> tuple[float, float | None]:
        """A part of a sound in seconds: start >= 0, end after it (or None: to the end)."""
        start = 0.0 if start is None else float(start)
        if not (math.isfinite(start) and 0 <= start <= 1e7):
            raise HTTPException(422, "start must be a time in seconds")
        if end is not None:
            end = float(end)
            if not (math.isfinite(end) and start < end <= 1e7):
                raise HTTPException(422, "end must be a time after start")
            end = round(end, 6)
        return round(start, 6), end

    @app.get("/api/audio/{item_id}/wave")
    def audio_wave(item_id: int, bins: int = 1600, start: float | None = None, end: float | None = None):
        """The waveform overview (min / max / RMS per bin); `start` / `end` (seconds) ask for a part of the
        sound in more detail (rows and zoom in the waveform view)."""
        from . import audio_analysis
        row = _sound(item_id)
        ranged = start is not None or end is not None
        bins = max(100, min(int(bins), 16000 if ranged else 8000))
        a, b = _span(start, end)
        try:
            data = _cached(("w", row[0], row[3], bins, a, b), lambda: audio_analysis.wave_data(row[0], bins, a, b))
        except Exception as e:
            raise HTTPException(422, f"can't read this sound: {e}") from None
        return _json(data)

    @app.get("/api/audio/{item_id}/spectrogram")
    def audio_spectrogram(item_id: int, w: int = 1600, h: int = 256, start: float | None = None, end: float | None = None,
                          ref: float | None = None):
        """Spectrogram PNG of the sound or of [start, end) seconds. Headers: X-Start / X-End (the part it
        really covers) and X-Ref (the level drawn brightest; pass it back as `ref` for matching colours)."""
        from . import audio_analysis
        row = _sound(item_id)
        w, h = max(200, min(int(w), 4000)), max(64, min(int(h), 1024))
        a, b = _span(start, end)
        if ref is not None and not (math.isfinite(ref) and -400 <= ref <= 400):
            raise HTTPException(422, "ref must be a level in dB")
        try:
            png, s0, s1, top = _cached(("s", row[0], row[3], w, h, a, b, ref),
                                       lambda: audio_analysis.spectrogram(row[0], w, h, a, b, ref))
        except Exception as e:
            raise HTTPException(422, f"can't read this sound: {e}") from None
        return Response(png, media_type="image/png", headers={
            "Cache-Control": "private, no-cache", "X-Start": f"{s0:.6f}", "X-End": f"{s1:.6f}", "X-Ref": f"{top:.3f}"})

    # -- sound: decoded samples and stored documents for Playhead analysis (in the browser) ---
    @app.get("/api/audio/{item_id}/samples")
    def audio_samples(item_id: int, rate: int = Query(0, ge=0, le=192000), mono: int = Query(0, ge=0, le=1),
                      max_seconds: float = Query(600, gt=0, le=3600)):
        """Little-endian float32 samples, interleaved (decoded by Kurator, so every format it reads works)."""
        from . import audio_analysis
        row = _sound(item_id)
        try:
            info, chunks = audio_analysis.samples(row[0], rate, bool(mono), max_seconds)
        except Exception as e:
            raise HTTPException(422, f"can't read this sound: {e}") from None
        sr = info["samplerate"]
        headers = {"X-Samplerate": str(sr), "X-Channels": str(info["channels"]), "X-Frames": str(info["frames"]),
                   "X-Truncated": "1" if info["truncated"] else "0", "Cache-Control": "private, no-store",
                   "Content-Length": str(info["frames"] * info["channels"] * 4)}

        def body():
            sent, need = 0, info["frames"] * info["channels"] * 4
            for c in chunks:
                b = c.tobytes()[: need - sent]
                sent += len(b)
                yield b
                if sent >= need:
                    return
            if sent < need:                    # the file was shorter than its header said: pad with silence
                yield bytes(need - sent)
        return StreamingResponse(body(), media_type="application/octet-stream", headers=headers)

    def _ph_file(row, v: str) -> str:
        if not playhead_index.VERSION_RE.fullmatch(v or ""):
            raise HTTPException(400, "bad Playhead version")
        return playhead_index.doc_file(ph_dir, row[0], row[3], v)

    @app.get("/api/audio/{item_id}/playhead")
    def playhead_get(item_id: int, v: str = ""):
        """A stored Playhead document for this file as it is now and this Playhead version (404: none)."""
        f = _ph_file(_sound(item_id), v)
        try:
            with open(f, "rb") as fh:
                data = fh.read()
        except OSError:
            raise HTTPException(404, "no Playhead analysis yet") from None
        return Response(data, media_type="application/json", headers={"Cache-Control": "private, no-store"})

    @app.put("/api/audio/{item_id}/playhead")
    async def playhead_put(item_id: int, request: Request, v: str = ""):
        """Store a document the browser made (and remember its summary for filters and sorts)."""
        row = await run_in_threadpool(_sound, item_id)
        f = _ph_file(row, v)
        if int(request.headers.get("content-length") or 0) > playhead_index.DOC_MAX:
            raise HTTPException(413, "Playhead document too big")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > playhead_index.DOC_MAX:
                raise HTTPException(413, "Playhead document too big")
        try:
            doc = playhead_index.check(bytes(body), v)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

        def save():
            playhead_index.save(ph_dir, f, bytes(body))
            playhead_index.index(db, item_id, row[3], doc)
        await run_in_threadpool(save)
        return _json({"ok": True, "bytes": len(body)})

    ph_thin: dict = {}

    @app.get("/api/audio/{item_id}/playhead/thumb")
    def playhead_thumb(item_id: int):
        """A light copy of the sound's latest stored document, for Playhead strips in the grid (404: none)."""
        row = _sound(item_id)
        s = db.cat.execute("SELECT version FROM ph_summary WHERE file_id = ? AND mtime_ns = ?", (item_id, row[3])).fetchone()
        if not s:
            raise HTTPException(404, "no Playhead analysis yet")
        f = playhead_index.doc_file(ph_dir, row[0], row[3], s[0])
        body = ph_thin.get(f)
        if body is None:
            try:
                with open(f, "rb") as fh:
                    doc = playhead_index.check(fh.read())
            except (OSError, ValueError):
                raise HTTPException(404, "no Playhead analysis yet") from None
            body = json.dumps(playhead_index.thin(doc), separators=(",", ":")).encode()
            if len(ph_thin) >= 400:
                ph_thin.pop(next(iter(ph_thin)))
            ph_thin[f] = body
        meta = db.cat.execute("SELECT json_extract(meta, '$.duration') FROM files WHERE id = ?", (item_id,)).fetchone()
        return Response(body, media_type="application/json",
                        headers={"Cache-Control": "private, no-store", "X-Duration": f"{float(meta[0] or 0):.3f}" if meta else "0"})

    # -- Playhead across the library: counts, the command line, analysing many sounds -------
    @app.get("/api/playhead/status")
    def playhead_status():
        """Sounds analysed so far, the command line (if installed) and the background job."""
        return _json({**playhead_index.counts(db), "cli": playhead_index.cli_info(), "job": ph_job.status(),
                      "filters": {k: {"group": g, "label": lab, "title": t} for k, (g, lab, t, _) in playhead_index.FILTERS.items()}})

    @app.get("/api/playhead/todo")
    def playhead_todo(scope: str = "missing", limit: int = Query(20000, ge=1, le=200000)):
        """Sounds to analyse in the browser: [[id, duration, name], ...] (not analysed yet, or all)."""
        if scope not in ("missing", "all"):
            raise HTTPException(422, "scope is missing or all")
        rows = playhead_index.todo(db, scope, limit=limit)
        return _json({"items": [[r[0], r[3] or 0, r[4]] for r in rows]})

    @app.post("/api/playhead/analyse")
    def playhead_analyse(body: dict):
        """Analyse sounds with the Playhead command line in the background: scope "missing" (not analysed
        yet), "all", or "ids" with a list of ids."""
        scope = body.get("scope") or "missing"
        if scope not in ("missing", "all", "ids"):
            raise HTTPException(422, "scope is missing, all or ids")
        ids = body.get("ids") or []
        if scope == "ids" and not (isinstance(ids, list) and all(isinstance(i, int) for i in ids)):
            raise HTTPException(422, "ids must be a list of numbers")
        try:
            return _json(ph_job.start(playhead_index.todo(db, scope, ids)))
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/playhead/cancel")
    def playhead_cancel():
        ph_job.cancel()
        return _json(ph_job.status())

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
                 "hues", "shape", "alpha", "minside", "bpm_min", "bpm_max", "key", "len_min", "len_max", "tex", "ph",
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
        fileops.remember_folder(db, plan["dest"])
        return _json(res)

    # -- copying -------------------------------------------------------------
    @app.post("/api/copy/plan")
    def copy_plan(body: CopyIn):
        plan = fileops.plan_copy(db, body.ids, body.dest, body.by_year, body.date_prefix)
        plan["count"] = len(plan["copies"])
        plan["copies"] = plan["copies"][:500]
        return _json(plan)

    @app.post("/api/copy")
    def copy(body: CopyIn):
        plan = fileops.plan_copy(db, body.ids, body.dest, body.by_year, body.date_prefix)
        if body.zip is not None:
            if not plan["copies"]:
                raise HTTPException(400, "Nothing to copy")
            res = fileops.copy_to_zip(db, scanner, plan["copies"], plan["dest"], body.zip, body.clean, body.credit)
        else:
            res = fileops.execute_copies(db, scanner, plan["copies"], body.keep_tags, body.clean, body.credit)
        res["skipped"] = plan["skipped"]
        if body.keep_tags:
            _auto_sidecars(res["new_ids"])
        fileops.remember_folder(db, plan["dest"])
        return _json(res)

    # -- renaming ------------------------------------------------------------
    def _rename_plan(body: dict) -> dict:
        try:
            return fileops.plan_rename(
                db, [int(i) for i in body.get("ids") or []], str(body.get("template") or ""), int(body.get("start") or 1),
                int(body.get("step") or 1), int(body.get("pad") or 0), str(body.get("find") or ""), str(body.get("replace") or ""),
                bool(body.get("regex")), str(body.get("case") or "keep"))
        except (TypeError, ValueError) as e:
            if isinstance(e, OpError):
                raise
            raise HTTPException(400, "Start, step and padding must be numbers") from None

    @app.post("/api/rename/plan")
    def rename_plan(body: dict):
        p = _rename_plan(body)
        p["renames"] = p["renames"][:500]
        return _json(p)

    @app.post("/api/rename")
    def rename(body: dict):
        p = _rename_plan(body)
        res = fileops.execute_renames(db, scanner, p["renames"])
        res["skipped"] = p["skipped"]
        _auto_sidecars([d["id"] for d in p["renames"]])
        return _json(res)

    # -- deleting (system trash or Kurator's trash folder) and duplicates -------------
    from . import dupes as dupes_mod, trash as trash_mod

    @app.post("/api/delete")
    def delete_files(body: dict):
        ids = [int(i) for i in body.get("ids") or []]
        if not ids:
            raise HTTPException(400, "Select files to delete first")
        return _json(trash_mod.delete_files(db, scanner, ids, body.get("mode")))

    @app.get("/api/trash")
    def trash_state():
        return _json({"mode": trash_mod.mode(db), "days": trash_mod.keep_days(db), "system": trash_mod.system_available(),
                      **trash_mod.holding_info(db)})

    @app.post("/api/trash/empty")
    def trash_empty():
        return _json(trash_mod.empty_holding(db))

    def _dupes_plan(body: dict) -> dict:
        return dupes_mod.plan(db, [int(i) for i in body.get("ids") or []], str(body.get("keep") or "tags"),
                              body.get("folder"), body.get("project") or None)

    @app.post("/api/duplicates/plan")
    def dupes_plan(body: dict):
        p = _dupes_plan(body)
        p["groups"] = p["groups"][:300]
        return _json(p)

    @app.post("/api/duplicates/clean")
    def dupes_clean(body: dict):
        return _json(dupes_mod.clean(db, scanner, _dupes_plan(body), body.get("merge_tags", True) is not False))

    # -- destination folders: recent ones, new folder, quick destinations (D) ----------
    @app.get("/api/folders/recent")
    def recent_folders():
        return _json({"recent": fileops.recent_folders(db), "pins": db.get_setting("pins", [])})

    @app.post("/api/folders/recent/forget")
    def forget_recent(body: dict):
        return _json({"recent": fileops.forget_folder(db, str(body.get("path") or ""))})

    @app.post("/api/fs/mkdir")
    def fs_mkdir(body: dict):
        return _json({"path": fileops.make_folder(str(body.get("parent") or ""), str(body.get("name") or ""))})

    # -- files dropped onto the page (from Explorer / Finder / another app) -----------------
    drops: dict[str, dict] = {}
    drops_lock = threading.Lock()

    @app.post("/api/drop/match")
    def drop_match(body: dict):
        """{files: [{name, size, mtime (ms)}]} -> {ids: [[catalog ids] per file]}"""
        files = list(body.get("files") or [])[:20000]
        return _json({"ids": fileops.match_dropped(db, files)})

    @app.post("/api/drop/file")
    async def drop_file(request: Request, batch: str, dest: str, name: str, mtime: float = 0):
        """The bytes of one dropped file (the request body), saved into `dest`."""
        import uuid
        folder = _drop_folder(dest)
        tmp = os.path.join(folder, f".kurator-drop-{uuid.uuid4().hex}.part")
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
            entry = await run_in_threadpool(fileops.receive_file, db, scanner, tmp, folder, name, mtime)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        _batch_add(batch, folder, entry)
        return _json({"id": entry["id"], "path": entry["to"]})

    def _batch_add(batch: str, folder: str, entry: dict) -> None:
        with drops_lock:
            for k in [k for k, v in drops.items() if time.time() - v["ts"] > 86400]:   # never finished
                drops.pop(k, None)
            b = drops.setdefault(batch[:64], {"entries": [], "dest": folder, "ts": time.time()})
            b["entries"].append(entry)

    def _drop_folder(dest: str) -> str:
        folder = os.path.normpath(os.path.abspath(os.path.expanduser(dest.strip().strip('"'))))
        if not dest.strip():
            raise HTTPException(400, "Choose a folder for the new files")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"Can't use {folder}: {e.strerror or e}") from None
        return folder

    @app.post("/api/drop/paths")
    def drop_paths(body: dict):
        """The desktop app's drops come with their paths: {paths} -> {files, folders, skipped}."""
        r = fileops.classify_dropped(db, [str(p) for p in (body.get("paths") or [])])
        if r["indexing"]:
            scanner.request_process()
        return _json(r)

    @app.post("/api/drop/copy")
    def drop_copy(body: dict):
        """One dropped file (by path) copied into `dest`; finish the batch with /api/drop/done."""
        folder = _drop_folder(str(body.get("dest") or ""))
        entry = fileops.copy_dropped(db, scanner, str(body.get("path") or ""), folder)
        _batch_add(str(body.get("batch") or ""), folder, entry)
        return _json({"id": entry["id"], "path": entry["to"]})

    @app.post("/api/drop/done")
    def drop_done(body: dict):
        """The end of a drop: one operation (Ctrl+Z deletes the received files), then indexing."""
        with drops_lock:
            b = drops.pop(str(body.get("batch") or "")[:64], None)
        if not b or not b["entries"]:
            return _json({"op_id": None, "received": 0, "indexed": 0})
        with db.write_lock:
            op_id = db.cat.execute("INSERT INTO operations(ts, kind, data) VALUES (?, ?, ?)",
                                   (time.time(), "copy", json.dumps(b["entries"]))).lastrowid
        new_ids = [e["id"] for e in b["entries"] if e["id"] is not None]
        if new_ids:
            scanner.request_process()
        fileops.remember_folder(db, b["dest"])
        return _json({"op_id": op_id, "received": len(b["entries"]), "indexed": len(new_ids), "ids": new_ids})

    @app.get("/api/send-slots")
    def get_send_slots():
        return _json(db.get_setting("send_slots", {}) or {})

    @app.put("/api/send-slots")
    def put_send_slots(body: dict):
        """{"scope": "global" or a project id, "slots": {"1": {"path", "action": "copy"|"move", "label"}, ...}}"""
        scope = str(body.get("scope") or "global")
        if scope != "global" and not (scope.isdigit() and projects.is_project(db, int(scope))):
            raise HTTPException(400, "Slots are global or belong to a project")
        clean = {}
        for k, v in (body.get("slots") or {}).items():
            if str(k) not in {str(i) for i in range(1, 10)}:
                raise HTTPException(400, "Slots are numbered 1-9")
            if not v or not str(v.get("path") or "").strip():
                continue
            action = v.get("action") or "copy"
            if action not in ("copy", "move"):
                raise HTTPException(400, "A slot copies or moves")
            clean[str(k)] = {"path": os.path.normpath(os.path.abspath(os.path.expanduser(str(v["path"]).strip().strip('"')))),
                             "action": action, "label": str(v.get("label") or "").strip()[:40]}
        allslots = db.get_setting("send_slots", {}) or {}
        if clean:
            allslots[scope] = clean
        else:
            allslots.pop(scope, None)
        db.set_setting("send_slots", allslots)
        return _json(allslots)

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
        a = _ai()
        a.save_settings(body)
        return _json(a.public_settings())

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
    @app.get("/api/ai/vlm")
    def ai_vlm_models():
        """Models of the chosen vision model server (Ollama or OpenAI-compatible)."""
        a = _ai()
        provider = a.settings()["vlm_provider"]
        try:
            return _json({"ok": True, "provider": provider, "models": a.vlm_models()})
        except Exception as e:
            return _json({"ok": False, "provider": provider, "error": str(e), "models": []})

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

    @app.post("/api/reveal-folder")
    def reveal_folder(body: dict):
        fileops.reveal_folder(str(body.get("path") or ""))
        return _json({"ok": True})

    @app.post("/api/paths")
    def file_paths(body: dict):
        """{ids} -> {paths}: for Copy path and for dragging files out of the desktop app."""
        ids = [int(i) for i in (body.get("ids") or [])][:5000]
        found: dict[int, str] = {}
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            q = ",".join("?" * len(chunk))
            found.update(db.cat.execute(f"SELECT id, path FROM files WHERE id IN ({q})", chunk).fetchall())
        return _json({"paths": [found[i] for i in ids if i in found]})

    @app.get("/api/items/{item_id}/png")
    def item_png(item_id: int, file: int = 0):
        """The picture as PNG, full size (Copy picture). ?file=1: written to a file in Kurator's cache,
        and its path returned - the desktop app puts that on the clipboard."""
        from .processing import render_png
        row = db.cat.execute("SELECT path FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if not os.path.isfile(row[0]):
            raise HTTPException(404, "The file is missing on disk")
        try:
            png = render_png(row[0])
        except (ValueError, OSError) as e:
            raise HTTPException(422, str(e)) from None
        if not file:
            return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})
        out = os.path.join(db.data_dir, "cache", "clipboard.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(png)
        return _json({"path": out})

    client_log = {"n": 0, "t": 0.0}

    @app.post("/api/client-log")
    def page_error(body: dict):
        """An error in the page, written to Kurator's log (at most 30 a minute)."""
        now = time.time()
        if now - client_log["t"] > 60:
            client_log.update(n=0, t=now)
        client_log["n"] += 1
        if client_log["n"] <= 30:
            print(f"[page] {str(body.get('text') or '')[:2000]}", file=sys.stderr, flush=True)
        return _json({"ok": True})

    @app.post("/api/remake")
    def remake(body: dict):
        """{ids}: make their previews again (after a renderer got better, or a file looks wrong)."""
        n = db.remake_files([int(i) for i in (body.get("ids") or [])][:20000])
        if n:
            scanner.request_process()
        return _json({"queued": n})

    @app.get("/api/apps")
    def list_apps():
        """Programs for Open with: found ones and those added in Settings."""
        return _json({"apps": apps.list_apps(db), "kinds": list(apps.KINDS), "ext_kind": extension_kinds()})

    @app.put("/api/apps")
    def save_apps(body: dict):
        """{custom: [{name, path, kinds}], hidden: [ids of found programs not to offer]}"""
        return _json({"apps": apps.save(db, list(body.get("custom") or []), [str(h) for h in body.get("hidden") or []])})

    @app.post("/api/open-with")
    def open_with(body: dict):
        """{app: id from /api/apps, ids: [catalog ids], layout: null | "tracks" | "layers"}: layout puts
        the files together first - sounds as tracks of a REAPER project, pictures as layers of one picture."""
        layout = body.get("layout") or None
        if layout not in (None, "tracks", "layers"):
            raise HTTPException(400, "layout: tracks or layers")
        ids = [int(i) for i in (body.get("ids") or [])][:200 if layout else apps.MAX_FILES]
        paths, durations = [], []
        for i in ids:
            row = db.cat.execute("SELECT path, meta FROM files WHERE id = ?", (i,)).fetchone()
            if row and os.path.exists(row[0]):
                paths.append(row[0])
                try:
                    durations.append(json.loads(row[1] or "{}").get("duration"))
                except ValueError:
                    durations.append(None)
        if not paths:
            raise HTTPException(400, "The files are missing on disk")
        name = apps.open_with(db, str(body.get("app") or ""), paths, layout, durations)
        return _json({"app": name, "files": len(paths), "layout": layout})

    # -- SFX Workbench: sounds there, results back (see sfxlink.py) -----------------------
    @app.post("/api/sfx/share")
    def sfx_share(body: dict):
        """{ids: [sounds] (none: new sounds), dest: folder for the results, tags: [ids], label} -> {url, ...}"""
        return _json(sfxlink.create(db, f"http://127.0.0.1:{port}", list(body.get("ids") or []),
                                    str(body.get("dest") or ""), [int(t) for t in body.get("tags") or []],
                                    str(body.get("label") or "")))

    @app.get("/api/sfx/links")
    def sfx_links():
        return _json({"links": sfxlink.active(db)})

    @app.delete("/api/sfx/links")
    def sfx_end_links():
        return _json({"ended": sfxlink.end_all(db)})

    def _link(token: str) -> dict:
        link = sfxlink.find(db, token)
        if link is None:
            raise HTTPException(404, "This link has ended. Send the sounds from Kurator again.")
        return link

    @app.get("/api/sfx/link/{token}")
    def sfx_manifest(token: str):
        return _json(sfxlink.manifest(db, _link(token)))

    @app.get("/api/sfx/link/{token}/file/{n}")
    def sfx_file(token: str, n: int):
        path = sfxlink.file_path(db, _link(token), n)
        return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/sfx/link/{token}/save")
    async def sfx_save(request: Request, token: str, name: str, src: str = ""):
        """One result (the request body, a WAV): a new file in the link's folder, never over another."""
        import uuid
        link = _link(token)
        if not sfxlink.accepts(name):
            raise HTTPException(400, f"{os.path.basename(name)}: send sounds (WAV, FLAC, OGG...)")
        if not os.path.isdir(link["dest"]):
            try:
                os.makedirs(link["dest"], exist_ok=True)
            except OSError as e:
                raise HTTPException(400, f"Can't use {link['dest']}: {e.strerror or e}") from None
        tmp = os.path.join(link["dest"], f".kurator-sfx-{uuid.uuid4().hex}.part")
        size = 0
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > sfxlink.MAX_BYTES:
                        raise HTTPException(413, "That sound is too large to send (2 GB at most)")
                    f.write(chunk)
            if not size:
                raise HTTPException(400, "The sound arrived empty")
            from_ = [int(x) for x in src.split(",") if x.strip().lstrip("-").isdigit()]
            res = await run_in_threadpool(sfxlink.received, db, scanner, token, link, tmp, name, from_)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return _json(res)

    @app.get("/api/update")
    def update_info(force: int = 0):
        """Is there a newer Kurator (GitHub's latest release, asked at most once a day)?"""
        from . import updates
        return _json(updates.check(db, bool(force)))

    @app.post("/api/update/skip")
    def update_skip(body: dict):
        db.set_setting("update_skip", str(body.get("version") or "") or None)
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
    def original(item_id: int, all: int | None = None):   # noqa: A002 - ?all=1|0: an SVG's content beyond its page
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
                    fixed = svg_for_view(f.read(60 * 1024 * 1024), None if all is None else bool(all))
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

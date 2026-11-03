"""HTTP API + static UI. Listens on loopback only."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.gzip import GZipMiddleware

from . import __version__, backup, dates, fileops, rclone, rules, tags, xmp
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

ITEM_COLS = "id, hash, color, date_ts, name, width, height, date_source, alpha, status, frames"
SORTS = {
    "date": "date_ts {o}, id {o}",
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
        ".flac": "audio/flac", ".m4a": "audio/mp4", ".aac": "audio/aac"}


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
    rclone_path: str | None = None


class ConnectIn(BaseModel):
    name: str


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
            "types": supported_types(),
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
    def folders():
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
    ):
        where, args = [], []
        if tag:
            for t in tag.split(","):
                if not t.strip().isdigit():
                    continue
                ids = tags.descendant_ids(db, int(t))
                if not ids:
                    continue
                where.append(f"id IN (SELECT file_id FROM file_tags WHERE tag_id IN ({','.join('?' * len(ids))}))")
                args += ids
        if untagged:
            where.append("id NOT IN (SELECT file_id FROM file_tags)")
        for fct in (missing or "").split(","):
            if fct.strip() in tags.FACET:  # e.g. "no project yet"
                where.append("id NOT IN (SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id = ft.tag_id WHERE t.facet = ?)")
                args.append(fct.strip())
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
        if suggested:
            where.append("id IN (SELECT file_id FROM suggestions" + (" WHERE profile_id = ?)" if profile else ")"))
            if profile:
                args.append(profile)
        sql = (f"SELECT {ITEM_COLS} FROM files" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order_sql}")
        return _json(_payload(db.cat.execute(sql, args)))

    def _payload(rows, scores: dict | None = None) -> dict:
        ids, v, c, d, n, w, h, ds, a, t, f, sg = [], [], [], [], [], [], [], [], [], [], [], []
        by_file = tags.tags_by_file(db)
        sugg = ai.suggestions_by_file() if ai else {}
        for row in rows:
            t.append(by_file.get(row[0], 0))
            sg.append(sugg.get(row[0], 0))
            f.append(1 if (row[10] or 0) > 1 else 0)
            ids.append(row[0])
            v.append(row[1][:12] if row[1] else ("e" if row[9] == 2 else ""))
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
    def get_tags():
        lib = "root_id IN (SELECT id FROM roots WHERE kind = 'library')"
        untagged = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE id NOT IN (SELECT file_id FROM file_tags) AND {lib}").fetchone()[0]
        missing = {}
        for f in tags.FACETS:
            missing[f["key"]] = db.cat.execute(
                "SELECT COUNT(*) FROM files WHERE id NOT IN (SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id = ft.tag_id "
                f"WHERE t.facet = ?) AND {lib}", (f["key"],)).fetchone()[0]
        return _json({"tags": tags.list_tags(db), "untagged": untagged, "facets": tags.FACETS, "missing": missing})

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
        return _json({"xmp_auto": db.get_setting("xmp_auto", False), "data_dir": db.data_dir,
                      "rclone_path": db.get_setting("rclone_path", None)})

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        if body.xmp_auto is not None:
            db.set_setting("xmp_auto", body.xmp_auto)
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
        if s not in THUMB_SIZES:
            raise HTTPException(400, "bad size")
        row = db.cat.execute("SELECT path, hash, status FROM files WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        path, h, status = row
        data = db.get_thumb(h, s) if h and status == 1 else None
        if data is None:
            if status == 2 and s == 256:
                raise HTTPException(422, "cannot decode")
            key = (item_id, s)
            with inflight_lock:  # coalesce duplicate requests for the same thumbnail
                fut = inflight.get(key)
                if fut is None:
                    fut = inflight[key] = ondemand.submit(_generate, item_id, path, s, status != 1)
            try:
                r = fut.result(timeout=120)
            finally:
                with inflight_lock:
                    inflight.pop(key, None)
            if "error" in r:
                raise HTTPException(422, r["error"])
            h = r["hash"]
            data = r["thumbs"][s]
        etag = f'"{h[:16]}-{s}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        cache = "private, max-age=31536000, immutable" if v and h.startswith(v) else "private, no-cache"
        return Response(data, media_type="image/webp", headers={"Cache-Control": cache, "ETag": etag})

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
        if row[1] == ".svg":
            # An SVG can contain scripts. Shown as <img> they never run; opened directly in a tab, this
            # policy keeps them from running in Kurator's origin.
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        return FileResponse(row[0], media_type=MIME.get(row[1], "application/octet-stream"),
                            headers=headers, content_disposition_type="inline")

    return app

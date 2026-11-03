"""Backups: incremental, verified copies of the library to one or more targets.

Design
  * A backup SET says what (scope), how (layout: original folders or tag folders) and where
    (one or more targets - disks, NAS folders, later cloud drives). New files are spread over the
    targets by free space; Kurator records which file went where (backup_files), so later runs
    only copy what is new or changed, and restore knows where everything is.
  * Nothing in a backup is ever deleted. A replaced version - or, in 'mirror' mode, a file removed
    from the library - is moved to _kurator_archive/<date>/ on the same target.
  * Each run is planned first (counts, bytes, free space per target) and shown before anything
    happens. Every finished file is recorded immediately, so an interrupted run resumes cheaply.
  * Each target gets _kurator/manifest.jsonl (file, original path, dates, tags, checksum) and a
    snapshot of the catalog, so a backup is understandable and restorable even without Kurator.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from datetime import datetime

from . import __version__
from .backup_targets import ARCHIVE_DIR, META_DIR, TargetError, make_target
from .db import Database

TOKENS = {"tagpath", "tag", "subtag", "year", "month", "project", "status", "source"}
_TOKEN_RE = re.compile(r"\{(\w+)\}")
_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')


class BackupError(ValueError):
    pass


# ------------------------------------------------------------------ sets
def _set_row(db: Database, set_id: int) -> dict:
    r = db.cat.execute("SELECT * FROM backup_sets WHERE id = ?", (set_id,)).fetchone()
    if not r:
        raise BackupError("No such backup set")
    out = dict(r)
    out["scope"] = json.loads(out["scope"] or "{}")
    out["last_result"] = json.loads(out["last_result"]) if out["last_result"] else None
    out["targets"] = [dict(t) for t in db.cat.execute(
        "SELECT * FROM backup_targets WHERE set_id = ? ORDER BY position, id", (set_id,))]
    return out


def list_sets(db: Database, with_space: bool = True) -> list[dict]:
    out = []
    for (sid,) in db.cat.execute("SELECT id FROM backup_sets ORDER BY id").fetchall():
        s = _set_row(db, sid)
        n, b = db.cat.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM backup_files WHERE set_id = ?", (sid,)).fetchone()
        s["files"], s["bytes"] = n, b
        for t in s["targets"]:
            t["files"], t["bytes"] = db.cat.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM backup_files WHERE target_id = ?", (t["id"],)).fetchone()
            if with_space:
                try:
                    tgt = make_target(t)
                    t["available"] = tgt.available()
                    t["space"] = tgt.space()
                except TargetError as e:
                    t["available"], t["space"], t["error"] = False, None, str(e)
        out.append(s)
    return out


def _check_location(db: Database, loc: str) -> str:
    p = os.path.normpath(os.path.abspath(os.path.expanduser(loc.strip().strip('"'))))
    if not loc.strip():
        raise BackupError("A target folder is empty")
    for r in db.roots("library"):
        rp = r["path"]
        if p == rp or p.startswith(rp.rstrip(os.sep) + os.sep):
            raise BackupError(f"Backup target {p} is inside the library folder {rp}. Choose a folder outside your library.")
        if rp.startswith(p.rstrip(os.sep) + os.sep):
            raise BackupError(f"Backup target {p} contains the library folder {rp}. Choose a dedicated folder, e.g. {os.path.join(p, 'Kurator Backup')}.")
    if os.path.normcase(os.path.abspath(db.data_dir)).startswith(os.path.normcase(p.rstrip(os.sep) + os.sep)):
        raise BackupError("A backup target cannot contain Kurator's own data folder")
    return p


def save_set(db: Database, data: dict, set_id: int | None = None) -> dict:
    name = str(data.get("name") or "").strip() or "Backup"
    layout = data.get("layout", "original")
    if layout not in ("original", "tags"):
        raise BackupError("Layout must be 'original' or 'tags'")
    tmpl = str(data.get("tag_template") or "{tagpath}").strip()
    unknown = set(_TOKEN_RE.findall(tmpl)) - TOKENS
    if unknown:
        raise BackupError(f"Unknown {{{', '.join(sorted(unknown))}}} in folder template")
    policy = data.get("policy", "fill")
    mode = data.get("mode", "copy")
    if policy not in ("fill", "free") or mode not in ("copy", "mirror"):
        raise BackupError("Invalid policy or mode")
    scope = data.get("scope") or {}
    scope = {k: v for k, v in scope.items() if k in ("tags", "folders") and v}
    targets = data.get("targets") or []
    if not targets:
        raise BackupError("Add at least one target folder")
    locs, kinds = [], []
    remotes = None
    for t in targets:
        kind = t.get("kind") or "local"
        if kind == "rclone":
            from .rclone import RcloneError, shared, split_location
            try:
                remote, rpath = split_location(str(t.get("location", "")))
            except RcloneError as e:
                raise BackupError(str(e)) from None
            if remotes is None:
                remotes = {r["name"] for r in shared().remotes()}
            if remote not in remotes:
                raise BackupError(f'rclone has no remote called "{remote}" - connect it first')
            p = f"{remote}:{rpath}"
            sep = "/"
        elif kind == "local":
            p = _check_location(db, str(t.get("location", "")))
            sep = os.sep
        else:
            raise BackupError(f"Unknown target type {kind}")
        for q in locs:
            if p == q or p.startswith(q.rstrip(sep) + sep) or q.startswith(p.rstrip(sep) + sep):
                raise BackupError("Two targets overlap - each target needs its own folder")
        locs.append(p)
        kinds.append(kind)
    c = db.cat
    with db.write_lock:
        c.execute("BEGIN")
        try:
            vals = (name, layout, tmpl, policy, mode, int(bool(data.get("verify", True))), json.dumps(scope))
            if set_id is None:
                set_id = c.execute("INSERT INTO backup_sets(name, layout, tag_template, policy, mode, verify, scope, created) "
                                   "VALUES (?,?,?,?,?,?,?,?)", (*vals, time.time())).lastrowid
            else:
                if not c.execute("SELECT 1 FROM backup_sets WHERE id=?", (set_id,)).fetchone():
                    raise BackupError("No such backup set")
                c.execute("UPDATE backup_sets SET name=?, layout=?, tag_template=?, policy=?, mode=?, verify=?, scope=? WHERE id=?",
                          (*vals, set_id))
            existing = {r[0]: r[1] for r in c.execute("SELECT id, location FROM backup_targets WHERE set_id=?", (set_id,))}
            keep = set()
            for pos, (t, loc, kind) in enumerate(zip(targets, locs, kinds)):
                reserve = int(float(t.get("reserve_gb", 1)) * 1024 ** 3)
                tid = t.get("id")
                if tid in existing:
                    if os.path.normcase(existing[tid]) != os.path.normcase(loc) and c.execute(
                            "SELECT 1 FROM backup_files WHERE target_id=? LIMIT 1", (tid,)).fetchone():
                        raise BackupError(f"Target {existing[tid]} already holds backed-up files - add a new target instead of changing its folder")
                    c.execute("UPDATE backup_targets SET kind=?, location=?, reserve=?, position=? WHERE id=?", (kind, loc, reserve, pos, tid))
                    keep.add(tid)
                else:
                    keep.add(c.execute("INSERT INTO backup_targets(set_id, kind, location, reserve, position) VALUES (?,?,?,?,?)",
                                       (set_id, kind, loc, reserve, pos)).lastrowid)
            for tid in set(existing) - keep:
                if c.execute("SELECT 1 FROM backup_files WHERE target_id=? LIMIT 1", (tid,)).fetchone():
                    raise BackupError(f"Target {existing[tid]} holds backed-up files and can't be removed from the set")
                c.execute("DELETE FROM backup_targets WHERE id=?", (tid,))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return _set_row(db, set_id)


def delete_set(db: Database, set_id: int) -> None:
    """Forget the set. The backed-up files on the targets are not touched."""
    with db.write_lock:
        db.cat.execute("DELETE FROM backup_sets WHERE id = ?", (set_id,))


# --------------------------------------------------------------- layout
def _safe(part: str) -> str:
    part = _BAD.sub("_", part).strip(" .")
    return part or "_"


def _root_names(db: Database) -> dict[int, str]:
    names, used = {}, {}
    for r in db.roots("library"):
        base = os.path.basename(r["path"].rstrip("\\/")) or r["path"].strip("\\/:") or "Library"
        base = _safe(base)
        n = used.get(base.lower(), 0) + 1
        used[base.lower()] = n
        names[r["id"]] = base if n == 1 else f"{base} ({n})"
    return names


def _tag_priority(db: Database) -> dict[str, tuple]:
    """Top-level tag -> sort key. Tags used in move rules come first, in rule order."""
    from .rules import get_rules
    names = {r[0]: r[1] for r in db.cat.execute("SELECT id, name FROM tags")}
    pri = {}
    for i, rule in enumerate(get_rules(db)):
        n = names.get(rule["tag_id"])
        if n:
            pri.setdefault(n.split("/")[0].lower(), (0, i))
    return pri


def _choose_tag(tags: list[str], pri: dict) -> str | None:
    if not tags:
        return None
    tops = sorted({t.split("/")[0] for t in tags}, key=lambda x: (pri.get(x.lower(), (1, 0)), x.lower()))
    top = tops[0].lower()
    mine = [t for t in tags if t.split("/")[0].lower() == top]
    return sorted(mine, key=lambda t: (-t.count("/"), t.lower()))[0]  # most specific


def layout_dest(s: dict, f: dict, root_names: dict, pri: dict) -> str:
    rel = os.path.relpath(f["path"], f["root"]).replace("\\", "/")
    root = root_names.get(f["root_id"], "Library")
    if s["layout"] == "original":
        return f"{root}/{rel}"
    groups = f.get("groups", {})
    tag = _choose_tag(groups.get("subject", []), pri)  # folders follow the Subjects group
    first = {g: (sorted(groups.get(g, []), key=str.lower) or [""])[0] for g in ("project", "status", "source")}
    uses_group = any("{" + g + "}" in s["tag_template"] for g in first)
    if tag is None and not (uses_group and any(first.values())):
        return f"_untagged/{root}/{rel}"
    d = datetime.fromtimestamp(f["date_ts"]) if f["date_ts"] else None
    parts = (tag or "").split("/")
    values = {"tagpath": tag or "_no-subject", "tag": parts[0] or "_no-subject", "subtag": parts[1] if len(parts) > 1 else "",
              "year": f"{d:%Y}" if d else "undated", "month": f"{d:%m}" if d else "",
              "project": first["project"] or "_no-project", "status": first["status"], "source": first["source"]}
    folder = _TOKEN_RE.sub(lambda m: values.get(m.group(1), ""), s["tag_template"])
    segs = [_safe(p) for p in re.split(r"[\\/]+", folder) if p.strip()]
    return "/".join(segs + [f["name"]])


def _is_variant(cur: str, want: str) -> bool:
    """cur is want, or want with the clash suffix _<id>[_n] that _unique() adds."""
    if cur.lower() == want.lower():
        return True
    cd, cb = cur.rsplit("/", 1) if "/" in cur else ("", cur)
    wd, wb = want.rsplit("/", 1) if "/" in want else ("", want)
    if cd.lower() != wd.lower():
        return False
    stem, ext = os.path.splitext(wb)
    return re.fullmatch(re.escape(stem) + r"_[0-9a-z]{1,10}(_\d+)?" + re.escape(ext), cb, re.I) is not None


def _unique(dest: str, taken: set[str], fid: int, h: str | None) -> str:
    key = dest.lower()
    if key not in taken:
        return dest
    stem, ext = os.path.splitext(dest)
    tag = (h or f"id{fid}")[:8]
    cand = f"{stem}_{tag}{ext}"
    n = 2
    while cand.lower() in taken:
        cand = f"{stem}_{tag}_{n}{ext}"
        n += 1
    return cand


# ----------------------------------------------------------------- plan
def _scope_files(db: Database, s: dict) -> list[dict]:
    from .tags import display_name
    names, facet = {}, {}
    for tid, name, fct in db.cat.execute("SELECT id, name, facet FROM tags"):
        names[tid], facet[tid] = name, fct
    ftags: dict[int, list[str]] = {}
    fgroups: dict[int, dict] = {}
    for fid, tid in db.cat.execute("SELECT file_id, tag_id FROM file_tags"):
        ftags.setdefault(fid, []).append(names[tid])
        fgroups.setdefault(fid, {}).setdefault(facet[tid], []).append(names[tid])
    roots = {r["id"]: r["path"] for r in db.roots("library")}
    scope = s["scope"]
    want_tags = None
    if scope.get("tags"):
        want = [names[t].lower() for t in scope["tags"] if t in names]
        want_tags = lambda ts: any(t.lower() == w or t.lower().startswith(w + "/") for t in ts for w in want)  # noqa: E731
    folders = [os.path.normpath(p) for p in scope.get("folders", [])]
    out = []
    for r in db.cat.execute("SELECT id, root_id, path, name, size, mtime_ns, hash, date_ts, date_source FROM files"):
        if r[1] not in roots:
            continue  # browsed (temporary) folders are not backed up
        tg = ftags.get(r[0], [])
        if want_tags and not want_tags(tg):
            continue
        if folders and not any(r[2].startswith(f.rstrip(os.sep) + os.sep) for f in folders):
            continue
        groups = fgroups.get(r[0], {})
        shown = sorted(display_name(n, g) for g, ns in groups.items() for n in ns)
        out.append({"id": r[0], "root_id": r[1], "root": roots[r[1]], "path": r[2], "name": r[3], "size": r[4] or 0,
                    "mtime_ns": r[5], "hash": r[6], "date_ts": r[7], "date_source": r[8], "tags": shown,
                    "groups": groups})
    return out


def plan(db: Database, set_id: int) -> dict:
    s = _set_row(db, set_id)
    targets = {t["id"]: make_target(t) for t in s["targets"]}
    order = [t["id"] for t in s["targets"]]
    info = {}
    for tid, tgt in targets.items():
        avail = tgt.available()
        if avail:
            tgt.prefetch()
        sp = tgt.space() if avail else {"free": None, "total": None, "used": None}
        free = sp["free"]
        info[tid] = {"id": tid, "location": tgt.label(), "available": avail, "space": sp, "reserve": tgt.reserve,
                     "budget": (free - tgt.reserve) if free is not None else (None if avail else -1),
                     "needed": 0, "files": 0}

    files = _scope_files(db, s)
    in_scope = {f["id"] for f in files}
    rows = {r["file_id"]: dict(r) for r in db.cat.execute("SELECT * FROM backup_files WHERE set_id = ?", (set_id,))}
    library_ids = {r[0] for r in db.cat.execute("SELECT id FROM files")}
    orphans_by_hash: dict[str, list[dict]] = {}
    for fid, r in rows.items():
        if fid not in library_ids and r["hash"]:
            orphans_by_hash.setdefault(r["hash"], []).append(r)
    taken = {r["dest"].lower() for r in rows.values()}
    root_names, pri = _root_names(db), _tag_priority(db)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    ops, skipped = [], []
    counts = {"new": 0, "changed": 0, "missing": 0, "relocate": 0, "relink": 0, "unchanged": 0, "archive": 0, "kept": 0}

    def allocate(size: int) -> int | None:
        cands = [t for t in order if info[t]["available"]]
        if s["policy"] == "free":
            cands.sort(key=lambda t: -(info[t]["budget"] if info[t]["budget"] is not None else float("inf")))
        for t in cands:
            b = info[t]["budget"]
            if b is None or b >= size:
                if b is not None:
                    info[t]["budget"] -= size
                info[t]["needed"] += size
                info[t]["files"] += 1
                return t
        return None

    for f in files:
        want = layout_dest(s, f, root_names, pri)
        row = rows.get(f["id"])
        if row is None and f["hash"] and orphans_by_hash.get(f["hash"]):
            row = orphans_by_hash[f["hash"]].pop()  # same content backed up under an old catalog id
            ops.append({"op": "relink", "old_id": row["file_id"], "id": f["id"]})
            counts["relink"] += 1
            row = {**row, "file_id": f["id"]}
        if row is not None and info.get(row["target_id"], {}).get("available") is False:
            counts["unchanged"] += 1  # target offline: leave as is
            continue
        if row is not None:
            same = (row["size"] == f["size"] and row["mtime_ns"] == f["mtime_ns"]
                    and (not f["hash"] or not row["hash"] or row["hash"] == f["hash"]))
            present = targets[row["target_id"]].exists(row["dest"]) if row["target_id"] in targets else False
            if same and present:
                if not _is_variant(row["dest"], want):  # e.g. re-tagged: move it inside the backup
                    dest = _unique(want, taken, f["id"], f["hash"])
                    taken.add(dest.lower())
                    ops.append({"op": "relocate", "id": f["id"], "target": row["target_id"], "from": row["dest"], "to": dest})
                    counts["relocate"] += 1
                else:
                    counts["unchanged"] += 1
                    if (row["src_path"] != f["path"] or json.loads(row["tags"] or "[]") != f["tags"]
                            or row["date_ts"] != f["date_ts"]):
                        ops.append({"op": "meta", "id": f["id"]})  # moved/re-tagged in the library
                continue
            reason = "changed" if present else "missing"
            dest = row["dest"] if _is_variant(row["dest"], want) else _unique(want, taken, f["id"], f["hash"])
            taken.add(dest.lower())
            t = allocate(f["size"])
            if t is None:
                skipped.append({"id": f["id"], "path": f["path"], "reason": "not enough space on any target"})
                continue
            op = {"op": "copy", "id": f["id"], "target": t, "dest": dest, "src": f["path"], "size": f["size"], "reason": reason}
            if present:
                op["archive"] = {"target": row["target_id"], "from": row["dest"], "to": f"{ARCHIVE_DIR}/{stamp}/{row['dest']}"}
            ops.append(op)
            counts[reason] += 1
            continue
        dest = _unique(want, taken, f["id"], f["hash"])
        taken.add(dest.lower())
        t = allocate(f["size"])
        if t is None:
            skipped.append({"id": f["id"], "path": f["path"], "reason": "not enough space on any target"})
            continue
        ops.append({"op": "copy", "id": f["id"], "target": t, "dest": dest, "src": f["path"], "size": f["size"], "reason": "new"})
        counts["new"] += 1

    # Backed-up files that left the library (or the set's scope)
    relinked = {o["old_id"] for o in ops if o["op"] == "relink"}
    for fid, r in rows.items():
        if fid in in_scope or fid in relinked:
            continue
        if s["mode"] == "mirror" and info.get(r["target_id"], {}).get("available"):
            ops.append({"op": "archive", "id": fid, "target": r["target_id"], "from": r["dest"],
                        "to": f"{ARCHIVE_DIR}/{stamp}/{r['dest']}"})
            counts["archive"] += 1
        else:
            counts["kept"] += 1

    # Targets on the same physical disk share its free space - spreading over them adds nothing.
    devs: dict = {}
    for tid, tgt in targets.items():
        if info[tid]["available"]:
            try:
                key = os.stat(tgt._existing_parent()).st_dev if tgt.kind == "local" else ("rclone", tgt.remote)
                devs.setdefault(key, []).append(tgt.label())
            except OSError:
                pass
    same_disk = [locs for locs in devs.values() if len(locs) > 1]

    copy_bytes = sum(o["size"] for o in ops if o["op"] == "copy")
    unavailable = [i["location"] for i in info.values() if not i["available"]]
    return {"set": {k: s[k] for k in ("id", "name", "layout", "mode", "policy")}, "ops": ops, "counts": counts,
            "skipped": skipped, "bytes": copy_bytes, "files_in_scope": len(files),
            "targets": [{k: v for k, v in info[t].items()} for t in order], "unavailable": unavailable,
            "enough_space": not any(x["reason"].startswith("not enough") for x in skipped), "same_disk": same_disk}


def plan_summary(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "ops"}
    out["examples"] = [{k: o.get(k) for k in ("op", "src", "dest", "from", "to", "target", "reason", "size")}
                       for o in p["ops"] if o["op"] in ("copy", "relocate", "archive")][:300]
    out["skipped"] = p["skipped"][:300]
    return out


# ------------------------------------------------------------------ jobs
class Jobs:
    """One background job at a time (backup or restore) with progress for the UI."""

    def __init__(self, db: Database, scanner=None):
        self.db = db
        self.scanner = scanner
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.state: dict = {"running": False}

    def status(self) -> dict:
        return dict(self.state)

    def cancel(self) -> None:
        self._cancel.set()

    def _start(self, kind: str, fn, *args) -> dict:
        if self._thread and self._thread.is_alive():
            raise BackupError("Another backup or restore is running")
        self._cancel.clear()
        self.state = {"running": True, "kind": kind, "started": time.time(), "finished": None, "files_done": 0,
                      "files_total": 0, "bytes_done": 0, "bytes_total": 0, "current": "", "errors": [], "result": None}

        def run():
            try:
                self.state["result"] = fn(*args)
            except Exception as e:
                self.state["errors"].append(f"{type(e).__name__}: {e}")
            finally:
                self.state["running"] = False
                self.state["finished"] = time.time()
                self.state["cancelled"] = self._cancel.is_set()

        self._thread = threading.Thread(target=run, name=f"kurator-{kind}", daemon=True)
        self._thread.start()
        return self.status()

    def start_backup(self, set_id: int) -> dict:
        return self._start("backup", self._run_backup, set_id)

    def start_restore(self, set_id: int, mode: str, folder: str | None) -> dict:
        return self._start("restore", self._run_restore, set_id, mode, folder)

    # -- backup ------------------------------------------------------------------
    def _err(self, msg: str) -> None:
        if len(self.state["errors"]) < 500:
            self.state["errors"].append(msg)

    def _run_backup(self, set_id: int) -> dict:
        db, c = self.db, self.db.cat
        p = plan(db, set_id)
        s = _set_row(db, set_id)
        targets = {t["id"]: make_target(t) for t in s["targets"]}
        for t in targets.values():
            if t.available():
                t.prefetch()
        ops = p["ops"]
        st = self.state
        st["files_total"] = sum(1 for o in ops if o["op"] == "copy")
        st["bytes_total"] = p["bytes"]
        st["set"] = s["name"]
        files = {f["id"]: f for f in _scope_files(db, s)}
        done = {"copied": 0, "relocated": 0, "relinked": 0, "archived": 0, "failed": 0}

        def upsert(f: dict, target_id: int, dest: str, h: str | None, birth: float | None):
            with db.write_lock:
                c.execute("DELETE FROM backup_files WHERE target_id = ? AND dest = ? COLLATE NOCASE AND NOT (set_id = ? AND file_id = ?)",
                          (target_id, dest, set_id, f["id"]))
                c.execute("INSERT OR REPLACE INTO backup_files(set_id, file_id, target_id, dest, src_path, hash, size, mtime_ns, birth, "
                          "date_ts, date_source, tags, backed_up) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (set_id, f["id"], target_id, dest, f["path"], h, f["size"], f["mtime_ns"], birth,
                           f["date_ts"], f["date_source"], json.dumps(f["tags"], ensure_ascii=False), time.time()))

        for o in ops:  # cheap operations first
            if self._cancel.is_set():
                break
            try:
                if o["op"] == "relink":
                    with db.write_lock:
                        c.execute("UPDATE backup_files SET file_id = ? WHERE set_id = ? AND file_id = ?", (o["id"], set_id, o["old_id"]))
                    done["relinked"] += 1
                elif o["op"] == "relocate":
                    targets[o["target"]].move(o["from"], o["to"])
                    with db.write_lock:
                        c.execute("UPDATE backup_files SET dest = ? WHERE set_id = ? AND file_id = ?", (o["to"], set_id, o["id"]))
                        self._refresh_meta(set_id, files.get(o["id"]))
                    done["relocated"] += 1
                elif o["op"] == "meta":
                    with db.write_lock:
                        self._refresh_meta(set_id, files.get(o["id"]))
                elif o["op"] == "archive":
                    targets[o["target"]].move(o["from"], o["to"])
                    with db.write_lock:
                        c.execute("DELETE FROM backup_files WHERE set_id = ? AND file_id = ?", (set_id, o["id"]))
                    done["archived"] += 1
            except Exception as e:
                done["failed"] += 1
                self._err(f"{o.get('from') or o.get('id')}: {e}")

        lock = threading.Lock()

        def copy_one(o):
            if self._cancel.is_set():
                return
            f = files.get(o["id"])
            st["current"] = o["src"]
            ok = False
            try:
                if f is None or not os.path.isfile(o["src"]):
                    raise TargetError("source file is gone")
                if o.get("archive"):
                    a = o["archive"]
                    try:
                        targets[a["target"]].move(a["from"], a["to"])
                    except TargetError:
                        pass  # old copy already missing
                birth = getattr(os.stat(o["src"]), "st_birthtime", None)
                h = targets[o["target"]].put(o["src"], o["dest"], verify=bool(s["verify"]), birth=birth)
                for side in (o["src"] + ".xmp", os.path.splitext(o["src"])[0] + ".xmp"):
                    if os.path.isfile(side):
                        targets[o["target"]].put_file_plain(side, o["dest"] + ".xmp")
                        break
                f = {**f, "size": os.path.getsize(o["src"]), "mtime_ns": os.stat(o["src"]).st_mtime_ns}
                upsert(f, o["target"], o["dest"], h, birth)
                ok = True
            except Exception as e:
                self._err(f"{o['src']}: {e}")
                if "upload limit" in str(e).lower() or "quota" in str(e).lower():
                    self._err("Cloud upload limit or quota reached - stopping. Run the backup again later to continue.")
                    self._cancel.set()
            with lock:
                done["copied" if ok else "failed"] += 1
                st["files_done"] += 1
                st["bytes_done"] += o["size"]

        copies = [o for o in ops if o["op"] == "copy"]
        # Cloud uploads are latency-bound: run a few in parallel. Local disks: one at a time.
        cloud = any(targets[o["target"]].kind != "local" for o in copies)
        if cloud:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="kurator-upload") as pool:
                list(pool.map(copy_one, copies))
        else:
            for o in copies:
                if self._cancel.is_set():
                    break
                copy_one(o)

        st["current"] = "writing manifest…"
        for tid, tgt in targets.items():
            if tgt.available():
                try:
                    self._write_meta(set_id, s, tgt)
                except Exception as e:
                    self._err(f"manifest on {tgt.label()}: {e}")
        result = {**done, "skipped": len(p["skipped"]), "cancelled": self._cancel.is_set(), "plan": p["counts"]}
        with db.write_lock:
            c.execute("UPDATE backup_sets SET last_run = ?, last_result = ? WHERE id = ?",
                      (time.time(), json.dumps(result), set_id))
        st["current"] = ""
        return result

    def _refresh_meta(self, set_id: int, f: dict | None) -> None:
        """Keep original location, tags and date of a backed-up file current (for restore + manifest)."""
        if not f:
            return
        self.db.cat.execute("UPDATE backup_files SET src_path = ?, tags = ?, date_ts = ?, date_source = ? WHERE set_id = ? AND file_id = ?",
                            (f["path"], json.dumps(f["tags"], ensure_ascii=False), f["date_ts"], f["date_source"], set_id, f["id"]))

    def _write_meta(self, set_id: int, s: dict, tgt) -> None:
        lines = []
        for r in self.db.cat.execute("SELECT * FROM backup_files WHERE set_id = ? AND target_id = ? ORDER BY dest", (set_id, tgt.id)):
            lines.append(json.dumps({
                "file": r["dest"], "original": r["src_path"], "size": r["size"], "blake2b_128": r["hash"],
                "date": datetime.fromtimestamp(r["date_ts"]).astimezone().isoformat(timespec="seconds") if r["date_ts"] else None,
                "date_source": r["date_source"], "tags": json.loads(r["tags"] or "[]"),
                "backed_up": datetime.fromtimestamp(r["backed_up"]).astimezone().isoformat(timespec="seconds") if r["backed_up"] else None,
            }, ensure_ascii=False))
        tgt.put_bytes(("\n".join(lines) + "\n").encode("utf-8"), f"{META_DIR}/manifest.jsonl")
        readme = (
            f"Backup made by Kurator {__version__} - https://github.com/eigensoundlab/kurator\n\n"
            f"Backup set: {s['name']}\nLayout: {'original folders' if s['layout'] == 'original' else 'tag folders (' + s['tag_template'] + ')'}\n"
            f"Updated: {datetime.now().astimezone().isoformat(timespec='seconds')}\n\n"
            "manifest.jsonl  one line per file: path in this backup, original location, creation date and its source,\n"
            "                tags, BLAKE2b-128 checksum. Plain text - readable without Kurator.\n"
            "catalog.db      snapshot of Kurator's catalog (tags, dates, rules) - SQLite.\n"
            f"../{ARCHIVE_DIR}/  older versions of changed files and (mirror mode) files removed from the library.\n"
        )
        tgt.put_bytes(readme.encode("utf-8"), f"{META_DIR}/README.txt")
        with tempfile.TemporaryDirectory() as tmpd:
            snap = os.path.join(tmpd, "catalog.db")
            dst = sqlite3.connect(snap)
            with dst:
                self.db.cat.backup(dst)
            dst.close()
            tgt.put_file_plain(snap, f"{META_DIR}/catalog.db")

    # -- restore -----------------------------------------------------------------
    def _run_restore(self, set_id: int, mode: str, folder: str | None) -> dict:
        p = plan_restore(self.db, set_id, mode, folder)
        s = _set_row(self.db, set_id)
        targets = {t["id"]: make_target(t) for t in s["targets"]}
        st = self.state
        st["files_total"], st["bytes_total"] = len(p["items"]), p["bytes"]
        done = {"restored": 0, "failed": 0}
        for it in p["items"]:
            if self._cancel.is_set():
                break
            st["current"] = it["to"]
            try:
                if os.path.lexists(it["to"]):
                    raise TargetError("a file already exists there - not overwritten")
                targets[it["target"]].get(it["dest"], it["to"], it["mtime_ns"], it["birth"], it["hash"])
                done["restored"] += 1
            except Exception as e:
                done["failed"] += 1
                self._err(f"{it['to']}: {e}")
            st["files_done"] += 1
            st["bytes_done"] += it["size"] or 0
        if self.scanner and done["restored"]:
            self.scanner.request_scan()
        return done


def plan_restore(db: Database, set_id: int, mode: str, folder: str | None) -> dict:
    """mode 'missing': put back files missing from their original place.
    mode 'folder':  copy the whole backup set into a folder, keeping the backup's layout."""
    s = _set_row(db, set_id)
    targets = {t["id"]: make_target(t) for t in s["targets"]}
    if mode == "folder":
        if not folder:
            raise BackupError("Choose a folder to restore into")
        folder = os.path.normpath(os.path.abspath(os.path.expanduser(folder.strip().strip('"'))))
    elif mode != "missing":
        raise BackupError("mode must be 'missing' or 'folder'")
    items, offline = [], set()
    for r in db.cat.execute("SELECT * FROM backup_files WHERE set_id = ? ORDER BY src_path", (set_id,)):
        if mode == "missing":
            if os.path.exists(r["src_path"]):
                continue
            dst = r["src_path"]
        else:
            dst = os.path.join(folder, *r["dest"].split("/"))
            if os.path.exists(dst):
                continue
        t = targets.get(r["target_id"])
        if t is None or not t.available():
            offline.add(t.label() if t else str(r["target_id"]))
            continue
        items.append({"target": r["target_id"], "dest": r["dest"], "to": dst, "size": r["size"], "mtime_ns": r["mtime_ns"],
                      "birth": r["birth"], "hash": r["hash"]})
    total = sum(i["size"] or 0 for i in items)
    space = None
    probe = folder if mode == "folder" else (os.path.dirname(items[0]["to"]) if items else None)
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        probe = None if parent == probe else parent
    if probe:
        import shutil
        space = shutil.disk_usage(probe).free
    return {"items": items, "bytes": total, "count": len(items), "offline_targets": sorted(offline),
            "free": space, "enough_space": space is None or space > total}


def coverage(db: Database) -> dict:
    """How much of the library is backed up (in at least one set, with current content)."""
    roots = {r["id"] for r in db.roots("library")}
    total = n_missing = b_missing = n_outdated = 0
    best: dict[int, tuple] = {}
    for fid, h, size, mt in db.cat.execute("SELECT file_id, hash, size, mtime_ns FROM backup_files"):
        best.setdefault(fid, []).append((h, size, mt))
    for fid, rid, h, size, mt in db.cat.execute("SELECT id, root_id, hash, size, mtime_ns FROM files"):
        if rid not in roots:
            continue
        total += 1
        copies = best.get(fid)
        if not copies:
            n_missing += 1
            b_missing += size or 0
        elif not any((bh == h or not h) and bs == size and bm == mt for bh, bs, bm in copies):
            n_outdated += 1
    return {"files": total, "not_backed_up": n_missing, "not_backed_up_bytes": b_missing, "outdated": n_outdated,
            "sets": db.cat.execute("SELECT COUNT(*) FROM backup_sets").fetchone()[0]}

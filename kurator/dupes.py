"""Cleaning up exact duplicates: one copy of each file stays, the others are deleted (to the system
trash or Kurator's trash folder, see trash.py), and Ctrl+Z brings them back.

Which copy stays: the one with the most tags, the oldest or newest, the one with the shortest path,
the one inside a chosen folder, or the one in the open project. Only files in the scope (the
selection or the Duplicates view) are deleted; the copy that stays may be outside it - the preview
says so. The copy that stays gets the tags of the deleted ones (optional, undone with them).
"""

from __future__ import annotations

import os

from . import projects, trash
from .db import Database
from .fileops import OpError

KEEP = {"tags": "Most tags", "oldest": "Oldest date", "newest": "Newest date", "shortest": "Shortest path",
        "folder": "Inside a folder", "project": "In the open project"}
LIB = "root_id IN (SELECT id FROM roots WHERE kind = 'library')"


def _key(policy: str, r: dict, ntags: int, inside: bool):
    date = r["date_ts"] if r["date_ts"] is not None else float("inf")
    plen = len(r["path"])
    if policy == "tags":
        return (-ntags, date, plen, r["id"])
    if policy == "newest":
        return (-(r["date_ts"] or 0), plen, r["id"])
    if policy == "shortest":
        return (plen, date, r["id"])
    if policy in ("folder", "project"):
        return (not inside, -ntags, date, plen, r["id"])
    return (date, plen, r["id"])                      # oldest


def plan(db: Database, ids: list[int], keep: str = "tags", folder: str | None = None, project: int | None = None) -> dict:
    if keep not in KEEP:
        raise OpError(f"Keep must be one of {', '.join(KEEP)}")
    if keep == "folder":
        if not (folder or "").strip():
            raise OpError("Choose the folder whose copies stay")
        folder = os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(folder.strip().strip('"')))))
    if keep == "project" and not project:
        raise OpError("Open a project first")
    scope = set(int(i) for i in ids)
    if not scope:
        raise OpError("Select duplicates first (or open Duplicates in the sidebar)")
    hashes: set[str] = set()
    sc = list(scope)
    for i in range(0, len(sc), 900):
        chunk = sc[i:i + 900]
        q = ",".join("?" * len(chunk))
        hashes |= {r[0] for r in db.cat.execute(
            f"SELECT hash FROM files WHERE id IN ({q}) AND hash IS NOT NULL AND status = 1", chunk)}
    members = set()
    if keep == "project":
        members = projects.member_ids(db, int(project))
    groups, total, size = [], 0, 0
    hs = sorted(hashes)
    for i in range(0, len(hs), 900):
        chunk = hs[i:i + 900]
        q = ",".join("?" * len(chunk))
        rows: dict[str, list[dict]] = {}
        for r in db.cat.execute(f"SELECT id, hash, path, name, size, date_ts FROM files WHERE hash IN ({q}) AND status = 1 "
                                f"AND {LIB} ORDER BY hash, id", chunk):
            rows.setdefault(r[1], []).append(dict(r))
        ids_all = [r["id"] for g in rows.values() for r in g]
        ntags: dict[int, int] = {}
        for k in range(0, len(ids_all), 900):
            c2 = ids_all[k:k + 900]
            ntags.update(dict(db.cat.execute(
                f"SELECT file_id, COUNT(*) FROM file_tags WHERE file_id IN ({','.join('?' * len(c2))}) GROUP BY file_id", c2).fetchall()))
        for h, g in rows.items():
            if len(g) < 2:
                continue

            def inside(r):
                if keep == "folder":
                    return os.path.normcase(r["path"]).startswith(folder.rstrip("\\/") + os.sep)
                return keep == "project" and r["id"] in members
            g.sort(key=lambda r: _key(keep, r, ntags.get(r["id"], 0), inside(r)))
            keeper = g[0]
            remove = [r for r in g[1:] if r["id"] in scope]
            if not remove:
                continue
            total += len(remove)
            size += sum(r["size"] or 0 for r in remove)
            groups.append({"hash": h, "keep": {"id": keeper["id"], "path": keeper["path"], "in_scope": keeper["id"] in scope,
                                               "tags": ntags.get(keeper["id"], 0)},
                           "remove": [{"id": r["id"], "path": r["path"], "tags": ntags.get(r["id"], 0)} for r in remove]})
    return {"groups": groups, "count": total, "bytes": size, "keep": keep}


def clean(db: Database, scanner, p: dict, merge_tags: bool = True, how: str | None = None) -> dict:
    """Delete what the plan removes; the kept copies get the tags of the removed ones that were
    really deleted (recorded with the delete, so Ctrl+Z takes them off again)."""
    import json
    if not p["count"]:
        raise OpError("No duplicates to remove")
    c = db.cat
    tags_of: dict[int, list] = {}
    if merge_tags:   # read before the rows leave the catalog
        for g in p["groups"]:
            for r in g["remove"]:
                tags_of[r["id"]] = c.execute("SELECT tag_id, source, confidence, added FROM file_tags WHERE file_id = ? "
                                             "ORDER BY added", (r["id"],)).fetchall()
    res = trash.delete_files(db, scanner, [r["id"] for g in p["groups"] for r in g["remove"]], how)
    deleted = set(res["ids"])
    merged: list[list[int]] = []
    if merge_tags and deleted:
        with db.write_lock:
            c.execute("BEGIN")
            try:
                for g in p["groups"]:
                    kid = g["keep"]["id"]
                    if not c.execute("SELECT 1 FROM files WHERE id = ?", (kid,)).fetchone():
                        continue
                    have = {r[0] for r in c.execute("SELECT tag_id FROM file_tags WHERE file_id = ?", (kid,))}
                    for r in g["remove"]:
                        if r["id"] not in deleted:
                            continue
                        for tag_id, source, conf, added in tags_of.get(r["id"], []):
                            if tag_id in have:
                                continue
                            c.execute("INSERT OR IGNORE INTO file_tags(file_id, tag_id, source, confidence, added) VALUES (?, ?, ?, ?, ?)",
                                      (kid, tag_id, source, conf, added))
                            have.add(tag_id)
                            merged.append([kid, tag_id])
                if merged and res["op_id"]:
                    data = json.loads(c.execute("SELECT data FROM operations WHERE id = ?", (res["op_id"],)).fetchone()[0])
                    data["merged"] = merged
                    c.execute("UPDATE operations SET data = ? WHERE id = ?", (json.dumps(data), res["op_id"]))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
    res["merged"] = len(merged)
    res["kept_ids"] = sorted({kid for kid, _ in merged})
    return res

"""Projects: tags in the "project" group that can also *include things automatically*.

A file belongs to a project when
  * it has the project's tag (added by hand, by drag & drop, with a hotkey...), or
  * it matches one of the project's sources, which are live:
      - a tag   ("everything tagged Creatures" - also sub-tags)
      - a folder ("everything in D:\\Game A\\Concepts", with subfolders)
      - another project ("Game A" includes "Game A - DLC")

Sources keep projects up to date without re-tagging: a new file in the folder, or a newly tagged
creature, is in the project at once. Sub-tags of a project (e.g. "Game A/Characters") are part of it.
"""

from __future__ import annotations

import os
import time

from . import tags
from .db import Database

KINDS = ("tag", "folder", "project")


class ProjectError(ValueError):
    pass


def is_project(db: Database, tag_id: int) -> bool:
    row = db.cat.execute("SELECT facet FROM tags WHERE id = ?", (tag_id,)).fetchone()
    return bool(row and row[0] == "project")


def sources(db: Database, tag_id: int) -> list[dict]:
    out = []
    for r in db.cat.execute("SELECT kind, ref FROM project_sources WHERE tag_id = ? ORDER BY id", (tag_id,)):
        s = {"kind": r[0], "ref": r[1]}
        if r[0] in ("tag", "project"):
            t = db.cat.execute("SELECT name, color, facet FROM tags WHERE id = ?", (int(r[1]),)).fetchone()
            if not t:
                continue   # the tag was deleted
            s.update(ref=int(r[1]), label=tags.display_name(t[0], t[2]), color=t[1])
        else:
            s["label"] = r[1]
        out.append(s)
    return out


def set_sources(db: Database, tag_id: int, items: list[dict]) -> list[dict]:
    if not is_project(db, tag_id):
        raise ProjectError("Only projects can include tags, folders or other projects")
    clean = []
    for it in items:
        kind = it.get("kind")
        if kind not in KINDS:
            raise ProjectError(f"unknown source kind {kind!r}")
        ref = it.get("ref")
        if kind == "folder":
            ref = os.path.normpath(os.path.abspath(os.path.expanduser(str(ref or ""))))
            if not ref or ref == os.sep:
                raise ProjectError("Choose a folder")
        else:
            ref = int(ref)
            if ref == tag_id:
                raise ProjectError("A project can't include itself")
            row = db.cat.execute("SELECT facet FROM tags WHERE id = ?", (ref,)).fetchone()
            if not row:
                raise ProjectError("That tag doesn't exist any more")
            if kind == "project" and row[0] != "project":
                raise ProjectError("Not a project")
            if kind == "project" and tag_id in _included_projects(db, ref):
                raise ProjectError("That project already includes this one (it would go round in a circle)")
        if (kind, str(ref)) not in [(k, str(r)) for k, r in clean]:
            clean.append((kind, ref))
    with db.write_lock:
        c = db.cat
        c.execute("BEGIN")
        c.execute("DELETE FROM project_sources WHERE tag_id = ?", (tag_id,))
        c.executemany("INSERT INTO project_sources(tag_id, kind, ref, created) VALUES (?, ?, ?, ?)",
                      [(tag_id, k, str(r), time.time()) for k, r in clean])
        c.execute("COMMIT")
    return sources(db, tag_id)


def hotkeys(db: Database, tag_id: int) -> dict[str, int]:
    """The project's own hotkeys {"1": tag id, ...}: while it is open they replace the global
    hotkeys of the same number; the other numbers keep their global tags."""
    return {str(k): t for k, t in db.cat.execute(
        "SELECT hotkey, tag_id FROM project_hotkeys WHERE project_id = ? ORDER BY hotkey", (tag_id,))}


def set_hotkeys(db: Database, tag_id: int, keys: dict) -> dict[str, int]:
    """keys: {"1": tag id or None, ...} - the whole set (keys not given are cleared)."""
    if not is_project(db, tag_id):
        raise ProjectError("Only projects have their own hotkeys")
    clean: dict[int, int] = {}
    for k, t in (keys or {}).items():
        try:
            k = int(k)
        except (TypeError, ValueError):
            raise ProjectError(f"Hotkey must be 1-9, not {k!r}") from None
        if not 1 <= k <= 9:
            raise ProjectError("Hotkey must be 1-9")
        if t in (None, "", 0):
            continue
        t = int(t)
        if not db.cat.execute("SELECT 1 FROM tags WHERE id = ?", (t,)).fetchone():
            raise ProjectError("That tag doesn't exist any more")
        for other in [o for o, v in clean.items() if v == t]:   # one key per tag
            del clean[other]
        clean[k] = t
    with db.write_lock:
        c = db.cat
        c.execute("BEGIN")
        c.execute("DELETE FROM project_hotkeys WHERE project_id = ?", (tag_id,))
        c.executemany("INSERT INTO project_hotkeys(project_id, hotkey, tag_id) VALUES (?, ?, ?)",
                      [(tag_id, k, t) for k, t in sorted(clean.items())])
        c.execute("COMMIT")
    return hotkeys(db, tag_id)


def _included_projects(db: Database, tag_id: int, seen: set | None = None) -> set[int]:
    """Projects reachable through 'project' sources (for cycle checks)."""
    seen = set() if seen is None else seen
    for (ref,) in db.cat.execute("SELECT ref FROM project_sources WHERE tag_id = ? AND kind = 'project'", (tag_id,)):
        p = int(ref)
        if p not in seen:
            seen.add(p)
            _included_projects(db, p, seen)
    return seen


def _folder_clause(path: str) -> tuple[str, list]:
    p = path.rstrip("\\/")
    return "(path LIKE ? ESCAPE '!')", [_like(p + os.sep) + "%"]


def _like(s: str) -> str:
    return s.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def member_clause(db: Database, tag_id: int, _seen: set | None = None) -> tuple[str, list]:
    """SQL condition on `files` (columns id, path) for "is in this tag/project", sources included."""
    seen = set() if _seen is None else _seen
    seen.add(tag_id)
    ids = tags.descendant_ids(db, tag_id)
    if not ids:
        return "0", []
    parts = [f"id IN (SELECT file_id FROM file_tags WHERE tag_id IN ({','.join('?' * len(ids))}))"]
    args: list = list(ids)
    srcs = db.cat.execute(f"SELECT kind, ref FROM project_sources WHERE tag_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    for kind, ref in srcs:
        if kind == "tag":
            tids = tags.descendant_ids(db, int(ref))
            if tids:
                parts.append(f"id IN (SELECT file_id FROM file_tags WHERE tag_id IN ({','.join('?' * len(tids))}))")
                args += tids
        elif kind == "folder":
            sql, a = _folder_clause(ref)
            parts.append(sql)
            args += a
        elif kind == "project" and int(ref) not in seen:
            sql, a = member_clause(db, int(ref), seen)
            parts.append(sql)
            args += a
    return "(" + " OR ".join(parts) + ")", args


def any_project_clause(db: Database) -> tuple[str, list]:
    """Files that are in at least one project (for "No project")."""
    tops = [r[0] for r in db.cat.execute("SELECT id FROM tags WHERE facet = 'project' AND instr(name, '/') = 0")]
    if not tops:
        return "0", []
    parts, args = [], []
    for t in tops:
        s, a = member_clause(db, t)
        parts.append(s)
        args += a
    return "(" + " OR ".join(parts) + ")", args


def has_sources(db: Database) -> bool:
    return db.cat.execute("SELECT 1 FROM project_sources LIMIT 1").fetchone() is not None


def member_ids(db: Database, tag_id: int) -> set[int]:
    sql, args = member_clause(db, tag_id)
    return {r[0] for r in db.cat.execute(f"SELECT id FROM files WHERE {sql}", args)}


def live_projects_by_file(db: Database) -> dict[int, list[str]]:
    """Project names a file gets only through sources (not tagged by hand) - used where projects
    matter outside the grid: {project} in move rules and backup folders, backup scopes."""
    out: dict[int, list[str]] = {}
    if not has_sources(db):
        return out
    names = {r[0]: r[1] for r in db.cat.execute("SELECT id, name FROM tags WHERE facet = 'project'")}
    explicit: dict[int, set[int]] = {}
    for fid, tid in db.cat.execute("SELECT ft.file_id, ft.tag_id FROM file_tags ft JOIN tags t ON t.id = ft.tag_id WHERE t.facet = 'project'"):
        explicit.setdefault(tid, set()).add(fid)
    for pid in {r[0] for r in db.cat.execute("SELECT DISTINCT tag_id FROM project_sources")}:
        if pid not in names:
            continue
        mine = explicit.get(pid, set())
        for fid in member_ids(db, pid):
            if fid not in mine:
                out.setdefault(fid, []).append(names[pid])
    return out


def overview(db: Database) -> list[dict]:
    """Every project with its sources and counts (for the project switcher and editor)."""
    rows = db.cat.execute("SELECT id, name, color, hotkey, archived FROM tags WHERE facet = 'project' ORDER BY name COLLATE NOCASE").fetchall()
    out = []
    lib = "root_id IN (SELECT id FROM roots WHERE kind = 'library')"
    for r in rows:
        sql, args = member_clause(db, r[0])
        total = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE {sql} AND {lib}", args).fetchone()[0]
        by_hand = db.cat.execute("SELECT COUNT(*) FROM file_tags WHERE tag_id = ?", (r[0],)).fetchone()[0]
        srcs = sources(db, r[0])
        for s in srcs:
            if s["kind"] == "folder":
                q, a = _folder_clause(s["ref"])
            else:
                q, a = member_clause(db, s["ref"])
            s["count"] = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE {q} AND {lib}", a).fetchone()[0]
        out.append({"id": r[0], "name": r[1], "color": r[2], "hotkey": r[3], "archived": bool(r[4]),
                    "count": total, "by_hand": by_hand, "sources": srcs, "hotkeys": hotkeys(db, r[0])})
    return out

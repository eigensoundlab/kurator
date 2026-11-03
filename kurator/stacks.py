"""Version stacks: several files that are versions of one picture (sketch -> lineart -> colour,
v01 -> v02 -> final) shown as one item in the grid, the newest on top.

Stacks only group catalog entries; files stay where they are. "Stack by name" finds versions among
the selection by their names (hero_sketch, hero_lineart, hero_v2, hero (1), hero copy...).
"""

from __future__ import annotations

import os
import re
import time

from .db import Database

_VERSION = re.compile(
    r"([ _.\-]*(v|ver|version|rev|r)?[ _.\-]*\d{1,3}"                 # _v01 _2 -r3
    r"|[ _.\-]*\(\d+\)|[ _.\-]*copy( \d+)?|[ _.\-]*kopie"              # (1)  copy  copy 2
    r"|[ _.\-]+(final|finished|done|wip|draft|sketch|sketches|rough|thumb|thumbnail|lineart|line|lines|ink|inks|inked"
    r"|flat|flats|color|colour|colored|coloured|paint|painted|render|shaded|shading|clean|cleanup|edit|edited|old|new|alt))+$",
    re.I)


class StackError(ValueError):
    pass


def version_key(name: str) -> str:
    stem = os.path.splitext(name)[0]
    base = _VERSION.sub("", stem).strip(" _.-")
    return (base or stem).lower()


def _refresh_cover(db: Database, stack_id: int) -> None:
    row = db.cat.execute("SELECT cover_id FROM stacks WHERE id = ?", (stack_id,)).fetchone()
    members = db.cat.execute("SELECT id FROM files WHERE stack_id = ? ORDER BY date_ts DESC, id DESC", (stack_id,)).fetchall()
    if len(members) < 2:   # a stack of one is no stack
        db.cat.execute("UPDATE files SET stack_id = NULL WHERE stack_id = ?", (stack_id,))
        db.cat.execute("DELETE FROM stacks WHERE id = ?", (stack_id,))
        return
    if not row or row[0] not in {m[0] for m in members}:
        db.cat.execute("UPDATE stacks SET cover_id = ? WHERE id = ?", (members[0][0], stack_id))


def make(db: Database, ids: list[int]) -> int:
    """One stack from these files (taken out of any other stack). The newest is shown on top."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    if len(ids) < 2:
        raise StackError("Select two or more versions to stack")
    q = ",".join("?" * len(ids))
    with db.write_lock:
        c = db.cat
        c.execute("BEGIN")
        old = {r[0] for r in c.execute(f"SELECT DISTINCT stack_id FROM files WHERE id IN ({q}) AND stack_id IS NOT NULL", ids)}
        sid = c.execute("INSERT INTO stacks(cover_id, created) VALUES (NULL, ?)", (time.time(),)).lastrowid
        c.execute(f"UPDATE files SET stack_id = ? WHERE id IN ({q})", (sid, *ids))
        for o in old:
            _refresh_cover(db, o)
        _refresh_cover(db, sid)
        c.execute("COMMIT")
    return sid


def by_name(db: Database, ids: list[int]) -> list[int]:
    """Stack the selection's versions by name, per folder. Returns the new stack ids."""
    groups: dict[tuple, list[int]] = {}
    for i in ids:
        r = db.cat.execute("SELECT dir, name FROM files WHERE id = ?", (int(i),)).fetchone()
        if r:
            groups.setdefault((r[0], version_key(r[1])), []).append(int(i))
    return [make(db, g) for g in groups.values() if len(g) > 1]


def unstack(db: Database, ids: list[int]) -> int:
    """Dissolve the stacks these files are in."""
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    q = ",".join("?" * len(ids))
    with db.write_lock:
        c = db.cat
        sids = [r[0] for r in c.execute(f"SELECT DISTINCT stack_id FROM files WHERE id IN ({q}) AND stack_id IS NOT NULL", ids)]
        for s in sids:
            c.execute("UPDATE files SET stack_id = NULL WHERE stack_id = ?", (s,))
            c.execute("DELETE FROM stacks WHERE id = ?", (s,))
    return len(sids)


def set_cover(db: Database, file_id: int) -> None:
    r = db.cat.execute("SELECT stack_id FROM files WHERE id = ?", (file_id,)).fetchone()
    if not r or r[0] is None:
        raise StackError("This file isn't in a stack")
    with db.write_lock:
        db.cat.execute("UPDATE stacks SET cover_id = ? WHERE id = ?", (file_id, r[0]))


def info(db: Database, file_id: int) -> dict | None:
    r = db.cat.execute("SELECT stack_id FROM files WHERE id = ?", (file_id,)).fetchone()
    if not r or r[0] is None:
        return None
    cover = db.cat.execute("SELECT cover_id FROM stacks WHERE id = ?", (r[0],)).fetchone()
    members = [{"id": m[0], "name": m[1], "date_ts": m[2]} for m in db.cat.execute(
        "SELECT id, name, date_ts FROM files WHERE stack_id = ? ORDER BY date_ts, id", (r[0],))]
    return {"id": r[0], "cover": cover[0] if cover else None, "members": members}


def collapse(db: Database, rows: list, stack_col: int) -> tuple[list, dict[int, int]]:
    """Keep one row per stack (its cover if it matches the filter, else the first match), in place of
    the first member. Returns (rows, {shown id: stack size})."""
    sizes = dict(db.cat.execute("SELECT stack_id, COUNT(*) FROM files WHERE stack_id IS NOT NULL GROUP BY stack_id").fetchall())
    if not sizes:
        return rows, {}
    covers = {r[0]: r[1] for r in db.cat.execute("SELECT id, cover_id FROM stacks")}
    pick: dict[int, tuple] = {}
    for r in rows:
        s = r[stack_col]
        if s is not None and (s not in pick or r[0] == covers.get(s)):
            pick[s] = r
    out, placed = [], set()
    for r in rows:
        s = r[stack_col]
        if s is None:
            out.append(r)
        elif s not in placed:
            placed.add(s)
            out.append(pick[s])
    return out, {pick[s][0]: sizes.get(s, 1) for s in placed}

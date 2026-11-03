"""Tags: hierarchical names ("Creatures/Dragons"), colours, hotkeys 1-9.

Tags belong to catalog files (by id), so they survive edits of the file content. When a file
disappears from its place (moved/renamed outside Kurator) its tags are parked by content hash
and re-attached when the same content shows up again (see Database.relink_orphans).
"""

from __future__ import annotations

import re
import time

from .db import TAG_COLORS, Database

MAX_NAME = 100

# Tag groups. 'single': an image has at most one tag of the group (setting one replaces the other).
# 'xmp': root keyword used in sidecars, so other programs see e.g. "Projects|Game A".
FACETS = [
    {"key": "subject", "label": "Subjects", "single": False, "xmp": None, "none": "Untagged"},
    {"key": "project", "label": "Projects", "single": False, "xmp": "Projects", "none": "No project"},
    {"key": "status", "label": "Status", "single": True, "xmp": "Status", "none": "No status"},
    {"key": "source", "label": "Source", "single": True, "xmp": "Source", "none": "No source"},
]
FACET = {f["key"]: f for f in FACETS}
XMP_ROOTS = {f["xmp"].lower(): f["key"] for f in FACETS if f["xmp"]}


def check_facet(facet: str) -> str:
    if facet not in FACET:
        raise TagError(f"Unknown tag group {facet!r}")
    return facet


class TagError(ValueError):
    pass


def normalize_name(name: str) -> str:
    parts = [re.sub(r"\s+", " ", p).strip() for p in str(name).replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    out = "/".join(parts)
    if not out:
        raise TagError("Tag name is empty")
    if len(out) > MAX_NAME:
        raise TagError(f"Tag name longer than {MAX_NAME} characters")
    return out


def list_tags(db: Database) -> list[dict]:
    rows = db.cat.execute(
        "SELECT t.id, t.name, t.color, t.hotkey, t.facet, t.archived, t.ai_prompt, COUNT(ft.file_id) AS count FROM tags t "
        "LEFT JOIN file_tags ft ON ft.tag_id = t.id GROUP BY t.id ORDER BY t.name COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]


def _free_hotkey(c) -> int | None:
    used = {r[0] for r in c.execute("SELECT hotkey FROM tags WHERE hotkey IS NOT NULL")}
    return next((k for k in range(1, 10) if k not in used), None)


def create_tag(db: Database, name: str, color: str | None = None, hotkey="auto", facet: str | None = None) -> dict:
    name = normalize_name(name)
    c = db.cat
    with db.write_lock:
        row = c.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            return get_tag(db, row[0])
        # Children always belong to their parent's group.
        parts = name.split("/")
        top = c.execute("SELECT facet FROM tags WHERE name = ?", (parts[0],)).fetchone()
        if top and facet and top[0] != facet:
            raise TagError(f'"{parts[0]}" is in {FACET[top[0]]["label"]} - sub-tags stay in the same group')
        facet = check_facet(top[0] if top else (facet or "subject"))
        # Create missing parents too, so the hierarchy is always complete.
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if not c.execute("SELECT 1 FROM tags WHERE name = ?", (parent,)).fetchone():
                n = c.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
                c.execute("INSERT INTO tags(name, color, hotkey, created, facet) VALUES (?, ?, NULL, ?, ?)",
                          (parent, TAG_COLORS[n % len(TAG_COLORS)], time.time(), facet))
        n = c.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        if color is None:
            color = TAG_COLORS[n % len(TAG_COLORS)]
        if hotkey == "auto":
            hotkey = _free_hotkey(c)
        elif hotkey is not None:
            hotkey = int(hotkey)
            if not 1 <= hotkey <= 9:
                raise TagError("Hotkey must be 1-9")
            c.execute("UPDATE tags SET hotkey = NULL WHERE hotkey = ?", (hotkey,))  # take it over
        tid = c.execute("INSERT INTO tags(name, color, hotkey, created, facet) VALUES (?, ?, ?, ?, ?)",
                        (name, _color(color), hotkey, time.time(), facet)).lastrowid
    return get_tag(db, tid)


def _color(color: str | None) -> str | None:
    if color is None:
        return None
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise TagError("Colour must look like #a1b2c3")
    return color.lower()


def get_tag(db: Database, tag_id: int) -> dict:
    row = db.cat.execute(
        "SELECT t.id, t.name, t.color, t.hotkey, t.facet, t.archived, t.ai_prompt, (SELECT COUNT(*) FROM file_tags WHERE tag_id = t.id) AS count "
        "FROM tags t WHERE t.id = ?", (tag_id,)).fetchone()
    if not row:
        raise TagError("No such tag")
    return dict(row)


def update_tag(db: Database, tag_id: int, name: str | None = None, color: str | None = None,
               hotkey: int | None | str = "keep", facet: str | None = None, archived: bool | None = None) -> dict:
    c = db.cat
    old = get_tag(db, tag_id)
    with db.write_lock:
        c.execute("BEGIN")
        try:
            if name is not None:
                new = normalize_name(name)
                if new.lower() != old["name"].lower():
                    if c.execute("SELECT 1 FROM tags WHERE name = ? AND id != ?", (new, tag_id)).fetchone():
                        raise TagError(f'A tag named "{new}" already exists')
                    # Rename children: Creatures/Dragons -> Monsters/Dragons
                    prefix = old["name"] + "/"
                    for cid, cname in c.execute("SELECT id, name FROM tags").fetchall():
                        if cname.lower().startswith(prefix.lower()):
                            c.execute("UPDATE tags SET name = ? WHERE id = ?", (new + "/" + cname[len(prefix):], cid))
                c.execute("UPDATE tags SET name = ? WHERE id = ?", (new, tag_id))
            if color is not None:
                c.execute("UPDATE tags SET color = ? WHERE id = ?", (_color(color), tag_id))
            if facet is not None and facet != old["facet"]:
                check_facet(facet)
                cur = c.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()[0]
                if "/" in cur:
                    raise TagError("Move the top-level tag to another group - sub-tags follow it")
                pre = cur.lower() + "/"
                for cid, cname in c.execute("SELECT id, name FROM tags").fetchall():
                    if cid == tag_id or cname.lower().startswith(pre):
                        c.execute("UPDATE tags SET facet = ? WHERE id = ?", (facet, cid))
            if archived is not None:
                c.execute("UPDATE tags SET archived = ? WHERE id = ?", (int(bool(archived)), tag_id))
            if hotkey != "keep":
                if hotkey is not None:
                    hotkey = int(hotkey)
                    if not 1 <= hotkey <= 9:
                        raise TagError("Hotkey must be 1-9")
                    c.execute("UPDATE tags SET hotkey = NULL WHERE hotkey = ? AND id != ?", (hotkey, tag_id))
                c.execute("UPDATE tags SET hotkey = ? WHERE id = ?", (hotkey, tag_id))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return get_tag(db, tag_id)


def delete_tag(db: Database, tag_id: int) -> None:
    """Deletes the tag and its children (only the labels - never files)."""
    t = get_tag(db, tag_id)
    ids = descendant_ids(db, tag_id)
    with db.write_lock:
        q = ",".join("?" * len(ids))
        db.cat.execute(f"DELETE FROM tags WHERE id IN ({q})", ids)
    return t


def descendant_ids(db: Database, tag_id: int) -> list[int]:
    """The tag itself plus every tag below it in the hierarchy."""
    row = db.cat.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if not row:
        return []
    prefix = row[0].lower() + "/"
    return [tag_id] + [r[0] for r in db.cat.execute("SELECT id, name FROM tags")
                       if r[1].lower().startswith(prefix)]


def apply(db: Database, file_ids: list[int], tag_id: int, op: str = "toggle",
          source: str = "manual", confidence: float | None = None) -> dict:
    """op: 'add' | 'remove' | 'toggle' (remove if every file has it, else add to the rest).
    Returns exactly what changed, so the client can undo it."""
    get_tag(db, tag_id)
    file_ids = list(dict.fromkeys(int(i) for i in file_ids))
    c = db.cat
    have: set[int] = set()
    for i in range(0, len(file_ids), 900):
        chunk = file_ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        have.update(r[0] for r in c.execute(
            f"SELECT file_id FROM file_tags WHERE tag_id = ? AND file_id IN ({q})", (tag_id, *chunk)))
    if op == "toggle":
        op = "remove" if file_ids and len(have) == len(file_ids) else "add"
    now = time.time()
    tag = get_tag(db, tag_id)
    also: list[dict] = []
    with db.write_lock:
        c.execute("BEGIN")
        if op == "add":
            changed = [i for i in file_ids if i not in have]
            c.executemany("INSERT OR IGNORE INTO file_tags(file_id, tag_id, source, confidence, added) "
                          "VALUES (?, ?, ?, ?, ?)", [(i, tag_id, source, confidence, now) for i in changed])
            if FACET[tag["facet"]]["single"] and file_ids:
                # one status / one source per image: setting "final" replaces "WIP"
                others = [r[0] for r in c.execute("SELECT id FROM tags WHERE facet = ? AND id != ?", (tag["facet"], tag_id))]
                for oid in others:
                    hit = []
                    for i in range(0, len(file_ids), 900):
                        chunk = file_ids[i:i + 900]
                        q = ",".join("?" * len(chunk))
                        hit += [r[0] for r in c.execute(
                            f"SELECT file_id FROM file_tags WHERE tag_id = ? AND file_id IN ({q})", (oid, *chunk))]
                    if hit:
                        c.executemany("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ?", [(i, oid) for i in hit])
                        also.append({"tag_id": oid, "ids": hit})
            res = {"added": changed, "removed": []}
        elif op == "remove":
            changed = [i for i in file_ids if i in have]
            c.executemany("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ?", [(i, tag_id) for i in changed])
            res = {"added": [], "removed": changed}
        else:
            c.execute("ROLLBACK")
            raise TagError("op must be add, remove or toggle")
        c.execute("COMMIT")
    res["tag"] = get_tag(db, tag_id)
    res["also_removed"] = also
    return res


def facet_of_names(db: Database) -> dict[str, str]:
    return {r[0]: r[1] for r in db.cat.execute("SELECT name, facet FROM tags")}


def display_name(name: str, facet: str) -> str:
    """'Game A' in Projects -> 'Projects/Game A' (for sidecars, manifests); subjects stay as they are."""
    root = FACET.get(facet, {}).get("xmp")
    return f"{root}/{name}" if root else name


def tags_by_file(db: Database) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for fid, tid in db.cat.execute("SELECT file_id, tag_id FROM file_tags"):
        out.setdefault(fid, []).append(tid)
    return out

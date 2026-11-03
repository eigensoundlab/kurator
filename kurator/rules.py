"""Move rules: "everything tagged Creatures goes to D:/Art/Creatures/{year}".

Rules are checked in order; the first rule whose tag (or a sub-tag of it) the file carries wins.
Destination templates may use:
    {year} {month} {day}   from the file's resolved date
    {tag}                  the rule's tag (last part of its name)
    {subtag}               the file's tag directly below the rule's tag (e.g. Dragons for
                           Creatures/Dragons); empty if the file has only the rule's tag
    {project} {status} {source}   the file's tag in that group (first A-Z), empty if none
Empty parts are dropped, so "Creatures/{subtag}" puts untyped creatures straight into Creatures.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from .db import Database

TOKENS = {"year", "month", "day", "tag", "subtag", "project", "status", "source"}
_TOKEN_RE = re.compile(r"\{(\w+)\}")
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


class RuleError(ValueError):
    pass


def get_rules(db: Database) -> list[dict]:
    return db.get_setting("rules", [])


def set_rules(db: Database, rules: list[dict]) -> list[dict]:
    tags = {r[0]: r[1] for r in db.cat.execute("SELECT id, name FROM tags")}
    clean = []
    for i, r in enumerate(rules):
        tid = int(r.get("tag_id") or 0)
        if tid not in tags:
            raise RuleError(f"Rule {i + 1}: choose a tag")
        dest = str(r.get("dest") or "").strip().strip('"')
        if not dest:
            raise RuleError(f"Rule {i + 1}: destination is empty")
        unknown = set(_TOKEN_RE.findall(dest)) - TOKENS
        if unknown:
            raise RuleError(f"Rule {i + 1}: unknown {{{', '.join(sorted(unknown))}}} - use {', '.join('{' + t + '}' for t in sorted(TOKENS))}")
        clean.append({"tag_id": tid, "dest": dest, "date_prefix": bool(r.get("date_prefix"))})
    db.set_setting("rules", clean)
    return clean


def _safe(part: str) -> str:
    return _BAD_CHARS.sub("_", part).strip(" .")


def render(dest: str, ts: float | None, rule_tag: str, file_tags: list[str], groups: dict | None = None) -> str:
    d = datetime.fromtimestamp(ts) if ts else None
    sub = ""
    prefix = rule_tag.lower() + "/"
    for t in sorted(file_tags):
        if t.lower().startswith(prefix):
            sub = t[len(prefix):].split("/")[0]
            break
    values = {
        "year": f"{d:%Y}" if d else "undated", "month": f"{d:%m}" if d else "", "day": f"{d:%d}" if d else "",
        "tag": rule_tag.split("/")[-1], "subtag": sub,
    }
    for g in ("project", "status", "source"):
        vals = sorted((groups or {}).get(g, []), key=str.lower)
        values[g] = vals[0] if vals else ""
    out = _TOKEN_RE.sub(lambda m: _safe(values.get(m.group(1), "")), dest)
    # drop empty path parts produced by empty tokens, keep drive / UNC / root prefixes intact
    drive, rest = os.path.splitdrive(os.path.expanduser(out))
    lead = rest[:1] if rest[:1] in ("/", "\\") else ""
    parts = [p for p in re.split(r"[\\/]+", rest) if p]
    return os.path.normpath(drive + lead + os.sep.join(parts))


def plan_entries(db: Database, ids: list[int]) -> tuple[list[dict], list[dict]]:
    """Map files to (destination folder, rule). Returns (entries, skipped)."""
    rules = get_rules(db)
    if not rules:
        raise RuleError("No rules yet - add one first")
    names = {r[0]: r[1] for r in db.cat.execute("SELECT id, name FROM tags")}
    facet = {r[0]: r[1] for r in db.cat.execute("SELECT id, facet FROM tags")}
    ids = list(dict.fromkeys(int(i) for i in ids))
    file_tags: dict[int, list[str]] = {}
    groups: dict[int, dict] = {}
    dates: dict[int, float] = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        for fid, tid in db.cat.execute(f"SELECT file_id, tag_id FROM file_tags WHERE file_id IN ({q})", chunk):
            file_tags.setdefault(fid, []).append(names[tid])
            groups.setdefault(fid, {}).setdefault(facet[tid], []).append(names[tid])
        for fid, ts in db.cat.execute(f"SELECT id, date_ts FROM files WHERE id IN ({q})", chunk):
            dates[fid] = ts
    from .projects import live_projects_by_file   # projects that include files automatically
    wanted = set(ids)
    for fid, pnames in live_projects_by_file(db).items():
        if fid in wanted:
            file_tags.setdefault(fid, []).extend(pnames)
            groups.setdefault(fid, {}).setdefault("project", []).extend(pnames)
    entries, skipped = [], []
    for fid in ids:
        tags_here = file_tags.get(fid, [])
        lowered = [t.lower() for t in tags_here]
        hit = None
        for n, rule in enumerate(rules):
            rt = names.get(rule["tag_id"])
            if rt is None:
                continue
            rl = rt.lower()
            if any(t == rl or t.startswith(rl + "/") for t in lowered):
                hit = (n, rule, rt)
                break
        if hit is None:
            skipped.append({"id": fid, "reason": "no matching rule"})
            continue
        n, rule, rt = hit
        entries.append({"id": fid, "dir": render(rule["dest"], dates.get(fid), rt, tags_here, groups.get(fid)),
                        "date_prefix": rule["date_prefix"], "rule": n})
    return entries, skipped

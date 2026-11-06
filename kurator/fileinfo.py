"""Where a file came from, who made it, and under which licence.

Three free-text fields per file, kept in the catalog, written into XMP sidecars (dc:source,
dc:creator, dc:rights and xmpRights:WebStatement for known licences), searchable
(source:artstation, author:jane, licence:cc-by, licence:none), and listed in exports and
training-dataset manifests. They follow a file that is moved or renamed outside Kurator.
"""

from __future__ import annotations

import time

from .db import Database

FIELDS = ("source", "author", "licence")
MAX_LEN = 1000

# Common licences: name -> URL of the licence text (None: no standard URL)
LICENCES = {
    "CC0 1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC BY-SA 4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC BY-NC 4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "CC BY-NC-SA 4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "CC BY-ND 4.0": "https://creativecommons.org/licenses/by-nd/4.0/",
    "CC BY-NC-ND 4.0": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    "Public domain": "https://creativecommons.org/publicdomain/mark/1.0/",
    "Own work": None,
    "All rights reserved": None,
    "Royalty-free (bought)": None,
    "Editorial use only": None,
    "Unknown": None,
}


def licence_url(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip()
    if t.lower().startswith(("http://", "https://")):
        return t
    for name, url in LICENCES.items():
        if url and t.lower() == name.lower():
            return url
    return None


def get(db: Database, file_id: int) -> dict:
    row = db.cat.execute("SELECT source, author, licence, updated FROM file_info WHERE file_id = ?", (file_id,)).fetchone()
    out = {k: (row[i] if row else None) or "" for i, k in enumerate(FIELDS)}
    out["licence_url"] = licence_url(out["licence"])
    out["updated"] = row[3] if row else None
    return out


def get_many(db: Database, ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        q = ",".join("?" * len(part))
        for fid, src, au, lic in db.cat.execute(
                f"SELECT file_id, source, author, licence FROM file_info WHERE file_id IN ({q})", part):
            out[fid] = {"source": src or "", "author": au or "", "licence": lic or ""}
    return out


def set_many(db: Database, ids: list[int], patch: dict) -> dict:
    """Set fields on many files. Fields not in `patch` (or None) stay; "" clears a field."""
    change = {k: str(v).strip()[:MAX_LEN] for k, v in patch.items() if k in FIELDS and v is not None}
    if not change:
        return {"changed": 0}
    ids = list(dict.fromkeys(int(i) for i in ids))
    now = time.time()
    with db.write_lock:
        c = db.cat
        c.execute("BEGIN")
        try:
            for i in range(0, len(ids), 900):
                part = ids[i:i + 900]
                q = ",".join("?" * len(part))
                exist = {r[0] for r in c.execute(f"SELECT id FROM files WHERE id IN ({q})", part)}
                c.executemany("INSERT OR IGNORE INTO file_info(file_id, updated) VALUES (?, ?)", [(f, now) for f in exist])
                sets = ", ".join(f"{k} = ?" for k in change)
                c.execute(f"UPDATE file_info SET {sets}, updated = ? WHERE file_id IN ({q})",
                          [*change.values(), now, *part])
            c.execute("DELETE FROM file_info WHERE COALESCE(source, '') = '' AND COALESCE(author, '') = '' "
                      "AND COALESCE(licence, '') = ''")
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return {"changed": len(ids), "fields": sorted(change)}


def search_clause(term: str) -> tuple[str, list] | None:
    """`source:x`, `author:x`, `licence:x` (or license:), `licence:none` - None for a plain word."""
    key, sep, val = term.partition(":")
    key = {"license": "licence"}.get(key.lower(), key.lower())
    if not sep or key not in FIELDS:
        return None
    if val.lower() in ("none", "-", ""):
        return (f"id NOT IN (SELECT file_id FROM file_info WHERE COALESCE({key}, '') != '')", [])
    esc = val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return (f"id IN (SELECT file_id FROM file_info WHERE {key} LIKE ? ESCAPE '\\')", [f"%{esc}%"])


def used_values(db: Database, field: str, limit: int = 50) -> list[str]:
    """The values used most, for suggestions while typing."""
    if field not in FIELDS:
        return []
    return [r[0] for r in db.cat.execute(
        f"SELECT {field}, COUNT(*) n FROM file_info WHERE COALESCE({field}, '') != '' GROUP BY {field} "
        "ORDER BY n DESC LIMIT ?", (limit,))]

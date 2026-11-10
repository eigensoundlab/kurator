"""SFX Workbench link: send sounds to SFX Workbench (a web app) and take its results back.

Kurator makes a link for the chosen sounds: a random token with which SFX Workbench - and only
pages from its own web address - can read those sounds, and save new sounds into one folder of
the library, tagged "SFX Workbench" (Source group) plus the tags chosen when the link was made,
with the files they were made from as their source.

The token travels in the #fragment of the address Kurator opens; browsers never send that part to
the web server. Kurator stores only the token's hash. A link lasts a day after its last use (and
across restarts), reads only its own files and never changes or overwrites anything: results are
new files. Settings → SFX Workbench ends every link at once.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from urllib.parse import quote, urlsplit

from . import fileinfo, tags
from .db import Database
from .handlers import handler_for

DEFAULT_URL = "https://eigensoundlab.github.io/SFXWorkbench/"
TAG = "SFX Workbench"
TTL = 24 * 3600            # a link ends a day after its last use
MAX_LINKS = 40
MAX_FILES = 200
MAX_MADE = 1000            # results remembered per link
MAX_BYTES = 2 << 30        # one result: 2 GB (a long 32-bit float recording)
_lock = threading.Lock()
_inbox = {"n": 0, "last": None, "dest": None}   # for the page: "SFX Workbench sent ..." (since start)


class LinkError(ValueError):
    pass


# ---------------------------------------------------------------- address
def address(db: Database) -> str:
    return db.get_setting("sfx_url", None) or DEFAULT_URL


def check_address(url: str) -> str:
    """The SFX Workbench address in Settings: the web app, your own copy, or the offline file."""
    u = url.strip().strip('"')
    if not u:
        return ""
    p = urlsplit(u)
    if p.scheme == "file" or (p.scheme in ("http", "https") and p.hostname):
        return u.split("#")[0]
    if os.path.isabs(u) and u.lower().endswith((".html", ".htm")):   # a path to sfx-workbench.html
        return "file:///" + u.replace("\\", "/").lstrip("/")
    raise LinkError("Use the web address of SFX Workbench (https://...) or the path of its offline file")


def origin_of(url: str) -> str:
    """The origin a page from this address sends with its requests ("null" for a local file)."""
    p = urlsplit(url)
    if p.scheme in ("http", "https") and p.hostname:
        default = {"http": 80, "https": 443}[p.scheme]
        host = p.hostname.lower()
        host = f"[{host}]" if ":" in host else host
        return f"{p.scheme}://{host}" + (f":{p.port}" if p.port and p.port != default else "")
    return "null"


def origin_ok(db: Database, origin: str | None) -> bool:
    return bool(origin) and origin == origin_of(address(db))


# ------------------------------------------------------------------ links
def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _links(db: Database) -> dict:
    now = time.time()
    return {k: v for k, v in (db.get_setting("sfx_links", {}) or {}).items() if now - v.get("used", 0) < TTL}


def _save(db: Database, links: dict) -> None:
    if len(links) > MAX_LINKS:   # the least recently used go first
        links = dict(sorted(links.items(), key=lambda kv: kv[1].get("used", 0))[-MAX_LINKS:])
    db.set_setting("sfx_links", links)


def _sound_rows(db: Database, ids: list[int]) -> list[dict]:
    out = []
    for i in ids:
        row = db.cat.execute("SELECT id, path, name, kind, size, meta FROM files WHERE id = ?", (int(i),)).fetchone()
        if row is None:
            raise LinkError("A file is not in the catalog any more - reload the page")
        if row["kind"] != "audio":
            raise LinkError(f"{row['name']}: SFX Workbench takes sounds only")
        out.append(dict(row))
    return out


def create(db: Database, server: str, ids: list[int], dest: str, tag_ids: list[int], label: str = "") -> dict:
    """A link for these sounds (none: new sounds), results into `dest` with `tag_ids`.
    server: Kurator's own address as SFX Workbench reaches it (http://127.0.0.1:port)."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    if len(ids) > MAX_FILES:
        raise LinkError(f"Send up to {MAX_FILES} sounds at a time")
    rows = _sound_rows(db, ids)
    dest = os.path.normpath(os.path.abspath(os.path.expanduser(str(dest or "").strip().strip('"'))))
    if not str(dest).strip() or db.root_for(dest, "library") is None:
        raise LinkError("Choose a folder in the library for what comes back")
    for t in tag_ids:
        tags.get_tag(db, int(t))                      # raises for a tag that is gone
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        links = _links(db)
        links[_key(token)] = {"ids": [r["id"] for r in rows], "dest": dest, "tags": [int(t) for t in tag_ids],
                              "label": str(label or "")[:80], "created": now, "used": now, "made": []}
        _save(db, links)
    base = address(db)
    url = f"{base}#kurator={quote(server, safe='')}&link={token}"
    return {"url": url, "token": token, "files": len(rows), "dest": dest, "address": base}


def find(db: Database, token: str) -> dict | None:
    """The link of this token (marked as used), or None when unknown or ended."""
    if not token or len(token) > 100:
        return None
    k = _key(token)
    with _lock:
        links = _links(db)
        link = links.get(k)
        if link is None:
            return None
        if time.time() - link["used"] > 60:      # write at most once a minute
            link["used"] = time.time()
            _save(db, links)
    return link


def active(db: Database) -> list[dict]:
    return [{"files": len(v["ids"]), "made": len(v.get("made", [])), "dest": v["dest"], "label": v.get("label", ""),
             "created": v["created"], "used": v["used"]} for v in sorted(_links(db).values(), key=lambda v: -v["used"])]


def end_all(db: Database) -> int:
    with _lock:
        n = len(_links(db))
        db.set_setting("sfx_links", {})
    return n


def manifest(db: Database, link: dict) -> dict:
    """What SFX Workbench shows: the sounds, and where results go."""
    from . import __version__
    files = []
    for n, fid in enumerate(link["ids"]):
        row = db.cat.execute("SELECT name, size, meta FROM files WHERE id = ?", (fid,)).fetchone()
        if row is None:
            files.append({"n": n, "name": None, "missing": True})
            continue
        try:
            meta = json.loads(row["meta"] or "{}")
        except ValueError:
            meta = {}
        files.append({"n": n, "name": row["name"], "size": row["size"], "duration": meta.get("duration"),
                      "sample_rate": meta.get("samplerate"), "channels": meta.get("channels")})
    names = []
    for t in link["tags"]:
        try:
            g = tags.get_tag(db, t)
            names.append(("◆ " if g["facet"] == "project" else "") + g["name"])
        except tags.TagError:
            pass
    return {"kurator": __version__, "label": link.get("label", ""), "files": files,
            "dest": os.path.basename(link["dest"].rstrip("/\\")) or link["dest"], "tags": [TAG, *names],
            "made": len(link.get("made", [])), "max_bytes": MAX_BYTES}


def file_path(db: Database, link: dict, n: int) -> str:
    if not 0 <= n < len(link["ids"]):
        raise LinkError("No such sound in this link")
    row = db.cat.execute("SELECT path FROM files WHERE id = ?", (link["ids"][n],)).fetchone()
    if row is None or not os.path.isfile(row[0]):
        raise LinkError("This sound is missing on disk")
    return row[0]


def accepts(name: str) -> bool:
    h = handler_for(os.path.splitext(str(name or ""))[1].lower())
    return bool(h and h.kind == "audio")


def received(db: Database, scanner, token: str, link: dict, tmp: str, name: str, src: list[int]) -> dict:
    """A result from SFX Workbench (in `tmp`, inside the link's folder): a new file - never over
    another - tagged, with where it came from."""
    from .fileops import receive_file
    entry = receive_file(db, scanner, tmp, link["dest"], name, None)
    fid = entry["id"]
    if fid is not None:
        tag_id = _own_tag(db)
        for t in [tag_id, *link["tags"]]:
            try:
                tags.apply(db, [fid], t, "add", source="sfxworkbench")
            except tags.TagError:
                pass                                   # a tag deleted meanwhile
        from_names = []
        for n in src[:20]:
            if 0 <= n < len(link["ids"]):
                row = db.cat.execute("SELECT name FROM files WHERE id = ?", (link["ids"][n],)).fetchone()
                if row:
                    from_names.append(row[0])
        source = "SFX Workbench" + (f", from {', '.join(from_names[:5])}" + (f" (+{len(from_names) - 5})" if len(from_names) > 5 else "")
                                    if from_names else "")
        fileinfo.set_many(db, [fid], {"source": source[:fileinfo.MAX_LEN]})
        scanner.request_process()
    with _lock:
        links = _links(db)
        k = _key(token)
        if k in links:
            links[k]["made"] = (links[k].get("made", []) + [fid])[-MAX_MADE:]
            links[k]["used"] = time.time()
            _save(db, links)
        _inbox.update(n=_inbox["n"] + 1, last=os.path.basename(entry["to"]), dest=link["dest"])
    return {"name": os.path.basename(entry["to"]), "id": fid, "shown": fid is not None}


def _own_tag(db: Database) -> int:
    """The "SFX Workbench" tag (Source group): the one made before, even renamed, else a new one."""
    tid = db.get_setting("sfx_tag", None)
    if tid is not None:
        try:
            tags.get_tag(db, int(tid))
            return int(tid)
        except tags.TagError:
            pass
    try:
        t = tags.create_tag(db, TAG, hotkey=None, facet="source")
    except tags.TagError:                              # "SFX Workbench" is a sub-tag elsewhere
        t = tags.create_tag(db, TAG, hotkey=None)
    db.set_setting("sfx_tag", t["id"])
    return t["id"]


def inbox() -> dict:
    return dict(_inbox)

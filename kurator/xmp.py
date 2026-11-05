"""XMP sidecars and keyword import - so your tags and dates are never locked into Kurator.

Writing: "photo.jpg" gets "photo.jpg.xmp" next to it with
    dc:subject              flat keywords (every level: Creatures, Dragons)
    lr:hierarchicalSubject  Creatures|Dragons      (Lightroom, Bridge, darktable ...)
    digiKam:TagsList        Creatures/Dragons      (digiKam)
    xmp:CreateDate / photoshop:DateCreated         the resolved creation date
Original image files are never modified. A sidecar written by another program is never
overwritten (it is reported as skipped); only Kurator's own sidecars are updated.

Reading: keywords from embedded XMP, Windows Explorer tags (EXIF XPKeywords) and sidecars can be
imported as tags. A Kurator sidecar's date is also used when indexing, so the date survives copies.
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

from PIL import Image

from . import __version__, tags as tagmod
from .db import Database
from .handlers.image import decode_xp

NS = "https://github.com/eigensoundlab/kurator/ns/1.0/"
MAX_SIDECAR = 4 * 1024 * 1024


def sidecar_candidates(path: str) -> list[str]:
    stem = os.path.splitext(path)[0]
    return [path + ".xmp", stem + ".xmp", path + ".XMP", stem + ".XMP"]


def _read_text(p: str) -> str | None:
    try:
        if os.path.getsize(p) > MAX_SIDECAR:
            return None
        with open(p, "rb") as f:
            return f.read().decode("utf-8", "ignore")
    except OSError:
        return None


def is_ours(text: str | None) -> bool:
    return bool(text) and NS in text


def build(tag_names: list[str], date_ts: float | None, date_source: str | None) -> str:
    hier = sorted(set(tag_names), key=str.lower)
    flat = sorted({part for t in hier for part in t.split("/")}, key=str.lower)
    attrs = ['xmlns:dc="http://purl.org/dc/elements/1.1/"',
             'xmlns:lr="http://ns.adobe.com/lightroom/1.0/"',
             'xmlns:digiKam="http://www.digikam.org/ns/1.0/"',
             'xmlns:xmp="http://ns.adobe.com/xap/1.0/"',
             'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"',
             f'xmlns:kurator="{NS}"']
    if date_ts:
        iso = datetime.fromtimestamp(date_ts).astimezone().isoformat(timespec="seconds")
        attrs += [f"xmp:CreateDate={quoteattr(iso)}", f"photoshop:DateCreated={quoteattr(iso)}"]
        if date_source:
            attrs.append(f"kurator:DateSource={quoteattr(date_source)}")
    attrs.append(f"kurator:Version={quoteattr(__version__)}")

    def bag(tag: str, kind: str, items: list[str]) -> str:
        if not items:
            return ""
        lis = "".join(f"\n     <rdf:li>{escape(i)}</rdf:li>" for i in items)
        return f"\n   <{tag}>\n    <rdf:{kind}>{lis}\n    </rdf:{kind}>\n   </{tag}>"

    body = (bag("dc:subject", "Bag", flat)
            + bag("lr:hierarchicalSubject", "Bag", [t.replace("/", "|") for t in hier])
            + bag("digiKam:TagsList", "Seq", hier))
    sep = "\n    "
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Kurator {escape(__version__)}">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        f'  <rdf:Description rdf:about=""{sep}{sep.join(attrs)}>{body}\n'
        "  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    )


def write_sidecars(db: Database, ids: list[int], include_untagged: bool = False) -> dict:
    # Projects/Status/Source get a root keyword ("Projects|Game A") so other programs keep them apart.
    names = {r[0]: tagmod.display_name(r[1], r[2]) for r in db.cat.execute("SELECT id, name, facet FROM tags")}
    from .projectdirs import engine_root
    res = {"written": 0, "unchanged": 0, "skipped_foreign": [], "skipped_untagged": 0, "skipped_project": 0, "errors": []}
    ids = list(dict.fromkeys(int(i) for i in ids))
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        by_file: dict[int, list[str]] = {}
        for fid, tid in db.cat.execute(f"SELECT file_id, tag_id FROM file_tags WHERE file_id IN ({q})", chunk):
            by_file.setdefault(fid, []).append(names[tid])
        for fid, path, ts, src in db.cat.execute(
                f"SELECT id, path, date_ts, date_source FROM files WHERE id IN ({q})", chunk):
            target = path + ".xmp"
            if engine_root(path):   # Unity/Godot/Unreal project or git repository: add no files there
                res["skipped_project"] += 1
                continue
            existing = {c: _read_text(c) for c in sidecar_candidates(path) if os.path.isfile(c)}
            foreign = [c for c, t in existing.items() if not is_ours(t)]
            if foreign:
                res["skipped_foreign"].append(foreign[0])
                continue
            tag_names = by_file.get(fid, [])
            if not tag_names and not include_untagged and target not in existing:
                res["skipped_untagged"] += 1
                continue
            content = build(tag_names, ts, src)
            if existing.get(target) == content:
                res["unchanged"] += 1
                continue
            try:
                tmp = target + ".kurator-tmp"
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(content)
                os.replace(tmp, target)
                res["written"] += 1
            except OSError as e:
                res["errors"].append(f"{path}: {e}")
    res["skipped_foreign"] = res["skipped_foreign"][:200]
    return res


# --------------------------------------------------------------------- reading
_BLOCK = {
    "hier": re.compile(r"<lr:hierarchicalSubject>(.*?)</lr:hierarchicalSubject>", re.S),
    "digikam": re.compile(r"<digiKam:TagsList>(.*?)</digiKam:TagsList>", re.S),
    "subject": re.compile(r"<dc:subject>(.*?)</dc:subject>", re.S),
}
_LI = re.compile(r"<rdf:li[^>]*>(.*?)</rdf:li>", re.S)


def keywords_from_xmp(text: str) -> list[str]:
    """Hierarchical keywords as 'A/B'. Flat dc:subject words are kept only if not already part of
    a hierarchy (Lightroom writes every level into dc:subject too)."""
    if not text:
        return []
    out: list[str] = []
    for key, sep in (("hier", "|"), ("digikam", "/")):
        for block in _BLOCK[key].findall(text):
            for li in _LI.findall(block):
                v = html.unescape(li).strip()
                if v:
                    out.append("/".join(p.strip() for p in v.split(sep) if p.strip()))
    covered = {p.lower() for k in out for p in k.split("/")}
    for block in _BLOCK["subject"].findall(text):
        for li in _LI.findall(block):
            v = html.unescape(li).strip()
            if v and v.lower() not in covered:
                out.append(v)
    return list(dict.fromkeys(k for k in out if k))


def read_keywords(path: str) -> list[str]:
    found: list[str] = []
    try:
        with Image.open(path) as img:
            xmp = img.info.get("xmp")
            if not xmp and img.format == "PNG":
                xmp = img.text.get("XML:com.adobe.xmp")
            if isinstance(xmp, bytes):
                xmp = xmp.decode("utf-8", "ignore")
            found += keywords_from_xmp(xmp or "")
            try:
                xp = decode_xp(img.getexif().get(0x9C9E))  # XPKeywords
            except Exception:
                xp = None
            if xp:
                found += [k.strip() for k in xp.split(";") if k.strip()]
    except Exception:
        pass
    for c in sidecar_candidates(path):
        if os.path.isfile(c):
            found += keywords_from_xmp(_read_text(c) or "")
    return list(dict.fromkeys(found))


def import_keywords(db: Database, ids: list[int]) -> dict:
    """Turn keywords found in the files (and their sidecars) into Kurator tags."""
    res = {"files": 0, "with_keywords": 0, "tags_created": 0, "applied": 0}
    before = {r[0] for r in db.cat.execute("SELECT id FROM tags")}
    per_tag: dict[int, list[int]] = {}
    ids = list(dict.fromkeys(int(i) for i in ids))
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        for fid, path in db.cat.execute(f"SELECT id, path FROM files WHERE id IN ({q})", chunk).fetchall():
            res["files"] += 1
            kws = read_keywords(path)
            if not kws:
                continue
            res["with_keywords"] += 1
            for kw in kws:
                facet = "subject"
                root, _, rest = kw.partition("/")
                if rest and root.lower() in tagmod.XMP_ROOTS:  # "Projects/Game A" -> project "Game A"
                    facet, kw = tagmod.XMP_ROOTS[root.lower()], rest
                try:
                    t = tagmod.create_tag(db, kw, hotkey=None, facet=facet)
                except tagmod.TagError:
                    continue
                per_tag.setdefault(t["id"], []).append(fid)
    for tid, fids in per_tag.items():
        res["applied"] += len(tagmod.apply(db, fids, tid, "add")["added"])
    res["tags_created"] = len({r[0] for r in db.cat.execute("SELECT id FROM tags")} - before)
    return res


def sidecar_date(path: str) -> float | None:
    """Creation date from a sidecar next to the file (used while indexing)."""
    from . import dates
    for c in sidecar_candidates(path):
        if os.path.isfile(c):
            ts = dates.parse_xmp_date(_read_text(c) or "")
            if ts:
                return ts
    return None

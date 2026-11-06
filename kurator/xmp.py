"""XMP sidecars and keyword import - so your tags and dates are never locked into Kurator.

Writing: "photo.jpg" gets "photo.jpg.xmp" next to it with
    dc:subject              flat keywords (every level: Creatures, Dragons)
    lr:hierarchicalSubject  Creatures|Dragons      (Lightroom, Bridge, darktable ...)
    digiKam:TagsList        Creatures/Dragons      (digiKam)
    xmp:CreateDate / photoshop:DateCreated         the resolved creation date
    dc:source, dc:creator, dc:rights, xmpRights:WebStatement    source, author, licence
Original image files are never modified. A sidecar written by another program is never
replaced: by default it is left alone (reported as skipped); with "merge" only its keyword lists
are updated - and source/author/licence added where it has none - and everything else in it
(develop settings, ratings, history...) stays exactly as it was. Before the first change a copy
goes to the data folder (xmp-backup).

Reading: keywords from embedded XMP, Windows Explorer tags (EXIF XPKeywords) and sidecars can be
imported as tags. A Kurator sidecar's date is also used when indexing, so the date survives copies.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import xml.etree.ElementTree as ET
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


URIS = {"dc": "http://purl.org/dc/elements/1.1/", "lr": "http://ns.adobe.com/lightroom/1.0/",
        "digiKam": "http://www.digikam.org/ns/1.0/", "xmpRights": "http://ns.adobe.com/xap/1.0/rights/"}


def _bag(tag: str, kind: str, items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(f"\n     <rdf:li>{escape(i)}</rdf:li>" for i in items)
    return f"\n   <{tag}>\n    <rdf:{kind}>{lis}\n    </rdf:{kind}>\n   </{tag}>"


def _keyword_blocks(tag_names: list[str], extra_flat: set | None = None) -> str:
    hier = sorted(set(tag_names), key=str.lower)
    flat = sorted({part for t in hier for part in t.split("/")} | (extra_flat or set()), key=str.lower)
    return (_bag("dc:subject", "Bag", flat)
            + _bag("lr:hierarchicalSubject", "Bag", [t.replace("/", "|") for t in hier])
            + _bag("digiKam:TagsList", "Seq", hier))


def _info_blocks(info: dict | None, have: set | None = None) -> str:
    """dc:source, dc:creator, dc:rights, xmpRights:WebStatement - the ones not in `have`."""
    from .fileinfo import licence_url
    info, have = info or {}, have or set()
    out = ""
    if info.get("source") and "dc:source" not in have:
        out += f"\n   <dc:source>{escape(info['source'])}</dc:source>"
    if info.get("author") and "dc:creator" not in have:
        out += _bag("dc:creator", "Seq", [info["author"]])
    if info.get("licence") and "dc:rights" not in have:
        out += f'\n   <dc:rights>\n    <rdf:Alt>\n     <rdf:li xml:lang="x-default">{escape(info["licence"])}</rdf:li>\n    </rdf:Alt>\n   </dc:rights>'
    url = licence_url(info.get("licence"))
    if url and "xmpRights:WebStatement" not in have and "dc:rights" not in have:   # never contradict their rights
        out += f"\n   <xmpRights:WebStatement>{escape(url)}</xmpRights:WebStatement>"
    return out


def build(tag_names: list[str], date_ts: float | None, date_source: str | None, info: dict | None = None) -> str:
    attrs = ['xmlns:dc="http://purl.org/dc/elements/1.1/"',
             'xmlns:lr="http://ns.adobe.com/lightroom/1.0/"',
             'xmlns:digiKam="http://www.digikam.org/ns/1.0/"',
             'xmlns:xmp="http://ns.adobe.com/xap/1.0/"',
             'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"',
             'xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/"',
             f'xmlns:kurator="{NS}"']
    if date_ts:
        iso = datetime.fromtimestamp(date_ts).astimezone().isoformat(timespec="seconds")
        attrs += [f"xmp:CreateDate={quoteattr(iso)}", f"photoshop:DateCreated={quoteattr(iso)}"]
        if date_source:
            attrs.append(f"kurator:DateSource={quoteattr(date_source)}")
    attrs.append(f"kurator:Version={quoteattr(__version__)}")
    body = _keyword_blocks(tag_names) + _info_blocks(info)
    sep = "\n    "
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Kurator {escape(__version__)}">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        f'  <rdf:Description rdf:about=""{sep}{sep.join(attrs)}>{body}\n'
        "  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    )


_KEYWORD_PROPS = ("dc:subject", "lr:hierarchicalSubject", "digiKam:TagsList")


def _list_items(text: str, prop: str) -> list[str]:
    out = []
    for block in re.findall(rf"<{prop}\b[^>]*>(.*?)</{prop}>", text, re.S):
        out += [html.unescape(li).strip() for li in _LI.findall(block)]
    return [x for x in out if x]
_INFO_PROPS = ("dc:source", "dc:creator", "dc:rights", "xmpRights:WebStatement")


def merge_foreign(text: str, tag_names: list[str], info: dict | None = None) -> str | None:
    """A sidecar written by another program with Kurator's keywords added (the keywords already
    in it stay) and source/author/licence where it has none; everything else untouched.
    None when it can't be done safely."""
    if not text or is_ours(text):
        return None
    m = re.search(r"<rdf:Description\b[^>]*?(/?)>", text)
    if not m:
        return None
    # the prefixes we write must mean what we think (or be free)
    for prefix, uri in URIS.items():
        bound = re.findall(rf'xmlns:{prefix}\s*=\s*["\']([^"\']*)["\']', text)
        if any(b != uri for b in bound):
            return None
        other = re.findall(rf'xmlns:(\w+)\s*=\s*["\']{re.escape(uri)}["\']', text)
        if any(o != prefix for o in other):
            return None
    # keywords already there stay: lists = theirs + Kurator's
    hier = {k.replace("|", "/") for k in _list_items(text, "lr:hierarchicalSubject")}
    hier |= set(_list_items(text, "digiKam:TagsList"))
    flat = set(_list_items(text, "dc:subject"))
    tag_names = sorted(hier | set(tag_names), key=str.lower)
    out = text
    mark = "\x00kurator-keywords\x00"
    for prop in _KEYWORD_PROPS:                    # replace the old keyword lists (elements, any form)
        for pat in (rf"\s*<{prop}\b[^>]*/>", rf"\s*<{prop}\b[^>]*>.*?</{prop}>"):
            out = re.sub(pat, lambda _m: "" if mark in out else mark, out, count=1, flags=re.S)
            out = re.sub(pat, "", out, flags=re.S)
    have = {p for p in _INFO_PROPS if re.search(rf"<{p}\b|\b{p}\s*=", out)}
    keywords = _keyword_blocks(tag_names, flat)
    extra = _info_blocks(info, have)
    blocks = keywords + extra
    if mark in out:                                 # the keywords go where the old ones were
        out = out.replace(mark, keywords)
        blocks = extra
    m = re.search(r"<rdf:Description\b[^>]*?(/?)>", out)
    start = m.group(0)
    need = [f'xmlns:{p}="{u}"' for p, u in URIS.items()
            if (f"<{p}:" in keywords + extra) and not re.search(rf"xmlns:{p}\s*=", out)]
    new_start = start[:-2 if m.group(1) else -1].rstrip() + "".join("\n    " + n for n in need) + ">"
    if m.group(1):                                  # <rdf:Description .../> becomes an element with content
        out = out[:m.start()] + new_start + blocks + "\n  </rdf:Description>" + out[m.end():]
    else:
        close = out.find("</rdf:Description>", m.end())
        if close < 0:
            return None
        body = out[m.end():close].rstrip()
        out = out[:m.start()] + new_start + body + blocks + "\n  " + out[close:]
    try:                                            # still well-formed XML?
        ET.fromstring(re.sub(r"<\?xpacket[^>]*\?>", "", out).strip())
    except ET.ParseError:
        return None
    return out


def _backup_foreign(db: Database, path: str) -> None:
    """A copy of another program's sidecar before Kurator first changes it (once per file)."""
    folder = os.path.join(db.data_dir, "xmp-backup")
    os.makedirs(folder, exist_ok=True)
    name = hashlib.sha1(os.path.abspath(path).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    dest = os.path.join(folder, f"{name}_{os.path.basename(path)}")
    if not os.path.exists(dest):
        shutil.copy2(path, dest)
        with open(os.path.join(folder, "index.txt"), "a", encoding="utf-8") as f:
            f.write(f"{os.path.basename(dest)}\t{path}\n")


def write_sidecars(db: Database, ids: list[int], include_untagged: bool = False, merge: bool | None = None) -> dict:
    """Write Kurator's sidecars; with `merge` (default: the "xmp_merge" setting) also update the
    keywords in sidecars written by other programs."""
    if merge is None:
        merge = bool(db.get_setting("xmp_merge", False))
    # Projects/Status/Source get a root keyword ("Projects|Game A") so other programs keep them apart.
    names = {r[0]: tagmod.display_name(r[1], r[2]) for r in db.cat.execute("SELECT id, name, facet FROM tags")}
    from .fileinfo import get_many
    from .projectdirs import engine_root
    res = {"written": 0, "unchanged": 0, "merged": 0, "skipped_foreign": [], "skipped_untagged": 0,
           "skipped_project": 0, "errors": []}
    ids = list(dict.fromkeys(int(i) for i in ids))
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ",".join("?" * len(chunk))
        by_file: dict[int, list[str]] = {}
        for fid, tid in db.cat.execute(f"SELECT file_id, tag_id FROM file_tags WHERE file_id IN ({q})", chunk):
            by_file.setdefault(fid, []).append(names[tid])
        infos = get_many(db, chunk)
        for fid, path, ts, src in db.cat.execute(
                f"SELECT id, path, date_ts, date_source FROM files WHERE id IN ({q})", chunk):
            target = path + ".xmp"
            if engine_root(path):   # Unity/Godot/Unreal project or git repository: add no files there
                res["skipped_project"] += 1
                continue
            existing = {c: _read_text(c) for c in sidecar_candidates(path) if os.path.isfile(c)}
            foreign = [c for c, t in existing.items() if not is_ours(t)]
            tag_names = by_file.get(fid, [])
            info = infos.get(fid)
            if foreign:
                if not merge:
                    res["skipped_foreign"].append(foreign[0])
                    continue
                fp = foreign[0]
                merged = merge_foreign(existing[fp] or "", tag_names, info)
                if merged is None:
                    res["skipped_foreign"].append(fp)
                    continue
                if merged == existing[fp]:
                    res["unchanged"] += 1
                    continue
                try:
                    _backup_foreign(db, fp)
                    tmp = fp + ".kurator-tmp"
                    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                        f.write(merged)
                    os.replace(tmp, fp)
                    res["merged"] += 1
                except OSError as e:
                    res["errors"].append(f"{path}: {e}")
                continue
            if not tag_names and not info and not include_untagged and target not in existing:
                res["skipped_untagged"] += 1
                continue
            content = build(tag_names, ts, src, info)
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

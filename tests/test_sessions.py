"""Recording sessions and plans from Ballistic Foley Lab: zips that arrive in a library folder are unpacked
and tagged, takes count against their plan's shots, and plan links work both ways."""

from __future__ import annotations

import io
import json
import math
import os
import struct
import time
import zipfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from kurator import plans, sessions, tags, trash, xmp
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app
from kurator.watcher import Watcher

BASE = "http://127.0.0.1:8765"
# packPlan() of this plan in Ballistic Foley Lab (src/engine.js), copied here so the two stay in step
BALLISTIC_LINK = ("eyJ2IjoxLCJpIjoicGxhbi1mb3Jlc3QiLCJuIjoiTGVzbsOtIHByb2Now6F6a2EiLCJwIjoiRm9yZXN0IHdhbGsiLCJjIjpbIk5hdHVyZSIsIk9iamVjdHMiXSwic"
                  "yI6W3siaSI6InMxIiwibiI6IkJsYWNrYmlyZCIsInQiOlsiTmF0dXJlL0JpcmRzIl0sImsiOjMsInUiOiJCSVJEU29uZyJ9LHsiaSI6InMyIiwibiI6IldpbmQiLCJv"
                  "IjoiZnJvbSB0aGUgcmlkZ2UifV19")
PLAN = {"id": "plan-forest", "name": "Lesní procházka", "project": "Forest walk", "chips": ["Nature", "Objects"],
        "shots": [{"id": "s1", "name": "Blackbird", "tags": ["Nature/Birds"], "takes": 3, "ucs": "BIRDSong", "notes": ""},
                  {"id": "s2", "name": "Wind", "tags": [], "takes": 1, "ucs": "", "notes": "from the ridge"}]}


def wait_for(cond, timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def xmp_packet(keywords: list[str], date: str) -> str:
    lis = "".join(f"<rdf:li>{k.replace('/', '|')}</rdf:li>" for k in keywords)
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description rdf:about="" xmp:CreateDate="{date}"><lr:hierarchicalSubject><rdf:Bag>{lis}</rdf:Bag>'
            '</lr:hierarchicalSubject></rdf:Description></rdf:RDF></x:xmpmeta>')


def wav_bytes(secs=0.2, sr=48000, chunks=()) -> bytes:
    frames = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / sr))) for i in range(int(secs * sr)))
    body = b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
    for cid, data in chunks:
        body += cid + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")
    body += b"data" + struct.pack("<I", len(frames)) + frames
    return b"RIFF" + struct.pack("<I", len(body)) + body


def session_zip(path, files, plan=PLAN, extra=()):
    """files: [(name, keywords, date, shot)] as Ballistic's Save session writes them."""
    entries = []
    with zipfile.ZipFile(path, "w") as z:
        for name, kws, date, shot in files:
            z.writestr(name, wav_bytes(chunks=[(b"_PMX", xmp_packet(kws, date).encode())]))
            z.writestr(name + ".xmp", xmp_packet(kws, date))
            entries.append({"file": name, "sidecar": name + ".xmp", "date": date, "tags": kws, "plan": plan["id"] if plan and shot else "",
                            "shot": shot or "", "take": 1})
        for n, data in extra:
            z.writestr(n, data)
        z.writestr("ballistic-session.json", json.dumps({"format": "ballistic-session", "version": 1, "plan": plan, "files": entries
                                                        + [{"file": "../evil.wav", "sidecar": "a/b.wav.xmp"}]}))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    trashed = []
    monkeypatch.setattr(trash, "to_system_trash", lambda p: (trashed.append(p), os.replace(p, str(tmp_path / "trashed.zip"))))
    lib = tmp_path / "lib"
    (lib / "Phone").mkdir(parents=True)
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    return db, sc, c, lib, trashed


def file_tags(db, name):
    fid = db.cat.execute("SELECT id FROM files WHERE name = ?", (name,)).fetchone()[0]
    return {(r[0], r[1]) for r in db.cat.execute(
        "SELECT t.facet, t.name FROM tags t JOIN file_tags ft ON ft.tag_id = t.id WHERE ft.file_id = ?", (fid,))}


def test_a_session_zip_is_unpacked_tagged_and_counted(env):
    db, sc, c, lib, trashed = env
    zp = lib / "Phone" / "Forest walk 2026-11-15 0542.zip"
    session_zip(zp, [("BIRDSong_Blackbird 1.wav", ["Nature/Birds", "Projects/Forest walk", "Source/Ballistic Foley Lab"], "2026-11-15T05:42:10+01:00", "s1"),
                     ("Wind 1.wav", ["Ambience/Wind", "Projects/Forest walk"], "2026-11-15T06:10:00+01:00", "s2")],
                extra=[("notes.txt", "not listed: stays in the zip")])
    sc.request_scan()
    folder = lib / "Phone" / "Forest walk 2026-11-15 0542"
    assert wait_for(lambda: folder.is_dir() and db.cat.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] >= 5)
    assert sorted(os.listdir(folder)) == ["BIRDSong_Blackbird 1.wav", "BIRDSong_Blackbird 1.wav.xmp", "Wind 1.wav", "Wind 1.wav.xmp", "ballistic-session.json"]
    assert not (lib / "evil.wav").exists() and not (lib / "Phone" / "evil.wav").exists()
    assert trashed == [str(zp)] and not zp.exists()
    # the recording date as the modified time
    assert abs(os.stat(folder / "BIRDSong_Blackbird 1.wav").st_mtime - datetime.fromisoformat("2026-11-15T05:42:10+01:00").timestamp()) < 1
    got = file_tags(db, "BIRDSong_Blackbird 1.wav")
    assert ("subject", "Nature/Birds") in got and ("project", "Forest walk") in got and ("source", "Ballistic Foley Lab") in got
    # the plan arrived with the session, with one take for each shot
    lst = c.get("/api/plans").json()["plans"]
    assert [p["id"] for p in lst] == ["plan-forest"]
    assert lst[0]["progress"] == {"s1": 1, "s2": 1}
    assert lst[0]["link"].startswith(plans.DEFAULT_URL + "#plan=")
    st = c.get("/api/state").json()["sessions"]
    assert st["n"] == 1 and st["sounds"] == 2 and st["last"] == "Forest walk 2026-11-15 0542"
    # the same session again (restored from the trash, synced twice): not unpacked again
    session_zip(zp, [("BIRDSong_Blackbird 1.wav", ["Nature/Birds", "Projects/Forest walk", "Source/Ballistic Foley Lab"], "2026-11-15T05:42:10+01:00", "s1"),
                     ("Wind 1.wav", ["Ambience/Wind", "Projects/Forest walk"], "2026-11-15T06:10:00+01:00", "s2")],
                extra=[("notes.txt", "not listed: stays in the zip")])
    assert sessions.unpack(db, sc, str(zp)) is None
    assert zp.exists() and not (lib / "Phone" / "Forest walk 2026-11-15 0542 2").exists()


def test_other_zips_and_folders_only_browsed_are_left_alone(env):
    db, sc, c, lib, trashed = env
    with zipfile.ZipFile(lib / "textures.zip", "w") as z:
        z.writestr("a.wav", wav_bytes())
    sc.request_scan()
    assert wait_for(lambda: sc.status()["phase"] == "idle" and sc.status()["finished"])
    time.sleep(0.5)
    assert (lib / "textures.zip").exists() and not (lib / "textures").exists() and not trashed
    # switched off: a session stays a zip
    assert c.put("/api/settings", json={"sessions_unpack": False}, headers={"origin": BASE}).status_code == 200
    assert c.get("/api/settings").json()["sessions_unpack"] is False
    session_zip(lib / "s.zip", [("a.wav", ["X"], "2026-11-15T05:42:10+01:00", "s1")])
    sc.request_scan()
    assert wait_for(lambda: sc.status()["phase"] == "idle" and sc.status()["finished"])
    time.sleep(0.5)
    assert (lib / "s.zip").exists() and not (lib / "s").exists()


def test_the_watcher_unpacks_a_session_that_arrives(env):
    db, sc, c, lib, trashed = env
    sc.request_scan()
    assert wait_for(lambda: sc.status()["phase"] == "idle" and sc.status()["finished"])
    w = Watcher(db, sc)
    w.start()
    try:
        time.sleep(0.5)
        tmp = lib / "Phone" / ".syncing.tmp"
        session_zip(tmp, [("Take 1.wav", ["Objects/Pot"], "2026-11-15T07:00:00+01:00", None)], plan=None)
        os.replace(tmp, lib / "Phone" / "Kitchen 2026-11-15 0700.zip")   # how sync tools finish a file
        assert wait_for(lambda: (lib / "Phone" / "Kitchen 2026-11-15 0700" / "Take 1.wav").exists()
                        and db.cat.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] >= 1)
        assert ("subject", "Objects/Pot") in file_tags(db, "Take 1.wav")
    finally:
        w.stop()


def test_keywords_inside_a_wav_without_a_sidecar(tmp_path):
    p = tmp_path / "rec.wav"
    junk = os.urandom(1001)
    p.write_bytes(wav_bytes(chunks=[(b"bext", b"\0" * 602), (b"JUNK", junk), (b"_PMX", xmp_packet(["Nature/Birds", "Projects/Forest walk"], "2026-11-15T05:42:10+01:00").encode())]))
    assert xmp.read_keywords(str(p)) == ["Nature/Birds", "Projects/Forest walk"]
    # an XMP chunk after the audio is found too
    q = tmp_path / "late.wav"
    raw = wav_bytes()
    pmx = xmp_packet(["Late"], "2026-11-15T05:42:10+01:00").encode()
    raw += b"_PMX" + struct.pack("<I", len(pmx)) + pmx + (b"\0" if len(pmx) & 1 else b"")
    raw = raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:]
    q.write_bytes(raw)
    assert xmp.read_keywords(str(q)) == ["Late"]
    assert xmp.wav_xmp(str(tmp_path / "missing.wav")) == ""


def test_plan_links_match_ballistic_and_the_api_keeps_plans(env):
    db, sc, c, lib, trashed = env
    assert plans.pack(plans.clean(PLAN)) == BALLISTIC_LINK
    assert plans.unpack(BALLISTIC_LINK) == plans.clean(PLAN)
    with pytest.raises(plans.PlanError):
        plans.unpack("bm90IGEgcGxhbg")
    r = c.post("/api/plans", json={"name": "Kitchen", "project": "Foley", "shots": [{"name": "Pot lid", "tags": ["Objects/Kitchen"], "takes": 500}, {"name": " "}]},
               headers={"origin": BASE})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["shots"] == [{"id": "s1", "name": "Pot lid", "tags": ["Objects/Kitchen"], "takes": 99, "ucs": "", "notes": ""}]
    assert plans.unpack(p["link"].split("#plan=")[1])["name"] == "Kitchen"
    got = c.post("/api/plans/from-link", json={"link": plans.DEFAULT_URL + "#plan=" + BALLISTIC_LINK}, headers={"origin": BASE})
    assert got.status_code == 200 and got.json()["name"] == "Lesní procházka"
    assert {x["name"] for x in c.get("/api/plans").json()["plans"]} == {"Kitchen", "Lesní procházka"}
    assert c.delete(f"/api/plans/{p['id']}", headers={"origin": BASE}).json() == {"deleted": True}
    assert c.post("/api/plans/from-link", json={"link": "https://example.com/"}, headers={"origin": BASE}).status_code == 400

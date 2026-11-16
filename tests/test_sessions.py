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


def session_zip(path, files, plan=PLAN, extra=(), session="", recorded_in=""):
    """files: [(name, keywords, date, shot)] as Ballistic's Save session writes them."""
    entries = []
    with zipfile.ZipFile(path, "w") as z:
        for name, kws, date, shot in files:
            z.writestr(name, wav_bytes(chunks=[(b"_PMX", xmp_packet(kws, date).encode())]))
            z.writestr(name + ".xmp", xmp_packet(kws, date))
            entries.append({"file": name, "sidecar": name + ".xmp", "date": date, "tags": kws, "plan": plan["id"] if plan and shot else "",
                            "shot": shot or "", "take": 1, "session": recorded_in or session})
        for n, data in extra:
            z.writestr(n, data)
        z.writestr("ballistic-session.json", json.dumps({"format": "ballistic-session", "version": 1, "session": session, "plan": plan, "files": entries
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


def test_more_takes_of_a_session_join_its_folder(env):
    db, sc, c, lib, trashed = env
    t1 = ("Take 1.wav", ["Objects/Pot"], "2026-11-15T07:00:00+01:00", "s1")
    t2 = ("Take 2.wav", ["Objects/Pot"], "2026-11-15T07:01:00+01:00", "s1")
    session_zip(lib / "Phone" / "Kitchen 0700.zip", [t1], session="ses-k")
    first = sessions.unpack(db, sc, str(lib / "Phone" / "Kitchen 0700.zip"))
    assert first["files"] == 1 and not first["more"]
    folder = lib / "Phone" / "Kitchen 0700"
    # the same session saved again later, with one more take: only the new take is added, to the same folder
    session_zip(lib / "Phone" / "Kitchen 0712.zip", [t1, t2], session="ses-k")
    again = sessions.unpack(db, sc, str(lib / "Phone" / "Kitchen 0712.zip"))
    assert again["folder"] == str(folder) and again["more"] and again["files"] == 1 and again["skipped"] == 1
    assert not (lib / "Phone" / "Kitchen 0712").exists()
    # a different take under a name already used keeps both (never over a file)
    session_zip(lib / "Phone" / "Kitchen 0730.zip", [("Take 2.wav", ["Objects/Lid"], "2026-11-15T07:30:00+01:00", "s1")], session="ses-k")
    sessions.unpack(db, sc, str(lib / "Phone" / "Kitchen 0730.zip"))
    assert sorted(os.listdir(folder)) == ["Take 1.wav", "Take 1.wav.xmp", "Take 2 2.wav", "Take 2 2.wav.xmp", "Take 2.wav", "Take 2.wav.xmp",
                                          "ballistic-session.json"]
    assert plans.progress(db, plans.get(db, "plan-forest"))["s1"] == 3
    # another session, even with the same file names, gets its own folder
    session_zip(lib / "Phone" / "Kitchen 0800.zip", [t1], session="ses-other")
    assert sessions.unpack(db, sc, str(lib / "Phone" / "Kitchen 0800.zip"))["folder"] == str(lib / "Phone" / "Kitchen 0800")
    # Your sounds sent later (their own session; each file names the session it was recorded in): the takes
    # already here are not added again, and no empty folder is made
    session_zip(lib / "Phone" / "Sounds.zip", [t1, t2], session="sounds-1", recorded_in="ses-k", plan=None)
    r = sessions.unpack(db, sc, str(lib / "Phone" / "Sounds.zip"))
    assert r["files"] == 0 and r["skipped"] == 2 and r["folder"] == str(folder)
    assert not (lib / "Phone" / "Sounds").exists()


def test_ballistic_on_this_computer_sends_a_session_straight_to_kurator(env, tmp_path):
    db, sc, c, lib, trashed = env
    ballistic = "https://eigensoundlab.github.io"
    info = c.get("/api/ballistic/receive").json()
    assert info == {"origin": ballistic, "address": plans.DEFAULT_URL, "on": True, "folder": str(lib / "Ballistic Foley Lab")}
    # the page Ballistic opens may be opened from its site; nothing else from there gets in
    page = c.get("/receive", headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"})
    assert page.status_code == 200 and "receive.js" in page.text
    zp = tmp_path / "s.zip"
    session_zip(zp, [("BIRDSong_Blackbird 1.wav", ["Nature/Birds", "Projects/Forest walk"], "2026-11-15T05:42:10+01:00", "s1")], session="ses-f")
    body = zp.read_bytes()
    url = "/api/ballistic/receive?name=Forest%20walk%202026-11-15%200542.zip"
    assert c.post(url, content=body, headers={"origin": ballistic}).status_code == 403
    assert c.post(url, content=body, headers={"origin": BASE, "sec-fetch-site": "cross-site"}).status_code == 403
    # from the /receive page (Kurator's own address)
    r = c.post(url, content=body, headers={"origin": BASE, "content-type": "application/zip"})
    assert r.status_code == 200, r.text
    j = r.json()
    folder = lib / "Ballistic Foley Lab" / "Forest walk 2026-11-15 0542"
    assert j["folder"] == str(folder) and j["files"] == 1 and not j["already"] and j["plan"] == PLAN["name"]
    assert sorted(os.listdir(folder)) == ["BIRDSong_Blackbird 1.wav", "BIRDSong_Blackbird 1.wav.xmp", "ballistic-session.json"]
    assert ("subject", "Nature/Birds") in file_tags(db, "BIRDSong_Blackbird 1.wav")
    assert not trashed and not [p for p in os.listdir(tmp_path) if p.startswith("kurator-session-")]
    assert c.get("/api/state").json()["sessions"]["last"] == "Forest walk 2026-11-15 0542"
    # sent twice: nothing new
    assert c.post(url, content=body, headers={"origin": BASE}).json()["already"] is True
    # not a session; switched off; a folder outside the library
    with zipfile.ZipFile(tmp_path / "plain.zip", "w") as z:
        z.writestr("a.wav", wav_bytes())
    r = c.post(url, content=(tmp_path / "plain.zip").read_bytes(), headers={"origin": BASE})
    assert r.status_code == 400 and "not a recording session" in r.json()["detail"]
    assert c.put("/api/settings", json={"ballistic_inbox": str(tmp_path)}, headers={"origin": BASE}).status_code == 400
    (lib / "Takes").mkdir()
    assert c.put("/api/settings", json={"ballistic_inbox": str(lib / "Takes")}, headers={"origin": BASE}).json()["ballistic_inbox"] == str(lib / "Takes")
    assert c.put("/api/settings", json={"ballistic_receive": False}, headers={"origin": BASE}).status_code == 200
    r = c.post(url, content=body, headers={"origin": BASE})
    assert r.status_code == 400 and "Settings" in r.json()["detail"]


def jpeg_bytes(color=(120, 90, 60), size=(64, 48)) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


CAPTURE_PLAN = {"id": "plan-castle", "name": "Castle", "project": "Castle", "chips": ["Stone"],
                "shots": [{"id": "t1", "name": "Granite wall", "tags": ["Surfaces/Stone/Granite"], "takes": 2, "kind": "texture"},
                          {"id": "p1", "name": "Gate", "tags": ["Places/Gate"], "takes": 1, "kind": "photo"},
                          {"id": "a1", "name": "Chains", "tags": ["Foley/Chains"], "takes": 1, "kind": "sound"}]}


def capture_files(session="cap-ses-1"):
    """What Kurator Capture writes for a session: [(name, bytes)] and the list (kurator-session.json)."""
    d = "2026-11-16T10:00:00+01:00"
    files = [("Granite wall 1.jpg", jpeg_bytes((140, 140, 130))), ("Granite wall 1 source.jpg", jpeg_bytes((90, 90, 80), (120, 90))),
             ("Gate 1.jpg", jpeg_bytes()), ("Chains 1.wav", wav_bytes())]
    kw = {"Granite wall 1.jpg": ["Surfaces/Stone/Granite", "Projects/Castle", "Source/Kurator Capture"],
          "Granite wall 1 source.jpg": ["Surfaces/Stone/Granite", "Projects/Castle", "Source/Kurator Capture"],
          "Gate 1.jpg": ["Places/Gate", "Projects/Castle", "Source/Kurator Capture"], "Chains 1.wav": ["Foley/Chains", "Projects/Castle", "Source/Kurator Capture"]}
    out = []
    for n, data in files:
        out += [(n, data), (n + ".xmp", xmp_packet(kw[n], d).encode())]
    entries = [{"file": "Granite wall 1.jpg", "sidecar": "Granite wall 1.jpg.xmp", "kind": "texture", "date": d, "plan": "plan-castle", "shot": "t1", "session": session,
                "size_m": {"w": 1.2, "h": 1.2}, "source": "Granite wall 1 source.jpg", "capture": "cap-t1"},
               {"file": "Granite wall 1 source.jpg", "sidecar": "Granite wall 1 source.jpg.xmp", "kind": "photo", "role": "source", "of": "Granite wall 1.jpg",
                "date": d, "plan": "plan-castle", "shot": "t1", "session": session, "capture": "cap-t1"},
               {"file": "Gate 1.jpg", "sidecar": "Gate 1.jpg.xmp", "kind": "photo", "date": d, "plan": "plan-castle", "shot": "p1", "session": session, "capture": "cap-p1"},
               {"file": "Chains 1.wav", "sidecar": "Chains 1.wav.xmp", "kind": "sound", "date": d, "plan": "plan-castle", "shot": "a1", "session": session, "capture": "cap-a1"}]
    manifest = {"format": "kurator-session", "version": 1, "app": "Kurator Capture 0.1.0", "session": session, "name": "Castle 2026-11-16 1000",
                "plan": CAPTURE_PLAN, "files": entries}
    return out, manifest


def test_a_kurator_capture_session_brings_photos_textures_and_sounds(env):
    db, sc, c, lib, trashed = env
    files, manifest = capture_files()
    zp = lib / "Phone" / "Castle 2026-11-16 1000.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for n, data in files:
            z.writestr(n, data)
        z.writestr("kurator-session.json", json.dumps(manifest))
    assert sessions.is_session(str(zp))
    r = sessions.unpack(db, sc, str(zp))
    folder = lib / "Phone" / "Castle 2026-11-16 1000"
    assert r["files"] == 4 and r["plan"] == "Castle"
    assert sorted(os.listdir(folder)) == sorted([n for n, _ in files] + ["kurator-session.json"])
    assert ("subject", "Surfaces/Stone/Granite") in file_tags(db, "Granite wall 1.jpg")
    assert ("source", "Kurator Capture") in file_tags(db, "Gate 1.jpg")
    # the shot kinds came with the plan; the source photo is not a second take of the texture
    p = plans.get(db, "plan-castle")
    assert [s["kind"] for s in p["shots"]] == ["texture", "photo", "sound"]
    assert c.get("/api/plans").json()["plans"][0]["progress"] == {"t1": 1, "p1": 1, "a1": 1}
    # a shot's kind travels in the link ("m"), and the link can open Kurator Capture
    assert plans.unpack(plans.pack(p))["shots"][0]["kind"] == "texture"
    assert c.get("/api/plans").json()["capture"] == plans.CAPTURE_URL
    # the backup folder Kurator unpacked itself is not filed a second time
    assert sessions.adopt(db, sc, str(folder)) is None
    # the same captures again by another way (their Drive backup synced into the library): filed and tagged,
    # but each capture counts once
    other = lib / "Google Drive" / "Kurator Capture" / "Castle 2026-11-16 1000"
    other.mkdir(parents=True)
    for n, data in files:
        (other / n).write_bytes(data)
    (other / "kurator-session.json").write_text(json.dumps(manifest))
    sc.walk_folder(db.root_for(str(other), "library")["id"], str(other), False)
    assert sessions.adopt(db, sc, str(other))["files"] == 4
    assert c.get("/api/plans").json()["plans"][0]["progress"] == {"t1": 1, "p1": 1, "a1": 1}


def test_a_backup_folder_synced_into_the_library_is_filed_where_it_lies(env):
    db, sc, c, lib, trashed = env
    files, manifest = capture_files("cap-ses-2")
    folder = lib / "Google Drive" / "Kurator Capture" / "Castle 2026-11-16 1000"
    folder.mkdir(parents=True)
    # the backup so far: the texture, its source and the gate (the chains are still uploading)
    for n, data in files:
        if not n.startswith("Chains"):
            (folder / n).write_bytes(data)
    manifest["files"] = manifest["files"][:3]
    (folder / "kurator-session.json").write_text(json.dumps(manifest))
    before = {n: (folder / n).stat().st_mtime_ns for n in os.listdir(folder)}
    sc.request_scan()
    assert wait_for(lambda: c.get("/api/state").json()["sessions"]["n"] >= 1 and db.cat.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] >= 6)
    assert ("subject", "Surfaces/Stone/Granite") in file_tags(db, "Granite wall 1.jpg")
    assert ("project", "Castle") in file_tags(db, "Gate 1.jpg")
    assert {n: (folder / n).stat().st_mtime_ns for n in os.listdir(folder)} == before, "nothing in a backup folder is changed"
    assert c.get("/api/plans").json()["plans"][0]["progress"] == {"t1": 1, "p1": 1, "a1": 0}
    # the next backup adds the chains and a longer list: only the new file is filed
    for n, data in files:
        if n.startswith("Chains"):
            (folder / n).write_bytes(data)
    _, full = capture_files("cap-ses-2")
    (folder / "kurator-session.json").write_text(json.dumps(full))
    sc.walk_folder(db.root_for(str(folder), "library")["id"], str(folder), False)
    r = sessions.adopt(db, sc, str(folder))
    assert r["files"] == 1
    assert ("subject", "Foley/Chains") in file_tags(db, "Chains 1.wav")
    assert c.get("/api/plans").json()["plans"][0]["progress"] == {"t1": 1, "p1": 1, "a1": 1}
    assert sessions.adopt(db, sc, str(folder)) is None
    assert sorted(os.listdir(folder)) == sorted([n for n, _ in files] + ["kurator-session.json"])
    # a folder only browsed is left alone
    other = lib.parent / "elsewhere"
    other.mkdir()
    (other / "kurator-session.json").write_text(json.dumps(full))
    assert sessions.adopt(db, sc, str(other)) is None


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
    assert p["shots"] == [{"id": "s1", "name": "Pot lid", "tags": ["Objects/Kitchen"], "takes": 99, "ucs": "", "notes": "", "kind": ""}]
    assert plans.unpack(p["link"].split("#plan=")[1])["name"] == "Kitchen"
    got = c.post("/api/plans/from-link", json={"link": plans.DEFAULT_URL + "#plan=" + BALLISTIC_LINK}, headers={"origin": BASE})
    assert got.status_code == 200 and got.json()["name"] == "Lesní procházka"
    assert {x["name"] for x in c.get("/api/plans").json()["plans"]} == {"Kitchen", "Lesní procházka"}
    assert c.delete(f"/api/plans/{p['id']}", headers={"origin": BASE}).json() == {"deleted": True}
    assert c.post("/api/plans/from-link", json={"link": "https://example.com/"}, headers={"origin": BASE}).status_code == 400

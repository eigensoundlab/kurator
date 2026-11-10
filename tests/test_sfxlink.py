"""The SFX Workbench link: sounds out to SFX Workbench (a web app at another address), results back
as new files - only for pages from SFX Workbench's address, only with the link's token."""

from __future__ import annotations

import hashlib
import math
import os
import struct
import time
import wave

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator import fileinfo, sfxlink, tags
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}
SFX = "https://eigensoundlab.github.io"
X = {"origin": SFX, "sec-fetch-site": "cross-site", "sec-fetch-mode": "cors"}


def wait(sc, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        st = sc.status()
        if st["phase"] == "idle" and st["finished"]:
            return
        time.sleep(0.05)
    raise TimeoutError


def tone(path, secs=0.25, sr=22050, hz=440):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / sr))) for i in range(int(secs * sr))))


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    (lib / "foley").mkdir(parents=True)
    (lib / "other").mkdir()
    tone(lib / "foley" / "gravel.wav")
    tone(lib / "other" / "door.wav", hz=220)
    Image.new("RGB", (20, 20), "red").save(lib / "other" / "pic.png")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    wait(sc)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    ids = dict(zip(*(lambda it: (it["n"], it["ids"]))(c.get("/api/items").json())))
    return c, db, sc, lib, ids


def share(c, **body):
    return c.post("/api/sfx/share", json=body, headers=H)


def token_of(url):
    frag = url.split("#", 1)[1]
    parts = dict(p.split("=", 1) for p in frag.split("&"))
    assert parts["kurator"] == "http%3A%2F%2F127.0.0.1%3A8765"
    return parts["link"]


def test_link_out_and_back(env):
    c, db, sc, lib, ids = env
    proj = tags.create_tag(db, "Eryxian Music", facet="project")
    # only sounds; results only into the library
    assert share(c, ids=[ids["pic.png"]], dest=str(lib / "foley")).status_code == 400
    assert share(c, ids=[ids["gravel.wav"]], dest=str(lib.parent / "elsewhere")).status_code == 400
    assert share(c, ids=[ids["gravel.wav"]], dest=str(lib / "foley"), tags=[999]).status_code == 400
    r = share(c, ids=[ids["gravel.wav"], ids["door.wav"]], dest=str(lib / "foley"), tags=[proj["id"]]).json()
    assert r["url"].startswith("https://eigensoundlab.github.io/SFXWorkbench/#kurator=") and r["files"] == 2
    tok = token_of(r["url"])
    stored = db.get_setting("sfx_links")
    assert list(stored) == [hashlib.sha256(tok.encode()).hexdigest()] and tok not in str(stored)   # only its hash is kept

    # what SFX Workbench sees, as SFX Workbench only
    m = c.get(f"/api/sfx/link/{tok}", headers=X)
    assert m.status_code == 200 and m.headers["access-control-allow-origin"] == SFX
    m = m.json()
    assert [f["name"] for f in m["files"]] == ["gravel.wav", "door.wav"]
    assert m["files"][0]["duration"] == pytest.approx(0.25, abs=0.01) and m["files"][0]["sample_rate"] == 22050
    assert m["dest"] == "foley" and m["tags"] == ["SFX Workbench", "◆ Eryxian Music"]
    for bad in ({}, H, {"origin": "https://evil.example"}, {"origin": "null"}):
        assert c.get(f"/api/sfx/link/{tok}", headers=bad).status_code == 403
    assert c.get("/api/sfx/link/not-a-token", headers=X).status_code == 404
    f = c.get(f"/api/sfx/link/{tok}/file/1", headers=X)
    assert f.content == (lib / "other" / "door.wav").read_bytes() and f.headers["access-control-allow-origin"] == SFX
    assert c.get(f"/api/sfx/link/{tok}/file/2", headers=X).status_code == 400
    pre = c.options(f"/api/sfx/link/{tok}/save", headers={**X, "access-control-request-method": "POST",
                                                          "access-control-request-headers": "content-type"})
    assert pre.status_code == 204 and "POST" in pre.headers["access-control-allow-methods"]
    assert pre.headers["access-control-allow-private-network"] == "true"

    # results come back as new files: tagged, with their source, never over another file
    body = (lib / "foley" / "gravel.wav").read_bytes()
    up = lambda name, src="0": c.post(f"/api/sfx/link/{tok}/save", params={"name": name, "src": src}, content=body,
                                     headers={**X, "content-type": "audio/wav"})
    a = up("gravel.wav").json()
    assert a["name"] == "gravel (2).wav" and a["shown"] and (lib / "foley" / "gravel.wav").read_bytes() == body
    b = up("../../../escape.wav", "0,1").json()
    assert b["name"] == "escape.wav" and (lib / "foley" / "escape.wav").exists()
    assert up("notes.txt").status_code == 400 and up("pic.png").status_code == 400
    assert c.post(f"/api/sfx/link/{tok}/save", params={"name": "e.wav"}, content=b"", headers=X).status_code == 400
    assert not [p for p in os.listdir(lib / "foley") if p.endswith(".part")]
    own = db.get_setting("sfx_tag")
    t = tags.get_tag(db, own)
    assert t["name"] == "SFX Workbench" and t["facet"] == "source"
    got = {r[0] for r in db.cat.execute("SELECT tag_id FROM file_tags WHERE file_id = ?", (b["id"],))}
    assert got == {own, proj["id"]}
    assert fileinfo.get(db, a["id"])["source"] == "SFX Workbench, from gravel.wav"
    assert fileinfo.get(db, b["id"])["source"] == "SFX Workbench, from gravel.wav, door.wav"
    assert c.get("/api/state").json()["sfx"]["n"] >= 2
    wait(sc)
    assert c.get(f"/api/items/{b['id']}").json()["name"] == "escape.wav"
    # the tag keeps being the same one when renamed
    tags.update_tag(db, own, name="Made in SFX Workbench")
    assert sfxlink._own_tag(db) == own

    # links: listed, ended all at once
    assert c.get("/api/sfx/links").json()["links"][0]["made"] == 2
    assert c.delete("/api/sfx/links", headers=H).json()["ended"] == 1
    assert c.get(f"/api/sfx/link/{tok}", headers=X).status_code == 404


def test_new_sounds_link_and_expiry(env, monkeypatch):
    c, db, sc, lib, ids = env
    r = share(c, ids=[], dest=str(lib / "other" / "new"), label="New sounds").json()   # a new folder is fine
    tok = token_of(r["url"])
    assert c.get(f"/api/sfx/link/{tok}", headers=X).json()["files"] == []
    up = c.post(f"/api/sfx/link/{tok}/save", params={"name": "whoosh.wav"}, content=(lib / "foley" / "gravel.wav").read_bytes(), headers=X)
    assert up.json()["name"] == "whoosh.wav" and (lib / "other" / "new" / "whoosh.wav").exists()
    assert fileinfo.get(db, up.json()["id"])["source"] == "SFX Workbench"
    later = time.time() + sfxlink.TTL + 5
    monkeypatch.setattr(sfxlink.time, "time", lambda: later)
    assert c.get(f"/api/sfx/link/{tok}", headers=X).status_code == 404


def test_address_setting(env):
    c, db, sc, lib, ids = env
    s = c.get("/api/settings").json()
    assert s["sfx_url"] == s["sfx_default"] == sfxlink.DEFAULT_URL
    assert c.put("/api/settings", json={"sfx_url": "not an address"}, headers=H).status_code == 400
    c.put("/api/settings", json={"sfx_url": "http://localhost:8080/"}, headers=H)
    tok = token_of(share(c, ids=[ids["gravel.wav"]], dest=str(lib / "foley")).json()["url"])
    assert c.get(f"/api/sfx/link/{tok}", headers={"origin": "http://localhost:8080"}).status_code == 200
    assert c.get(f"/api/sfx/link/{tok}", headers=X).status_code == 403
    # the offline file: pages from a file send "null"
    off = os.path.join(str(lib.parent), "sfx-workbench.html")
    c.put("/api/settings", json={"sfx_url": off}, headers=H)
    assert sfxlink.address(db).startswith("file:///") and sfxlink.origin_of(sfxlink.address(db)) == "null"
    assert c.get(f"/api/sfx/link/{tok}", headers={"origin": "null"}).status_code == 200
    c.put("/api/settings", json={"sfx_url": ""}, headers=H)
    assert db.get_setting("sfx_url") is None
    assert sfxlink.origin_of("https://Example.org:443/x/") == "https://example.org"
    assert sfxlink.origin_of("http://[::1]:8080/") == "http://[::1]:8080"

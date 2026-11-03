"""Move rules, XMP sidecars, keyword import, sidecar dates."""

import os
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator import rules, xmp
from kurator.db import Database
from kurator.processing import process_file
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    (lib / "inbox").mkdir(parents=True)
    for i in range(4):
        p = lib / "inbox" / f"f{i}.png"
        Image.new("RGB", (10, 10), (i * 50, 0, 0)).save(p)
        ts = datetime(2019 + i, 5, 1).timestamp()
        os.utime(p, (ts, ts))
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 30:
        time.sleep(0.05)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    it = c.get("/api/items").json()
    return c, db, lib, dict(zip(it["n"], it["ids"]))


def tag(c, name):
    return c.post("/api/tags", json={"name": name}, headers=H).json()["id"]


def test_render_template():
    ts = datetime(2021, 3, 4).timestamp()
    sep = os.sep
    out = rules.render(f"{sep}art{sep}{{tag}}{sep}{{subtag}}{sep}{{year}}", ts, "Creatures", ["Creatures/Dragons/Red"])
    assert out == os.path.normpath(f"{sep}art{sep}Creatures{sep}Dragons{sep}2021")
    out = rules.render(f"{sep}art{sep}{{tag}}{sep}{{subtag}}{sep}{{year}}-{{month}}", ts, "Creatures", ["Creatures"])
    assert out == os.path.normpath(f"{sep}art{sep}Creatures{sep}2021-03")  # empty {subtag} dropped


def test_rules_plan_apply_undo(env):
    c, db, lib, ids = env
    cre, dra, ico = tag(c, "Creatures"), tag(c, "Creatures/Dragons"), tag(c, "Icons")
    c.post(f"/api/tags/{dra}/apply", json={"ids": [ids["f0.png"]]}, headers=H)
    c.post(f"/api/tags/{cre}/apply", json={"ids": [ids["f1.png"]]}, headers=H)
    c.post(f"/api/tags/{ico}/apply", json={"ids": [ids["f2.png"], ids["f1.png"]]}, headers=H)
    bad = c.put("/api/rules", json={"rules": [{"tag_id": cre, "dest": str(lib / "{nope}")}]}, headers=H)
    assert bad.status_code == 400
    r = c.put("/api/rules", json={"rules": [
        {"tag_id": cre, "dest": str(lib / "Creatures" / "{subtag}" / "{year}")},
        {"tag_id": ico, "dest": str(lib / "Icons"), "date_prefix": True},
    ]}, headers=H)
    assert r.status_code == 200
    plan = c.post("/api/rules/plan", json={"ids": list(ids.values())}, headers=H).json()
    assert plan["count"] == 3 and plan["per_rule"] == {"0": 2, "1": 1}
    assert any(s["reason"] == "no matching rule" for s in plan["skipped"])  # f3 untagged
    res = c.post("/api/rules/apply", json={"ids": list(ids.values())}, headers=H).json()
    assert res["moved"] == 3
    assert (lib / "Creatures" / "Dragons" / "2019" / "f0.png").exists()
    assert (lib / "Creatures" / "2020" / "f1.png").exists()       # first rule wins over Icons
    assert (lib / "Icons" / "2021-05-01_f2.png").exists()
    c.post(f"/api/operations/{res['op_id']}/undo", headers=H)
    assert all((lib / "inbox" / f"f{i}.png").exists() for i in range(4))


def test_sidecars_write_import_and_date(env, tmp_path):
    c, db, lib, ids = env
    dra = tag(c, "Creatures/Dragons")
    c.post(f"/api/tags/{dra}/apply", json={"ids": [ids["f0.png"], ids["f1.png"]]}, headers=H)
    foreign = lib / "inbox" / "f1.xmp"
    foreign.write_text("<x:xmpmeta>made by another app</x:xmpmeta>")
    r = c.post("/api/xmp/write", json={"ids": list(ids.values())}, headers=H).json()
    assert r["written"] == 1 and len(r["skipped_foreign"]) == 1 and r["skipped_untagged"] == 2
    assert foreign.read_text().startswith("<x:xmpmeta>made by")  # never overwritten
    side = (lib / "inbox" / "f0.png.xmp").read_text(encoding="utf-8")
    assert "Creatures|Dragons" in side and "<rdf:li>Dragons</rdf:li>" in side and xmp.NS in side
    assert xmp.keywords_from_xmp(side) == ["Creatures/Dragons"]
    again = c.post("/api/xmp/write", json={"ids": [ids["f0.png"]]}, headers=H).json()
    assert again["unchanged"] == 1

    # the sidecar keeps the date even if the file's own timestamps are lost (copy, cloud sync)
    p0 = lib / "inbox" / "f0.png"
    os.utime(p0, None)
    r0 = process_file(str(p0))
    assert r0["date_source"] == "sidecar" and datetime.fromtimestamp(r0["date_ts"]).year == 2019


def test_import_windows_tags_and_xmp(tmp_path):
    p = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[0x9C9E] = "cat;Items/Swords".encode("utf-16-le") + b"\x00\x00"
    Image.new("RGB", (8, 8)).save(p, exif=exif)
    (tmp_path / "photo.jpg.xmp").write_text(
        '<x:xmpmeta><lr:hierarchicalSubject><rdf:Bag><rdf:li>Creatures|Orcs</rdf:li></rdf:Bag>'
        '</lr:hierarchicalSubject><dc:subject><rdf:Bag><rdf:li>Creatures</rdf:li><rdf:li>Orcs</rdf:li>'
        '<rdf:li>moody</rdf:li></rdf:Bag></dc:subject></x:xmpmeta>')
    kws = xmp.read_keywords(str(p))
    assert set(kws) == {"cat", "Items/Swords", "Creatures/Orcs", "moody"}
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(str(tmp_path), "library")
    Scanner(db, workers=1).walk_root(rid, str(tmp_path))
    fid = db.cat.execute("SELECT id FROM files").fetchone()[0]
    res = xmp.import_keywords(db, [fid])
    assert res["applied"] == 4 and res["with_keywords"] == 1
    names = {r[0] for r in db.cat.execute("SELECT name FROM tags")}
    assert {"cat", "Items", "Items/Swords", "Creatures", "Creatures/Orcs", "moody"} <= names
    assert process_file(str(p))["meta"]["exif"]["XPKeywords"] == "cat;Items/Swords"


def test_auto_sidecars(env):
    c, db, lib, ids = env
    c.put("/api/settings", json={"xmp_auto": True}, headers=H)
    t = tag(c, "Icons")
    c.post(f"/api/tags/{t}/apply", json={"ids": [ids["f3.png"]]}, headers=H)
    p = lib / "inbox" / "f3.png.xmp"
    deadline = time.time() + 5
    while not p.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert "Icons" in p.read_text(encoding="utf-8")

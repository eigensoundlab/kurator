"""Every supported format gets indexed with a thumbnail; type groups; audio metadata; SVG safety."""

import os
import time

from fastapi.testclient import TestClient

from format_samples import make_all
from kurator.db import Database
from kurator.handlers import handler_for, supported_types
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"


def test_all_formats_index(tmp_path):
    lib = tmp_path / "lib"
    expected = make_all(str(lib))
    assert len(expected) >= 20
    for name, group in expected.items():
        assert handler_for(os.path.splitext(name)[1]).group == group, name
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 120:
        time.sleep(0.1)
    rows = {r["name"]: dict(r) for r in db.cat.execute("SELECT * FROM files")}
    assert set(rows) == set(expected)
    bad = {n: r["error"] for n, r in rows.items() if r["status"] != 1}
    assert not bad, bad
    for n, r in rows.items():
        assert db.has_thumb(r["hash"], 256), n
    import json
    wav = json.loads(rows["DOORWood_Creak Slow_Test.wav"]["meta"])
    assert wav["bwf"]["description"].startswith("Wooden door") and wav["ucs_catid"] == "DOORWood"
    assert rows["DOORWood_Creak Slow_Test.wav"]["date_source"] == "document"  # BWF origination date
    assert len(wav["peaks"]) > 100 and wav["duration"] == 0.5
    assert rows["DOORWood_Creak Slow_Test.wav"]["kind"] == "audio" and rows["DOORWood_Creak Slow_Test.wav"]["width"] is None
    assert json.loads(rows["comic.clip"]["meta"])["canvas"] == "2480 × 3508"
    assert json.loads(rows["photo.afphoto"]["meta"])["placeholder"] is True
    assert rows["vector.svg"]["width"] == 200

    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    groups = {g["key"]: g["exts"] for g in c.get("/api/state").json()["types"]["groups"]}
    assert {"image", "work", "vector", "document", "texture", "audio"} <= set(groups)
    it = c.get("/api/items", params={"ext": ",".join(groups["audio"])}).json()
    assert all(n.split(".")[-1] in groups["audio"] for n in it["n"]) and it["count"] >= 2
    svg_id = rows["vector.svg"]["id"]
    r = c.get(f"/f/{svg_id}")
    assert r.headers["content-type"].startswith("image/svg+xml") and "sandbox" in r.headers["content-security-policy"]
    wav_id = rows["DOORWood_Creak Slow_Test.wav"]["id"]
    assert c.get(f"/f/{wav_id}").headers["content-type"] == "audio/wav"


def test_types_registry_has_no_duplicates():
    t = supported_types()
    seen = [e for g in t["groups"] for e in g["exts"]]
    assert len(seen) == len(set(seen))

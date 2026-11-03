from fastapi.testclient import TestClient
from PIL import Image

from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def make(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    Image.new("RGB", (300, 200), "green").save(lib / "x.jpg")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib))
    sc = Scanner(db, workers=1)
    sc.walk_root(1, str(lib))  # catalog only; thumbnails are made on demand
    return TestClient(create_app(db, sc, PORT), base_url=BASE), lib


def test_items_and_on_demand_thumb(tmp_path):
    c, _ = make(tmp_path)
    items = c.get("/api/items").json()
    assert items["count"] == 1
    r = c.get(f"/t/{items['ids'][0]}?s=256")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert c.get(f"/t/{items['ids'][0]}?s=999").status_code == 400
    # after on-demand processing the item has a content hash -> immutable cache URL
    items = c.get("/api/items").json()
    v = items["v"][0]
    r = c.get(f"/t/{items['ids'][0]}?s=256&v={v}")
    assert "immutable" in r.headers["cache-control"]
    assert c.get(f"/f/{items['ids'][0]}").headers["content-type"] == "image/jpeg"


def test_security_rejects_foreign_host_and_origin(tmp_path):
    c, lib = make(tmp_path)
    assert c.get("/api/state", headers={"host": "evil.example"}).status_code == 403
    r = c.post("/api/roots", json={"path": str(lib)}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/roots", json={"path": str(lib)})  # no Origin header -> rejected too
    assert r.status_code == 403
    r = c.post("/api/roots", json={"path": str(lib / "nope")}, headers={"origin": BASE})
    assert r.status_code == 400  # not a folder
    assert c.get("/f/999999").status_code == 404
    assert "default-src 'self'" in c.get("/").headers["content-security-policy"]

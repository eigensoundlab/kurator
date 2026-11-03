"""Contact sheets (PDF / PNG pages) and joined images (horizontal, vertical, grid, target size)."""

import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    sizes = [(400, 300), (200, 400), (300, 300), (640, 480), (100, 50)]
    for k, s in enumerate(sizes):
        Image.new("RGB", s, (40 * k, 100, 200 - 30 * k)).save(lib / f"img{k}.png")
    Image.new("RGBA", (120, 120), (255, 0, 0, 0)).save(lib / "clear.png")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 60:
        time.sleep(0.05)
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    out = tmp_path / "out"
    out.mkdir()
    return TestClient(create_app(db, sc, 8765), base_url=BASE), ids, out


def test_join_layouts(env):
    c, ids, out = env
    five = [ids[f"img{k}.png"] for k in range(5)]
    r = c.post("/api/sheets/join", headers=H, json={"ids": five[:3], "layout": "horizontal", "gap": 10, "dest": str(out), "name": "row"}).json()
    im = Image.open(r["files"][0])
    assert im.height == 300 and im.width == 400 + 150 + 300 + 20      # all scaled to the smallest height
    r = c.post("/api/sheets/join", headers=H, json={"ids": five[:2], "layout": "vertical", "size": "largest", "dest": str(out), "name": "col"}).json()
    assert Image.open(r["files"][0]).size == (400, 300 + 800)
    r = c.post("/api/sheets/join", headers=H, json={"ids": five, "layout": "grid", "columns": 2, "target_width": 500,
                                                    "dest": str(out), "name": "grid", "format": "jpg"}).json()
    assert r["files"][0].endswith(".jpg") and Image.open(r["files"][0]).width == 500
    r = c.post("/api/sheets/join", headers=H, json={"ids": [ids["clear.png"], five[0]], "transparent": True, "dest": str(out), "name": "t"}).json()
    assert Image.open(r["files"][0]).getpixel((0, 0))[3] == 0
    again = c.post("/api/sheets/join", headers=H, json={"ids": five[:3], "dest": str(out), "name": "row"}).json()
    assert again["files"][0].endswith("row_2.png")                    # never overwrites
    p = c.post("/api/sheets/join", headers=H, json={"ids": five, "preview": True})
    assert p.headers["content-type"] == "image/png" and "x" in p.headers["x-size"]


def test_contact_sheet(env):
    c, ids, out = env
    allids = list(ids.values()) * 8                                    # 48 images
    p = c.post("/api/sheets/contact", headers=H, json={"ids": allids, "columns": 4, "caption": "name_date", "title": "Sheet", "preview": True})
    assert p.status_code == 200 and int(p.headers["x-pages"]) >= 2
    r = c.post("/api/sheets/contact", headers=H, json={"ids": allids, "columns": 4, "dest": str(out), "name": "sheet"}).json()
    assert r["files"][0].endswith(".pdf") and r["pages"] >= 2
    with open(r["files"][0], "rb") as f:
        assert f.read(5) == b"%PDF-"
    r = c.post("/api/sheets/contact", headers=H, json={"ids": allids[:6], "page": "custom", "width": 1200, "height": 800,
                                                       "format": "png", "dest": str(out), "name": "web", "background": "dark"}).json()
    assert Image.open(r["files"][0]).size == (1200, 800)
    assert c.post("/api/sheets/contact", headers=H, json={"ids": []}).status_code == 400

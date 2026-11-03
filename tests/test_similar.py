"""Similar images: dHash survives resizing and re-saving; groups; backfill for older catalogs."""

import io
import random
import time

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter

from kurator import similar
from kurator.db import Database
from kurator.processing import dhash
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"


def picture(seed, size=(400, 300)):
    rnd = random.Random(seed)
    im = Image.new("RGB", size, tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(12):
        x0, y0 = rnd.randrange(size[0]), rnd.randrange(size[1])
        d.ellipse((x0, y0, x0 + rnd.randrange(30, 160), y0 + rnd.randrange(30, 160)), fill=tuple(rnd.randrange(256) for _ in range(3)))
    return im


def test_hash_is_robust_and_discriminating():
    a = picture(1)
    small = a.resize((133, 100))
    buf = io.BytesIO()
    a.save(buf, "JPEG", quality=40)
    jpeg = Image.open(io.BytesIO(buf.getvalue()))
    blurred = a.filter(ImageFilter.GaussianBlur(1.5))
    d = lambda x, y: (similar._u(dhash(x)) ^ similar._u(dhash(y))).bit_count()  # noqa: E731
    assert d(a, small) <= 6 and d(a, jpeg) <= 6 and d(a, blurred) <= 8
    others = [d(a, picture(s)) for s in range(2, 30)]
    assert min(others) > 12


def test_groups():
    items = [(1, 0b1011 << 20), (2, (0b1011 << 20) | 1), (3, 0x5555_5555_5555_5555), (4, 0x5555_5555_5555_5554),
             (5, -0x1234_5678_9ABC_DEF0), (6, 0)]
    assert similar.groups(items, 4) == [[1, 2], [3, 4]]


def test_view_and_backfill(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for s in range(6):
        picture(s).save(lib / f"p{s}.png")
    picture(0).resize((200, 150)).save(lib / "p0_small.jpg", quality=80)          # similar to p0
    picture(3).filter(ImageFilter.GaussianBlur(1)).save(lib / "p3_soft.png")       # similar to p3
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 60:
        time.sleep(0.05)
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    out = c.get("/api/items", params={"similar": 8}).json()
    groups = {}
    for n, g in zip(out["n"], out["g"]):
        groups.setdefault(g, set()).add(n)
    assert sorted(map(sorted, groups.values())) == [["p0.png", "p0_small.jpg"], ["p3.png", "p3_soft.png"]]
    # an older catalog without hashes: filled in from the thumbnail cache
    db.cat.execute("UPDATE files SET phash = NULL")
    st = c.get("/api/similar/status").json()
    assert st["pending"] == 8 or st["running"]
    t = time.time()
    while c.get("/api/similar/status").json()["pending"] and time.time() - t < 30:
        time.sleep(0.05)
    out2 = c.get("/api/items", params={"similar": 8}).json()
    assert sorted(out2["n"]) == sorted(out["n"])

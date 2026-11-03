import io
import os
import shutil
import time
import zipfile
from datetime import datetime

import pytest
from PIL import Image, PngImagePlugin

from kurator.db import Database
from kurator.processing import process_file
from kurator.scanner import Scanner


def _wait(scanner, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        st = scanner.status()
        if st["phase"] == "error":
            raise AssertionError(st)
        if st["phase"] == "idle" and st["finished"]:
            return st
        time.sleep(0.1)
    raise TimeoutError


@pytest.fixture()
def lib(tmp_path):
    root = tmp_path / "lib"
    (root / "a" / "deep").mkdir(parents=True)
    (root / ".hidden").mkdir()
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = "2019:04:12 15:30:00"
    Image.new("RGB", (640, 480), "red").save(root / "a" / "photo.jpg", exif=exif)
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a dragon\nSteps: 20, Sampler: Euler")
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(root / "a" / "deep" / "icon.png", pnginfo=info)
    Image.new("RGB", (50, 50)).save(root / "2020-02-02 sketch.jfif", "JPEG")
    Image.new("RGB", (50, 50)).save(root / ".hidden" / "skip.png")
    (root / "notes.txt").write_text("not an image")
    (root / "broken.png").write_bytes(b"\x89PNG broken")
    buf = io.BytesIO()
    Image.new("RGBA", (100, 80), "blue").save(buf, "PNG")
    with zipfile.ZipFile(root / "paint.kra", "w") as z:
        z.writestr("mergedimage.png", buf.getvalue())
        z.writestr("documentinfo.xml", "<creation-date>2018-06-01T10:20:30</creation-date>")
    return root


def test_process_file_keeps_metadata(lib):
    r = process_file(str(lib / "a" / "deep" / "icon.png"))
    assert "error" not in r
    assert r["meta"]["generation"]["tool"] == "A1111-compatible"
    assert r["alpha"] is True
    assert 256 in r["thumbs"] and r["thumbs"][256][:4] == b"RIFF"


def test_scan_and_rescan(lib, tmp_path):
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib))
    sc = Scanner(db, workers=2)
    sc.request_scan()
    _wait(sc)
    rows = {r["name"]: dict(r) for r in db.cat.execute("SELECT * FROM files")}
    assert set(rows) == {"photo.jpg", "icon.png", "2020-02-02 sketch.jfif", "broken.png", "paint.kra"}
    assert rows["photo.jpg"]["date_source"] == "exif-original"
    assert datetime.fromtimestamp(rows["photo.jpg"]["date_ts"]) == datetime(2019, 4, 12, 15, 30)
    assert rows["2020-02-02 sketch.jfif"]["date_source"] == "filename"
    assert rows["paint.kra"]["date_source"] == "document"
    assert rows["paint.kra"]["width"] == 100
    assert rows["broken.png"]["status"] == 2
    assert rows["photo.jpg"]["status"] == 1 and db.has_thumb(rows["photo.jpg"]["hash"], 256)

    # Originals are never modified by indexing
    st_before = os.stat(lib / "a" / "photo.jpg").st_mtime_ns

    # Move a file outside the app + delete one -> rescan follows
    shutil.move(str(lib / "a" / "photo.jpg"), str(lib / "photo_moved.jpg"))
    os.remove(lib / "broken.png")
    sc.request_scan()
    _wait(sc)
    names = {r[0] for r in db.cat.execute("SELECT name FROM files")}
    assert "photo_moved.jpg" in names and "photo.jpg" not in names and "broken.png" not in names
    moved = db.cat.execute("SELECT hash, date_source FROM files WHERE name='photo_moved.jpg'").fetchone()
    assert moved[0] == rows["photo.jpg"]["hash"]  # same content hash -> same thumbnail, tags can follow
    assert moved[1] == "exif-original"
    assert os.stat(lib / "photo_moved.jpg").st_mtime_ns == st_before

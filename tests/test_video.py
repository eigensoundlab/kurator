"""Video: thumbnails, hover strip, metadata and dates, playback with range requests, no-ffmpeg fallback."""

import json
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from kurator.db import Database
from kurator.handlers import handler_for, video
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


def make(path, *args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(path)], check=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("vid")
    make(d / "phone.mp4", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=4", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         "-metadata", "creation_time=2023-04-05T06:07:08Z")
    make(d / "loop.webm", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2", "-c:v", "libvpx-vp9", "-b:v", "200k")
    make(d / "old.avi", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2", "-c:v", "mpeg4")
    return d


def test_handler(clips):
    p = str(clips / "phone.mp4")
    loaded = handler_for(".mp4").load(None, p, 256)
    assert loaded.size == (640, 360) and loaded.meta["preview"] == "frame" and loaded.meta["strip"] == 8
    vi = loaded.meta["video_info"]
    assert vi["duration"] == pytest.approx(4, abs=0.1) and vi["video_codec"] == "h264" and vi["audio_codec"] == "aac" and vi["fps"] == 25
    assert loaded.dates["document"] == pytest.approx(1680674828)
    strip = loaded.extra[video.STRIP]
    assert strip.height == video.STRIP_HEIGHT and strip.width == 8 * 256
    assert max(loaded.image.size) <= 640   # never upscaled


def test_without_ffmpeg(clips, monkeypatch):
    monkeypatch.setattr(video, "tool", lambda name: None)
    loaded = handler_for(".mp4").load(None, str(clips / "phone.mp4"), 256)
    assert loaded.meta["placeholder"] is True and "ffmpeg" in loaded.meta["preview_note"]
    assert loaded.size == (640, 360) and loaded.meta["video_info"]["duration"] == pytest.approx(4, abs=0.1)
    assert loaded.dates["document"] == pytest.approx(1680674828)   # MP4 'mvhd' read by Kurator itself


def test_index_strip_and_playback(clips, tmp_path):
    db = Database(str(tmp_path / "data"))
    db.add_root(str(clips), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 120:
        time.sleep(0.1)
    rows = {r["name"]: dict(r) for r in db.cat.execute("SELECT * FROM files")}
    assert {n: r["status"] for n, r in rows.items()} == {"phone.mp4": 1, "loop.webm": 1, "old.avi": 1}
    assert rows["phone.mp4"]["kind"] == "video" and rows["phone.mp4"]["width"] == 640
    assert rows["phone.mp4"]["date_source"] == "document"
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    groups = {g["key"]: g["exts"] for g in c.get("/api/state").json()["types"]["groups"]}
    assert {"mp4", "webm", "avi", "mkv"} <= set(groups["video"])
    vid = rows["phone.mp4"]["id"]
    r = c.get(f"/t/{vid}", params={"s": 1})
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    r = c.get(f"/f/{vid}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers["content-type"] == "video/mp4"
    assert c.get(f"/f/{rows['old.avi']['id']}").status_code == 415   # AVI: open in a player
    meta = json.loads(rows["loop.webm"]["meta"])
    assert meta["video_info"]["video_codec"] == "vp9"


def test_previews_remade_when_ffmpeg_arrives(clips, tmp_path, monkeypatch):
    """Indexed without ffmpeg -> card; with ffmpeg later the previews are remade and the URL changes."""
    monkeypatch.setattr(video, "tool", lambda name: None)
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(str(clips), "library")
    sc = Scanner(db, workers=1)
    sc.walk_root(rid, str(clips))

    def process():   # in this process (worker processes wouldn't see the monkeypatch)
        from kurator.processing import process_file
        db.store_results([process_file(r[0], (256,)) for r in db.cat.execute("SELECT path FROM files WHERE status = 0")])
    process()
    c = TestClient(create_app(db, sc, 8765), base_url=BASE)
    before = c.get("/api/items").json()
    monkeypatch.undo()
    n = db.remake_previews("kind = 'video' AND json_extract(meta, '$.placeholder') IS NOT NULL")
    assert n == 3
    process()
    after = c.get("/api/items").json()
    assert [v.split(".")[0] for v in before["v"]] == [v.split(".")[0] for v in after["v"]]   # same content
    assert all(v.endswith(".1") for v in after["v"]) and before["v"] != after["v"]
    vid = after["ids"][after["n"].index("phone.mp4")]
    r = c.get(f"/t/{vid}", params={"s": 256, "v": after["v"][after["n"].index("phone.mp4")]})
    assert "immutable" in r.headers["cache-control"] and r.headers["etag"].endswith('-256-1"')
    assert c.get(f"/t/{vid}", params={"s": 1}).status_code == 200

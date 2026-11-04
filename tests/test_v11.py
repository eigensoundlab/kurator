"""v0.11: sound analysis (loudness, tempo, key), waveform data, colour / shape / sound filters,
sprite sheets and texture atlases."""

import json
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from PIL import Image

from kurator import atlas
from kurator.audio_analysis import analyse, key, loudness, mono, tempo
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}
SR = 44100
NOTES = {"C": 261.63, "D": 293.66, "E": 329.63, "F": 349.23, "G": 392.0, "A": 440.0, "B": 493.88}


def _sine(freq, seconds, amp):
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def _beat(bpm, seconds, chord):
    """Clicks on every beat over a soft chord - a stand-in for a piece of music."""
    n = int(SR * seconds)
    t = np.arange(n) / SR
    x = sum(0.08 * np.sin(2 * np.pi * NOTES[c] * t) + 0.04 * np.sin(2 * np.pi * NOTES[c] / 2 * t) for c in chord)
    step = 60 / bpm
    for k in range(int(seconds / step)):
        s = int(k * step * SR)
        d = np.arange(min(1500, n - s))
        x[s:s + len(d)] += 0.6 * np.exp(-d / 200) * np.sin(2 * np.pi * 1800 * d / SR)
    return x


def test_loudness_of_a_sine(tmp_path):
    p = tmp_path / "tone.wav"
    sf.write(p, _sine(1000, 10, 0.1), SR)          # -20 dBFS peak → -23 LUFS by the BS.1770 calibration
    r = loudness(str(p))
    assert abs(r["lufs"] - (-23.0)) < 0.3
    sf.write(p, np.zeros(SR * 3), SR)               # silence has no loudness
    assert loudness(str(p))["lufs"] is None


@pytest.mark.parametrize("bpm", [96, 128])
def test_tempo_and_key(tmp_path, bpm):
    p = tmp_path / f"beat{bpm}.wav"
    sf.write(p, _beat(bpm, 20, "CEG"), SR)
    x, sr = mono(str(p), 11025, 120)
    b, conf = tempo(x, sr)
    assert b is not None and abs(b - bpm) <= 1 and conf > 0.15
    name, _ = key(x, sr)
    assert name in ("C major", "A minor", "E minor")       # C-E-G: C major or a close relative
    a = analyse(str(p), 20.0)
    assert "lufs" in a and abs(a["bpm"] - bpm) <= 1
    assert "bpm" not in analyse(str(p), 3.0)               # short sounds get loudness only


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    Image.new("RGB", (400, 200), (220, 30, 30)).save(lib / "red_wide.png")
    Image.new("RGB", (200, 400), (30, 60, 220)).save(lib / "blue_tall.png")
    Image.new("RGB", (2400, 2400), (30, 180, 60)).save(lib / "green_big.png")
    sprite = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    sprite.paste((250, 220, 0, 255), (16, 8, 48, 56))
    sprite.save(lib / "coin.png")
    frames = [Image.new("RGBA", (32, 32), (k * 60, 100, 200, 255)) for k in range(4)]
    frames[0].save(lib / "walk.gif", save_all=True, append_images=frames[1:], duration=80, loop=0)
    sf.write(lib / "song.wav", _beat(120, 12, "ACE"), SR)
    sf.write(lib / "hit.wav", _sine(300, 0.5, 0.5), SR)
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 90:
        time.sleep(0.05)
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    out = tmp_path / "out"
    out.mkdir()
    return TestClient(create_app(db, sc, 8765), base_url=BASE), ids, out, db


def _names(c, ids, **params):
    r = c.get("/api/items", params=params).json()
    back = {v: k for k, v in ids.items()}
    return {back[i] for i in r["ids"]}


def test_colour_shape_and_sound_filters(env):
    c, ids, _, db = env
    hues = dict(db.cat.execute("SELECT name, hues FROM files"))
    assert hues["red_wide.png"] & 1                          # bit 0 = red
    assert _names(c, ids, hues=1) == {"red_wide.png"}
    assert "blue_tall.png" in _names(c, ids, hues=(1 << 8) | (1 << 7))
    assert _names(c, ids, shape="landscape") == {"red_wide.png"}
    assert _names(c, ids, shape="panorama") == {"red_wide.png"}
    assert {"blue_tall.png"} <= _names(c, ids, shape="portrait")
    assert _names(c, ids, minside=2048) == {"green_big.png"}
    assert "coin.png" in _names(c, ids, alpha=1) and "red_wide.png" not in _names(c, ids, alpha=1)
    assert _names(c, ids, len_min=5) == {"song.wav"}
    assert _names(c, ids, len_max=1) == {"hit.wav"}
    song = c.get(f"/api/items/{ids['song.wav']}").json()
    assert abs(song["meta"]["bpm"] - 120) <= 1 and song["meta"]["lufs"] < 0
    assert _names(c, ids, bpm_min=110, bpm_max=130) == {"song.wav"}
    assert not _names(c, ids, bpm_min=140)
    red = c.get(f"/api/items/{ids['red_wide.png']}").json()
    assert red["meta"]["palette"][0][0].lower().startswith("#d") or red["meta"]["palette"][0][0].lower().startswith("#e")
    assert c.get("/api/items", params={"sort": "bpm"}).status_code == 200


def test_wave_and_spectrogram_endpoints(env):
    c, ids, _, _ = env
    w = c.get(f"/api/audio/{ids['song.wav']}/wave", params={"bins": 500}).json()
    assert w["bins"] <= 500 and w["channels"] == 1 and abs(w["duration"] - 12) < 0.01
    assert max(w["max"][0]) > 0.5 and min(w["min"][0]) < -0.5
    s = c.get(f"/api/audio/{ids['song.wav']}/spectrogram", params={"w": 400, "h": 128})
    assert s.headers["content-type"] == "image/png"
    assert c.get(f"/api/audio/{ids['red_wide.png']}/wave").status_code == 415


def test_texture_atlas(env):
    c, ids, out, _ = env
    pick = [ids["coin.png"], ids["red_wide.png"], ids["blue_tall.png"]]
    p = c.post("/api/sheets/atlas", headers=H, json={"ids": pick, "preview": True})
    assert p.headers["content-type"] == "image/png" and p.headers["x-sprites"] == "3"
    r = c.post("/api/sheets/atlas", headers=H, json={"ids": pick, "padding": 2, "extrude": 1, "pot": True,
                                                     "dest": str(out), "name": "atlas"}).json()
    png, js = r["files"]
    im = Image.open(png)
    w, h = im.size
    assert w & (w - 1) == 0 and h & (h - 1) == 0           # power of two
    data = json.load(open(js))
    assert data["meta"]["image"] == "atlas.png" and data["meta"]["size"] == {"w": w, "h": h}
    coin = data["frames"]["coin.png"]
    assert coin["trimmed"] and coin["frame"]["w"] == 32 and coin["frame"]["h"] == 48
    assert coin["spriteSourceSize"]["x"] == 16 and coin["sourceSize"] == {"w": 64, "h": 64}
    f = coin["frame"]
    assert im.getpixel((f["x"] + 5, f["y"] + 5))[:3] == (250, 220, 0)
    assert im.getpixel((f["x"] - 1, f["y"] + 5))[:3] == (250, 220, 0)      # extruded edge
    boxes = [(v["frame"]["x"], v["frame"]["y"], v["frame"]["w"], v["frame"]["h"]) for v in data["frames"].values()]
    for i, a in enumerate(boxes):                                          # nothing overlaps, padding kept
        for b in boxes[i + 1:]:
            assert a[0] + a[2] + 2 <= b[0] or b[0] + b[2] + 2 <= a[0] or a[1] + a[3] + 2 <= b[1] or b[1] + b[3] + 2 <= a[1]


def test_sprite_sheet_splits_animations(env):
    c, ids, out, _ = env
    r = c.post("/api/sheets/atlas", headers=H, json={"ids": [ids["walk.gif"]], "mode": "frames", "columns": 4, "padding": 0,
                                                     "dest": str(out), "name": "walk"}).json()
    assert r["sprites"] == 4 and r["columns"] == 4
    im = Image.open(r["files"][0])
    assert im.size == (128, 32)
    data = json.load(open(r["files"][1]))
    assert data["animations"]["walk"] == [f"walk_{k:03d}.png" for k in range(4)]
    assert data["frames"]["walk_002.png"]["frame"] == {"x": 64, "y": 0, "w": 32, "h": 32}
    r2 = c.post("/api/sheets/atlas", headers=H, json={"ids": [ids["walk.gif"]], "mode": "frames", "dest": str(out), "name": "walk"}).json()
    assert r2["files"][0].endswith("walk_2.png") and r2["files"][1].endswith("walk_2.json")   # never overwrites
    bad = c.post("/api/sheets/atlas", headers=H, json={"ids": [ids["hit.wav"]]})
    assert bad.status_code == 400


def test_maxrects_packs_without_overlap():
    import random
    random.seed(3)
    sizes = [(random.randint(4, 90), random.randint(4, 90)) for _ in range(300)]
    W, H, places = atlas._pack(sizes, False, 4096)
    rects = [(x, y, w, h) for (x, y), (w, h) in zip(places, sizes)]
    assert all(x + w <= W and y + h <= H for x, y, w, h in rects)
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
    assert sum(w * h for w, h in sizes) / (W * H) > 0.8


def test_backfill_fills_colours_of_an_older_catalog(env):
    from kurator.similar import Backfill
    c, ids, _, db = env
    db.cat.execute("UPDATE files SET hues = NULL, meta = json_remove(meta, '$.palette')")
    b = Backfill(db)
    assert b.pending() >= 5
    b._run()
    hues, meta = db.cat.execute("SELECT hues, meta FROM files WHERE id = ?", (ids["red_wide.png"],)).fetchone()
    assert hues & 1 and json.loads(meta)["palette"]
    assert _names(c, ids, hues=1) == {"red_wide.png"}


def test_saving_needs_a_folder(env, tmp_path, monkeypatch):
    c, ids, _, _ = env
    monkeypatch.chdir(tmp_path)
    for kind in ("atlas", "join"):
        r = c.post(f"/api/sheets/{kind}", headers=H, json={"ids": [ids["coin.png"]], "dest": "  "})
        assert r.status_code == 400 and "folder" in r.json()["detail"]
    assert not list(tmp_path.glob("*.png"))

"""v0.17: rows and detail for long sounds (ranged waveform / spectrogram), Playhead across the library
(summaries, the Look filter's sound character, sorts, the command-line job, grid strips), M4A thumbnails."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from kurator import audio_analysis, playhead_index
from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}
SR = 8000
FFMPEG = shutil.which("ffmpeg")


def _scan(db):
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t0 = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t0 < 60:
        time.sleep(0.05)
    sc.stop()
    return sc


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("KURATOR_PLAYHEAD", str(tmp_path / "no-playhead-here"))   # no command line unless a test adds one
    root = tmp_path / "lib"
    root.mkdir()
    t = np.arange(SR * 40) / SR                                                   # 40 s: a click every 5 s
    x = np.zeros_like(t, dtype=np.float32)
    for k in range(0, 40, 5):
        x[k * SR:k * SR + 40] = 0.9
    sf.write(str(root / "clicks.wav"), x, SR, subtype="FLOAT")
    sf.write(str(root / "tone.wav"), (0.5 * np.sin(2 * np.pi * 440 * t[:SR * 2])).astype(np.float32), SR)
    sf.write(str(root / "hiss.wav"), np.random.default_rng(1).normal(0, 0.1, SR * 3).astype(np.float32), SR)
    db = Database(str(tmp_path / "data"))
    db.add_root(str(root), "library")
    sc = _scan(db)
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    return root, db, ids, TestClient(create_app(db, sc, 8765), base_url=BASE)


# ---------------------------------------------------------------------------------- rows: detail on demand
def test_wave_part(lib):
    root, db, ids, c = lib
    whole = c.get(f"/api/audio/{ids['clicks.wav']}/wave", params={"bins": 400}).json()
    assert whole["start"] == 0 and whole["end"] == pytest.approx(40) and whole["bins"] == 400
    r = c.get(f"/api/audio/{ids['clicks.wav']}/wave", params={"bins": 12000, "start": 4.9, "end": 5.1})
    d = r.json()
    assert d["start"] == pytest.approx(4.9) and d["end"] == pytest.approx(5.1)
    assert d["bins"] == int(0.2 * SR)                       # never more bins than samples (1600 here)
    mx = np.array(d["max"][0])
    assert mx.max() == pytest.approx(0.9) and np.argmax(mx) == int(0.1 * SR)   # the click at 5.0 s
    assert c.get(f"/api/audio/{ids['clicks.wav']}/wave", params={"bins": 99999, "start": 0, "end": 40}).json()["bins"] == 16000
    assert c.get(f"/api/audio/{ids['clicks.wav']}/wave", params={"bins": 99999}).json()["bins"] == 8000
    for bad in ({"start": -1}, {"start": 5, "end": 5}, {"start": 5, "end": 2}, {"start": "nan"}):
        assert c.get(f"/api/audio/{ids['clicks.wav']}/wave", params=bad).status_code == 422


def test_spectrogram_part_and_reference(lib):
    root, db, ids, c = lib
    r = c.get(f"/api/audio/{ids['clicks.wav']}/spectrogram", params={"w": 400, "h": 64})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "content-encoding" not in r.headers
    assert float(r.headers["x-start"]) == 0 and float(r.headers["x-end"]) == pytest.approx(40)
    ref = float(r.headers["x-ref"])
    p = c.get(f"/api/audio/{ids['clicks.wav']}/spectrogram", params={"w": 400, "h": 64, "start": 10, "end": 20, "ref": ref})
    assert float(p.headers["x-start"]) == 10 and float(p.headers["x-end"]) == 20 and float(p.headers["x-ref"]) == pytest.approx(ref, abs=1e-3)
    assert c.get(f"/api/audio/{ids['clicks.wav']}/spectrogram", params={"ref": 1e9}).status_code == 422


def test_spectrogram_whole_long_sound_by_seeking(tmp_path, monkeypatch):
    """A long part is read one window per column; the picture matches reading it all at once."""
    t = np.arange(SR * 30) / SR
    x = (0.3 * np.sin(2 * np.pi * (100 + 20 * t) * t)).astype(np.float32)
    f = str(tmp_path / "sweep.wav")
    sf.write(f, x, SR)
    seq = audio_analysis._spec_windows(f, 200, 2048, 0.0, None)
    monkeypatch.setattr(audio_analysis, "_SEQ_LIMIT", 1000)
    seek = audio_analysis._spec_windows(f, 200, 2048, 0.0, None)
    assert seq[2:] == seek[2:] == (0.0, 30.0)
    assert np.allclose(seq[0], seek[0], atol=1e-6)
    png, a, b, top = audio_analysis.spectrogram(f, 300, 64)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and (a, b) == (0.0, 30.0)


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_ffmpeg_only_sound(tmp_path, monkeypatch):
    """AAC in MP4 with its index at the end (ffmpeg's default): the thumbnail reads it from a file, and its
    spectrogram covers at most MAX_ANALYSE_SECONDS from where it starts."""
    from kurator.handlers import audio as ah
    wav, m4a = str(tmp_path / "t.wav"), str(tmp_path / "t.m4a")
    sf.write(wav, (0.3 * np.sin(2 * np.pi * 330 * np.arange(22050 * 8) / 22050)).astype(np.float32), 22050)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", wav, "-c:a", "aac", m4a], check=True)
    with open(m4a, "rb") as fh:
        data = fh.read()
    a, sr, _, dur = ah._decode(data, ".m4a")
    assert a is not None and dur == pytest.approx(8, abs=0.2) and np.abs(a).max() > 0.2
    monkeypatch.setattr(audio_analysis, "MAX_ANALYSE_SECONDS", 3)
    png, s0, s1, _ = audio_analysis.spectrogram(m4a, 200, 64, 2.0, None)
    assert s0 == 2.0 and s1 == pytest.approx(5.0, abs=0.05)


# ---------------------------------------------------------------------------------- Playhead documents
def _doc(name="x", version="0.2.0", duration=2.0, arrow=None, burst=None, material=None, reliable=True, t60=None,
         kurt=None, coherence=None, harsh=0.3, tempo=None, sections=None, events=1, columns=None):
    n = int(duration * 100)
    cols = columns or n
    return {
        "schema": "playhead-analysis", "schemaVersion": 1, "generator": {"name": "playhead", "version": version},
        "source": {"name": name, "sampleRate": 44100, "channels": 1, "duration": duration},
        "profile": "music" if tempo else "sfx", "assetClass": "sfx",
        "summary": {"peakLufs": -12.0, "loudnessRange": 6.5, "attack": 0.01},
        "curves": {"rate": 100, "start": 0, "harshness": [harsh] * n, "loudness": [None] * 5 + [0.5] * (n - 5)},
        "wave": {"columns": 1000, "min": [-0.5] * 1000, "max": [0.5] * 1000, "rms": [0.2] * 1000},
        "spectrum": {"scale": "mel", "bands": 4, "fmin": 40, "fmax": 16000, "columns": cols, "dbTop": -10, "dbRange": 80,
                     "encoding": "base64-u8", "data": base64.b64encode(bytes(range(4)) * cols).decode()},
        "events": [{"start": 0.1 * i, "end": 0.1 * i + 0.05, "tag": "N", "word": "hit"} for i in range(events)],
        "rhythm": {"tempo": tempo, "reliable": True, "downbeats": [0.0, 2.0]} if tempo else None,
        "sections": [{"start": 0, "end": 1, "label": "A"}, {"start": 1, "end": 2, "label": "B"}] if sections else None,
        "descriptors": {"kurtosis": {"K": kurt}, "events": {"burstiness": burst, "rate": 1.0},
                        "arrow": {"T20": arrow}, "periodicity": {"coherence": coherence}, "repetition": {"hz": None},
                        "decay": {"t60": t60, "logTauD": None, "material": material or "", "reliable": reliable}},
    }


def test_check_and_summarize():
    d = _doc(arrow=5.0, burst=0.3, material="metal-like", t60=2.0, kurt=12, coherence=0.9, tempo=120, sections=True)
    assert playhead_index.check(json.dumps(d).encode(), "0.2.0")["schema"] == "playhead-analysis"
    for bad, msg in ((b"{", "not JSON"), (json.dumps({"schema": "x"}).encode(), "not a Playhead"),
                     (json.dumps({**d, "generator": {}}).encode(), "no Playhead version")):
        with pytest.raises(ValueError, match=msg):
            playhead_index.check(bad)
    with pytest.raises(ValueError, match="doesn't match"):
        playhead_index.check(json.dumps(d).encode(), "0.3.0")
    s = playhead_index.summarize(d)
    assert s["arrow"] == 5.0 and s["burst"] == 0.3 and s["material"] == "metal-like" and s["mat_ok"] == 1.0
    assert s["t60"] == 2.0 and s["kurt"] == 12 and s["tonal"] == 0.9 and s["harsh"] == pytest.approx(0.3)
    assert s["tempo"] == 120 and s["tempo_ok"] == 1.0 and s["sections"] == 2 and s["form"] == "AB" and s["events"] == 1
    assert s["lra"] == 6.5 and s["version"] == "0.2.0"
    empty = playhead_index.summarize({"schema": "playhead-analysis", "schemaVersion": 1, "descriptors": None, "curves": None})
    assert all(v is None for v in empty.values())


def test_thin_keeps_it_drawable():
    d = _doc(duration=60, columns=1200)
    t = playhead_index.thin(d, points=300)
    c = t["curves"]
    assert len(c["harshness"]) == 300 and c["rate"] * 60 == pytest.approx(300)       # length / rate = duration
    assert c["loudness"][0] == pytest.approx(0.5)                                  # nulls skipped in the mean
    assert t["wave"]["columns"] == 300 and len(t["wave"]["max"]) == 300 and t["wave"]["max"][0] == 0.5
    sp = t["spectrum"]
    assert sp["columns"] == 300 and len(base64.b64decode(sp["data"])) == 300 * 4
    assert base64.b64decode(sp["data"])[:4] == bytes(range(4))
    assert t["events"] == d["events"] and t["source"] == d["source"]
    small = _doc(duration=1.0)
    assert playhead_index.thin(small, 300)["curves"] == small["curves"]               # short curves stay as they are


def test_store_prune(tmp_path):
    d = str(tmp_path / "ph")
    for i in range(5):
        playhead_index.save(d, os.path.join(d, f"{i}.json"), b"x" * 1000)
        os.utime(os.path.join(d, f"{i}.json"), (i, i))
    assert playhead_index.prune(d, limit=2500) == 3
    assert sorted(os.listdir(d)) == ["3.json", "4.json"]


def _put(c, fid, doc):
    return c.put(f"/api/audio/{fid}/playhead", params={"v": doc["generator"]["version"]}, content=json.dumps(doc), headers=H)


def _names(c, **params):
    return sorted(c.get("/api/items", params={"stacks": "none", **params}).json()["n"])


def test_summaries_filters_and_sorts(lib):
    root, db, ids, c = lib
    assert c.get("/api/playhead/status").json()["analysed"] == 0
    assert _put(c, ids["clicks.wav"], _doc("clicks", duration=40, arrow=3.0, burst=-0.5, t60=0.1, kurt=40, coherence=0.05, harsh=0.9)).status_code == 200
    assert _put(c, ids["tone.wav"], _doc("tone", arrow=0.0, material="metal-like", t60=1.5, kurt=-1, coherence=1.0, harsh=0.2)).status_code == 200
    st = c.get("/api/playhead/status").json()
    assert (st["sounds"], st["analysed"], st["missing"]) == (3, 2, 1) and st["cli"] is None and not st["job"]["running"]
    assert st["filters"]["metal"]["label"] == "Metal-like"
    assert _names(c, ph="hit") == ["clicks.wav"]
    assert _names(c, ph="regular") == ["clicks.wav"]
    assert _names(c, ph="metal") == ["tone.wav"] and _names(c, ph="ring") == ["tone.wav"] and _names(c, ph="dry") == ["clicks.wav"]
    assert _names(c, ph="tonal") == ["tone.wav"] and _names(c, ph="noisy") == ["clicks.wav"]
    assert _names(c, ph="hit,metal") == []                                      # groups combine
    assert _names(c, ph="tonal,noisy") == ["clicks.wav", "tone.wav"]            # alternatives in a group
    assert _names(c, ph="harsh") == ["clicks.wav"] and _names(c, ph="impulsive") == ["clicks.wav"]   # the top quarter
    assert _names(c, ph="unanalysed") == ["hiss.wav"] and _names(c, ph="analysed") == ["clicks.wav", "tone.wav"]
    assert _names(c, ph="nonsense") == ["clicks.wav", "hiss.wav", "tone.wav"]
    n = c.get("/api/items", params={"sort": "harshness", "order": "desc", "stacks": "none"}).json()["n"]
    assert n == ["clicks.wav", "tone.wav", "hiss.wav"]                          # without an analysis: last
    n = c.get("/api/items", params={"sort": "harshness", "order": "asc", "stacks": "none"}).json()["n"]
    assert n == ["tone.wav", "clicks.wav", "hiss.wav"]
    n = c.get("/api/items", params={"sort": "ring", "order": "desc", "stacks": "none"}).json()["n"]
    assert n[:2] == ["tone.wav", "clicks.wav"]
    # a changed file: its summary no longer counts
    db.cat.execute("UPDATE files SET mtime_ns = mtime_ns + 1 WHERE id = ?", (ids["tone.wav"],))
    assert _names(c, ph="metal") == [] and c.get("/api/playhead/status").json()["analysed"] == 1


def test_grid_strips_and_saved_views(lib):
    root, db, ids, c = lib
    _put(c, ids["clicks.wav"], _doc("clicks", duration=40, columns=800))
    out = c.get("/api/items", params={"phinfo": 1, "stacks": "none"}).json()
    stamps = dict(zip(out["n"], out["p"]))
    assert stamps["clicks.wav"] > 0 and stamps["tone.wav"] == 0
    assert "p" not in c.get("/api/items").json()
    r = c.get(f"/api/audio/{ids['clicks.wav']}/playhead/thumb")
    assert r.status_code == 200 and float(r.headers["x-duration"]) == pytest.approx(40, abs=0.01)
    t = r.json()
    assert t["schema"] == "playhead-analysis" and len(t["curves"]["harshness"]) == 320 and t["spectrum"]["columns"] == 320
    assert c.get(f"/api/audio/{ids['tone.wav']}/playhead/thumb").status_code == 404
    v = c.post("/api/views", json={"name": "metal", "query": {"ph": "metal,ring", "sort": "ring"}}, headers=H)
    assert v.status_code == 200
    saved = [x for x in c.get("/api/views").json() if x["name"] == "metal"][0]
    assert saved["query"] == {"ph": "metal,ring", "sort": "ring"}


def test_backfill_from_016_store(lib):
    root, db, ids, c = lib
    path, mt = db.cat.execute("SELECT path, mtime_ns FROM files WHERE id = ?", (ids["tone.wav"],)).fetchone()
    store = os.path.join(db.data_dir, "cache", "playhead")
    os.makedirs(store, exist_ok=True)
    with open(playhead_index.doc_file(store, path, mt, "0.1.9"), "w") as fh:
        json.dump(_doc("tone", version="0.1.9", material="glass-like", t60=1.2), fh)
    assert playhead_index.backfill(db, store) == 1
    assert _names(c, ph="glass") == ["tone.wav"]
    assert playhead_index.backfill(db, store) == 0                             # once


def test_migration_from_schema_10(tmp_path):
    db = Database(str(tmp_path / "d"))
    db.cat.execute("DROP TABLE ph_summary")
    db.cat.execute("UPDATE settings SET value = '10' WHERE key = 'schema_version'")
    db2 = Database(str(tmp_path / "d"))
    assert db2.cat.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()[0] == "11"
    cols = [r[1] for r in db2.cat.execute("PRAGMA table_info(ph_summary)")]
    assert cols[:2] == ["file_id", "mtime_ns"] and set(playhead_index.COLUMNS) <= set(cols)


# ---------------------------------------------------------------------------------- the command line
FAKE = r'''#!{python}
"""A stand-in for the Playhead command line: version, and analyze - (raw float32 on stdin)."""
import json, sys
a = sys.argv[1:]
if a and a[0] == "version":
    print("9.9.9"); sys.exit(0)
o = {{a[i][2:]: a[i + 1] for i in range(2, len(a) - 1, 2)}}
raw = sys.stdin.buffer.read()
if "bad" in o.get("name", ""):
    sys.stderr.write("playhead: cannot analyse this\n"); sys.exit(1)
ch, sr = int(o["channels"]), float(o["sr"])
dur = len(raw) / 4 / ch / sr
ring = "tone" in o["name"]
print(json.dumps({{"schema": "playhead-analysis", "schemaVersion": 1, "generator": {{"name": "playhead", "version": "9.9.9"}},
    "source": {{"name": o["name"], "sampleRate": sr, "channels": ch, "duration": dur}}, "profile": o["profile"],
    "curves": {{"rate": 100, "harshness": [0.5] * 10}},
    "descriptors": {{"arrow": {{"T20": 2.0 if ring else -2.0}},
                    "decay": {{"t60": 1.5 if ring else None, "material": "metal-like" if ring else "", "reliable": True}}}}}}))
'''


@pytest.mark.skipif(sys.platform == "win32", reason="a script with a #! line")
def test_command_line_job(lib, tmp_path, monkeypatch):
    root, db, ids, c = lib
    sf.write(str(root / "bad.wav"), np.zeros(SR, np.float32), SR)
    _scan(db)
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    exe = tmp_path / "playhead"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("KURATOR_PLAYHEAD", str(exe))
    st = c.get("/api/playhead/status").json()
    assert st["cli"] == {"path": str(exe), "version": "9.9.9"} and st["missing"] == 4
    assert c.post("/api/playhead/analyse", json={"scope": "nope"}, headers=H).status_code == 422
    assert c.post("/api/playhead/analyse", json={"scope": "missing"}).status_code == 403          # no Origin
    r = c.post("/api/playhead/analyse", json={"scope": "missing"}, headers=H)
    assert r.status_code == 200 and r.json()["total"] == 4
    t0 = time.time()
    while c.get("/api/playhead/status").json()["job"]["running"] and time.time() - t0 < 60:
        time.sleep(0.1)
    st = c.get("/api/playhead/status").json()
    job = st["job"]
    assert (job["done"], job["failed"], job["total"]) == (3, 1, 4) and "cannot analyse" in job["errors"][0]
    assert st["analysed"] == 3 and st["missing"] == 1
    doc = c.get(f"/api/audio/{ids['tone.wav']}/playhead", params={"v": "9.9.9"}).json()
    assert doc["source"]["duration"] == pytest.approx(2.0) and doc["source"]["channels"] == 1 and doc["profile"] == "auto"
    clicks = c.get(f"/api/audio/{ids['clicks.wav']}/playhead", params={"v": "9.9.9"}).json()["source"]
    assert clicks["duration"] == pytest.approx(40, abs=0.01)                     # under 120 s: the file's own rate
    assert _names(c, ph="metal") == ["tone.wav"] and _names(c, ph="swell") == ["clicks.wav", "hiss.wav"]
    # again, for given sounds
    r = c.post("/api/playhead/analyse", json={"scope": "ids", "ids": [ids["hiss.wav"]]}, headers=H)
    assert r.status_code == 200 and r.json()["total"] == 1
    t0 = time.time()
    while c.get("/api/playhead/status").json()["job"]["running"] and time.time() - t0 < 30:
        time.sleep(0.05)
    assert c.get("/api/playhead/status").json()["job"]["done"] == 1


def test_command_line_missing(lib):
    root, db, ids, c = lib
    assert playhead_index.find_cli() is None
    r = c.post("/api/playhead/analyse", json={"scope": "all"}, headers=H)
    assert r.status_code == 409 and "isn't installed" in r.json()["detail"]
    todo = c.get("/api/playhead/todo").json()["items"]
    assert sorted(x[2] for x in todo) == ["clicks.wav", "hiss.wav", "tone.wav"] and all(x[1] > 0 for x in todo)

"""Playhead analysis support (v0.16): decoded samples for the browser, and the document store."""

from __future__ import annotations

import json
import os
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from kurator.db import Database
from kurator.scanner import Scanner
from kurator.server import create_app

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}
SR = 8000


@pytest.fixture()
def lib(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    t = np.arange(SR * 2) / SR                                    # 2 s
    left = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    right = np.linspace(-1, 1, len(t)).astype(np.float32)
    sf.write(str(root / "tone.wav"), np.stack([left, right], 1), SR, subtype="FLOAT")
    six = np.tile(np.arange(6, dtype=np.float32)[None, :] / 10, (SR // 2, 1))
    sf.write(str(root / "six.wav"), six, SR, subtype="FLOAT")    # 6 channels
    sf.write(str(root / "long.wav"), np.zeros(SR * 5, np.float32), SR)
    from PIL import Image
    Image.new("RGB", (8, 8)).save(root / "pic.png")
    db = Database(str(tmp_path / "data"))
    db.add_root(str(root), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t0 = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t0 < 60:
        time.sleep(0.05)
    sc.stop()
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    return root, db, ids, TestClient(create_app(db, sc, 8765), base_url=BASE), left, right


def _f32(r) -> np.ndarray:
    return np.frombuffer(r.content, "<f4")


def test_samples_native_stereo(lib):
    root, db, ids, c, left, right = lib
    r = c.get(f"/api/audio/{ids['tone.wav']}/samples")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-samplerate"] == str(SR) and r.headers["x-channels"] == "2"
    assert r.headers["x-frames"] == str(2 * SR) and r.headers["x-truncated"] == "0"
    assert "content-encoding" not in r.headers                    # not gzipped
    a = _f32(r)
    assert len(r.content) == 2 * SR * 2 * 4
    a = a.reshape(-1, 2)
    assert np.allclose(a[:, 0], left) and np.allclose(a[:, 1], right)


def test_samples_mono_rate_and_limits(lib):
    root, db, ids, c, left, right = lib
    r = c.get(f"/api/audio/{ids['tone.wav']}/samples", params={"mono": 1, "rate": 4000})
    assert r.headers["x-samplerate"] == "4000" and r.headers["x-channels"] == "1" and r.headers["x-frames"] == str(SR)
    a = _f32(r)
    ref = ((left + right) / 2).reshape(-1, 2).mean(axis=1)           # the box filter of mono()
    assert len(a) == SR and np.allclose(a, ref, atol=1e-6)
    r = c.get(f"/api/audio/{ids['tone.wav']}/samples", params={"rate": 3000})   # factor round(8000/3000) = 3
    assert r.headers["x-samplerate"] == "2666.6666666666665" and r.headers["x-channels"] == "2"
    assert len(_f32(r)) == int(r.headers["x-frames"]) * 2 == (2 * SR // 3) * 2
    r = c.get(f"/api/audio/{ids['long.wav']}/samples", params={"max_seconds": 1.5})
    assert r.headers["x-truncated"] == "1" and r.headers["x-frames"] == str(int(1.5 * SR))
    assert len(_f32(r)) == int(1.5 * SR)
    r = c.get(f"/api/audio/{ids['six.wav']}/samples")                # at most two channels
    a = _f32(r).reshape(-1, 2)
    assert r.headers["x-channels"] == "2" and np.allclose(a[0], [0.0, 0.1])
    r = c.get(f"/api/audio/{ids['six.wav']}/samples", params={"mono": 1})
    assert np.allclose(_f32(r)[:4], 0.25)                           # the mean of all six


def test_samples_errors(lib):
    root, db, ids, c, *_ = lib
    assert c.get(f"/api/audio/{ids['pic.png']}/samples").status_code == 415
    assert c.get("/api/audio/99999/samples").status_code == 404
    os.remove(root / "long.wav")
    assert c.get(f"/api/audio/{ids['long.wav']}/samples").status_code == 404
    assert c.get(f"/api/audio/{ids['tone.wav']}/samples", params={"max_seconds": 0}).status_code == 422


def _doc(version="0.2.0", **extra):
    d = {"schema": "playhead-analysis", "schemaVersion": 1, "generator": {"name": "playhead", "version": version},
         "profile": "sfx", "source": {"duration": 2.0}, "summary": {"peakLufs": -12.0}, "curves": {"rate": 100}}
    d.update(extra)
    return d


def test_playhead_document_store(lib):
    root, db, ids, c, *_ = lib
    tid = ids["tone.wav"]
    url = f"/api/audio/{tid}/playhead"
    assert c.get(url, params={"v": "0.2.0"}).status_code == 404
    doc = _doc(futureField={"x": 1})                                  # unknown fields are kept as they are
    r = c.put(url, params={"v": "0.2.0"}, content=json.dumps(doc), headers=H)
    assert r.status_code == 200, r.text
    got = c.get(url, params={"v": "0.2.0"})
    assert got.status_code == 200 and got.json() == doc
    assert c.get(url, params={"v": "0.3.0"}).status_code == 404       # another Playhead version: analyse again
    assert os.listdir(os.path.join(db.data_dir, "cache", "playhead"))
    # the file changes: the stored document is stale
    st = os.stat(root / "tone.wav")
    os.utime(root / "tone.wav", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    with db.write_lock:
        db.cat.execute("UPDATE files SET mtime_ns = ? WHERE id = ?", (st.st_mtime_ns + 5_000_000_000, tid))
    assert c.get(url, params={"v": "0.2.0"}).status_code == 404


def test_playhead_document_store_refuses(lib):
    root, db, ids, c, *_ = lib
    url = f"/api/audio/{ids['tone.wav']}/playhead"
    put = lambda body, v="0.2.0", **kw: c.put(url, params={"v": v}, content=body, headers={**H, **kw})  # noqa: E731
    assert put(json.dumps(_doc(schema="other"))).status_code == 400
    assert put(json.dumps({**_doc(), "schemaVersion": 2})).status_code == 400
    assert put(json.dumps(_doc(version="0.1.9"))).status_code == 400   # not the version it claims
    assert put("not json").status_code == 400
    assert put(json.dumps(_doc()), v="../../etc").status_code == 400
    big = json.dumps(_doc(pad="x" * (8 * 1024 * 1024)))
    assert put(big).status_code == 413
    assert c.put(url, params={"v": "0.2.0"}, content=json.dumps(_doc())).status_code == 403   # no Origin
    assert c.put(f"/api/audio/{ids['pic.png']}/playhead", params={"v": "0.2.0"}, content=json.dumps(_doc()),
                 headers=H).status_code == 415
    assert c.get(url, params={"v": "0.2.0"}).status_code == 404

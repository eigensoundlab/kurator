"""AI: embeddings, search, tag pools (zero-shot / trained / VLM), suggestions review.

Runs offline with a randomly initialised open_clip model (skipped if torch/open_clip aren't installed).
The VLM part talks to a fake Ollama server that checks the request uses a fixed answer list.
"""

import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

torch = pytest.importorskip("torch")
pytest.importorskip("open_clip")

from fastapi.testclient import TestClient  # noqa: E402

from kurator import tags  # noqa: E402
from kurator.ai import AIService  # noqa: E402
from kurator.db import Database  # noqa: E402
from kurator.processing import process_file  # noqa: E402
from kurator.scanner import Scanner  # noqa: E402
from kurator.server import create_app  # noqa: E402

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


def blob(color, seed):
    rnd = random.Random(seed)
    im = Image.new("RGB", (96, 96), color)
    px = im.load()
    for _ in range(400):
        x, y = rnd.randrange(96), rnd.randrange(96)
        px[x, y] = tuple(min(255, max(0, c + rnd.randint(-60, 60))) for c in color)
    return im


@pytest.fixture()
def env(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for i in range(14):
        blob((220, 30, 30), i).save(lib / f"red{i}.png")
        blob((30, 30, 220), 100 + i).save(lib / f"blue{i}.png")
    db = Database(str(tmp_path / "data"))
    rid = db.add_root(str(lib), "library")
    Scanner(db, workers=1).walk_root(rid, str(lib))
    db.store_results([process_file(r[0], (256, 512)) for r in db.cat.execute("SELECT path FROM files")])
    ai = AIService(db)
    ai.save_settings({"model": "custom", "custom_model": "ViT-B-32", "custom_pretrained": ""})  # random weights, offline
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    client = TestClient(create_app(db, Scanner(db), 8765, None, ai), base_url=BASE)
    return db, ai, ids, client


def test_embed_search_similar(env):
    db, ai, ids, c = env
    r = ai.embed()
    assert r["embedded"] == 28
    assert ai.embed()["embedded"] == 0  # cached by content hash
    sim = c.get(f"/api/ai/similar/{ids['red3.png']}").json()
    assert sim["ids"][0] == ids["red3.png"] and sim["score"][0] > 0.99
    top = sim["n"][1:8]
    assert sum(n.startswith("red") for n in top) >= 6  # colour clusters, even with random weights
    s = c.post("/api/ai/search", json={"q": "a red thing"}, headers=H).json()
    assert s["count"] == 28 and "score" in s


def test_pool_zero_shot_accept_reject(env):
    db, ai, ids, c = env
    red = tags.create_tag(db, "Red")
    blue = tags.create_tag(db, "Blue")
    c.put(f"/api/tags/{red['id']}/prompt", json={"prompt": "a red image; something red"}, headers=H)
    tags.apply(db, [ids["red0.png"]], red["id"], "add")
    p = c.post("/api/ai/profiles", json={"name": "Colour", "tag_ids": [red["id"], blue["id"]], "mode": "single",
                                         "method": "zeroshot", "threshold": 0.0}, headers=H).json()
    ai.run_profile(p["id"], list(ids.values()))
    sugg = ai.suggestions_by_file()
    assert ids["red0.png"] not in sugg  # already sorted into the pool
    assert len(sugg) == 27 and all(len(v) == 1 for v in sugg.values())  # single mode: one tag each
    it = c.get("/api/items", params={"suggested": 1}).json()
    assert it["count"] == 27 and all(it["s"])
    one = ids["red1.png"]
    tid = sugg[one][0][0]
    r = c.post("/api/ai/suggestions/accept", json={"ids": [one]}, headers=H).json()
    assert r["accepted"] == 1
    src = db.cat.execute("SELECT source, confidence FROM file_tags WHERE file_id = ?", (one,)).fetchone()
    assert src[0] == "ai" and src[1] is not None
    two = ids["blue1.png"]
    t2 = sugg[two][0][0]
    c.post("/api/ai/suggestions/reject", json={"ids": [two]}, headers=H)
    ai.run_profile(p["id"], [two])
    assert all(t != t2 for t, _ in ai.suggestions_by_file().get(two, []))  # never suggested again
    assert tid in (red["id"], blue["id"])


def test_train_from_your_tags(env):
    db, ai, ids, c = env
    red = tags.create_tag(db, "Red")
    blue = tags.create_tag(db, "Blue")
    tags.apply(db, [ids[f"red{i}.png"] for i in range(10)], red["id"], "add")
    tags.apply(db, [ids[f"blue{i}.png"] for i in range(10)], blue["id"], "add")
    p = ai.save_profile({"name": "Colour", "tag_ids": [red["id"], blue["id"]], "mode": "single", "method": "trained", "threshold": 0.5})
    ai.embed()
    res = c.post(f"/api/ai/profiles/{p['id']}/train", headers=H).json()
    assert res["examples"] == 20 and res["score"] >= 0.75
    ai.run_profile(p["id"], list(ids.values()))
    sugg = ai.suggestions_by_file()
    new = [f"red{i}.png" for i in range(10, 14)] + [f"blue{i}.png" for i in range(10, 14)]
    right = sum(1 for n in new if sugg.get(ids[n]) and sugg[ids[n]][0][0] == (red["id"] if n.startswith("red") else blue["id"]))
    assert right >= 7, right


class _FakeOllama(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send({"models": [{"name": "gemma3:12b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOllama.seen.append(body)
        enum = body["format"]["properties"]["tag"]["enum"]
        assert body["messages"][0]["images"] and "none of these" in enum
        self._send({"message": {"content": json.dumps({"tag": enum[0], "caption": "a red square with noise"})}})

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_vlm_fixed_answer_list_and_captions(env):
    db, ai, ids, c = env
    srv = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ai.save_settings({"ollama_url": f"http://127.0.0.1:{srv.server_port}"})
        assert c.get("/api/ai/ollama").json()["models"] == ["gemma3:12b"]
        red = tags.create_tag(db, "Red")
        blue = tags.create_tag(db, "Blue")
        p = c.post("/api/ai/profiles", json={"name": "V", "tag_ids": [red["id"], blue["id"]], "mode": "single", "method": "vlm",
                                             "vlm_model": "gemma3:12b", "captions": True}, headers=H).json()
        c.post(f"/api/ai/profiles/{p['id']}/run", json={"ids": [ids["red2.png"], ids["blue2.png"]]}, headers=H)
        st = ai.wait(60)
        assert not st["errors"], st["errors"]
        assert st["result"] == {"files": 2, "suggested": 2, "captions": 2}
        assert _FakeOllama.seen[-1]["format"]["properties"]["tag"]["enum"][:2] == ["Red", "Blue"]
        d = c.get(f"/api/items/{ids['red2.png']}").json()
        assert d["captions"][0]["text"] == "a red square with noise"
    finally:
        srv.shutdown()

"""v0.15: Basis Universal textures, HDR pictures (EXR / Radiance), PDF pages, OpenAI-compatible
vision servers, training datasets, source / author / licence, XMP merging, backup schedules."""

from __future__ import annotations

import glob
import io
import json
import os
import struct
import time
import zlib

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kurator import basisu
from kurator.db import Database
from kurator.handlers import handler_for, hdr, ktx2
from kurator.scanner import Scanner
from kurator.server import create_app

BASIS = os.path.join(os.path.dirname(__file__), "data", "basis")


def _ref(path: str) -> np.ndarray:
    return np.array(Image.open(path).convert("RGBA"))


# --------------------------------------------------------------------------------- Basis Universal

@pytest.mark.parametrize("name", sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(BASIS, "*.ktx2"))))
def test_basis_matches_the_official_transcoder(name):
    """Every fixture decodes to exactly the RGBA32 picture `basisu -unpack` made from it."""
    data = open(os.path.join(BASIS, name + ".ktx2"), "rb").read()
    img, facts = ktx2.decode(data)
    mine = np.array(img.convert("RGBA"))
    ref = _ref(os.path.join(BASIS, name + ".png"))
    if not facts.get("alpha"):
        ref[..., 3] = 255
    assert facts["texture"].startswith("Basis Universal ")
    assert mine.shape == ref.shape and np.array_equal(mine, ref)
    level2 = os.path.join(BASIS, name + "_level2.png")
    if os.path.exists(level2):                       # mip levels too
        info = ktx2.parse(data)
        target = max(info["width"] >> 2, info["height"] >> 2)
        assert ktx2._pick_level(info, target) == 2
        img2, _ = ktx2.decode(data, target)
        ref2 = _ref(level2)
        if not facts.get("alpha"):
            ref2[..., 3] = 255
        assert np.array_equal(np.array(img2.convert("RGBA")), ref2)


def test_uastc_fixtures_cover_the_modes():
    seen = set()
    for p in glob.glob(os.path.join(BASIS, "uastc*.ktx2")):
        data = open(p, "rb").read()
        info = ktx2.parse(data)
        for k in range(info["levels"]):
            blocks = np.frombuffer(ktx2._level(data, info, k), np.uint8).reshape(-1, 16)
            seen |= set(np.frombuffer(basisu._UASTC_HUFF_MODES, np.uint8)[blocks[:, 0] & 127].tolist())
    assert seen >= set(range(19)) - {14}           # mode 14 was checked on bigger pictures during development


def test_basis_handler_facts_and_thumbnail_level():
    data = open(os.path.join(BASIS, "etc1s_rgba_mips.ktx2"), "rb").read()
    loaded = handler_for(".ktx2").load(data, "t.ktx2", 16)
    assert loaded.size == (61, 37)                   # the full size, though a smaller mip level was decoded
    assert loaded.image.size == (30, 18) and loaded.meta["alpha"] and loaded.meta["levels"] > 3
    assert loaded.meta["texture"] == "Basis Universal ETC1S" and "placeholder" not in loaded.meta


def test_two_channel_etc1s_is_shown_as_red_green():
    """RRR + GGG channel ids (KTX-Software normal maps): red from the colour slice, green from the second."""
    data = bytearray(open(os.path.join(BASIS, "etc1s_rgba_mips.ktx2"), "rb").read())
    dfd = struct.unpack_from("<I", data, 48)[0]
    data[dfd + 31] = (data[dfd + 31] & 0xF0) | 3
    data[dfd + 47] = (data[dfd + 47] & 0xF0) | 4
    img, facts = ktx2.decode(bytes(data))
    ref = _ref(os.path.join(BASIS, "etc1s_rgba_mips.png"))
    px = np.array(img)
    assert facts["channels"] == "RG" and img.mode == "RGB"
    assert np.array_equal(px[..., 0], ref[..., 0]) and np.array_equal(px[..., 1], ref[..., 3]) and not px[..., 2].any()


def test_damaged_basis_files_never_crash():
    rng = np.random.default_rng(5)
    for p in sorted(glob.glob(os.path.join(BASIS, "*.ktx2")))[:4]:
        good = open(p, "rb").read()
        for _ in range(60):
            bad = bytearray(good)
            for i in rng.integers(0, len(bad), 6):
                bad[i] = int(rng.integers(0, 256))
            if rng.random() < 0.2:
                bad = bad[:int(rng.integers(1, len(bad)))]
            try:
                loaded = handler_for(".ktx2").load(bytes(bad), "x.ktx2", 64)
                assert loaded.image is not None
            except ktx2.KTXError:
                pass


def test_newer_basis_formats_go_through_the_basisu_tool(monkeypatch, tmp_path):
    data = bytearray(open(os.path.join(BASIS, "etc1s_rgb.ktx2"), "rb").read())
    dfd = struct.unpack_from("<I", data, 48)[0]
    data[dfd + 12] = 169                              # pretend: XUASTC LDR
    data[44] = 0
    monkeypatch.setenv("KURATOR_BASISU", "")
    monkeypatch.setattr(ktx2.shutil, "which", lambda name: None)
    loaded = handler_for(".ktx2").load(bytes(data), "x.ktx2", 64)
    assert loaded.meta["placeholder"] and "basisu" in loaded.meta["preview_note"]
    assert loaded.meta["texture"] == "Basis Universal XUASTC LDR"

    calls = []

    def fake_run(cmd, **kw):                          # what `basisu -unpack -no_ktx -output_path DIR FILE` writes
        calls.append(cmd)
        out = cmd[cmd.index("-output_path") + 1]
        Image.new("RGBA", (61, 37), (10, 200, 30, 255)).save(
            os.path.join(out, "t_unpacked_rgba_RGBA32_level_0_face_0_layer0000.png"))
    exe = tmp_path / "basisu.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("KURATOR_BASISU", str(exe))
    monkeypatch.setattr(ktx2.subprocess, "run", fake_run)
    loaded = handler_for(".ktx2").load(bytes(data), "x.ktx2", 64)
    assert calls and calls[0][0] == str(exe) and "-unpack" in calls[0]
    assert loaded.image.mode == "RGB" and loaded.image.getpixel((5, 5)) == (10, 200, 30)


def test_gltf_with_basis_texture_is_textured(tmp_path):
    """KHR_texture_basisu: the base colour map of a glTF is a KTX2 (ETC1S) image."""
    from textured_samples import QUAD_F, QUAD_UV, QUAD_V

    from kurator.mesh3d import texture_summary
    from kurator.mesh3d.formats import read_mesh
    from kurator.mesh3d.render import render

    ktx = open(os.path.join(BASIS, "red_blue_etc1s.ktx2"), "rb").read()
    bufs = [QUAD_V.tobytes(), QUAD_UV.tobytes(), np.array(QUAD_F, np.uint16).tobytes(), ktx]
    blob, views = b"", []
    for b in bufs:
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(b)})
        blob += b + b"\0" * (-len(b) % 4)
    doc = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "extensionsUsed": ["KHR_texture_basisu"], "extensionsRequired": ["KHR_texture_basisu"],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"name": "Paint", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "textures": [{"extensions": {"KHR_texture_basisu": {"source": 0}}}],
        "images": [{"bufferView": 3, "mimeType": "image/ktx2", "name": "paint_albedo"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": 6, "type": "SCALAR"}],
        "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    glb = struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
    m = read_mesh(glb, str(tmp_path / "q.glb"), full=True)
    assert m.materials[0].maps["base"].open(64).size == (16, 16)
    assert texture_summary(m)["textured"]
    img = render(m, 128)
    w, h = img.size
    left, right = img.getpixel((int(w * 0.33), h // 2)), img.getpixel((int(w * 0.62), h // 2))
    assert left[0] > 100 and left[2] < 60 and right[2] > 100 and right[0] < 60


# --------------------------------------------------------------------------------- HDR pictures

def _exr(chans: dict[str, np.ndarray], compression: int = 0) -> bytes:
    """A minimal scanline OpenEXR writer (half or float channels), uncompressed or ZIP."""
    names = sorted(chans)
    h, w = next(iter(chans.values())).shape

    def attr(name, typ, val):
        return name.encode() + b"\0" + typ.encode() + b"\0" + struct.pack("<i", len(val)) + val
    chl = b"".join(n.encode() + b"\0" + struct.pack("<iB3xii", 1 if chans[n].dtype == np.float16 else 2, 0, 1, 1)
                   for n in names) + b"\0"
    head = (b"\x76\x2f\x31\x01" + struct.pack("<I", 2) + attr("channels", "chlist", chl)
            + attr("compression", "compression", bytes([compression]))
            + attr("dataWindow", "box2i", struct.pack("<4i", 0, 0, w - 1, h - 1))
            + attr("displayWindow", "box2i", struct.pack("<4i", 0, 0, w - 1, h - 1))
            + attr("lineOrder", "lineOrder", b"\0") + attr("pixelAspectRatio", "float", struct.pack("<f", 1))
            + attr("screenWindowCenter", "v2f", struct.pack("<2f", 0, 0))
            + attr("screenWindowWidth", "float", struct.pack("<f", 1)) + b"\0")
    per = 16 if compression == 3 else 1
    chunks = []
    for y in range(0, h, per):
        raw = b"".join(chans[n][yy].tobytes() for yy in range(y, min(h, y + per)) for n in names)
        if compression == 3:
            t = np.frombuffer(raw, np.uint8)
            half = (len(t) + 1) // 2
            r = np.concatenate([t[0::2], t[1::2]])[:len(t)]
            r = np.concatenate([r[:half], r[half:]])
            d = r.astype(np.int16)
            d[1:] = (r[1:].astype(np.int16) - r[:-1].astype(np.int16) + 128) & 255
            raw = zlib.compress(d.astype(np.uint8).tobytes())
        chunks.append(struct.pack("<ii", y, len(raw)) + raw)
    table_end = len(head) + 8 * len(chunks)
    offsets, pos = [], table_end
    for c in chunks:
        offsets.append(pos)
        pos += len(c)
    return head + struct.pack(f"<{len(chunks)}Q", *offsets) + b"".join(chunks)


def _radiance(rgb: np.ndarray, rle: bool = True) -> bytes:
    h, w, _ = rgb.shape
    mx = rgb.max(axis=2)
    e = np.where(mx > 1e-32, np.floor(np.log2(np.maximum(mx, 1e-32))) + 1, 0)
    scale = np.where(mx > 1e-32, 256 / np.exp2(e), 0)
    px = np.dstack([np.clip(rgb * scale[..., None], 0, 255), np.where(mx > 1e-32, e + 128, 0)]).astype(np.uint8)
    out = io.BytesIO()
    out.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n" + f"-Y {h} +X {w}\n".encode())
    for row in px:
        if not rle:
            out.write(row.tobytes())
            continue
        out.write(bytes([2, 2, w >> 8, w & 255]))
        for c in range(4):
            vals = row[:, c].tobytes()
            i = 0
            while i < w:                                  # runs of equal bytes, else literal stretches
                j = i
                while j < w and j - i < 127 and vals[j] == vals[i]:
                    j += 1
                if j - i >= 3:
                    out.write(bytes([128 + j - i, vals[i]]))
                    i = j
                else:
                    k = min(w, i + 128)
                    out.write(bytes([k - i]) + vals[i:k])
                    i = k
    return out.getvalue()


def _hdr_scene(h=40, w=56):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return np.dstack([x / w * 8, y / h * 2, np.full((h, w), 0.25, np.float32)])


@pytest.mark.parametrize("compression", [0, 3])
def test_exr_scanline_half_and_float(compression):
    rgb = _hdr_scene()
    chans = {"R": rgb[..., 0].astype(np.float16), "G": rgb[..., 1].astype(np.float16),
             "B": rgb[..., 2].astype(np.float16), "A": np.ones(rgb.shape[:2], np.float32)}
    data = _exr(chans, compression)
    got, facts = hdr.read_exr(data)
    assert facts["compression"] == ("ZIP" if compression else "none") and facts["pixels"] == "float/half"
    for c in "RGB":
        assert np.array_equal(got[c], chans[c].astype(np.float32))
    loaded = handler_for(".exr").load(data, "sky.exr", None)
    assert loaded.size == (56, 40) and loaded.image.mode == "RGB" and loaded.meta["brightest"] > 3
    left, right = loaded.image.getpixel((1, 20)), loaded.image.getpixel((54, 20))
    assert right[0] > left[0] + 80                     # brighter to the right, tone-mapped (not clipped flat)
    small, _ = hdr.read_exr(data, 14)                  # strided read for thumbnails
    assert small["R"].shape == (20, 28) and np.array_equal(small["R"], chans["R"].astype(np.float32)[::2, ::2])


def test_exr_layers_depth_and_unsupported_compression():
    z = np.arange(30 * 20, dtype=np.float32).reshape(20, 30)
    loaded = handler_for(".exr").load(_exr({"Z": z}, 3), "depth.exr", None)
    px = np.asarray(loaded.image)
    assert loaded.meta["data"] and px[0, 0, 0] < 10 and px[-1, -1, 0] > 240
    lay = {"beauty.R": np.full((8, 8), 2, np.float16), "beauty.G": np.zeros((8, 8), np.float16),
           "beauty.B": np.zeros((8, 8), np.float16)}
    img = handler_for(".exr").load(_exr(lay), "l.exr", None).image
    assert img.getpixel((3, 3))[0] > 200 and img.getpixel((3, 3))[1] == 0
    piz = bytearray(_exr(lay))
    piz[piz.index(b"compression\0compression\0") + 28] = 4
    loaded = handler_for(".exr").load(bytes(piz), "p.exr", None)
    assert loaded.meta["placeholder"] and "PIZ" in loaded.meta["preview_note"]


@pytest.mark.parametrize("rle", [True, False])
def test_radiance_hdr(rle):
    rgb = _hdr_scene()
    data = _radiance(rgb, rle)
    got, facts = hdr.read_hdr(data)
    assert got.shape == rgb.shape and facts["format"] == "Radiance HDR"
    assert np.all(np.abs(got - rgb) <= rgb.max(axis=2, keepdims=True) / 200 + 1e-6)   # 8-bit shared exponent
    loaded = handler_for(".hdr").load(data, "sky.hdr", 10)
    assert loaded.size == (56, 40) and loaded.image.size == (28, 20)


# --------------------------------------------------------------------------------- server helpers

BASE = "http://127.0.0.1:8765"
H = {"origin": BASE}


def _index(tmp_path, lib, ai=None):
    db = Database(str(tmp_path / "data"))
    db.add_root(str(lib), "library")
    sc = Scanner(db, workers=1)
    sc.request_scan()
    t = time.time()
    while not (sc.status()["phase"] == "idle" and sc.status()["finished"]) and time.time() - t < 120:
        time.sleep(0.05)
    ids = {r[0]: r[1] for r in db.cat.execute("SELECT name, id FROM files")}
    return db, sc, ids, TestClient(create_app(db, sc, 8765, None, ai), base_url=BASE)


# --------------------------------------------------------------------------------- PDF pages

def test_pdf_pages_in_the_viewer(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    pages = [Image.new("RGB", (300, 200), c) for c in ((220, 30, 30), (30, 200, 40), (40, 40, 220))]
    pages[2] = Image.new("RGB", (200, 400), (40, 40, 220))           # a page of another size
    pages[0].save(lib / "doc.pdf", save_all=True, append_images=pages[1:], resolution=72)
    db, sc, ids, c = _index(tmp_path, lib)
    pid = ids["doc.pdf"]
    item = c.get(f"/api/items/{pid}").json()
    assert item["meta"]["pages"] == 3
    d = c.get(f"/api/items/{pid}/pages").json()
    assert d["count"] == 3 and d["pages"][0] == [400, 267] and d["pages"][2] == [267, 533]
    r = c.get(f"/api/items/{pid}/page/1?s=300")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    assert img.size == (300, 200) and img.getpixel((150, 100))[1] > 150 and img.getpixel((150, 100))[0] < 80
    big = Image.open(io.BytesIO(c.get(f"/api/items/{pid}/page/2?s=3000").content))
    assert big.size == (1500, 3000)                                   # sharper when the viewer zooms in
    info = c.get(f"/api/items/{pid}/page/2/info").json()
    assert info["width"] == 267 and info["size_mm"] == [70.6, 141.1] and "boxes_mm" in info
    assert c.get(f"/api/items/{pid}/page/7").status_code == 404
    assert c.get(f"/api/items/{pid}/page/0?s=99999").status_code == 422
    sc.stop()


# --------------------------------------------------------------------------------- OpenAI-compatible servers

class _FakeOpenAI:
    """A tiny OpenAI-compatible server: /v1/models and /v1/chat/completions (llama.cpp / LM Studio style)."""

    def __init__(self, strict_schema: bool = True):
        import http.server
        import threading
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.requests.append(("GET", self.path, dict(self.headers), None))
                if self.path == "/v1/models":
                    self._send(200, {"object": "list", "data": [{"id": "qwen2.5-vl-7b"}, {"id": "gemma-3-12b"}]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(("POST", self.path, dict(self.headers), body))
                if "response_format" in body and not strict_schema:
                    self._send(400, {"error": {"message": "response_format json_schema is not supported"}})
                    return
                text = '{"tags": ["robot", "invented tag"], "caption": "A small red robot."}'
                if "response_format" not in body:
                    text = "Sure! ```json\n" + text + "\n```"
                self._send(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize("strict", [True, False])
def test_openai_compatible_vision_server(strict):
    from kurator.ai.vlm import OpenAICompatible, VLMError
    srv = _FakeOpenAI(strict)
    try:
        cl = OpenAICompatible(srv.url, key="sk-local")          # no /v1 given: added
        assert cl.url.endswith("/v1") and cl.models() == ["gemma-3-12b", "qwen2.5-vl-7b"]
        r = cl.classify("qwen2.5-vl-7b", b"\xff\xd8jpeg", [("robot", "a machine"), ("human", "")], single=False, caption=True)
        assert r == {"tags": ["robot"], "caption": "A small red robot."}       # invented tags are dropped
        post = [q for q in srv.requests if q[0] == "POST"]
        body = post[0][3]
        assert post[0][1] == "/v1/chat/completions" and post[0][2]["Authorization"] == "Bearer sk-local"
        parts = body["messages"][0]["content"]
        assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert body["response_format"]["json_schema"]["schema"]["properties"]["tags"]["items"]["enum"] == ["robot", "human"]
        assert len(post) == (1 if strict else 2) and ("response_format" not in post[-1][3]) == (not strict)
    finally:
        srv.close()
    with pytest.raises(VLMError, match="No OpenAI-compatible server"):
        OpenAICompatible(srv.url + "/v1").models()
    with pytest.raises(VLMError, match="address"):
        OpenAICompatible("localhost:1234")


def test_ai_settings_keep_the_api_key_private(tmp_path):
    from kurator.ai.service import AIService
    from kurator.ai.vlm import Ollama, OpenAICompatible
    db = Database(str(tmp_path / "data"))
    a = AIService(db)
    assert isinstance(a.vlm_client(), Ollama)
    a.save_settings({"vlm_provider": "openai", "openai_url": " http://127.0.0.1:1234/v1 ", "openai_key": "secret"})
    pub = a.public_settings()
    assert pub["openai_key"] == "" and pub["openai_key_set"] and pub["openai_url"] == "http://127.0.0.1:1234/v1"
    cl = a.vlm_client()
    assert isinstance(cl, OpenAICompatible) and cl.key == "secret"
    a.save_settings({"model": "fast"})                          # other changes keep the key
    assert a.settings()["openai_key"] == "secret"
    a.save_settings({"openai_key": ""})
    assert not a.public_settings()["openai_key_set"]
    with pytest.raises(Exception):
        a.save_settings({"vlm_provider": "cloud-magic"})


# --------------------------------------------------------------------------------- training datasets

def test_dataset_export(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    rng = np.random.default_rng(0)
    base = (rng.random((96, 128, 3)) * 255).astype(np.uint8)
    Image.fromarray(base).save(lib / "dragon.png")
    Image.fromarray(base).save(lib / "dragon_copy.png")                       # identical
    Image.fromarray(base).resize((64, 48)).save(lib / "dragon_small.jpg", quality=92)   # near-duplicate
    y, x = np.mgrid[0:200, 0:300]
    rgba = np.dstack([x % 256, y % 256, np.full((200, 300), 90), np.where(x < 150, 255, 0)]).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(lib / "robot.png")
    Image.fromarray(255 - base).save(lib / "knight.webp", lossless=True)
    import soundfile as sf
    sf.write(str(lib / "roar.wav"), np.zeros(800, np.float32), 8000)     # not a picture
    db, sc, ids, c = _index(tmp_path, lib)
    sc.stop()
    tid = c.post("/api/tags", json={"name": "Creatures/Dragon"}, headers=H).json()["id"]
    c.post(f"/api/tags/{tid}/apply", json={"ids": [ids["dragon.png"], ids["dragon_copy.png"]], "mode": "add"}, headers=H)
    st = c.post("/api/tags", json={"name": "Status/WIP", "facet": "status"}, headers=H).json()["id"]
    c.post(f"/api/tags/{st}/apply", json={"ids": [ids["dragon.png"]], "mode": "add"}, headers=H)
    with db.write_lock:
        db.cat.execute("INSERT INTO captions VALUES (?,?,?,?)", (ids["robot.png"], "gemma3", "A robot on a gradient.", 1.0))
        db.cat.execute("INSERT INTO captions VALUES (?,?,?,?)", (ids["dragon.png"], "gemma3", "A dragon made of noise.", 2.0))

    all_ids = list(ids.values())
    body = {"ids": all_ids, "dest": str(tmp_path / "out"), "folder": "set1", "captions": "both", "trigger": "kstyle",
            "max_side": 128, "format": "keep", "val": 0, "names": "original"}
    r = c.post("/api/dataset/plan", json=body, headers=H)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["count"] == 3 and p["skipped"]["identical"] == 1 and p["skipped"]["near"] == 1 and p["skipped"]["not_picture"] == 1
    texts = {e["file"]: e["text"] for e in p["examples"]}
    assert texts["dragon.png"] == "kstyle, A dragon made of noise, dragon"      # caption + subject tags (not status)
    assert texts["robot.png"] == "kstyle, A robot on a gradient"
    assert texts["knight.webp"] == "kstyle" and p["skipped"]["no_caption"] == 1
    assert c.post("/api/dataset/run", json=body, headers=H).status_code == 200
    t = time.time()
    while c.get("/api/dataset/job").json()["running"] and time.time() - t < 30:
        time.sleep(0.05)
    job = c.get("/api/dataset/job").json()
    assert job["result"]["written"] == 3 and not job["errors"]
    root = tmp_path / "out" / "set1"
    assert sorted(os.listdir(root)) == ["dragon.png", "dragon.txt", "knight.txt", "knight.webp", "kurator_dataset.json",
                                        "metadata.jsonl", "robot.png", "robot.txt"]
    assert (root / "dragon.txt").read_text() == "kstyle, A dragon made of noise, dragon\n"
    assert Image.open(root / "robot.png").size == (128, 85) and Image.open(root / "robot.png").mode == "RGBA"
    assert Image.open(root / "dragon.png").size == (128, 96)                  # copied as it is
    lines = [json.loads(x) for x in (root / "metadata.jsonl").read_text().splitlines()]
    assert {x["file_name"] for x in lines} == {"dragon.png", "robot.png", "knight.webp"}

    # JPEG, train/val split, numbered names, tags only, near-duplicates kept
    body.update({"folder": "set2", "format": "jpg", "val": 0.34, "names": "numbers", "captions": "tags", "trigger": "",
                 "near_dupes": False, "max_side": 0})
    p = c.post("/api/dataset/plan", json=body, headers=H).json()
    assert p["count"] == 4 and p["val"] == 1 and p["train"] == 3
    c.post("/api/dataset/run", json=body, headers=H)
    t = time.time()
    while c.get("/api/dataset/job").json()["running"] and time.time() - t < 30:
        time.sleep(0.05)
    root = tmp_path / "out" / "set2"
    train = sorted(os.listdir(root / "train"))
    assert len([f for f in train if f.endswith(".jpg")]) == 3 and len(os.listdir(root / "val")) == 3
    assert all(Image.open(root / "train" / f).mode == "RGB" for f in train if f.endswith(".jpg"))
    again = c.post("/api/dataset/plan", json=body, headers=H).json()
    assert again["examples"] == p["examples"]                                 # the same seed gives the same split
    assert c.post("/api/dataset/plan", json={**body, "dest": "relative/path"}, headers=H).status_code == 400


# --------------------------------------------------------------------------------- source / author / licence

def _rescan(sc):
    """A new scan, waited for until it has finished (not the previous one)."""
    old = sc.status().get("started")
    sc.request_scan()
    t = time.time()
    while time.time() - t < 60:
        st = sc.status()
        if st.get("started") != old and st["phase"] == "idle" and st["finished"] and st["finished"] >= st["started"]:
            return
        time.sleep(0.05)
    raise TimeoutError


def test_source_author_licence(tmp_path):
    from kurator import xmp
    lib = tmp_path / "lib"
    (lib / "refs").mkdir(parents=True)
    for i, name in enumerate(["castle.png", "forest.png", "own.png"]):
        Image.new("RGB", (40 + i, 30), (40 * i, 90, 120)).save(lib / "refs" / name)
    db, sc, ids, c = _index(tmp_path, lib)
    a, b = ids["castle.png"], ids["forest.png"]
    r = c.post("/api/info", json={"ids": [a, b], "source": "https://artstation.example/castle", "author": "Jane Doe",
                                  "licence": "CC BY 4.0"}, headers=H)
    assert r.json()["changed"] == 2
    c.post("/api/info", json={"ids": [b], "licence": "CC0 1.0", "source": None}, headers=H)   # None: leave as it is
    d = c.get(f"/api/items/{a}").json()["info"]
    assert d["author"] == "Jane Doe" and d["licence_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert c.get(f"/api/items/{b}").json()["info"]["source"] == "https://artstation.example/castle"
    names = lambda q: sorted(c.get("/api/items", params={"q": q}).json()["n"])   # noqa: E731
    assert names("licence:cc0") == ["forest.png"]
    assert names("license:none") == ["own.png"]
    assert names("author:jane") == ["castle.png", "forest.png"]
    assert names("artstation") == ["castle.png", "forest.png"]            # plain words look in the fields too
    assert names("own") == ["own.png"]
    opts = c.get("/api/info/options").json()
    assert "CC BY 4.0" in opts["licences"] and opts["used"]["author"] == ["Jane Doe"]

    # XMP: Kurator's own sidecar gets the fields
    res = xmp.write_sidecars(db, [a])
    assert res["written"] == 1
    side = (lib / "refs" / "castle.png.xmp").read_text()
    assert "<dc:source>https://artstation.example/castle</dc:source>" in side and "<rdf:li>Jane Doe</rdf:li>" in side
    assert 'xml:lang="x-default">CC BY 4.0<' in side and "licenses/by/4.0/</xmpRights:WebStatement>" in side

    # moved outside Kurator: the fields follow the file by its content
    os.rename(lib / "refs" / "castle.png", lib / "castle_moved.png")
    _rescan(sc)
    new_id = db.cat.execute("SELECT id FROM files WHERE name = 'castle_moved.png'").fetchone()[0]
    assert c.get(f"/api/items/{new_id}").json()["info"]["author"] == "Jane Doe"
    c.post("/api/info", json={"ids": [new_id], "source": "", "author": "", "licence": ""}, headers=H)
    assert db.cat.execute("SELECT COUNT(*) FROM file_info WHERE file_id = ?", (new_id,)).fetchone()[0] == 0
    sc.stop()


LR_SIDECAR = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"
   crs:Exposure2012="+0.35">
   <dc:subject><rdf:Bag><rdf:li>Mountains</rdf:li></rdf:Bag></dc:subject>
   <lr:hierarchicalSubject><rdf:Bag><rdf:li>Places|Mountains</rdf:li></rdf:Bag></lr:hierarchicalSubject>
   <dc:rights><rdf:Alt><rdf:li xml:lang="x-default">(c) The Photographer</rdf:li></rdf:Alt></dc:rights>
   <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li><rdf:li>255, 255</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def test_keywords_merge_into_sidecars_of_other_programs(tmp_path):
    from kurator import xmp
    lib = tmp_path / "lib"
    lib.mkdir()
    Image.new("RGB", (30, 20), "white").save(lib / "peak.jpg")
    Image.new("RGB", (30, 20), "gray").save(lib / "odd.jpg")
    (lib / "peak.xmp").write_text(LR_SIDECAR, encoding="utf-8")           # Lightroom style: name.xmp
    odd = LR_SIDECAR.replace('xmlns:dc="http://purl.org/dc/elements/1.1/"', 'xmlns:dc="http://example.com/not-dublin-core/"')
    (lib / "odd.jpg.xmp").write_text(odd, encoding="utf-8")
    db, sc, ids, c = _index(tmp_path, lib)
    sc.stop()
    tid = c.post("/api/tags", json={"name": "Creatures/Dragons"}, headers=H).json()["id"]
    c.post(f"/api/tags/{tid}/apply", json={"ids": list(ids.values()), "mode": "add"}, headers=H)
    c.post("/api/info", json={"ids": list(ids.values()), "author": "Jane", "licence": "CC BY 4.0"}, headers=H)

    res = xmp.write_sidecars(db, list(ids.values()), merge=False)           # default: left alone
    assert res["merged"] == 0 and len(res["skipped_foreign"]) == 2
    assert (lib / "peak.xmp").read_text(encoding="utf-8") == LR_SIDECAR

    res = c.post("/api/xmp/write", json={"ids": list(ids.values()), "merge": True}, headers=H).json()
    assert res["merged"] == 1 and res["skipped_foreign"] == [str(lib / "odd.jpg.xmp")]   # a prefix it can't trust
    merged = (lib / "peak.xmp").read_text(encoding="utf-8")
    assert 'crs:Exposure2012="+0.35"' in merged and "<rdf:li>255, 255</rdf:li>" in merged   # theirs, untouched
    assert "<rdf:li>Places|Mountains</rdf:li>" in merged and "<rdf:li>Creatures|Dragons</rdf:li>" in merged
    assert "<rdf:li>Mountains</rdf:li>" in merged and "<rdf:li>Dragons</rdf:li>" in merged
    assert "(c) The Photographer" in merged and "CC BY 4.0" not in merged and "WebStatement" not in merged
    assert "<rdf:li>Jane</rdf:li>" in merged and xmp.NS not in merged         # still theirs, not Kurator's
    assert sorted(xmp.keywords_from_xmp(merged)) == ["Creatures/Dragons", "Places/Mountains"]
    backups = os.listdir(os.path.join(db.data_dir, "xmp-backup"))
    assert any(f.endswith("_peak.xmp") for f in backups) and "index.txt" in backups
    again = xmp.write_sidecars(db, [ids["peak.jpg"]], merge=True)
    assert again["unchanged"] == 1 and again["merged"] == 0


# --------------------------------------------------------------------------------- backup schedule

def test_backup_schedule_reminder_and_auto(tmp_path):
    from kurator import backup
    lib = tmp_path / "lib"
    lib.mkdir()
    Image.new("RGB", (20, 20), "red").save(lib / "a.png")
    db, sc, ids, c = _index(tmp_path, lib)
    sc.stop()
    disk = tmp_path / "disk"
    disk.mkdir()
    remind = c.post("/api/backups", json={"name": "Weekly", "targets": [{"location": str(disk / "weekly")}], "every_days": 7},
                    headers=H).json()
    auto = c.post("/api/backups", json={"name": "Nightly", "targets": [{"location": str(tmp_path / "unplugged" / "x" / "b")}],
                                        "every_days": 1, "auto": True}, headers=H).json()
    plain = c.post("/api/backups", json={"name": "By hand", "targets": [{"location": str(disk / "hand")}]}, headers=H).json()
    assert remind["every_days"] == 7 and not remind["auto"] and auto["auto"] and plain["every_days"] is None
    due = {d["name"]: d for d in c.get("/api/state").json()["backup_due"]}
    assert set(due) == {"Weekly", "Nightly"} and due["Weekly"]["days"] is None
    assert due["Weekly"]["reachable"] and not due["Nightly"]["reachable"]        # its disk isn't there
    jobs = backup.Jobs(db)
    assert backup.run_due(db, jobs) == []                 # reminders never start by themselves; Nightly can't
    (tmp_path / "unplugged" / "x").mkdir(parents=True)    # the disk is back
    assert backup.run_due(db, jobs) == [auto["id"]]
    t = time.time()
    while jobs.status()["running"] and time.time() - t < 30:
        time.sleep(0.05)
    assert os.path.isdir(tmp_path / "unplugged" / "x" / "b")
    due = {d["name"] for d in backup.due(db)}
    assert due == {"Weekly"}                               # Nightly ran just now
    later = {d["name"] for d in backup.due(db, now=time.time() + 2 * 86400)}
    assert later == {"Weekly", "Nightly"}

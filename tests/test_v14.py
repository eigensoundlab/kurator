"""v0.14: KTX2 textures, Unity sprite import settings, print (ICC, inks, proofing, PDF facts),
text-to-sound search (CLAP), and fixes."""

import io
import json
import os
import struct
import time
import zlib

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from PIL import Image, ImageCms

from icc_samples import cmyk_profile
from kurator import assets, printinfo, unity
from kurator.db import Database
from kurator.handlers import ktx2
from kurator.scanner import Scanner
from kurator.server import create_app

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


# ---------------------------------------------------------------- KTX2
def make_ktx2(vk: int, w: int, h: int, level: bytes, scheme: int = 0, dfd_model: int | None = None) -> bytes:
    blob = level
    if scheme == 2:
        import zstandard
        blob = zstandard.ZstdCompressor().compress(level)
    elif scheme == 3:
        blob = zlib.compress(level)
    dfd = b""
    if dfd_model is not None:
        dfd = struct.pack("<I", 44) + struct.pack("<II", 0, (2 << 16) | 40) + bytes([dfd_model]) + b"\0" * 31
    head = 12 + 36 + 32 + 24          # identifier, header, index, one level
    dfd_off = head
    data_off = dfd_off + len(dfd)
    data_off += -data_off % 16
    out = ktx2.MAGIC + struct.pack("<9I", vk, 1, w, h, 0, 0, 1, 1, scheme)
    out += struct.pack("<4I", dfd_off if dfd else 0, len(dfd), 0, 0) + struct.pack("<2Q", 0, 0)
    out += struct.pack("<3Q", data_off, len(blob), len(level))
    out += dfd
    out += b"\0" * (data_off - len(out))
    return out + blob


def bc1_solid(w, h, rgb) -> bytes:
    r, g, b = rgb
    c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    block = struct.pack("<HHI", c, c, 0)
    return block * ((w // 4) * (h // 4))


def test_ktx2_formats():
    w, h = 8, 4
    rgba = bytes([200, 30, 30, 255]) * (w * h)
    img, facts = ktx2.decode(make_ktx2(37, w, h, rgba))
    assert img.size == (8, 4) and img.getpixel((0, 0)) == (200, 30, 30, 255) and facts["texture"] == "R8G8B8A8"
    img, _ = ktx2.decode(make_ktx2(43, w, h, rgba, scheme=2))       # zstd supercompressed
    assert img.getpixel((7, 3))[:3] == (200, 30, 30)
    img, _ = ktx2.decode(make_ktx2(37, w, h, rgba, scheme=3))       # zlib
    assert img.getpixel((7, 3))[:3] == (200, 30, 30)
    img, facts = ktx2.decode(make_ktx2(131, w, h, bc1_solid(w, h, (0, 0, 255))))
    assert facts["texture"] == "BC1" and img.getpixel((3, 3))[2] > 240 and img.getpixel((3, 3))[0] < 10
    hdr = np.full((h, w, 4), [4.0, 1.0, 0.25, 1.0], np.float16).tobytes()
    img, facts = ktx2.decode(make_ktx2(97, w, h, hdr))
    assert facts["hdr"] and img.getpixel((0, 0))[0] > img.getpixel((0, 0))[1] > img.getpixel((0, 0))[2]
    # UASTC is decoded since 0.15 (see test_v15); the newest Basis formats still need the basisu tool
    img, facts = ktx2.decode(make_ktx2(0, w, h, b"\0" * (16 * 4 * 2), dfd_model=166))
    assert facts["texture"] == "Basis Universal UASTC" and img.size == (w, h)
    with pytest.raises(ktx2.KTXError):
        ktx2.decode(b"not a texture at all")


def test_ktx2_indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("KURATOR_BASISU", str(tmp_path / "no-basisu"))   # also for the scan workers
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "red.ktx2").write_bytes(make_ktx2(37, 8, 8, bytes([200, 30, 30, 255]) * 64))
    (lib / "basis.ktx2").write_bytes(make_ktx2(0, 8, 8, b"\0" * 64, dfd_model=169))   # XUASTC LDR
    db, sc, ids, c = _index(tmp_path, lib)
    red = c.get(f"/api/items/{ids['red.ktx2']}").json()
    assert red["status"] == 1 and red["width"] == 8 and red["meta"]["texture"] == "R8G8B8A8"
    basis = c.get(f"/api/items/{ids['basis.ktx2']}").json()
    assert basis["status"] == 1 and basis["meta"]["placeholder"] and "Basis" in basis["meta"]["preview_note"]


# ---------------------------------------------------------------- Unity
@pytest.fixture()
def unity_project(tmp_path):
    lib = tmp_path / "lib"
    proj = lib / "Game"
    for d in ("Assets/Sprites", "ProjectSettings"):
        (proj / d).mkdir(parents=True)
    art = proj / "Assets/Sprites"
    Image.new("RGBA", (32, 32), (200, 30, 30, 255)).save(art / "hero.png")
    Image.new("RGB", (32, 32), (128, 128, 255)).save(art / "hero_normal.png")
    Image.new("RGBA", (16, 16), (30, 200, 30, 255)).save(art / "coin.png")
    (art / "coin.png.meta").write_text("fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\nTextureImporter:\n"
                                       "  serializedVersion: 13\n  mipmaps:\n    enableMipMap: 1\n  textureSettings:\n    filterMode: 1\n"
                                       "  spriteMode: 0\n  spritePixelsToUnits: 100\n  alphaIsTransparency: 0\n  textureType: 0\n"
                                       "  platformSettings:\n  - serializedVersion: 4\n    textureCompression: 1\n"
                                       "  spriteSheet:\n    serializedVersion: 2\n    sprites: []\n    secondaryTextures: []\n  userData: keep me\n")
    Image.new("RGB", (8, 8), "white").save(lib / "outside.png")
    db, sc, ids, c = _index(tmp_path, lib)
    return db, ids, c, art, lib


def test_unity_sprite_settings_and_undo(unity_project):
    db, ids, c, art, lib = unity_project
    assert c.get(f"/api/items/{ids['coin.png']}").json()["engine"] == "Unity"
    r = c.post("/api/unity/settings", headers=H, json={"ids": [ids["coin.png"], ids["hero.png"], ids["outside.png"]],
                                                       "ppu": 16, "filter": "point", "compression": "none"}).json()
    assert r["changed"] == 1 and r["outside"] == 1 and r["created"] >= 1 and r["undo"]
    coin = (art / "coin.png.meta").read_text()
    assert "guid: 0123456789abcdef0123456789abcdef" in coin and "userData: keep me" in coin       # GUID and the rest kept
    for line in ("  textureType: 8", "  spriteMode: 1", "  spritePixelsToUnits: 16", "    filterMode: 0",
                 "    enableMipMap: 0", "    textureCompression: 0", "  alphaIsTransparency: 1"):
        assert line in coin.splitlines()
    hero = (art / "hero.png.meta").read_text()
    normal_guid = unity.read_guid(str(art / "hero_normal.png.meta"))
    assert normal_guid and f"- name: _NormalMap\n      texture: {{fileID: 2800000, guid: {normal_guid}, type: 3}}" in hero
    assert "  textureType: 1" in (art / "hero_normal.png.meta").read_text()                    # imported as a normal map
    u = c.post("/api/unity/undo", headers=H, json={"id": r["undo"]}).json()
    assert u["restored"] >= 3
    assert "  textureType: 0" in (art / "coin.png.meta").read_text()
    assert not (art / "hero.png.meta").exists() and not (art / "hero_normal.png.meta").exists()
    assert c.post("/api/unity/settings", headers=H, json={"ids": [ids["outside.png"]]}).status_code == 400


def test_unity_meta_for_atlas(unity_project):
    db, ids, c, art, lib = unity_project
    r = c.post("/api/sheets/atlas", headers=H, json={"ids": [ids["hero.png"], ids["coin.png"]], "padding": 0,
                                                     "dest": str(art), "name": "atlas", "ppu": 32, "pixel_art": True}).json()
    meta = [f for f in r["files"] if f.endswith(".meta")]
    assert meta == [str(art / "atlas.png.meta")]                       # automatic inside a Unity project
    text = open(meta[0]).read()
    assert "  spriteMode: 2" in text and "  spritePixelsToUnits: 32" in text and "    filterMode: 0" in text
    assert text.count("    - serializedVersion: 2\n      name: ") == 2 and "nameFileIdTable:\n      hero: " in text
    png = Image.open(art / "atlas.png")
    data = json.load(open(art / "atlas.json"))
    f = data["frames"]["coin.png"]["frame"]
    # Unity rects start at the bottom left
    assert f"        x: {f['x']}\n        y: {png.height - f['y'] - f['h']}\n        width: {f['w']}" in text
    outside = c.post("/api/sheets/atlas", headers=H, json={"ids": [ids["coin.png"]], "dest": str(lib), "name": "plain"}).json()
    assert not any(x.endswith(".meta") for x in outside["files"])


# ---------------------------------------------------------------- print
def _cmyk_jpeg(path, with_profile=True):
    im = Image.new("CMYK", (64, 32), (0, 0, 0, 0))
    im.paste((255, 0, 0, 0), (0, 0, 32, 32))           # cyan half
    im.paste((0, 255, 255, 0), (32, 0, 64, 32))        # red half (magenta + yellow)
    kw = {"icc_profile": cmyk_profile()} if with_profile else {}
    im.save(path, "JPEG", quality=95, **kw)


def test_cmyk_preview_uses_the_profile(tmp_path):
    p = tmp_path / "print.jpg"
    _cmyk_jpeg(p)
    from kurator.processing import process_file
    r = process_file(str(p), (256,))
    assert r["meta"]["colour"] == {"mode": "CMYK", "profile": "Kurator test CMYK", "converted": True}
    img = Image.open(io.BytesIO(r["thumbs"][256])).convert("RGB")
    cy, red = img.getpixel((10, 10)), img.getpixel((50, 10))
    assert cy[0] < 60 and cy[2] > 180 and red[0] > 180 and red[1] < 80


def test_inks_and_proof(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _cmyk_jpeg(lib / "print.jpg")
    Image.new("RGB", (40, 20), (20, 60, 255)).save(lib / "screen.png")
    prof = tmp_path / "test_cmyk.icc"
    prof.write_bytes(cmyk_profile())
    db, sc, ids, c = _index(tmp_path, lib)
    body = c.get(f"/api/items/{ids['print.jpg']}/inks").content
    assert body[:4] == b"KINK"
    hl = struct.unpack_from("<I", body, 4)[0]
    head = json.loads(body[8:8 + hl])
    assert head["source"] == "file" and head["profile"] == "Kurator test CMYK" and not head["gamut"]
    px = np.frombuffer(body[8 + hl:], np.uint8).reshape(head["height"], head["width"], 4)
    assert px[10, 10, 0] > 240 and px[10, 10, 1] < 20 and px[10, 50, 1] > 240 and px[10, 50, 2] > 240
    assert c.get(f"/api/items/{ids['screen.png']}/inks").status_code == 415      # RGB without a proofing profile
    assert c.put("/api/settings", headers=H, json={"proof_profile": str(lib / "screen.png")}).status_code == 400
    assert c.put("/api/settings", headers=H, json={"proof_profile": str(prof)}).status_code == 200
    assert c.get("/api/state").json()["proof_profile"] is True
    body = c.get(f"/api/items/{ids['screen.png']}/inks").content
    hl = struct.unpack_from("<I", body, 4)[0]
    head = json.loads(body[8:8 + hl])
    n = head["width"] * head["height"]
    assert head["source"] == "proof" and head["gamut"] and len(body) == 8 + hl + n * 5
    cmyk = np.frombuffer(body[8 + hl: 8 + hl + 4 * n], np.uint8).reshape(-1, 4)
    assert cmyk[0, 0] > cmyk[0, 1] and cmyk[0, 0] > 100            # bright blue: mostly cyan
    proof = c.get(f"/api/items/{ids['screen.png']}/proof")
    assert proof.headers["content-type"] == "image/webp"
    found = printinfo.cmyk_profiles([str(tmp_path)])
    assert found[0]["name"] == "Kurator test CMYK"


def _pdf(path):
    """A small hand-made PDF: trim and bleed boxes, a spot colour, PDF/X, an output intent, a font."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R /OutputIntents [<< /Type /OutputIntent /S /GTS_PDFX /OutputConditionIdentifier (FOGRA39) >>] >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 858] /BleedBox [9 9 603 849] /TrimBox [17.5 17.5 594.5 840.5] "
        b"/Resources << /ColorSpace << /CS0 [/Separation /PANTONE#20185#20C /DeviceCMYK 5 0 R] >> /Font << /F1 6 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 44 >>\nstream\n/CS0 cs 1 scn 50 50 200 200 re f\nBT ET\nendstream",
        b"<< /FunctionType 2 /Domain [0 1] /C0 [0 0 0 0] /C1 [0 0.91 0.76 0] /N 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /ABCDEF+Garamond-Bold >>",
        b"<< /GTS_PDFXVersion (PDF/X-4) /Title (Flyer) >>",
    ]
    out = b"%PDF-1.6\n"
    offs = []
    for k, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % k + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 7 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(out)


def test_pdf_print_facts(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _pdf(lib / "flyer.pdf")
    db, sc, ids, c = _index(tmp_path, lib)
    p = c.get(f"/api/items/{ids['flyer.pdf']}").json()["meta"]["print"]
    assert p["pdfx"] == "PDF/X-4" and p["output_intent"] and p["output_condition"] == "FOGRA39"
    assert p["spot_colours"] == ["PANTONE 185 C"] and p["fonts"] == ["Garamond-Bold"] and p["fonts_subset"]
    assert p["boxes_mm"]["trim"] == [203.6, 290.3] and p["bleed_mm"] == 3.0
    x, y, w, h = p["boxes_rel"]["trim"]
    assert abs(x - 17.5 / 612) < 1e-3 and abs(y - 17.5 / 858) < 1e-3


# ---------------------------------------------------------------- fixes
def test_blend_companions_dont_crash(tmp_path):
    p = tmp_path / "scene.blend"
    p.write_bytes(b"BLENDER-v300" + b"\0" * 64)
    assert assets.model_companions(str(p), {"model": {"images": 3, "textures": 2}}) == []


# ---------------------------------------------------------------- text-to-sound search (CLAP)
def test_sound_search_with_a_tiny_clap(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    pytest.importorskip("open_clip")
    from transformers import ClapConfig, ClapFeatureExtractor, ClapModel

    from kurator.ai import AIService
    from kurator.ai.sound import SoundEmbedder

    torch.manual_seed(0)
    cfg = ClapConfig(text_config=dict(vocab_size=100, hidden_size=32, num_hidden_layers=1, num_attention_heads=2,
                                      intermediate_size=37, max_position_embeddings=64, projection_dim=16),
                     audio_config=dict(depths=[1, 1, 1, 1], num_attention_heads=[2, 2, 2, 2], hidden_size=64,
                                       patch_embeds_hidden_size=8, projection_dim=16), projection_dim=16)

    class Tok:
        def __call__(self, texts, **kw):
            ids = [[0] + [3 + (ord(ch) % 90) for ch in t[:30]] + [2] for t in texts]
            n = max(map(len, ids))
            return {"input_ids": torch.tensor([i + [1] * (n - len(i)) for i in ids]),
                    "attention_mask": torch.tensor([[1] * len(i) + [0] * (n - len(i)) for i in ids])}

    class Proc:
        feature_extractor = ClapFeatureExtractor()
        tokenizer = Tok()

    def fake_load(self):
        if self.model is None:
            self.model, self.processor = ClapModel(cfg).eval(), Proc()
    monkeypatch.setattr(SoundEmbedder, "_load", fake_load)

    lib = tmp_path / "lib"
    lib.mkdir()
    t = np.arange(48000 * 3) / 48000
    sf.write(lib / "tone_a.wav", 0.4 * np.sin(2 * np.pi * 440 * t), 48000)
    sf.write(lib / "tone_b.wav", 0.4 * np.sin(2 * np.pi * 440 * t + 0.3), 48000)
    sf.write(lib / "noise.wav", np.random.default_rng(1).normal(size=len(t)) * 0.3, 48000)
    Image.new("RGB", (32, 32), "red").save(lib / "red.png")
    db = Database(str(tmp_path / "data"))
    ai = AIService(db)
    ai.save_settings({"model": "custom", "custom_model": "ViT-B-32", "custom_pretrained": ""})   # random image model, offline
    db.add_root(str(lib), "library")
    db2, sc, ids, c = _index(tmp_path, lib, ai)
    res = ai.embed()
    assert res["sounds_embedded"] == 3
    info = c.get("/api/ai").json()
    assert info["sounds"] == 3 and info["sounds_embedded"] == 3 and info["sound_search"]
    sim = c.get(f"/api/ai/similar/{ids['tone_a.wav']}").json()["ids"]
    assert sim[0] == ids["tone_a.wav"] and sim.index(ids["tone_b.wav"]) < sim.index(ids["noise.wav"])
    found = c.post("/api/ai/search", headers=H, json={"q": "a beep"}).json()["ids"]
    assert {ids["tone_a.wav"], ids["noise.wav"], ids["red.png"]} <= set(found)        # sounds and pictures together

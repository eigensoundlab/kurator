"""Sample library for v0.15: Basis Universal KTX2 textures, HDR pictures, a multi-page PDF,
pictures for source/licence and dataset export.

    python tools/make_v15_samples.py LIBRARY_FOLDER
"""

import os
import shutil
import struct
import sys
import zlib

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
lib = sys.argv[1]
for d in ("textures", "hdr", "docs", "refs"):
    os.makedirs(os.path.join(lib, d), exist_ok=True)

# Basis Universal textures (the test fixtures, made by the official encoder)
for name in ("etc1s_mix_q255", "uastc_mix_l4", "uastc_photo_l4", "etc1s_rgba_mips", "red_blue_etc1s"):
    shutil.copy(os.path.join(ROOT, "tests", "data", "basis", name + ".ktx2"), os.path.join(lib, "textures", name + ".ktx2"))

# HDR: an EXR (ZIP) sky gradient with a sun, and a Radiance .hdr
h, w = 180, 320
y, x = np.mgrid[0:h, 0:w].astype(np.float32)
sky = np.dstack([0.3 + 0.4 * y / h, 0.5 + 0.3 * y / h, 1.2 - 0.5 * y / h])
sun = np.exp(-((x - 240) ** 2 + (y - 50) ** 2) / 120.0)[..., None] * np.array([40, 36, 28], np.float32)
img = (sky + sun).astype(np.float32)
img[130:] = np.array([0.15, 0.25, 0.08]) * (1 + 0.3 * np.sin(x[130:] / 9))[..., None]


def exr(chans):
    names = sorted(chans)
    hh, ww = next(iter(chans.values())).shape

    def attr(n, t, v):
        return n.encode() + b"\0" + t.encode() + b"\0" + struct.pack("<i", len(v)) + v
    chl = b"".join(n.encode() + b"\0" + struct.pack("<iB3xii", 1, 0, 1, 1) for n in names) + b"\0"
    head = (b"\x76\x2f\x31\x01" + struct.pack("<I", 2) + attr("channels", "chlist", chl)
            + attr("compression", "compression", bytes([3]))
            + attr("dataWindow", "box2i", struct.pack("<4i", 0, 0, ww - 1, hh - 1))
            + attr("displayWindow", "box2i", struct.pack("<4i", 0, 0, ww - 1, hh - 1))
            + attr("lineOrder", "lineOrder", b"\0") + attr("pixelAspectRatio", "float", struct.pack("<f", 1))
            + attr("screenWindowCenter", "v2f", struct.pack("<2f", 0, 0))
            + attr("screenWindowWidth", "float", struct.pack("<f", 1)) + b"\0")
    chunks = []
    for yy in range(0, hh, 16):
        raw = b"".join(chans[n][r].astype(np.float16).tobytes() for r in range(yy, min(hh, yy + 16)) for n in names)
        t = np.frombuffer(raw, np.uint8)
        r = np.concatenate([t[0::2], t[1::2]])
        d = r.astype(np.int16)
        d[1:] = (r[1:].astype(np.int16) - r[:-1].astype(np.int16) + 128) & 255
        z = zlib.compress(d.astype(np.uint8).tobytes())
        chunks.append(struct.pack("<ii", yy, len(z)) + z)
    pos = len(head) + 8 * len(chunks)
    offs = []
    for c in chunks:
        offs.append(pos)
        pos += len(c)
    return head + struct.pack(f"<{len(chunks)}Q", *offs) + b"".join(chunks)


with open(os.path.join(lib, "hdr", "sky.exr"), "wb") as f:
    f.write(exr({"R": img[..., 0], "G": img[..., 1], "B": img[..., 2]}))
mx = img.max(axis=2)
e = np.floor(np.log2(np.maximum(mx, 1e-32))) + 1
rgbe = np.dstack([np.clip(img * (256 / np.exp2(e))[..., None], 0, 255), e + 128]).astype(np.uint8)
with open(os.path.join(lib, "hdr", "sky.hdr"), "wb") as f:
    f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n" + f"-Y {h} +X {w}\n".encode() + rgbe.tobytes())

# a multi-page PDF: pages of different colours and sizes, numbered
pages = []
for i, (col, size) in enumerate([((230, 90, 60), (595, 842)), ((60, 160, 90), (595, 842)), ((60, 90, 200), (842, 595)),
                                 ((200, 170, 40), (595, 842))]):
    p = Image.new("RGB", size, (250, 248, 240))
    d = ImageDraw.Draw(p)
    d.rectangle([40, 40, size[0] - 40, size[1] - 40], outline=col, width=12)
    d.ellipse([size[0] // 2 - 120, size[1] // 2 - 120, size[0] // 2 + 120, size[1] // 2 + 120], fill=col)
    d.text((60, 60), f"Page {i + 1}", fill=(20, 20, 20))
    pages.append(p)
pages[0].save(os.path.join(lib, "docs", "brochure.pdf"), save_all=True, append_images=pages[1:], resolution=72)

# reference pictures (for source / licence and the dataset export)
rng = np.random.default_rng(3)
for i, name in enumerate(["castle", "forest", "harbour", "market", "tower"]):
    a = rng.random((8, 8, 3))
    pic = Image.fromarray((a * 255).astype(np.uint8)).resize((480, 320), Image.BICUBIC)
    pic.save(os.path.join(lib, "refs", f"{name}.png"))
pic.resize((240, 160)).save(os.path.join(lib, "refs", "tower_small.jpg"), quality=90)   # a near-duplicate
print("made", lib)

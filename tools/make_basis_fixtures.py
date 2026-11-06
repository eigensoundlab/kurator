"""Make the Basis Universal test textures in tests/data/basis (needs the official basisu tool).

    KURATOR_BASISU=/path/to/basisu python tools/make_basis_fixtures.py

Each texture is written next to the reference picture the official tool unpacks it to
(<name>.png, the RGBA32 output of `basisu -unpack`), which the tests compare against bit for bit.
The source pictures are synthetic (made here), so the fixtures carry no third-party content.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "data", "basis")


def sources(tmp: str) -> dict[str, str]:
    rng = np.random.default_rng(7)
    y, x = np.mgrid[0:96, 0:96].astype(np.float32)
    rgb = np.dstack([(np.sin(x / 7) * 0.5 + 0.5) * 255, y / 95 * 255, (x + y) / 190 * 255])
    rgb[0:32, 0:32] = rng.integers(0, 256, (32, 32, 3))                     # noise
    rgb[0:32, 32:64] = np.where((x[0:32, 32:64] // 3) % 2 == 0, 230, 20)[..., None]   # stripes
    rgb[32:48, 64:96] = (200, 40, 90)                                         # solid
    rgb[48:96, 0:48] = np.dstack([np.hypot(x - 24, y - 72) * 4] * 3)[48:96, 0:48]  # grey radial
    rgb[64:96, 48:96] = rng.integers(0, 256, (32, 48, 1)).repeat(3, 2)         # grey noise
    alpha = np.full((96, 96), 255.0)
    alpha[48:96, 0:48] = np.clip(x[48:96, 0:48] * 6, 0, 255)                  # grey + alpha
    alpha[0:32, 64:96] = rng.integers(0, 256, (32, 32))
    alpha[64:96, 48:96] = np.where(((x + y)[64:96, 48:96] // 5) % 2 == 0, 255, 0)
    rgba = np.dstack([rgb, alpha]).clip(0, 255).astype(np.uint8)
    small = rgba[10:47, 20:81]                                                  # 61 x 37: partial blocks
    # smooth "photographic" colour: brings out the UASTC modes for gradients (2, 18)
    y, x = np.mgrid[0:64, 0:64].astype(np.float32)
    waves = []
    for seed in range(3):
        r = np.random.default_rng(seed)
        a = np.zeros((64, 64), np.float32)
        for k in range(6):
            f, t, p = r.uniform(0.02, 0.4), r.uniform(0, 6.28), r.uniform(0, 6.28)
            a += np.sin((x * np.cos(t) + y * np.sin(t)) * f + p) / (k + 1)
        waves.append(a)
    photo = np.dstack(waves)
    photo = (photo - photo.min()) / (photo.max() - photo.min()) * 255 + rng.normal(0, 3, photo.shape)
    red_blue = np.zeros((16, 16, 3), np.uint8)                                 # as tests/textured_samples.py
    red_blue[:, :8] = (220, 20, 20)
    red_blue[:, 8:] = (20, 40, 220)
    out = {}
    for name, arr in (("mix", rgba), ("small", small), ("small_rgb", small[..., :3]),
                      ("photo", photo.clip(0, 255).astype(np.uint8)), ("red_blue", red_blue)):
        p = os.path.join(tmp, name + ".png")
        Image.fromarray(np.ascontiguousarray(arr)).save(p)
        out[name] = p
    return out


VARIANTS = {
    "etc1s_rgb": ("small_rgb", ["-ktx2"]),
    "etc1s_rgba_mips": ("small", ["-ktx2", "-mipmap"]),
    "etc1s_mix_q255": ("mix", ["-ktx2", "-q", "255"]),
    "etc1s_mix_q1": ("mix", ["-ktx2", "-q", "1"]),
    "uastc_rgba_zstd": ("small", ["-ktx2", "-uastc", "-ktx2_zstandard_level", "9"]),
    "uastc_mix_l4": ("mix", ["-ktx2", "-uastc", "-uastc_level", "4"]),
    "uastc_mix_l2": ("mix", ["-ktx2", "-uastc", "-uastc_level", "2", "-mipmap"]),
    "uastc_photo_l4": ("photo", ["-ktx2", "-uastc", "-uastc_level", "4"]),
    "red_blue_etc1s": ("red_blue", ["-ktx2"]),
}


def main() -> int:
    exe = os.environ.get("KURATOR_BASISU") or shutil.which("basisu")
    if not exe:
        print("set KURATOR_BASISU to the basisu tool")
        return 1
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        srcs = sources(tmp)
        for name, (src, opts) in VARIANTS.items():
            ktx = os.path.join(OUT, name + ".ktx2")
            subprocess.run([exe, *opts, srcs[src], "-output_file", ktx], check=True, capture_output=True)
            un = os.path.join(tmp, name)
            os.makedirs(un)
            subprocess.run([exe, "-unpack", "-no_ktx", ktx, "-output_path", un], check=True, capture_output=True)
            for level in (0, 2):
                ref = glob.glob(os.path.join(un, f"*_unpacked_rgba_RGBA32_level_{level}_*.png"))
                if ref:
                    suffix = "" if level == 0 else f"_level{level}"
                    shutil.copy(ref[0], os.path.join(OUT, f"{name}{suffix}.png"))
            print(name, os.path.getsize(ktx), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

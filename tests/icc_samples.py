"""A small CMYK printer profile (ICC v2, Lab connection space, lut8 tables) for the print tests:
simple ink mixing, total ink limited to 300 %, no licensed characterisation data needed."""

from __future__ import annotations

import struct

import numpy as np
from PIL import Image, ImageCms


def _s15(v: float) -> bytes:
    return struct.pack(">i", int(round(v * 65536)))


def _tag_desc(text: str) -> bytes:
    a = text.encode("ascii") + b"\0"
    return b"desc" + b"\0" * 4 + struct.pack(">I", len(a)) + a + struct.pack(">II", 0, 0) + struct.pack(">HB", 0, 0) + b"\0" * 67


def _tag_text(text: str) -> bytes:
    return b"text" + b"\0" * 4 + text.encode("ascii") + b"\0"


def _tag_xyz(x, y, z) -> bytes:
    return b"XYZ " + b"\0" * 4 + _s15(x) + _s15(y) + _s15(z)


def _lut8(nin: int, nout: int, grid: int, clut: np.ndarray) -> bytes:
    out = b"mft1" + b"\0" * 4 + bytes([nin, nout, grid, 0])
    for r in range(3):
        for c in range(3):
            out += _s15(1.0 if r == c else 0.0)
    ramp = bytes(range(256))
    out += ramp * nin + clut.astype(np.uint8).tobytes() + ramp * nout
    return out


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(n, 3) uint8 sRGB -> (n, 3) uint8 in the lut8 Lab encoding (a, b offset by 128)."""
    img = Image.fromarray(rgb.reshape(1, -1, 3).astype(np.uint8), "RGB")
    t = ImageCms.buildTransform(ImageCms.createProfile("sRGB"), ImageCms.createProfile("LAB"), "RGB", "LAB")
    out = np.asarray(ImageCms.applyTransform(img, t)).reshape(-1, 3).astype(np.int16)
    out[:, 1:] = out[:, 1:].astype(np.uint8).view(np.int8).astype(np.int16) + 128   # Pillow keeps a/b as signed bytes
    return out.astype(np.uint8)


def _lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    lab = lab.astype(np.int16).copy()
    lab[:, 1:] = (lab[:, 1:] - 128) & 0xFF
    img = Image.fromarray(lab.reshape(1, -1, 3).astype(np.uint8), "LAB")
    t = ImageCms.buildTransform(ImageCms.createProfile("LAB"), ImageCms.createProfile("sRGB"), "LAB", "RGB")
    return np.asarray(ImageCms.applyTransform(img, t)).reshape(-1, 3)


def cmyk_profile(name: str = "Kurator test CMYK") -> bytes:
    # A2B0: CMYK -> Lab on a 3^4 grid with simple subtractive mixing
    g = 5
    v = np.linspace(0, 1, g)
    c, m, y, k = (a.ravel() for a in np.meshgrid(v, v, v, v, indexing="ij"))
    rgb = np.stack([(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)], 1) * 255
    a2b = _rgb_to_lab(np.round(rgb))
    # B2A0: Lab -> CMYK on a 9^3 grid: grey component replacement, total ink kept under 300 %
    G = 17
    w = np.round(np.linspace(0, 255, G))
    L, A, B = (q.ravel() for q in np.meshgrid(w, w, w, indexing="ij"))
    rgb2 = _lab_to_rgb(np.stack([L, A, B], 1)).astype(np.float64) / 255
    kk = 1 - rgb2.max(1)
    den = np.where(kk < 1, 1 - kk, 1)
    cmy = np.stack([(1 - rgb2[:, 0] - kk) / den, (1 - rgb2[:, 1] - kk) / den, (1 - rgb2[:, 2] - kk) / den], 1)
    cmyk = np.c_[cmy, kk]
    tot = cmyk.sum(1, keepdims=True)
    cmyk = np.where(tot > 3.0, cmyk * 3.0 / tot, cmyk)
    b2a = np.clip(np.round(cmyk * 255), 0, 255)
    tags = [(b"desc", _tag_desc(name)), (b"cprt", _tag_text("No copyright, use freely")),
            (b"wtpt", _tag_xyz(0.9642, 1.0, 0.8249)), (b"A2B0", _lut8(4, 3, g, a2b)), (b"B2A0", _lut8(3, 4, G, b2a))]
    n = len(tags)
    offset = 128 + 4 + 12 * n
    table, blobs = struct.pack(">I", n), b""
    for sig, data in tags:
        data += b"\0" * (-len(data) % 4)
        table += sig + struct.pack(">II", offset + len(blobs), len(data))
        blobs += data
    size = offset + len(blobs)
    header = (struct.pack(">I", size) + b"lcms" + struct.pack(">I", 0x02100000) + b"prtr" + b"CMYK" + b"Lab "
              + struct.pack(">6H", 2026, 1, 1, 0, 0, 0) + b"acsp" + b"APPL" + struct.pack(">I", 0) + b"\0" * 4 + b"\0" * 4
              + b"\0" * 8 + struct.pack(">I", 0) + _s15(0.9642) + _s15(1.0) + _s15(0.8249) + b"\0" * 4)
    header += b"\0" * (128 - len(header))
    return header + table + blobs

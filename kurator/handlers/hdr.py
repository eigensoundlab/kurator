"""HDR pictures: OpenEXR (.exr) and Radiance (.hdr), shown tone-mapped.

* OpenEXR: single-part scanline or tiled files, uncompressed, RLE, ZIPS and ZIP compression (the
  defaults of Blender, Nuke, Houdini, Substance and most renderers), half/float/uint channels.
  Colour (R, G, B, A, also inside layers like "beauty.R"), luminance (Y), or any other single
  channel (depth Z, masks) shown from its own minimum to maximum. PIZ, PXR24, B44 and DWA
  compressed files get a card that says so.
* Radiance RGBE (.hdr), flat or run-length encoded, the usual HDRI format.

Big files are read with a row/column stride when only a thumbnail is needed.
"""

from __future__ import annotations

import os
import re
import struct
import zlib

import numpy as np
from PIL import Image

from . import Handler, Loaded, register
from .common import placeholder

MAX_PIXELS = 16384 * 16384
COMPRESSION = {0: "none", 1: "RLE", 2: "ZIPS", 3: "ZIP", 4: "PIZ", 5: "PXR24", 6: "B44", 7: "B44A",
               8: "DWAA", 9: "DWAB", 10: "HTJ2K"}
_LINES = {0: 1, 1: 1, 2: 1, 3: 16}
_TYPES = {0: (np.uint32, 4, "uint"), 1: (np.float16, 2, "half"), 2: (np.float32, 4, "float")}


class HDRError(ValueError):
    pass


def tonemap(rgb: np.ndarray) -> Image.Image:
    """Scene-linear float RGB to an 8-bit picture (Reinhard, then gamma 2.2)."""
    rgb = np.nan_to_num(np.maximum(rgb.astype(np.float32), 0), posinf=1e4)
    rgb = rgb / (1 + rgb)
    return Image.fromarray((rgb ** (1 / 2.2) * 255 + 0.5).astype(np.uint8), "RGB")


def _stride(w: int, h: int, target: int | None) -> int:
    return max(1, max(w, h) // (2 * target)) if target and max(w, h) > 2 * target else 1


# ---------------------------------------------------------------------------------------------
# OpenEXR

def _header(data: bytes) -> tuple[dict, int]:
    if data[:4] != b"\x76\x2f\x31\x01":
        raise HDRError("not an OpenEXR file")
    flags = struct.unpack_from("<I", data, 4)[0]
    if flags & 0x1000:
        raise HDRError("multi-part OpenEXR: no preview yet")
    if flags & 0x800:
        raise HDRError("deep OpenEXR: no preview yet")
    attrs: dict = {"tiled": bool(flags & 0x200)}
    pos = 8
    while data[pos]:
        e = data.index(b"\0", pos)
        name = data[pos:e].decode("latin-1")
        e2 = data.index(b"\0", e + 1)
        typ = data[e + 1:e2].decode("latin-1")
        n = struct.unpack_from("<i", data, e2 + 1)[0]
        val = data[e2 + 5:e2 + 5 + n]
        pos = e2 + 5 + n
        if typ == "chlist":
            chans, q = [], 0
            while q < len(val) and val[q]:
                z = val.index(b"\0", q)
                ptype, _lin, xs, ys = struct.unpack_from("<iB3xii", val, z + 1)
                chans.append((val[q:z].decode("latin-1"), ptype, xs, ys))
                q = z + 17
            attrs["channels"] = chans
        elif typ == "compression":
            attrs["compression"] = val[0]
        elif name == "dataWindow" and typ == "box2i":
            attrs["window"] = struct.unpack("<4i", val)
        elif typ == "tiledesc":
            attrs["tiles"] = struct.unpack_from("<IIB", val)
    if "channels" not in attrs or "window" not in attrs:
        raise HDRError("damaged OpenEXR header")
    return attrs, pos + 1


def _unzip(raw: bytes, size: int) -> bytes:
    """ZIP/RLE output: undo the byte predictor and the two-halves interleaving."""
    t = np.frombuffer(raw, np.uint8)
    if len(t) != size:
        raise HDRError("damaged OpenEXR data")
    d = t.astype(np.int64)
    d[1:] -= 128
    t = (np.cumsum(d) & 255).astype(np.uint8)
    out = np.empty(size, np.uint8)
    half = (size + 1) // 2
    out[0::2] = t[:half]
    out[1::2] = t[half:]
    return out.tobytes()


def _unrle(raw: bytes, size: int) -> bytes:
    out = bytearray()
    i, n = 0, len(raw)
    while i < n and len(out) < size:
        c = raw[i]
        if c > 127:                                   # -count literal bytes
            k = 256 - c
            out += raw[i + 1:i + 1 + k]
            i += 1 + k
        else:                                         # count+1 repeats
            out += raw[i + 1:i + 2] * (c + 1)
            i += 2
    return bytes(out)


def read_exr(data: bytes, target: int | None = None) -> tuple[dict[str, np.ndarray], dict]:
    """({channel: float32 (rows, cols)}, facts). With `target`, rows and columns are strided."""
    attrs, pos = _header(data)
    comp = attrs.get("compression", 0)
    x0, y0, x1, y1 = attrs["window"]
    w, h = x1 - x0 + 1, y1 - y0 + 1
    if not (0 < w and 0 < h and w * h <= MAX_PIXELS):
        raise HDRError("bad picture size")
    chans = attrs["channels"]
    facts = {"format": "OpenEXR", "compression": COMPRESSION.get(comp, str(comp)),
             "channels": ", ".join(c[0] for c in chans)[:120], "hdr": True,
             "pixels": "/".join(sorted({_TYPES.get(c[1], (0, 0, "?"))[2] for c in chans}))}
    if comp not in _LINES:
        raise HDRError(f"OpenEXR with {facts['compression']} compression: no preview yet", facts)
    if any(c[2] != 1 or c[3] != 1 for c in chans) or any(c[1] not in _TYPES for c in chans):
        raise HDRError("OpenEXR with subsampled or unknown channels: no preview yet", facts)
    step = _stride(w, h, target)
    rows = range(0, h, step)
    out = {c[0]: np.zeros((len(rows), len(range(0, w, step))), np.float32) for c in chans}
    sizes = [_TYPES[c[1]][1] for c in chans]

    def unpack(chunk: bytes, ndata: int) -> bytes:
        if len(chunk) == ndata:                       # stored as is when compression didn't help
            return chunk
        if comp in (2, 3):
            return _unzip(zlib.decompress(chunk), ndata)
        if comp == 1:
            return _unzip(_unrle(chunk, ndata), ndata)
        return chunk

    def put(block: bytes, by: int, nlines: int, bx: int, bw: int) -> None:
        """A block of nlines lines x bw pixels at (bx, by), channel-interleaved per line."""
        line = sum(s * bw for s in sizes)
        for li in range(nlines):
            y = by + li
            if y % step or not 0 <= y < h:
                continue
            o = li * line
            for (name, ptype, _, _), s in zip(chans, sizes):
                a = np.frombuffer(block, _TYPES[ptype][0], bw, o).astype(np.float32)
                o += s * bw
                xs = np.arange(bx, bx + bw)
                keep = (xs % step == 0) & (xs < w)
                out[name][y // step, xs[keep] // step] = a[keep]

    if attrs["tiled"]:
        tw, th, mode = attrs.get("tiles", (0, 0, 0))
        if not tw or not th or (mode & 15) == 2:
            raise HDRError("OpenEXR with rip-map tiles: no preview yet", facts)
        nx, ny = -(-w // tw), -(-h // th)
        offsets = struct.unpack_from(f"<{nx * ny}Q", data, pos)
        for off in offsets:
            tx, ty, lx, ly, n = struct.unpack_from("<5i", data, off)
            if lx or ly:
                continue
            bw, bh = min(tw, w - tx * tw), min(th, h - ty * th)
            if not any((ty * th + k) % step == 0 for k in range(bh)):
                continue
            ndata = bh * sum(s * bw for s in sizes)
            put(unpack(data[off + 20:off + 20 + n], ndata), ty * th, bh, tx * tw, bw)
    else:
        per = _LINES[comp]
        nchunks = -(-h // per)
        offsets = struct.unpack_from(f"<{nchunks}Q", data, pos)
        for off in offsets:
            y, n = struct.unpack_from("<ii", data, off)
            by = y - y0
            nl = min(per, h - by)
            if nl <= 0 or not any((by + k) % step == 0 for k in range(nl)):
                continue
            ndata = nl * sum(s * w for s in sizes)
            put(unpack(data[off + 8:off + 8 + n], ndata), by, nl, 0, w)
    facts["size"] = (w, h)
    return out, facts


def exr_rgb(data: bytes, target: int | None = None) -> tuple[np.ndarray, dict]:
    """(float32 rows x cols x 3, facts): colour, luminance, or a single channel normalised."""
    chans, facts = read_exr(data, target)

    def find(c: str):
        if c in chans:
            return chans[c]
        for name, a in chans.items():
            if name.rsplit(".", 1)[-1] == c:
                return a
        return None

    r, g, b = find("R"), find("G"), find("B")
    if r is not None and g is not None and b is not None:
        return np.dstack([r, g, b]), facts
    y = find("Y")
    if y is not None:
        return np.dstack([y, y, y]), facts
    a = next(iter(chans.values()))
    lo, hi = np.nanmin(a), np.nanmax(a)
    a = (a - lo) / (hi - lo) if hi > lo else np.zeros_like(a)
    a = a / (1.0001 - a)                                   # tonemap() maps this back to 0..1 linear-ish
    facts["data"] = "values shown from lowest to highest"
    return np.dstack([a, a, a]), facts


# ---------------------------------------------------------------------------------------------
# Radiance RGBE

def read_hdr(data: bytes, target: int | None = None) -> tuple[np.ndarray, dict]:
    if not data.startswith(b"#?"):
        raise HDRError("not a Radiance HDR file")
    end = data.find(b"\n\n")
    if end < 0:
        raise HDRError("damaged Radiance header")
    head = data[:end].decode("latin-1", "replace")
    if "FORMAT=" in head and "32-bit_rle_rgbe" not in head:
        raise HDRError("Radiance XYZE: no preview yet")
    nl = data.index(b"\n", end + 2)
    m = re.match(r"\s*([-+])Y\s+(\d+)\s+([-+])X\s+(\d+)", data[end + 2:nl].decode("latin-1"))
    if not m:
        raise HDRError("unusual Radiance orientation: no preview yet")
    h, w = int(m.group(2)), int(m.group(4))
    if not (0 < w and 0 < h and w * h <= MAX_PIXELS):
        raise HDRError("bad picture size")
    step = _stride(w, h, target)
    out = np.zeros((len(range(0, h, step)), len(range(0, w, step)), 4), np.uint8)
    pos = nl + 1
    n = len(data)
    for y in range(h):
        if pos + 4 > n:
            break
        if 8 <= w < 32768 and data[pos] == 2 and data[pos + 1] == 2 and (data[pos + 2] << 8 | data[pos + 3]) == w:
            pos += 4
            line = bytearray()
            for _c in range(4):
                got = bytearray()
                while len(got) < w and pos < n:
                    c = data[pos]
                    if c > 128:
                        got += data[pos + 1:pos + 2] * (c - 128)
                        pos += 2
                    else:
                        got += data[pos + 1:pos + 1 + c]
                        pos += 1 + c
                line += got[:w].ljust(w, b"\0")
            if y % step == 0:
                px = np.frombuffer(bytes(line), np.uint8).reshape(4, w).T
                out[y // step] = px[::step]
        else:                                             # flat RGBE
            if y % step == 0:
                px = np.frombuffer(data, np.uint8, w * 4, pos).reshape(w, 4) if pos + 4 * w <= n else None
                if px is not None:
                    out[y // step] = px[::step]
            pos += 4 * w
    e = out[..., 3].astype(np.int32)
    scale = np.where(e > 0, np.ldexp(1.0, e - 136), 0.0).astype(np.float32)
    rgb = (out[..., :3].astype(np.float32) + 0.5) * scale[..., None]      # as Radiance's colr_color()
    if m.group(1) == "+":
        rgb = rgb[::-1]
    if m.group(3) == "-":
        rgb = rgb[:, ::-1]
    return rgb, {"format": "Radiance HDR", "hdr": True, "size": (w, h)}


# ---------------------------------------------------------------------------------------------

def load(data: bytes, path: str, target: int | None) -> Loaded:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".exr":
            rgb, facts = exr_rgb(data, target)
        else:
            rgb, facts = read_hdr(data, target)
    except HDRError as e:
        facts = e.args[1] if len(e.args) > 1 else {"format": "OpenEXR" if ext == ".exr" else "Radiance HDR"}
        facts["preview_note"] = e.args[0]
        facts["placeholder"] = True
        return Loaded(placeholder(ext, facts["format"]), facts, {}, None)
    except (struct.error, zlib.error, IndexError, ValueError) as e:
        raise HDRError(f"damaged HDR picture: {e}") from e
    size = facts.pop("size", None)
    lum = rgb.mean(axis=2)
    if lum.size:
        facts["brightest"] = round(float(np.nanmax(lum)), 2)
    return Loaded(tonemap(rgb), facts, {}, size)


register(Handler("image", (".exr", ".hdr"), load, group="texture"))

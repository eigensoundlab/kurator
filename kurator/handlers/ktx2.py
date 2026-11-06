"""KTX2 textures (Khronos): the container, uncompressed and GPU block formats.

* Uncompressed 8-bit (R, RG, RGB, RGBA, BGRA) and 16/32-bit float formats (HDR, tone-mapped).
* BC1-BC7 (desktop) through Pillow's own decoder.
* ETC2/EAC and ASTC (mobile) through texture2ddecoder (in the optional 3D add-on).
* Zstandard and zlib supercompression.
* Basis Universal ETC1S (BasisLZ) and UASTC LDR 4x4 through Kurator's own decoder (kurator.basisu).
* The newer Basis formats (UASTC HDR, ASTC HDR 6x6, XUASTC LDR) through the official `basisu`
  command-line tool when it is installed (on PATH, or KURATOR_BASISU=path); otherwise a card.

Thumbnails decode the smallest mip level that is still big enough, not the full texture.
"""

from __future__ import annotations

import glob
import os
import shutil
import struct
import subprocess
import tempfile
import zlib

import numpy as np
from PIL import Image

from .. import basisu
from . import Handler, Loaded, register
from .common import placeholder

MAGIC = b"\xabKTX 20\xbb\r\n\x1a\n"
MAX_PIXELS = 16384 * 16384
MAX_LEVEL_BYTES = 1 << 31
SUPER = {0: None, 1: "BasisLZ", 2: "Zstandard", 3: "ZLIB"}

# vkFormat -> (name, kind, args)
_RAW = {9: ("R8", "L", 1), 16: ("R8G8", "RG", 2), 23: ("R8G8B8", "RGB", 3), 29: ("R8G8B8 sRGB", "RGB", 3),
        37: ("R8G8B8A8", "RGBA", 4), 43: ("R8G8B8A8 sRGB", "RGBA", 4), 44: ("B8G8R8A8", "BGRA", 4), 50: ("B8G8R8A8 sRGB", "BGRA", 4)}
_FLOAT = {97: ("R16G16B16A16 float", np.float16, 4), 109: ("R32G32B32A32 float", np.float32, 4),
          90: ("R16G16B16 float", np.float16, 3), 106: ("R32G32B32 float", np.float32, 3)}
_BCN = {131: ("BC1", "RGBA", 1, "BC1"), 132: ("BC1 sRGB", "RGBA", 1, "BC1"), 133: ("BC1 alpha", "RGBA", 1, "BC1"),
        134: ("BC1 alpha sRGB", "RGBA", 1, "BC1"), 135: ("BC2", "RGBA", 2, "BC2"), 136: ("BC2 sRGB", "RGBA", 2, "BC2"),
        137: ("BC3", "RGBA", 3, "BC3"), 138: ("BC3 sRGB", "RGBA", 3, "BC3"), 139: ("BC4", "L", 4, "BC4"),
        141: ("BC5", "RGB", 5, "BC5"), 142: ("BC5 signed", "RGB", 5, "BC5S"), 143: ("BC6H", "RGB", 6, "BC6H"),
        144: ("BC6H signed", "RGB", 6, "BC6HS"), 145: ("BC7", "RGBA", 7, "BC7"), 146: ("BC7 sRGB", "RGBA", 7, "BC7")}
_ETC = {147: ("ETC2 RGB", "decode_etc2"), 148: ("ETC2 RGB sRGB", "decode_etc2"), 149: ("ETC2 RGB A1", "decode_etc2a1"),
        150: ("ETC2 RGB A1 sRGB", "decode_etc2a1"), 151: ("ETC2 RGBA", "decode_etc2a8"), 152: ("ETC2 RGBA sRGB", "decode_etc2a8"),
        153: ("EAC R11", "decode_eacr"), 155: ("EAC R11G11", "decode_eacrg")}
_ASTC_DIMS = [(4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6), (8, 8),
              (10, 5), (10, 6), (10, 8), (10, 10), (12, 10), (12, 12)]
_ASTC = {157 + 2 * k: dims for k, dims in enumerate(_ASTC_DIMS)}
_ASTC.update({k + 1: v for k, v in list(_ASTC.items())})   # the sRGB twin of each
_ASTC_HDR = {1000066000 + k: dims for k, dims in enumerate(_ASTC_DIMS)}   # ASTC ..._SFLOAT_BLOCK


class KTXError(ValueError):
    pass


def parse(data: bytes) -> dict:
    try:
        return _parse(data)
    except (struct.error, IndexError) as e:
        raise KTXError(f"damaged KTX2 file ({e.__class__.__name__})") from None


def _parse(data: bytes) -> dict:
    if data[:12] != MAGIC:
        raise KTXError("not a KTX2 file")
    (vk, type_size, w, h, depth, layers, faces, levels, scheme) = struct.unpack_from("<9I", data, 12)
    dfd_off, dfd_len, kvd_off, kvd_len = struct.unpack_from("<4I", data, 48)
    sgd_off, sgd_len = struct.unpack_from("<2Q", data, 64)
    levels_n = max(1, levels)
    idx = [struct.unpack_from("<3Q", data, 80 + 24 * k) for k in range(levels_n)]
    kv = {}
    pos, end = kvd_off, kvd_off + kvd_len
    while kvd_len and pos + 4 <= end:
        n = struct.unpack_from("<I", data, pos)[0]
        entry = data[pos + 4: pos + 4 + n]
        key, _, val = entry.partition(b"\0")
        kv[key.decode("utf-8", "replace")] = val.rstrip(b"\0").decode("utf-8", "replace")[:200]
        pos += 4 + n + (-n % 4)
    # first descriptor block: colorModel, transferFunction, and the channel ids of the first two samples
    color_model = data[dfd_off + 12] if dfd_len >= 16 else None
    transfer = data[dfd_off + 14] if dfd_len >= 16 else None
    chans = [data[dfd_off + 31 + 16 * k] & 15 for k in range(2) if dfd_len >= 32 + 16 * k]
    return {"vk": vk, "width": w, "height": h, "depth": depth, "layers": layers, "faces": faces, "levels": levels_n,
            "scheme": scheme, "level0": idx[0], "index": idx, "kv": kv, "color_model": color_model,
            "transfer": transfer, "channels": chans, "dfd_len": dfd_len, "sgd": (sgd_off, sgd_len),
            "type_size": type_size}


def _level0(data: bytes, info: dict) -> bytes:
    return _level(data, info, 0)


def _level(data: bytes, info: dict, k: int) -> bytes:
    off, length, raw_len = info["index"][k]
    if off + length > len(data):
        raise KTXError("damaged KTX2 file (level data cut off)")
    blob = data[off:off + length]
    limit = min(raw_len or MAX_LEVEL_BYTES, MAX_LEVEL_BYTES)
    try:
        if info["scheme"] == 2:
            blob = _unzstd(blob, limit)
        elif info["scheme"] == 3:
            blob = zlib.decompressobj().decompress(blob, limit)
    except MemoryError:
        raise
    except Exception as e:                  # zstd/zlib errors, absurd sizes in a damaged header
        raise KTXError(f"damaged KTX2 file (supercompression: {e.__class__.__name__})") from None
    return blob


def _unzstd(blob: bytes, limit: int) -> bytes:
    try:
        from compression import zstd   # Python 3.14+
        return zstd.ZstdDecompressor().decompress(blob, max_length=limit)
    except ImportError:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(blob, max_output_size=limit)


# Basis Universal formats, by DFD colour model
BASIS_MODELS = {163: "ETC1S", 166: "UASTC", 167: "UASTC HDR 4x4", 168: "UASTC HDR 6x6",
                169: "XUASTC LDR", 170: "XUBC7"}


def _pick_level(info: dict, target: int | None) -> int:
    """The smallest mip level whose longer side is still at least `target` (0 = the full texture)."""
    k = 0
    if target:
        w, h = info["width"], info["height"]
        while k + 1 < info["levels"] and max(w >> (k + 1), h >> (k + 1)) >= target:
            k += 1
    return k


def decode(data: bytes, target: int | None = None) -> tuple[Image.Image, dict]:
    """(first image of a mip level, facts) - raises KTXError for content it can't show.

    With `target`, the smallest level that is still at least that big is decoded."""
    info = parse(data)
    w0, h0, vk = info["width"], info["height"], info["vk"]
    facts = {"format": "KTX2", "texture": _format_name(info), "levels": info["levels"]}
    if info["faces"] == 6:
        facts["cubemap"] = True
    if info["layers"] > 1:
        facts["layers"] = info["layers"]
    if info["kv"].get("KTXwriter"):
        facts["writer"] = info["kv"]["KTXwriter"]
    if not (0 < w0 and 0 < h0 and w0 * h0 <= MAX_PIXELS):
        raise KTXError("bad texture size")
    k = _pick_level(info, target)
    w, h = max(1, w0 >> k), max(1, h0 >> k)
    if info["scheme"] == 1 or info["color_model"] in BASIS_MODELS:
        img = _basis(data, info, k, w, h, facts)
        return img, facts
    if vk in _ASTC_HDR:                                          # HDR ASTC: only the basisu tool unpacks it
        return _basis_tool(data, facts), facts
    blob = _level(data, info, k)
    if vk in _RAW:
        _, mode, n = _RAW[vk]
        need = w * h * n
        if len(blob) < need:
            raise KTXError("damaged KTX2 file")
        if mode == "RG":
            a = np.frombuffer(blob, np.uint8, need).reshape(h, w, 2)
            img = Image.fromarray(np.dstack([a, np.zeros((h, w), np.uint8)]), "RGB")
        elif mode == "BGRA":
            img = Image.frombuffer("RGBA", (w, h), blob[:need], "raw", "BGRA", 0, 1)
        else:
            img = Image.frombuffer(mode, (w, h), blob[:need], "raw", mode, 0, 1)
    elif vk in _FLOAT:
        _, dt, n = _FLOAT[vk]
        a = np.frombuffer(blob, dt, w * h * n).reshape(h, w, n).astype(np.float32)
        from .hdr import tonemap
        img = tonemap(a[..., :3])
        facts["hdr"] = True
    elif vk in _BCN:
        _, mode, n, pf = _BCN[vk]
        img = Image.frombytes(mode, (w, h), blob, "bcn", (n, pf))
    elif vk in _ETC or vk in _ASTC:
        try:
            import texture2ddecoder as t2d
        except ImportError:
            raise KTXError("ETC2/ASTC texture: install the optional 3D add-on to see it (install.bat 3d)", facts) from None
        if vk in _ETC:
            out = getattr(t2d, _ETC[vk][1])(blob, w, h)
        else:
            bw, bh = _ASTC[vk]
            out = t2d.decode_astc(blob, w, h, bw, bh)
        img = Image.frombytes("RGBA", (w, h), out, "raw", "BGRA")
    else:
        raise KTXError(f"texture format {vk} can't be shown yet", facts)
    return img, facts


def _basis(data: bytes, info: dict, k: int, w: int, h: int, facts: dict) -> Image.Image:
    """One image of a Basis Universal texture: ETC1S and UASTC here, the rest through `basisu`."""
    model = info["color_model"]
    chans = info["channels"]
    if info["transfer"] == 2:
        facts["srgb"] = True
    try:
        if model == 163 and info["scheme"] == 1:
            per_level = max(info["layers"], 1) * info["faces"]
            off, length = info["sgd"]
            if off + length > len(data):
                raise KTXError("damaged KTX2 file (global data cut off)")
            cb = basisu.ETC1SCodebook(data[off:off + length], per_level * info["levels"])
            loff, llen, _ = info["index"][k]
            if loff + llen > len(data):
                raise KTXError("damaged KTX2 file (level data cut off)")
            alpha = info["dfd_len"] == 60                        # two slices: RGB + alpha
            px = basisu.etc1s_image(cb, data[loff:loff + llen], k * per_level, w, h, alpha)
            two = alpha and chans[:2] == [3, 4]                  # RRR + GGG: a two-channel texture
        elif model == 166 and info["scheme"] in (0, 2):
            px = basisu.uastc_image(_level(data, info, k), w, h)
            alpha = chans[:1] in ([3], [5])                       # RGBA, RRRG
            two = chans[:1] == [5]
        else:
            return _basis_tool(data, facts)
    except basisu.BasisError as e:
        raise KTXError(f"damaged Basis texture: {e}", facts) from e
    if two:
        facts["channels"] = "RG"
        rg = np.dstack([px[..., 0], px[..., 3], np.zeros(px.shape[:2], np.uint8)])
        return Image.fromarray(rg, "RGB")
    if alpha:
        facts["alpha"] = True
        return Image.fromarray(px, "RGBA")
    return Image.fromarray(np.ascontiguousarray(px[..., :3]), "RGB")


def basisu_tool() -> str | None:
    """The official Basis Universal command-line tool, if installed."""
    exe = os.environ.get("KURATOR_BASISU", "").strip()
    if exe:
        return exe if os.path.isfile(exe) else None
    return shutil.which("basisu")


def _basis_tool(data: bytes, facts: dict) -> Image.Image:
    """Unpack with `basisu -unpack` into a temporary folder and read the plain RGBA (or float) result."""
    kind = facts.get("texture", "Basis Universal")
    exe = basisu_tool()
    if not exe:
        raise KTXError(f"{kind}: install the Basis Universal tool (basisu) to see it", facts)
    with tempfile.TemporaryDirectory(prefix="kurator-basisu-") as tmp:
        src = os.path.join(tmp, "t.ktx2")
        with open(src, "wb") as f:
            f.write(data)
        out = os.path.join(tmp, "out")
        os.mkdir(out)
        try:
            subprocess.run([exe, "-unpack", "-no_ktx", "-output_path", out, src], cwd=tmp, timeout=120,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as e:
            raise KTXError(f"{kind}: the basisu tool failed ({e.__class__.__name__})", facts) from None
        ldr = sorted(glob.glob(os.path.join(out, "*_unpacked_rgba_RGBA32_level_0_*.png")))
        if ldr:
            with Image.open(ldr[0]) as im:
                im.load()
                im = im.copy()
            if im.mode == "RGBA" and im.getextrema()[3][0] == 255:
                return im.convert("RGB")
            if im.mode == "RGBA":
                facts["alpha"] = True
            return im
        hdr = sorted(glob.glob(os.path.join(out, "*_unpacked_rgba_RGBA_HALF_level_0_*.exr")) +
                     glob.glob(os.path.join(out, "*_unpacked_rgb_RGB_HALF_level_0_*.exr")))
        if hdr:
            from . import hdr as hdr_pics
            with open(hdr[0], "rb") as f:
                rgb, _ = hdr_pics.exr_rgb(f.read())
            facts["hdr"] = True
            return hdr_pics.tonemap(rgb)
    raise KTXError(f"{kind}: the basisu tool gave no image", facts)


def _format_name(info: dict) -> str:
    vk = info["vk"]
    if info["color_model"] in BASIS_MODELS:
        return "Basis Universal " + BASIS_MODELS[info["color_model"]]
    if info["scheme"] == 1:
        return "Basis Universal ETC1S"
    for table in (_RAW, _FLOAT, _BCN, _ETC):
        if vk in table:
            return table[vk][0]
    if vk in _ASTC:
        bw, bh = _ASTC[vk]
        return f"ASTC {bw}x{bh}" + (" sRGB" if (vk - 157) % 2 else "")
    if vk in _ASTC_HDR:
        bw, bh = _ASTC_HDR[vk]
        return f"ASTC HDR {bw}x{bh}"
    return f"Vulkan format {vk}"


def load(data: bytes, path: str, target: int | None) -> Loaded:
    try:
        img, facts = decode(data, target)
    except KTXError as e:
        facts = e.args[1] if len(e.args) > 1 else {"format": "KTX2"}
        try:
            info = parse(data)
            facts.setdefault("texture", _format_name(info))
            size = (info["width"], info["height"])
        except (KTXError, struct.error):
            size = None
        facts["preview_note"] = e.args[0]
        facts["placeholder"] = True
        return Loaded(placeholder(os.path.splitext(path)[1], facts.get("texture", "KTX2")), facts, {}, size)
    except (struct.error, ValueError, OSError) as e:
        raise KTXError(f"damaged KTX2 file: {e}") from e
    info = parse(data)
    return Loaded(img, facts, {}, (info["width"], info["height"]))


register(Handler("image", (".ktx2",), load, group="texture"))

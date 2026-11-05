"""KTX2 textures (Khronos): the container, uncompressed and GPU block formats.

* Uncompressed 8-bit (R, RG, RGB, RGBA, BGRA) and 16/32-bit float formats (HDR, tone-mapped).
* BC1-BC7 (desktop) through Pillow's own decoder.
* ETC2/EAC and ASTC (mobile) through texture2ddecoder (in the optional 3D add-on).
* Zstandard and zlib supercompression.
* Basis Universal content (ETC1S/BasisLZ, UASTC) needs the Basis transcoder, which has no Python
  package with wheels for every platform yet: those files are listed with their facts and a card.
"""

from __future__ import annotations

import os
import struct
import zlib

import numpy as np
from PIL import Image

from . import Handler, Loaded, register
from .common import placeholder

MAGIC = b"\xabKTX 20\xbb\r\n\x1a\n"
MAX_PIXELS = 16384 * 16384
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
_ASTC = {157 + 2 * k: dims for k, dims in enumerate([(4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6), (8, 8),
                                                    (10, 5), (10, 6), (10, 8), (10, 10), (12, 10), (12, 12)])}
_ASTC.update({k + 1: v for k, v in list(_ASTC.items())})   # the sRGB twin of each


class KTXError(ValueError):
    pass


def parse(data: bytes) -> dict:
    if data[:12] != MAGIC:
        raise KTXError("not a KTX2 file")
    (vk, type_size, w, h, depth, layers, faces, levels, scheme) = struct.unpack_from("<9I", data, 12)
    dfd_off, dfd_len, kvd_off, kvd_len = struct.unpack_from("<4I", data, 48)
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
    color_model = data[dfd_off + 12] if dfd_len >= 16 else None   # first descriptor block: colorModel
    return {"vk": vk, "width": w, "height": h, "depth": depth, "layers": layers, "faces": faces, "levels": levels_n,
            "scheme": scheme, "level0": idx[0], "kv": kv, "color_model": color_model, "type_size": type_size}


def _level0(data: bytes, info: dict) -> bytes:
    off, length, raw_len = info["level0"]
    if off + length > len(data):
        raise KTXError("damaged KTX2 file (level data cut off)")
    blob = data[off:off + length]
    if info["scheme"] == 2:
        try:
            from compression import zstd   # Python 3.14+
            blob = zstd.decompress(blob)
        except ImportError:
            import zstandard
            blob = zstandard.ZstdDecompressor().decompress(blob, max_output_size=raw_len or 1 << 30)
    elif info["scheme"] == 3:
        blob = zlib.decompressobj().decompress(blob, raw_len or 1 << 30)
    return blob


def decode(data: bytes) -> tuple[Image.Image, dict]:
    """(first image of the base level, facts) - raises KTXError for content it can't show."""
    info = parse(data)
    w, h, vk = info["width"], info["height"], info["vk"]
    facts = {"format": "KTX2", "texture": _format_name(info), "levels": info["levels"]}
    if info["faces"] == 6:
        facts["cubemap"] = True
    if info["layers"] > 1:
        facts["layers"] = info["layers"]
    if info["kv"].get("KTXwriter"):
        facts["writer"] = info["kv"]["KTXwriter"]
    if not (0 < w and 0 < h and w * h <= MAX_PIXELS):
        raise KTXError("bad texture size")
    if info["scheme"] == 1 or (vk == 0 and info["color_model"] in (163, 166, 167)):
        raise KTXError("Basis Universal texture (ETC1S / UASTC): no preview yet", facts)
    blob = _level0(data, info)
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
        rgb = a[..., :3]
        rgb = rgb / (1 + rgb)                                    # Reinhard: HDR into 0..1
        img = Image.fromarray((np.clip(rgb, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8), "RGB")
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


def _format_name(info: dict) -> str:
    vk = info["vk"]
    if info["scheme"] == 1:
        return "Basis Universal ETC1S"
    if vk == 0 and info["color_model"] == 166:
        return "Basis Universal UASTC"
    for table in (_RAW, _FLOAT, _BCN, _ETC):
        if vk in table:
            return table[vk][0]
    if vk in _ASTC:
        bw, bh = _ASTC[vk]
        return f"ASTC {bw}x{bh}" + (" sRGB" if (vk - 157) % 2 else "")
    return f"Vulkan format {vk}"


def load(data: bytes, path: str, target: int | None) -> Loaded:
    try:
        img, facts = decode(data)
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
    return Loaded(img, facts, {}, img.size)


register(Handler("image", (".ktx2",), load, group="texture"))

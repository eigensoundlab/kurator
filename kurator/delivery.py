"""Copies for delivery (Copy to… → For delivery): private details out, author and licence in - with
the picture data copied byte for byte (no re-encoding). JPEG, PNG and WebP; other files are copied
as they are.

Clean removes
  * the GPS location, camera / lens serial numbers, the camera owner, maker notes, user comments,
    the computer name, the EXIF thumbnail (it can show the uncropped original);
  * XMP and IPTC (editing history, document ids, sometimes a location) and JPEG comments;
  * PNG text chunks - AI generation parameters and ComfyUI workflows live there - and other
    private chunks.
  Kept: colour profile, orientation, resolution, dates, camera make and model, exposure.
Credit writes the author and licence of Kurator's *Source & licence* into the file: XMP
(dc:creator, dc:rights, xmpRights:WebStatement, dc:source) and, for JPEG/WebP when cleaning,
EXIF Artist / Copyright; PNG also gets Author / Copyright text chunks.
"""

from __future__ import annotations

import io
import struct
import zlib
from xml.sax.saxutils import escape

from PIL import Image

from . import __version__

CLEANABLE = {".jpg", ".jpeg", ".jfif", ".jpe", ".png", ".webp"}
GPS_IFD, EXIF_IFD = 0x8825, 0x8769
_EXIF_DROP = {0x927C, 0x9286, 0xA430, 0xA431, 0xA435, 0xA420}           # maker note, comment, owner, serials, unique id
_BASE_DROP = {0x013B, 0x013C, 0x9C9C, 0x9C9D}                          # artist, host computer, XPComment, XPAuthor
XMP_JPEG = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_JPEG_EXT = b"http://ns.adobe.com/xmp/extension/\x00"
# PNG chunks that describe how to show the picture (kept when cleaning); the rest is metadata
_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT",
             b"pHYs", b"bKGD", b"acTL", b"fcTL", b"fdAT", b"cICP", b"mDCv", b"cLLi", b"hIST", b"sPLT"}


class DeliveryError(ValueError):
    pass


def _xmp_packet(info: dict) -> bytes:
    from .xmp import _info_blocks
    body = _info_blocks(info)
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Kurator {escape(__version__)}">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        f'    xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/">{body}\n'
        "  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    ).encode("utf-8")


def _credit_xmp(old: bytes | None, info: dict) -> bytes:
    """XMP with the credit: merged into the file's own XMP when that is safe, else a new packet."""
    if old:
        from .xmp import merge_foreign
        try:
            merged = merge_foreign(old.decode("utf-8"), [], info)
        except UnicodeDecodeError:
            merged = None
        if merged:
            return merged.encode("utf-8")
    return _xmp_packet(info)


def _clean_exif(raw: bytes, info: dict | None, clean: bool) -> bytes | None:
    """EXIF without the private parts (and with Artist / Copyright when crediting). TIFF bytes
    with the "Exif\\0\\0" header, or None when there is nothing left worth keeping."""
    ex = Image.Exif()
    try:
        ex.load(raw)
    except Exception:
        return None
    if clean:
        ex.pop(GPS_IFD, None)
        for t in _BASE_DROP:
            ex.pop(t, None)
        sub = ex.get_ifd(EXIF_IFD)
        for t in _EXIF_DROP:
            sub.pop(t, None)
    if info:
        if info.get("author"):
            ex[0x013B] = info["author"]
        if info.get("licence"):
            ex[0x8298] = info["licence"]
    if not len(ex) and not ex.get_ifd(EXIF_IFD):
        return None
    out = ex.tobytes()
    return out if out.startswith(b"Exif\x00\x00") else b"Exif\x00\x00" + out


# ---------------------------------------------------------------- JPEG
def _jpeg_segments(data: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """(marker, payload) up to the start of scan, and the rest (scan data onwards, untouched)."""
    if data[:2] != b"\xff\xd8":
        raise DeliveryError("not a JPEG")
    segs, i = [], 2
    while i < len(data):
        if data[i] != 0xFF:
            raise DeliveryError("damaged JPEG")
        while i < len(data) and data[i] == 0xFF:      # fill bytes
            i += 1
        marker = data[i]
        i += 1
        if marker == 0xDA or marker in range(0xD0, 0xD8) or marker == 0x01:   # SOS / standalone
            return segs, bytes([0xFF, marker]) + data[i:]
        n = struct.unpack(">H", data[i:i + 2])[0]
        segs.append((marker, data[i + 2:i + n]))
        i += n
    raise DeliveryError("JPEG without picture data")


def _seg(marker: int, payload: bytes) -> bytes:
    if len(payload) + 2 > 0xFFFF:
        raise DeliveryError("metadata block too large")
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _jpeg(data: bytes, clean: bool, info: dict | None) -> bytes:
    segs, rest = _jpeg_segments(data)
    out, old_xmp, exif_at = [], None, None
    for marker, p in segs:
        if marker == 0xE1 and p.startswith(b"Exif\x00\x00"):
            if clean:                         # (credit only: the EXIF stays byte for byte - maker notes use offsets)
                new = _clean_exif(p, info, True)
                if new:
                    exif_at = len(out)
                    out.append((marker, new))
                continue
            exif_at = len(out)
        elif marker == 0xE1 and p.startswith(XMP_JPEG):
            old_xmp = p[len(XMP_JPEG):]
            if clean or info:
                continue                      # rewritten (credit) or dropped (clean) below
        elif marker == 0xE1 and p.startswith(XMP_JPEG_EXT):
            if clean or info:
                continue
        elif clean and marker in (0xED, 0xFE):            # IPTC / Photoshop resources, comments
            continue
        out.append((marker, p))
    if info:
        packet = XMP_JPEG + _credit_xmp(None if clean else old_xmp, info)
        if len(packet) + 2 <= 0xFFFF:
            pos = exif_at + 1 if exif_at is not None else next((k + 1 for k, (m, _) in enumerate(out) if m == 0xE0), 0)
            out.insert(pos, (0xE1, packet))
    return b"\xff\xd8" + b"".join(_seg(m, p) for m, p in out) + rest


# ---------------------------------------------------------------- PNG
def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DeliveryError("not a PNG")
    out, i = [], 8
    while i + 8 <= len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        out.append((kind, data[i + 8:i + 8 + n]))
        i += 12 + n
        if kind == b"IEND":
            break
    return out


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _png_old_xmp(chunks) -> bytes | None:
    for kind, body in chunks:
        if kind == b"iTXt" and body.startswith(b"XML:com.adobe.xmp\x00"):
            rest = body[len(b"XML:com.adobe.xmp\x00"):]
            comp, _method = rest[0], rest[1]
            rest = rest[2:]
            rest = rest[rest.index(b"\x00") + 1:]          # language
            rest = rest[rest.index(b"\x00") + 1:]          # translated keyword
            try:
                return zlib.decompress(rest) if comp else rest
            except zlib.error:
                return None
    return None


def _png(data: bytes, clean: bool, info: dict | None) -> bytes:
    chunks = _png_chunks(data)
    old_xmp = _png_old_xmp(chunks)
    out = []
    for kind, body in chunks:
        if kind == b"eXIf":
            if clean:
                new = _clean_exif(body, info, True)
                if new:
                    out.append((kind, new[6:]))
                continue
        elif kind == b"iTXt" and body.startswith(b"XML:com.adobe.xmp\x00") and (clean or info):
            continue
        elif clean and kind not in _PNG_KEEP:
            continue                              # text (AI parameters, workflows), time, private chunks
        elif info and kind in (b"tEXt", b"iTXt", b"zTXt") and body.split(b"\x00", 1)[0] in (b"Author", b"Copyright"):
            continue
        out.append((kind, body))
    if info:
        extra = [(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + _credit_xmp(None if clean else old_xmp, info))]
        for key, val in (("Author", info.get("author")), ("Copyright", info.get("licence"))):
            if val:
                extra.append((b"iTXt", key.encode() + b"\x00\x00\x00\x00\x00" + val.encode("utf-8")))
        pos = next(k for k, (kind, _) in enumerate(out) if kind in (b"IDAT", b"IEND"))
        out[pos:pos] = extra
    return b"\x89PNG\r\n\x1a\n" + b"".join(_chunk(k, b) for k, b in out)


# ---------------------------------------------------------------- WebP
def _webp_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise DeliveryError("not a WebP")
    out, i = [], 12
    end = min(len(data), 8 + struct.unpack("<I", data[4:8])[0])
    while i + 8 <= end:
        kind = data[i:i + 4]
        n = struct.unpack("<I", data[i + 4:i + 8])[0]
        out.append((kind, data[i + 8:i + 8 + n]))
        i += 8 + n + (n & 1)
    return out


def _webp_size(chunks) -> tuple[int, int, bool]:
    """Canvas size and alpha of a simple (VP8 / VP8L) WebP, for a new VP8X header."""
    for kind, b in chunks:
        if kind == b"VP8L" and len(b) >= 5:
            bits = int.from_bytes(b[1:5], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, bool((bits >> 28) & 1)
        if kind == b"VP8 " and len(b) >= 10:
            w, h = struct.unpack("<HH", b[6:10])
            return w & 0x3FFF, h & 0x3FFF, False
    raise DeliveryError("WebP without picture data")


def _webp(data: bytes, clean: bool, info: dict | None) -> bytes:
    chunks = _webp_chunks(data)
    old_xmp = next((b for k, b in chunks if k == b"XMP "), None)
    out = []
    for kind, body in chunks:
        if kind == b"EXIF":
            if clean or info:
                raw = body if body.startswith(b"Exif\x00\x00") else b"Exif\x00\x00" + body
                new = _clean_exif(raw, info if clean else None, clean) if clean else raw
                if new:
                    out.append((kind, new[6:]))
                continue
        elif kind == b"XMP " and (clean or info):
            continue
        out.append((kind, body))
    if info:
        out.append((b"XMP ", _credit_xmp(None if clean else old_xmp, info)))
    has = {k for k, _ in out}
    if (has & {b"EXIF", b"XMP ", b"ICCP"}) and b"VP8X" not in has:
        w, h, alpha = _webp_size(out)
        out.insert(0, (b"VP8X", bytes(4) + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")))
        if alpha:
            out[0] = (b"VP8X", bytes([0x10]) + out[0][1][1:])
    if b"VP8X" in {k for k, _ in out}:          # the feature flags must match the chunks
        k0 = next(k for k, (kind, _) in enumerate(out) if kind == b"VP8X")
        flags = out[k0][1][0] & ~(0x20 | 0x08 | 0x04)
        flags |= (0x20 if b"ICCP" in has else 0) | (0x08 if b"EXIF" in has else 0) | (0x04 if b"XMP " in has else 0)
        out[k0] = (b"VP8X", bytes([flags]) + out[k0][1][1:])
    body = b"".join(k + struct.pack("<I", len(b)) + b + (b"\x00" if len(b) & 1 else b"") for k, b in out)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


# ---------------------------------------------------------------- one file
def prepare(data: bytes, ext: str, clean: bool, info: dict | None) -> bytes | None:
    """The delivered bytes of a file, or None when its format is copied unchanged. The result is
    decoded once to make sure the picture is intact."""
    ext = ext.lower()
    info = {k: v for k, v in (info or {}).items() if v} or None
    if ext not in CLEANABLE or not (clean or info):
        return None
    if ext in (".png",):
        out = _png(data, clean, info)
    elif ext == ".webp":
        out = _webp(data, clean, info)
    else:
        out = _jpeg(data, clean, info)
    with Image.open(io.BytesIO(out)) as im:          # still the same picture?
        im.load()
    return out

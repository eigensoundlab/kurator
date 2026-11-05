"""Decoder for glTF's EXT_meshopt_compression (meshoptimizer's vertex and index codecs), in Python
and numpy: no native code, so a damaged file can only raise an error.

Implements the bitstreams of the extension: attributes (vertex codec version 0), triangles
(index codec 0 and 1), index sequences, and the octahedral, quaternion and exponential filters.
"""

from __future__ import annotations

import numpy as np

_BLOCK_BYTES = 8192
_BLOCK_MAX = 256
_GROUP = 16
_TAIL_MAX = 32


class MeshoptError(ValueError):
    pass


def _block_size(stride: int) -> int:
    return min((_BLOCK_BYTES // stride) & ~(_GROUP - 1), _BLOCK_MAX)


def _bytes_group(data: bytes, pos: int, bits: int, out: bytearray, at: int) -> int:
    if bits == 0:
        out[at:at + 16] = b"\0" * 16
        return pos
    if bits == 3:
        out[at:at + 16] = data[pos:pos + 16]
        return pos + 16
    width = 2 if bits == 1 else 4          # bits per value
    per = 8 // width
    nhead = 16 // per                       # bytes of packed values
    var = pos + nhead                       # values that don't fit come after, a byte each
    sentinel = (1 << width) - 1
    k = at
    for b in data[pos:pos + nhead]:
        for s in range(per):
            v = (b >> (8 - width * (s + 1))) & sentinel
            if v == sentinel:
                v = data[var]
                var += 1
            out[k] = v
            k += 1
    return var


def decode_vertex_buffer(data: bytes, count: int, stride: int) -> bytes:
    if len(data) < 1 + stride or data[0] & 0xF0 != 0xA0:
        raise MeshoptError("not meshopt vertex data")
    if data[0] & 0x0F != 0:
        raise MeshoptError(f"meshopt vertex format version {data[0] & 0x0F} isn't supported yet")
    if stride <= 0 or stride > 256 or stride % 4:
        raise MeshoptError("bad meshopt stride")
    tail = max(stride, _TAIL_MAX)
    if len(data) < 1 + tail:
        raise MeshoptError("meshopt data too short")
    last = bytearray(data[len(data) - stride:])       # the baseline element at the very end
    end = len(data) - tail
    out = np.zeros((count, stride), np.uint8)
    pos = 1
    bsize = _block_size(stride)
    for start in range(0, count, bsize):
        n = min(bsize, count - start)
        aligned = (n + 15) & ~15
        buf = bytearray(aligned)
        for k in range(stride):
            groups = aligned // _GROUP
            hsize = (groups + 3) // 4
            if pos + hsize > end:
                raise MeshoptError("meshopt data is cut short")
            header = data[pos:pos + hsize]
            pos += hsize
            for g in range(groups):
                bits = (header[g // 4] >> ((g % 4) * 2)) & 3
                if pos > end:
                    raise MeshoptError("meshopt data is cut short")
                pos = _bytes_group(data, pos, bits, buf, g * _GROUP)
            enc = np.frombuffer(bytes(buf[:n]), np.uint8)
            delta = (enc >> 1) ^ (0 - (enc & 1)).astype(np.uint8)          # unzigzag, 8 bit
            col = (np.cumsum(delta, dtype=np.uint64) + last[k]) & 0xFF
            out[start:start + n, k] = col.astype(np.uint8)
            last[k] = int(col[-1])
    if pos != end:
        raise MeshoptError("meshopt data has the wrong length")
    return out.tobytes()


def _vbyte(data: bytes, pos: int) -> tuple[int, int]:
    v, shift = 0, 0
    for _ in range(5):
        b = data[pos]
        pos += 1
        v |= (b & 0x7F) << shift
        if b < 0x80:
            return v, pos
        shift += 7
    return v, pos


def _index(data: bytes, pos: int, last: int) -> tuple[int, int]:
    v, pos = _vbyte(data, pos)
    d = (v >> 1) ^ -(v & 1)
    return (last + d) & 0xFFFFFFFF, pos


def decode_index_buffer(data: bytes, count: int) -> np.ndarray:
    """Triangle lists (mode TRIANGLES): uint32 array of `count` indices."""
    if count % 3 or len(data) < 1 + count // 3 + 16 or data[0] & 0xF0 != 0xE0:
        raise MeshoptError("not meshopt triangle data")
    version = data[0] & 0x0F
    if version > 1:
        raise MeshoptError(f"meshopt index format version {version} isn't supported")
    out = np.zeros(count, np.uint32)
    edge = [[0, 0] for _ in range(16)]
    vert = [0] * 16
    eo = vo = 0
    nxt = last = 0
    fecmax = 13 if version >= 1 else 15
    code = 1
    pos = 1 + count // 3
    aux = data[len(data) - 16:]
    safe_end = len(data) - 16
    for i in range(0, count, 3):
        ct = data[code]
        code += 1
        if ct < 0xF0:
            fe = ct >> 4
            a, b = edge[(eo - 1 - fe) & 15]
            fec = ct & 15
            if fec < fecmax:
                c = nxt if fec == 0 else vert[(vo - 1 - fec) & 15]
                if fec == 0:
                    nxt += 1
                    vert[vo] = c
                    vo = (vo + 1) & 15
            else:
                if fec != 15:
                    c = (last + (fec - (fec ^ 3))) & 0xFFFFFFFF
                else:
                    c, pos = _index(data, pos, last)
                last = c
                vert[vo] = c
                vo = (vo + 1) & 15
            out[i:i + 3] = (a, b, c)
            edge[eo] = [c, b]
            eo = (eo + 1) & 15
            edge[eo] = [a, c]
            eo = (eo + 1) & 15
        else:
            if ct < 0xFE:
                ca = aux[ct & 15]
                feb, fec = ca >> 4, ca & 15
                a = nxt
                nxt += 1
                b = nxt if feb == 0 else vert[(vo - feb) & 15]
                nxt += feb == 0
                c = nxt if fec == 0 else vert[(vo - fec) & 15]
                nxt += fec == 0
                push_b, push_c = feb == 0, fec == 0
            else:
                ca = data[pos]
                pos += 1
                fea = 0 if ct == 0xFE else 15
                feb, fec = ca >> 4, ca & 15
                if ca == 0:
                    nxt = 0
                if fea == 0:
                    a = nxt
                    nxt += 1
                else:
                    a = 0
                if feb == 0:
                    b = nxt
                    nxt += 1
                else:
                    b = vert[(vo - feb) & 15]
                if fec == 0:
                    c = nxt
                    nxt += 1
                else:
                    c = vert[(vo - fec) & 15]
                if fea == 15:
                    a, pos = _index(data, pos, last)
                    last = a
                if feb == 15:
                    b, pos = _index(data, pos, last)
                    last = b
                if fec == 15:
                    c, pos = _index(data, pos, last)
                    last = c
                push_b, push_c = feb in (0, 15), fec in (0, 15)
            out[i:i + 3] = (a, b, c)
            vert[vo] = a
            vo = (vo + 1) & 15
            vert[vo] = b
            vo = (vo + push_b) & 15
            vert[vo] = c
            vo = (vo + push_c) & 15
            edge[eo] = [b, a]
            eo = (eo + 1) & 15
            edge[eo] = [c, b]
            eo = (eo + 1) & 15
            edge[eo] = [a, c]
            eo = (eo + 1) & 15
        if pos > safe_end:
            raise MeshoptError("meshopt index data is cut short")
    if pos != safe_end:
        raise MeshoptError("meshopt index data has the wrong length")
    return out


def decode_index_sequence(data: bytes, count: int) -> np.ndarray:
    """Index sequences (mode INDICES): uint32 array."""
    if len(data) < 1 + count + 4 or data[0] & 0xF0 != 0xD0:
        raise MeshoptError("not meshopt index sequence data")
    out = np.zeros(count, np.uint32)
    base = [0, 0]
    pos = 1
    end = len(data) - 4
    for i in range(count):
        v, pos = _vbyte(data, pos)
        cur = v & 1
        v >>= 1
        d = (v >> 1) ^ -(v & 1)
        base[cur] = (base[cur] + d) & 0xFFFFFFFF
        out[i] = base[cur]
        if pos > end:
            raise MeshoptError("meshopt index data is cut short")
    if pos != end:
        raise MeshoptError("meshopt index data has the wrong length")
    return out


def _round(x: np.ndarray) -> np.ndarray:
    return np.trunc(x + np.where(x >= 0, 0.5, -0.5)).astype(np.int64)


def filter_octahedral(raw: bytes, count: int, stride: int) -> bytes:
    t = np.int8 if stride == 4 else np.int16
    a = np.frombuffer(raw, t).reshape(count, 4).astype(np.float32)
    mx = float((1 << (np.dtype(t).itemsize * 8 - 1)) - 1)
    x, y = a[:, 0].copy(), a[:, 1].copy()
    z = a[:, 2] - np.abs(x) - np.abs(y)
    tt = np.minimum(z, 0)
    x += np.where(x >= 0, tt, -tt)
    y += np.where(y >= 0, tt, -tt)
    ln = np.sqrt(x * x + y * y + z * z)
    s = mx / np.where(ln > 0, ln, 1)
    out = np.frombuffer(raw, t).reshape(count, 4).copy()
    out[:, 0], out[:, 1], out[:, 2] = _round(x * s), _round(y * s), _round(z * s)
    return out.tobytes()


def filter_quaternion(raw: bytes, count: int) -> bytes:
    a = np.frombuffer(raw, np.int16).reshape(count, 4)
    sf = (a[:, 3].astype(np.int32) | 3).astype(np.float32)
    ss = np.float32(1 / np.sqrt(2.0)) / sf
    x, y, z = (a[:, k].astype(np.float32) * ss for k in range(3))
    ww = 1 - x * x - y * y - z * z
    w = np.sqrt(np.maximum(ww, 0))
    qc = (a[:, 3] & 3).astype(np.int64)
    out = np.zeros((count, 4), np.int16)
    rows = np.arange(count)
    for vals, k in ((x, 1), (y, 2), (z, 3), (w, 0)):
        out[rows, (qc + k) & 3] = _round(vals * 32767)
    return out.tobytes()


def filter_exponential(raw: bytes, count: int, stride: int) -> bytes:
    v = np.frombuffer(raw, np.uint32).astype(np.int64)
    m = ((v << 8) & 0xFFFFFFFF).astype(np.uint32).view(np.int32).astype(np.int64) >> 8
    e = v.astype(np.uint32).view(np.int32).astype(np.int64) >> 24
    return np.ldexp(m.astype(np.float32), e.astype(np.int32)).astype(np.float32).tobytes()


def decode(data: bytes, count: int, stride: int, mode: str, flt: str = "NONE") -> bytes:
    """The bytes of a bufferView compressed with EXT_meshopt_compression."""
    if count * stride > 512 * 1024 * 1024:
        raise MeshoptError("meshopt buffer too large")
    if mode == "ATTRIBUTES":
        raw = decode_vertex_buffer(data, count, stride)
        if flt == "OCTAHEDRAL":
            raw = filter_octahedral(raw, count, stride)
        elif flt == "QUATERNION":
            raw = filter_quaternion(raw, count)
        elif flt == "EXPONENTIAL":
            raw = filter_exponential(raw, count * stride // 4, stride)
        elif flt not in ("NONE", None, ""):
            raise MeshoptError(f"meshopt filter {flt} isn't supported")
        return raw
    if mode in ("TRIANGLES", "INDICES"):
        idx = decode_index_buffer(data, count) if mode == "TRIANGLES" else decode_index_sequence(data, count)
        return idx.astype(np.uint16 if stride == 2 else np.uint32).tobytes()
    raise MeshoptError(f"meshopt mode {mode} isn't supported")

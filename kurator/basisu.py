"""Basis Universal textures (ETC1S and UASTC LDR 4x4) to RGBA pixels, in Python and numpy.

Changed file notice (Apache License 2.0, section 4b): this is a Python port of the decoding parts
of transcoder/basisu_transcoder.cpp from Basis Universal (Copyright (C) 2016-2026 Binomial LLC,
Apache License 2.0, https://github.com/BinomialLLC/basis_universal; see NOTICE), rewritten for
Kurator: it only unpacks to plain pixels (the original also transcodes to many GPU formats).
The output matches the original's RGBA32 output exactly (tests/data/basis has reference pictures
unpacked by the official tool).

* ETC1S (BasisLZ supercompression): a codebook of colour endpoints and selector patterns shared
  by all the images, Huffman-coded block indices per image, an optional second (alpha) slice.
  The index decoding is sequential, so it is plain Python; turning indices into pixels is numpy.
* UASTC LDR 4x4: 128-bit blocks in 19 modes (ASTC-style endpoints and weights). Blocks are
  grouped by mode and partition pattern, so every field is at a fixed bit position in a group
  and the whole group is decoded at once with numpy.

The newer Basis formats (UASTC HDR, ASTC HDR 6x6, XUASTC LDR, XUBC7) are not handled here.
"""

from __future__ import annotations

import struct

import numpy as np


class BasisError(ValueError):
    pass


MAX_ETC1S_BLOCKS = 2048 * 2048          # an 8192 x 8192 image: about 10 s of index decoding


# ---------------------------------------------------------------------------------------------
# Bit reader and Huffman tables (bitwise_decoder, huffman_decoding_table)

_SORTED_CODELENGTH_CODES = (17, 18, 19, 20, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15, 16)


class _Bits:
    """LSB-first bit reader. Reading past the end gives zero bits, as in the reference."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes):
        self.data = bytes(data) + b"\0" * 8
        self.pos = 0

    def get(self, n: int) -> int:
        p = self.pos
        self.pos = p + n
        q = p >> 3
        return (int.from_bytes(self.data[q:q + 6], "little") >> (p & 7)) & ((1 << n) - 1)

    def huff(self, table) -> int:
        lut, nbits = table
        p = self.pos
        q = p >> 3
        e = lut[(int.from_bytes(self.data[q:q + 4], "little") >> (p & 7)) & ((1 << nbits) - 1)]
        self.pos = p + (e >> 16)
        return e & 0xFFFF

    def vlc(self, chunk_bits: int) -> int:
        size = 1 << chunk_bits
        v = ofs = 0
        while True:
            s = self.get(chunk_bits + 1)
            v |= (s & (size - 1)) << ofs
            ofs += chunk_bits
            if not s & size or ofs >= 32:
                return v

    def table(self):
        """read_huffman_table: None for an empty table."""
        total = self.get(14)
        if not total:
            return None
        n = self.get(5)
        if not 1 <= n <= 21:
            raise BasisError("bad Huffman table")
        cl = [0] * 21
        for i in range(n):
            cl[_SORTED_CODELENGTH_CODES[i]] = self.get(3)
        cl_table = _make_table(cl)
        sizes = [0] * total
        cur = 0
        while cur < total:
            c = self.huff(cl_table)
            if c <= 16:
                sizes[cur] = c
                cur += 1
            elif c == 17:
                cur += self.get(3) + 3
            elif c == 18:
                cur += self.get(7) + 11
            else:
                if not cur:
                    raise BasisError("bad Huffman table")
                run = self.get(2) + 3 if c == 19 else self.get(7) + 7
                prev = sizes[cur - 1]
                if not prev or cur + run > total:
                    raise BasisError("bad Huffman table")
                sizes[cur:cur + run] = [prev] * run
                cur += run
        if cur != total:
            raise BasisError("bad Huffman table")
        return _make_table(sizes)


def _make_table(sizes: list[int]):
    """Canonical Huffman decoding table: lut[next bits, reversed] = (length << 16) | symbol."""
    counts = [0] * 32
    for s in sizes:
        if s > 16:
            raise BasisError("bad Huffman code size")
        counts[s] += 1
    used = total = 0
    next_code = [0] * 33
    for i in range(1, 31):
        used += counts[i]
        total = (total + counts[i]) << 1
        next_code[i + 1] = total
    if total != 1 << 31 and used != 1:
        raise BasisError("bad Huffman code")
    nbits = max(sizes) or 1
    size = 1 << nbits
    lut = [0] * size
    for sym, n in enumerate(sizes):
        if not n:
            continue
        code = next_code[n]
        next_code[n] += 1
        rev = int(f"{code:0{n}b}"[::-1], 2)
        step = 1 << n
        lut[rev::step] = [(n << 16) | sym] * len(range(rev, size, step))
    return lut, nbits


# ---------------------------------------------------------------------------------------------
# ETC1S

_INTEN = np.array([[-8, -2, 2, 8], [-17, -5, 5, 17], [-29, -9, 9, 29], [-42, -13, 13, 42],
                   [-60, -18, 18, 60], [-80, -24, 24, 80], [-106, -33, 33, 106], [-183, -47, 47, 183]], np.int32)


class ETC1SCodebook:
    """The global data of a BasisLZ KTX2 file: endpoint/selector codebooks and the slice tables."""

    def __init__(self, sgd: bytes, image_count: int):
        if len(sgd) < 20:
            raise BasisError("ETC1S global data is missing")
        ne, ns, elen, slen, tlen, xlen = struct.unpack_from("<HHIIII", sgd, 0)
        if not (ne and ns and elen and slen and tlen):
            raise BasisError("bad ETC1S global data")
        pos = 20
        if pos + 20 * image_count + elen + slen + tlen + xlen > len(sgd):
            raise BasisError("ETC1S global data is cut off")
        self.images = [struct.unpack_from("<5I", sgd, pos + 20 * i) for i in range(image_count)]
        pos += 20 * image_count
        endpoints, pos = sgd[pos:pos + elen], pos + elen
        selectors, pos = sgd[pos:pos + slen], pos + slen
        tables = sgd[pos:pos + tlen]
        self._tables(tables)
        self._palettes(ne, endpoints, ns, selectors)

    def _tables(self, data: bytes) -> None:
        b = _Bits(data)
        self.pred_model = b.table()
        self.delta_model = b.table()
        self.selector_model = b.table()
        self.rle_model = b.table()
        if None in (self.pred_model, self.delta_model, self.selector_model, self.rle_model):
            raise BasisError("bad ETC1S tables")
        self.history_size = b.get(13)
        if not self.history_size:
            raise BasisError("bad ETC1S tables")

    def _palettes(self, ne: int, edata: bytes, ns: int, sdata: bytes) -> None:
        b = _Bits(edata)
        m0, m1, m2, mi = b.table(), b.table(), b.table(), b.table()
        if None in (m0, m1, m2, mi):
            raise BasisError("bad ETC1S endpoint codebook")
        grey = b.get(1)
        color = np.zeros((ne, 3), np.int32)
        inten = np.zeros(ne, np.int32)
        prev = [16, 16, 16]
        prev_i = 0
        comps = 1 if grey else 3
        huff = b.huff
        for i in range(ne):
            prev_i = (huff(mi) + prev_i) & 7
            inten[i] = prev_i
            for c in range(comps):
                p = prev[c]
                d = huff(m0) if p <= 9 else huff(m1) if p <= 21 else huff(m2)
                prev[c] = (p + d) & 31
            color[i] = prev if not grey else (prev[0],) * 3
        self.color5, self.inten = color, inten

        b = _Bits(sdata)
        if b.get(1):
            raise BasisError("ETC1S global selector codebooks are not supported")
        if b.get(1):
            raise BasisError("ETC1S hybrid selector codebooks are not supported")
        rows = np.zeros((ns, 4), np.uint8)
        if b.get(1):                                     # raw selectors
            for i in range(ns):
                for j in range(4):
                    rows[i, j] = b.get(8)
        else:
            model = b.table()
            if ns > 1 and model is None:
                raise BasisError("bad ETC1S selector codebook")
            prev_b = [b.get(8) for _ in range(4)]
            rows[0] = prev_b
            for i in range(1, ns):
                for j in range(4):
                    prev_b[j] ^= b.huff(model)
                rows[i] = prev_b
        # selectors[s, y, x] in 0..3 (row byte y holds x at bits 2x)
        self.selectors = (rows[:, :, None] >> np.array([0, 2, 4, 6], np.uint8)) & 3

    def colors(self) -> np.ndarray:
        """(endpoints, 4, 3) uint8: the four colours each endpoint can give."""
        base = (self.color5 << 3) | (self.color5 >> 2)
        return np.clip(base[:, None, :] + _INTEN[self.inten][:, :, None], 0, 255).astype(np.uint8)

    def slice_indices(self, data: bytes, nbx: int, nby: int) -> tuple[np.ndarray, np.ndarray]:
        """transcode_slice: (endpoint index, selector index) of every block, each (nby, nbx)."""
        ne, ns = len(self.inten), len(self.selectors)
        b = _Bits(data)
        huff, vlc = b.huff, b.vlc
        pred_model, delta_model = self.pred_model, self.delta_model
        sel_model, rle_model = self.selector_model, self.rle_model
        hist_n = self.history_size
        history = [0] * hist_n
        rover = hist_n // 2
        rle_symbol = hist_n + ns
        total = nbx * nby
        ep_out = [0] * total
        sel_out = [0] * total
        preds = [[0] * nbx, [0] * nbx]            # pred bits for the next row (per even column)
        ep_rows = [[0] * nbx, [0] * nbx]          # endpoint index per column, this row and the last
        cur_bits = prev_sym = repeat = prev_ep = 0
        rle = 0
        k = 0
        for by in range(nby):
            cur = by & 1
            row, above = ep_rows[cur], ep_rows[cur ^ 1]
            for bx in range(nbx):
                if not bx & 1:
                    if not cur:
                        if repeat:
                            repeat -= 1
                            cur_bits = prev_sym
                        else:
                            cur_bits = huff(pred_model)
                            if cur_bits == 256:
                                repeat = vlc(4) + 3 - 1
                                cur_bits = prev_sym
                            else:
                                prev_sym = cur_bits
                        preds[1][bx] = cur_bits >> 4
                    else:
                        cur_bits = preds[1][bx]
                pred = cur_bits & 3
                cur_bits >>= 2
                if pred == 0:
                    if not bx:
                        raise BasisError("bad ETC1S data (left)")
                    ep = prev_ep
                elif pred == 1:
                    if not by:
                        raise BasisError("bad ETC1S data (up)")
                    ep = above[bx]
                elif pred == 2:
                    if not bx or not by:
                        raise BasisError("bad ETC1S data (up-left)")
                    ep = above[bx - 1]
                else:
                    ep = huff(delta_model) + prev_ep
                    if ep >= ne:
                        ep -= ne
                row[bx] = ep
                prev_ep = ep

                if rle:
                    rle -= 1
                    sym = ns
                else:
                    sym = huff(sel_model)
                    if sym == rle_symbol:
                        run = huff(rle_model)
                        rle = (vlc(7) if run == 63 else run) + 3
                        if rle > total:
                            raise BasisError("bad ETC1S data (run)")
                        sym = ns
                        rle -= 1
                if sym >= ns:
                    h = sym - ns
                    if h >= hist_n:
                        raise BasisError("bad ETC1S data (history)")
                    sel = history[h]
                    if h:
                        history[h >> 1], history[h] = history[h], history[h >> 1]
                else:
                    sel = sym
                    history[rover] = sel
                    rover += 1
                    if rover == hist_n:
                        rover = hist_n // 2
                if ep >= ne or sel >= ns:
                    raise BasisError("bad ETC1S data (index)")
                ep_out[k] = ep
                sel_out[k] = sel
                k += 1
        shape = (nby, nbx)
        return (np.array(ep_out, np.int32).reshape(shape), np.array(sel_out, np.int32).reshape(shape))

    def slice_pixels(self, data: bytes, nbx: int, nby: int) -> np.ndarray:
        """(nby*4, nbx*4, 3) uint8."""
        ep, sel = self.slice_indices(data, nbx, nby)
        px = self.colors()[ep[:, :, None, None], self.selectors[sel]]      # (nby, nbx, 4, 4, 3)
        return px.transpose(0, 2, 1, 3, 4).reshape(nby * 4, nbx * 4, 3)


def etc1s_image(cb: ETC1SCodebook, level_data: bytes, image_index: int, width: int, height: int,
                alpha: bool) -> np.ndarray:
    """(height, width, 4) uint8 RGBA of one image (one level/layer/face) of an ETC1S texture."""
    if image_index >= len(cb.images):
        raise BasisError("ETC1S image is missing")
    _flags, rgb_off, rgb_len, a_off, a_len = cb.images[image_index]
    nbx, nby = (width + 3) // 4, (height + 3) // 4
    if nbx * nby > MAX_ETC1S_BLOCKS:
        raise BasisError("ETC1S texture too big to unpack without smaller mip levels")
    if rgb_off + rgb_len > len(level_data) or a_off + a_len > len(level_data):
        raise BasisError("ETC1S slice is cut off")
    out = np.empty((nby * 4, nbx * 4, 4), np.uint8)
    out[..., :3] = cb.slice_pixels(level_data[rgb_off:rgb_off + rgb_len], nbx, nby)
    if alpha and a_len:
        out[..., 3] = cb.slice_pixels(level_data[a_off:a_off + a_len], nbx, nby)[..., 1]
    else:
        out[..., 3] = 255
    return out[:height, :width]


# ---------------------------------------------------------------------------------------------
# UASTC LDR 4x4

_UASTC_HUFF_MODES = bytes([
    11, 0, 10, 3, 11, 15, 12, 7, 11, 18, 10, 5, 11, 14, 12, 9, 11, 0, 10, 4, 11, 16, 12, 8, 11, 18, 10, 6, 11, 2, 12, 13,
    11, 0, 10, 3, 11, 17, 12, 7, 11, 18, 10, 5, 11, 14, 12, 9, 11, 0, 10, 4, 11, 1, 12, 8, 11, 18, 10, 6, 11, 2, 12, 13,
    11, 0, 10, 3, 11, 19, 12, 7, 11, 18, 10, 5, 11, 14, 12, 9, 11, 0, 10, 4, 11, 16, 12, 8, 11, 18, 10, 6, 11, 2, 12, 13,
    11, 0, 10, 3, 11, 17, 12, 7, 11, 18, 10, 5, 11, 14, 12, 9, 11, 0, 10, 4, 11, 1, 12, 8, 11, 18, 10, 6, 11, 2, 12, 13])
_CODE_LEN = (4, 6, 5, 5, 5, 5, 5, 5, 5, 5, 3, 2, 3, 5, 5, 7, 6, 6, 4)
_WEIGHT_BITS = (4, 2, 3, 2, 2, 3, 2, 2, 0, 2, 4, 2, 3, 1, 2, 4, 2, 2, 5)
_ENDPOINT_RANGE = (19, 20, 8, 7, 12, 20, 18, 12, 0, 8, 13, 13, 19, 20, 20, 20, 20, 20, 11)
_SUBSETS = (1, 1, 2, 3, 2, 1, 1, 2, 0, 2, 1, 1, 1, 1, 1, 1, 2, 1, 1)
_PLANES = (1, 1, 1, 1, 1, 1, 2, 1, 0, 1, 1, 2, 1, 2, 1, 1, 1, 2, 1)
_COMPS = (3, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 2, 2, 2, 3)
_HINT_BITS = (15, 15, 15, 15, 15, 15, 15, 15, 0, 23, 17, 17, 17, 23, 23, 23, 23, 23, 15)
_BISE = ((1, 0, 0), (0, 1, 0), (2, 0, 0), (0, 0, 1), (1, 1, 0), (3, 0, 0), (1, 0, 1), (2, 1, 0), (4, 0, 0),
         (2, 0, 1), (3, 1, 0), (5, 0, 0), (3, 0, 1), (4, 1, 0), (6, 0, 0), (4, 0, 1), (5, 1, 0), (7, 0, 0),
         (5, 0, 1), (6, 1, 0), (8, 0, 0))
_UNQUANT_PARAMS = {4: ("000000000", 204), 6: ("000000000", 113), 7: ("b000b0bb0", 93), 9: ("b0000bb00", 54),
                   10: ("cb000cbcb", 44), 12: ("cb0000cbc", 26), 13: ("dcb000dcb", 22), 15: ("dcb0000dc", 13),
                   16: ("edcb000ed", 11), 18: ("edcb0000e", 6), 19: ("fedcb000f", 5)}
_WEIGHTS = {1: (0, 64), 2: (0, 21, 43, 64), 3: (0, 9, 18, 27, 37, 46, 55, 64),
            4: (0, 4, 8, 12, 17, 21, 25, 29, 35, 39, 43, 47, 52, 56, 60, 64),
            5: (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30,
                34, 36, 38, 40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 62, 64)}


def _patterns(rows: str) -> np.ndarray:
    return np.array([[int(c) for c in r] for r in rows.split()], np.int32)


_PATTERNS2 = _patterns(
    "0011001100110011 0001000100010001 1000100010001000 0001001100110111 1111111011101100 0011011101111111 "
    "1110110010000000 1111111011001000 0000000000010011 1100100000000000 0000000101111111 1111111111101000 "
    "1110100000000000 1111111100000000 0000111111111111 1111111111110000 1000111011111111 1111111101110001 "
    "0111001100010000 0011000100000000 0000100011001110 1111111101110011 1000110011001110 0011000100010000 "
    "1111011101110011 0110011001100110 1111000000001111 1010101010101010 1111000011110000 1001001101101100")
_PATTERNS3 = _patterns(
    "0000000011221122 1111111100002222 1111000000002222 1111222200000000 1120112011201120 0112011201120112 "
    "0211021102110211 2000200021112111 2012201220122012 1111000022221111 0022001100110022")
_PATTERNS_3TO2 = _patterns(
    "0000111100000000 0010001000100010 1100110010000000 0000000100110011 1111111100001111 0100010001000100 "
    "0001001111111111 0111001100110011 1100000000111100 0111011100000000 0000000011101110 1100000000001100 "
    "0111001100000000 0000000111111111 1111111111110110 1100110011001000 1111111110001000 0011011011001000 "
    "1111011100000000")
_ANCHORS2 = ((0, 2), (0, 3), (1, 0), (0, 3), (7, 0), (0, 2), (3, 0), (7, 0), (0, 11), (2, 0), (0, 7), (11, 0),
             (3, 0), (8, 0), (0, 4), (12, 0), (1, 0), (8, 0), (0, 1), (0, 2), (0, 4), (8, 0), (1, 0), (0, 2),
             (4, 0), (0, 1), (4, 0), (1, 0), (4, 0), (1, 0))
_ANCHORS3 = ((0, 8, 10), (8, 0, 12), (4, 0, 12), (8, 0, 4), (3, 0, 2), (0, 1, 3), (0, 2, 1), (1, 9, 0),
             (1, 2, 0), (4, 0, 8), (0, 6, 2))
_ANCHORS_3TO2 = ((0, 4), (0, 2), (2, 0), (0, 7), (8, 0), (0, 1), (0, 3), (0, 1), (2, 0), (0, 1), (0, 8), (2, 0),
                 (0, 1), (0, 7), (12, 0), (2, 0), (9, 0), (0, 2), (4, 0))


def _unquant(value: int, rng: int) -> int:
    """unquant_astc_endpoint_val: an ASTC colour endpoint value to 0..255."""
    bits, trits, quints = _BISE[rng]
    if not trits and not quints:
        val, left = 0, 8
        while left > 0:
            n = min(left, bits)
            val |= (value >> (bits - n)) << (left - n)
            left -= n
        return val
    low = value & ((1 << bits) - 1)
    d = value >> bits
    pat, c = _UNQUANT_PARAMS[rng]
    a = 511 if low & 1 else 0
    b = 0
    for ch in pat:
        b = (b << 1) | (0 if ch == "0" else (low >> (ord(ch) - 97)) & 1)
    val = (d * c + b) ^ a
    return (a & 0x80) | (val >> 2)


_UNQUANT = np.zeros((21, 256), np.int32)
for _r, (_b, _t, _q) in enumerate(_BISE):
    if (_t or _q) and _r not in _UNQUANT_PARAMS:          # ranges 0-2 and 0-4: not valid endpoint ranges
        continue
    for _v in range((1 + 2 * _t + 4 * _q) << _b):
        _UNQUANT[_r, _v] = _unquant(_v, _r)


def _interp(lo: np.ndarray, hi: np.ndarray, w: int) -> np.ndarray:
    lo = (lo << 8) | lo
    hi = (hi << 8) | hi
    return ((lo * (64 - w) + hi * w + 32) >> 6) >> 8


def _fields(bits: np.ndarray, ofs: int, widths: list[int]) -> tuple[np.ndarray, int]:
    """Consecutive little-endian fields from a (n, 128) bit matrix: (n, len(widths)) and the end offset."""
    out = np.zeros((bits.shape[0], len(widths)), np.int32)
    for i, w in enumerate(widths):
        if w:
            out[:, i] = bits[:, ofs:ofs + w].astype(np.int32) @ (1 << np.arange(w, dtype=np.int32))
        ofs += w
    return out, ofs


def _uastc_group(blocks: np.ndarray, mode: int, pattern: int) -> np.ndarray:
    """(n, 16, 4) uint8 pixels of blocks that share a mode (and partition pattern)."""
    n = len(blocks)
    bits = np.unpackbits(blocks, axis=1, bitorder="little")
    ofs = _CODE_LEN[mode]
    if mode == 8:                                            # solid colour
        rgba, _ = _fields(bits, ofs, [8, 8, 8, 8])
        return np.repeat(rgba[:, None, :].astype(np.uint8), 16, axis=1)
    ofs += _HINT_BITS[mode]
    subsets = _SUBSETS[mode]
    if mode in (2, 4, 7, 9, 16):
        ofs += 5
    elif mode == 3:
        ofs += 4
    planes = _PLANES[mode]
    if mode in (6, 11, 13):
        ccs, ofs = _fields(bits, ofs, [2])
        ccs = ccs[:, 0]
    else:
        ccs = np.full(n, 3, np.int32)
    comps = _COMPS[mode]
    nvals = comps * 2 * subsets
    rng = _ENDPOINT_RANGE[mode]
    ep_bits, trits, quints = _BISE[rng]
    digits = np.zeros((n, nvals), np.int32)
    if trits or quints:
        bundle, mul = (5, 3) if trits else (3, 5)
        ntq = (nvals + bundle - 1) // bundle
        widths = []
        for i in range(ntq):
            nb = 8 if trits else 7
            if i == ntq - 1:
                rem = nvals - (ntq - 1) * bundle
                nb = {1: 2, 2: 4, 3: 5, 4: 7}.get(rem, nb) if trits else {1: 3, 2: 5}.get(rem, nb)
            widths.append(nb)
        tq, ofs = _fields(bits, ofs, widths)
        for i in range(nvals):
            digits[:, i] = (tq[:, i // bundle] // mul ** (i % bundle)) % mul
    low, ofs = _fields(bits, ofs, [ep_bits] * nvals)
    values = _UNQUANT[rng][low | (digits << ep_bits)]                 # (n, nvals)

    wbits = _WEIGHT_BITS[mode]
    if subsets == 3:
        part, anchors = _PATTERNS3[pattern], _ANCHORS3[pattern]
    elif subsets == 2:
        part, anchors = (_PATTERNS_3TO2[pattern], _ANCHORS_3TO2[pattern]) if mode == 7 else \
            (_PATTERNS2[pattern], _ANCHORS2[pattern])
    else:
        part, anchors = np.zeros(16, np.int32), (0,)
    if planes == 2:
        widths = [wbits - 1, wbits - 1] + [wbits] * 30
    else:
        widths = [wbits - 1 if i in anchors else wbits for i in range(16)]
    weights, _ = _fields(bits, ofs, widths)                           # (n, 16 * planes)

    wtab = _WEIGHTS[wbits]
    levels = 1 << wbits
    # endpoints per subset as RGBA (lo, hi)
    ep = values.reshape(n, subsets, comps, 2)
    if comps == 2:                                                    # luminance + alpha
        lum, alp = ep[:, :, 0], ep[:, :, 1]
        ep = np.stack([lum, lum, lum, alp], axis=2)
    elif comps == 3:
        ep = np.concatenate([ep, np.full((n, subsets, 1, 2), 255, np.int32)], axis=2)
    lo, hi = ep[..., 0], ep[..., 1]                                    # (n, subsets, 4)
    pal = np.stack([_interp(lo, hi, wtab[k]) for k in range(levels)], axis=2)   # (n, subsets, levels, 4)
    if comps == 3:
        pal[..., 3] = 255
    rows = np.arange(n)[:, None]
    if planes == 1:
        px = pal[rows, part[None, :], weights]                         # (n, 16, 4)
    else:
        w0, w1 = weights[:, 0::2], weights[:, 1::2]
        px = pal[rows, 0, w0]
        alt = pal[rows, 0, w1]
        sel = np.arange(4)[None, None, :] == ccs[:, None, None]
        px = np.where(sel, alt, px)
    return px.astype(np.uint8)


def uastc_image(data: bytes, width: int, height: int) -> np.ndarray:
    """(height, width, 4) uint8 RGBA of one UASTC LDR 4x4 image (raw 16-byte blocks)."""
    nbx, nby = (width + 3) // 4, (height + 3) // 4
    need = nbx * nby * 16
    if len(data) < need:
        raise BasisError("UASTC image is cut off")
    blocks = np.frombuffer(data, np.uint8, need).reshape(-1, 16)
    out = np.empty((len(blocks), 16, 4), np.uint8)
    for start in range(0, len(blocks), _CHUNK):                    # bounded memory for big textures
        out[start:start + _CHUNK] = _uastc_blocks(blocks[start:start + _CHUNK])
    img = out.reshape(nby, nbx, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(nby * 4, nbx * 4, 4)
    return img[:height, :width]


_CHUNK = 1 << 16


def _uastc_blocks(blocks: np.ndarray) -> np.ndarray:
    modes = np.frombuffer(_UASTC_HUFF_MODES, np.uint8)[blocks[:, 0] & 127]
    if (modes >= 19).any():
        raise BasisError("bad UASTC block")
    # the partition pattern sits right after the mode code and the hints
    pattern = np.zeros(len(blocks), np.int32)
    for m in (2, 3, 4, 7, 9, 16):
        sel = np.flatnonzero(modes == m)
        if len(sel):
            bits = np.unpackbits(blocks[sel], axis=1, bitorder="little")
            p, _ = _fields(bits, _CODE_LEN[m] + _HINT_BITS[m], [4 if m == 3 else 5])
            pattern[sel] = p[:, 0]
            limit = 11 if m == 3 else 19 if m == 7 else 30
            if (p[:, 0] >= limit).any():
                raise BasisError("bad UASTC partition pattern")
    out = np.empty((len(blocks), 16, 4), np.uint8)
    key = modes.astype(np.int32) * 64 + pattern
    for k in np.unique(key):
        sel = np.flatnonzero(key == k)
        out[sel] = _uastc_group(blocks[sel], int(k) // 64, int(k) % 64)
    return out

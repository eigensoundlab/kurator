"""Sound analysis: detailed waveforms (min / max / RMS per channel), spectrograms, loudness (LUFS,
ITU-R BS.1770 / EBU R128 integrated), tempo (BPM) and musical key.

Decoding streams the file in blocks (soundfile; ffmpeg for M4A/AAC/WMA), so long music never has
to fit in memory as one array. Tempo and key are estimates, shown as such.
"""

from __future__ import annotations

import math
import shutil
import subprocess

import numpy as np

MAX_ANALYSE_SECONDS = 600     # loudness over the first 10 minutes; tempo/key over the first 2


# ---------------------------------------------------------------- decoding
def blocks(path: str, blocksize: int = 65536):
    """Yield (samplerate, channels, frames-total or None) once, then float32 blocks (frames x channels)."""
    import soundfile as sf
    try:
        f = sf.SoundFile(path)
    except Exception:
        f = None
    if f is not None:
        with f:
            yield f.samplerate, f.channels, f.frames
            for b in f.blocks(blocksize=blocksize, dtype="float32", always_2d=True):
                yield b
        return
    ff = shutil.which("ffmpeg")
    if not ff:
        raise ValueError("can't decode this sound (install ffmpeg for M4A/AAC/WMA)")
    sr = 44100
    p = subprocess.Popen([ff, "-v", "error", "-i", path, "-ac", "2", "-ar", str(sr), "-f", "f32le", "pipe:1"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    yield sr, 2, None
    try:
        while True:
            raw = p.stdout.read(blocksize * 8)
            if not raw:
                break
            a = np.frombuffer(raw[: len(raw) // 8 * 8], dtype="<f4").reshape(-1, 2)
            yield a
    finally:
        p.kill()


def wave_data(path: str, bins: int = 1600) -> dict:
    """Per channel min, max and RMS in `bins` equal slices of the whole sound."""
    it = blocks(path)
    sr, ch, frames = next(it)
    data = None
    if frames:   # known length: fill the bins as blocks arrive
        edges = np.linspace(0, frames, bins + 1).astype(np.int64)
        mn = np.full((ch, bins), np.inf, np.float32)
        mx = np.full((ch, bins), -np.inf, np.float32)
        ss = np.zeros((ch, bins), np.float64)
        cnt = np.zeros(bins, np.int64)
        pos = 0
        for b in it:
            n = len(b)
            idx = np.searchsorted(edges, np.arange(pos, pos + n), side="right") - 1
            idx = np.clip(idx, 0, bins - 1)
            starts = np.r_[0, np.nonzero(np.diff(idx))[0] + 1]
            for c in range(ch):
                col = b[:, c]
                mn[c, idx[starts]] = np.minimum(mn[c, idx[starts]], np.minimum.reduceat(col, starts))
                mx[c, idx[starts]] = np.maximum(mx[c, idx[starts]], np.maximum.reduceat(col, starts))
                np.add.at(ss[c], idx[starts], np.add.reduceat(col.astype(np.float64) ** 2, starts))
            np.add.at(cnt, idx[starts], np.diff(np.r_[starts, n]))
            pos += n
        frames = pos
    else:        # streamed from ffmpeg: collect a downsampled copy first
        parts = [b for b in it]
        data = np.concatenate(parts) if parts else np.zeros((0, ch), np.float32)
        frames = len(data)
        sl = np.array_split(np.arange(frames), bins) if frames >= bins else [np.arange(frames)]
        mn = np.array([[data[s, c].min() if len(s) else 0 for s in sl] for c in range(ch)], np.float32)
        mx = np.array([[data[s, c].max() if len(s) else 0 for s in sl] for c in range(ch)], np.float32)
        ss = np.array([[float((data[s, c].astype(np.float64) ** 2).sum()) for s in sl] for c in range(ch)])
        cnt = np.array([len(s) for s in sl])
    ok = cnt > 0
    mn = np.where(np.isfinite(mn), mn, 0)
    mx = np.where(np.isfinite(mx), mx, 0)
    rms = np.sqrt(np.where(ok, ss / np.maximum(cnt, 1), 0))
    r3 = lambda a: [[round(float(x), 4) for x in row] for row in a]  # noqa: E731
    return {"samplerate": sr, "channels": ch, "duration": frames / sr if sr else 0, "bins": int(mn.shape[1]),
            "min": r3(mn), "max": r3(mx), "rms": r3(rms)}


def mono(path: str, rate: int = 11025, seconds: float | None = None) -> tuple[np.ndarray, int]:
    """Mono mix at about `rate` Hz (box-filter decimation, good enough for analysis)."""
    it = blocks(path)
    sr, ch, _ = next(it)
    k = max(1, int(round(sr / rate)))
    limit = int(seconds * sr) if seconds else None
    out, got = [], 0
    for b in it:
        m = b.mean(axis=1)
        if limit is not None and got + len(m) > limit:
            m = m[: limit - got]
        got += len(m)
        out.append(m)
        if limit is not None and got >= limit:
            break
    x = np.concatenate(out) if out else np.zeros(0, np.float32)
    if k > 1:
        x = x[: len(x) // k * k].reshape(-1, k).mean(axis=1)
    return x.astype(np.float32), int(sr / k)


# ---------------------------------------------------------------- loudness (BS.1770)
def _biquad_power(b, a, w):
    z = np.exp(-1j * w)
    h = (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return np.abs(h) ** 2


def k_weighting(freqs: np.ndarray, sr: float) -> np.ndarray:
    """|H(f)|^2 of the BS.1770 K-weighting (high shelf + RLB high-pass), for any sample rate."""
    w = 2 * np.pi * freqs / sr
    G, Q, fc = 4.0, 1 / math.sqrt(2), 1500.0
    A = 10 ** (G / 40)
    w0 = 2 * math.pi * fc / sr
    al = math.sin(w0) / (2 * Q)
    c = math.cos(w0)
    sh_b = [A * ((A + 1) + (A - 1) * c + 2 * math.sqrt(A) * al), -2 * A * ((A - 1) + (A + 1) * c),
            A * ((A + 1) + (A - 1) * c - 2 * math.sqrt(A) * al)]
    sh_a = [(A + 1) - (A - 1) * c + 2 * math.sqrt(A) * al, 2 * ((A - 1) - (A + 1) * c),
            (A + 1) - (A - 1) * c - 2 * math.sqrt(A) * al]
    fc2, Q2 = 38.0, 0.5
    w2 = 2 * math.pi * fc2 / sr
    al2 = math.sin(w2) / (2 * Q2)
    c2 = math.cos(w2)
    hp_b = [(1 + c2) / 2, -(1 + c2), (1 + c2) / 2]
    hp_a = [1 + al2, -2 * c2, 1 - al2]
    return _biquad_power(sh_b, sh_a, w) * _biquad_power(hp_b, hp_a, w)


def loudness(path: str) -> dict:
    """Integrated loudness (LUFS) and loudness range-ish stats, over the first 10 minutes."""
    it = blocks(path, 1 << 16)
    sr, ch, _ = next(it)
    win, hop = int(0.4 * sr), int(0.1 * sr)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    kw = k_weighting(freqs, sr)
    kw[1:-1] *= 2   # one-sided spectrum: count the mirrored half (Parseval)
    buf = np.zeros((0, ch), np.float32)
    powers = []
    limit = MAX_ANALYSE_SECONDS * sr
    seen = 0
    for b in it:
        buf = np.concatenate([buf, b])
        seen += len(b)
        nblk = (len(buf) - win) // hop + 1 if len(buf) >= win else 0
        if nblk > 0:
            idx = np.arange(win)[None, :] + hop * np.arange(nblk)[:, None]
            seg = buf[idx]                                      # blocks x win x ch
            spec = np.abs(np.fft.rfft(seg, axis=1)) ** 2        # blocks x freq x ch
            p = (spec * kw[None, :, None]).sum(axis=1) / (win * win)   # mean square per channel
            powers.append(p.sum(axis=1))                        # all channels weighted 1 (L, R, C)
            buf = buf[nblk * hop:]
        if seen >= limit:
            break
    if not powers:
        return {}
    z = np.concatenate(powers)
    lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    abs_g = z[lk > -70]
    if not len(abs_g):
        return {"lufs": None}
    rel = -0.691 + 10 * np.log10(abs_g.mean()) - 10
    gated = abs_g[(-0.691 + 10 * np.log10(np.maximum(abs_g, 1e-12))) > rel]
    integrated = -0.691 + 10 * math.log10(gated.mean()) if len(gated) else None
    st = lk[lk > -70]
    return {"lufs": round(integrated, 1) if integrated is not None else None,
            "max_momentary_lufs": round(float(st.max()), 1) if len(st) else None}


# ---------------------------------------------------------------- tempo and key
def tempo(x: np.ndarray, sr: int) -> tuple[float | None, float]:
    """(BPM, confidence 0..1) from the autocorrelation of an onset envelope."""
    if len(x) < sr * 8:
        return None, 0.0
    n_fft, hopn = 1024, 256
    frames = (len(x) - n_fft) // hopn
    if frames < 64:
        return None, 0.0
    idx = np.arange(n_fft)[None, :] + hopn * np.arange(frames)[:, None]
    mag = np.log1p(10 * np.abs(np.fft.rfft(x[idx] * np.hanning(n_fft), axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux = flux - np.convolve(flux, np.ones(16) / 16, mode="same")
    flux = np.maximum(flux, 0)
    if not flux.any():
        return None, 0.0
    fps = sr / hopn
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    lags = np.arange(len(ac))
    bpm_of = lambda lag: 60 * fps / lag  # noqa: E731
    lo, hi = int(60 * fps / 200), int(60 * fps / 60)
    if hi >= len(ac) or lo < 1:
        return None, 0.0
    cand = ac[lo:hi + 1].copy()
    bpms = bpm_of(lags[lo:hi + 1])
    cand *= np.exp(-0.5 * (np.log2(bpms / 120) / 0.9) ** 2)   # prefer common tempos around 120
    k = int(np.argmax(cand))
    lag = lo + k
    if 1 <= k < len(cand) - 1:   # parabolic refinement
        y0, y1, y2 = cand[k - 1], cand[k], cand[k + 1]
        d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) else 0
        lag = lag + d
    conf = float(ac[int(round(lag))] / ac[0]) if ac[0] else 0.0
    bpm = float(bpm_of(lag))
    bpm = round(bpm) if abs(bpm - round(bpm)) < 0.75 else round(bpm * 2) / 2   # most music sits on whole BPM
    return bpm, max(0.0, min(1.0, conf))


_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def key(x: np.ndarray, sr: int) -> tuple[str | None, float]:
    """('A minor', confidence) from an averaged chroma vector and Krumhansl-Kessler profiles."""
    if len(x) < sr * 4:
        return None, 0.0
    n_fft, hopn = 4096, 2048
    frames = (len(x) - n_fft) // hopn
    if frames < 4:
        return None, 0.0
    idx = np.arange(n_fft)[None, :] + hopn * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(n_fft), axis=1))
    f = np.fft.rfftfreq(n_fft, 1 / sr)
    band = (f >= 60) & (f <= 2000)
    pc = (np.round(12 * np.log2(f[band] / 440.0)) + 9) % 12
    chroma = np.zeros(12)
    np.add.at(chroma, pc.astype(int), (spec[:, band] ** 2).sum(axis=0))
    if not chroma.any():
        return None, 0.0
    chroma = np.sqrt(chroma)
    best, name = -2.0, None
    for t in range(12):
        for prof, mode in ((_MAJOR, "major"), (_MINOR, "minor")):
            r = np.corrcoef(chroma, np.roll(prof, t))[0, 1]
            if r > best:
                best, name = r, f"{NOTES[t]} {mode}"
    return name, float(best)


def analyse(path: str, duration: float | None) -> dict:
    """Everything worth storing for a sound: loudness always; tempo and key for longer sounds."""
    out: dict = {}
    try:
        out.update(loudness(path))
    except Exception:
        pass
    if duration and duration >= 8:
        try:
            x, sr = mono(path, 11025, 120)
            b, conf = tempo(x, sr)
            if b and conf >= 0.15:
                out["bpm"] = b
            k, kc = key(x, sr)
            if k and kc >= 0.55:
                out["key"] = k
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- spectrogram picture
_PALETTE = [(0, 0, 4), (40, 11, 84), (101, 21, 110), (159, 42, 99), (212, 72, 66), (245, 125, 21), (250, 193, 39), (252, 255, 164)]


def _lut() -> np.ndarray:
    xs = np.linspace(0, 1, len(_PALETTE))
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, xs, [c[k] for c in _PALETTE]) for k in range(3)], 1).astype(np.uint8)


def spectrogram_png(path: str, width: int = 1600, height: int = 256) -> bytes:
    """Log-frequency spectrogram (30 Hz .. Nyquist), one column per pixel, magma-like colours."""
    import io
    from PIL import Image
    x, sr = mono(path, 22050, MAX_ANALYSE_SECONDS)
    n_fft = 2048
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    pos = np.linspace(0, len(x) - n_fft, width).astype(np.int64)
    seg = x[pos[:, None] + np.arange(n_fft)[None, :]] * np.hanning(n_fft)
    mag = np.abs(np.fft.rfft(seg, axis=1))
    f = np.fft.rfftfreq(n_fft, 1 / sr)
    edges = np.geomspace(30, sr / 2, height + 1)
    bins = np.clip(np.searchsorted(f, edges), 1, len(f) - 1)
    rows = np.stack([mag[:, a:max(b, a + 1)].max(axis=1) for a, b in zip(bins[:-1], bins[1:])], 1)   # width x height
    db = 20 * np.log10(np.maximum(rows, 1e-9))
    top = np.percentile(db, 99.5)
    v = np.clip((db - (top - 80)) / 80, 0, 1)
    img = _lut()[(v * 255).astype(np.uint8)]                    # width x height x 3
    img = np.flipud(np.transpose(img, (1, 0, 2)))               # low frequencies at the bottom
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(img), "RGB").save(buf, "PNG", optimize=False)
    return buf.getvalue()


# ---------------------------------------------------------------- cutting a region out
EXPORT_FORMATS = {   # key: (extension, soundfile format, subtype)
    "wav16": (".wav", "WAV", "PCM_16"), "wav24": (".wav", "WAV", "PCM_24"), "wav32f": (".wav", "WAV", "FLOAT"),
    "flac": (".flac", "FLAC", "PCM_24"), "ogg": (".ogg", "OGG", "VORBIS"), "mp3": (".mp3", "MP3", "MPEG_LAYER_III"),
}


def read_region(path: str, start: float, end: float) -> tuple[np.ndarray, int, dict]:
    """(frames x channels float32, samplerate, source info) of start..end seconds."""
    import soundfile as sf
    try:
        info = sf.info(path)
    except Exception:
        info = None
    if info is not None:
        sr = info.samplerate
        a, b = max(0, int(round(start * sr))), min(info.frames, int(round(end * sr)))
        if b <= a:
            raise ValueError("the region is empty")
        data, _ = sf.read(path, start=a, stop=b, dtype="float32", always_2d=True)
        return data, sr, {"format": info.format, "subtype": info.subtype}
    ff = shutil.which("ffmpeg")
    if not ff:
        raise ValueError("can't decode this sound (install ffmpeg for M4A/AAC/WMA)")
    sr = 48000
    p = subprocess.run([ff, "-v", "error", "-ss", f"{start:.6f}", "-t", f"{max(end - start, 0.001):.6f}", "-i", path,
                        "-ac", "2", "-ar", str(sr), "-f", "f32le", "pipe:1"], capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
    data = np.frombuffer(p.stdout[: len(p.stdout) // 8 * 8], "<f4").reshape(-1, 2)
    if not len(data):
        raise ValueError("the region is empty")
    return data.copy(), sr, {"format": None, "subtype": None}


def shape_region(data: np.ndarray, sr: int, fade_in: float = 0, fade_out: float = 0, normalize: float | None = None) -> np.ndarray:
    """Fades (seconds, equal-power curves) and peak normalisation (dBFS) of a region."""
    data = data.copy()
    n = len(data)
    for secs, rising in ((fade_in, True), (fade_out, False)):
        k = min(n, int(round(max(0.0, secs) * sr)))
        if k > 1:
            ramp = np.sin(np.linspace(0, np.pi / 2, k, dtype=np.float32)) ** 2
            if rising:
                data[:k] *= ramp[:, None]
            else:
                data[n - k:] *= ramp[::-1, None]
    if normalize is not None:
        peak = float(np.abs(data).max()) if n else 0.0
        if peak > 0:
            data *= (10 ** (normalize / 20)) / peak
    return np.clip(data, -1.0, 1.0) if normalize is not None else data


def export_region(path: str, start: float, end: float, dest_dir: str, name: str, fmt: str = "same",
                  fade_in: float = 0, fade_out: float = 0, normalize: float | None = None) -> str:
    import os

    import soundfile as sf
    data, sr, src = read_region(path, start, end)
    data = shape_region(data, sr, fade_in, fade_out, normalize)
    if fmt == "same":
        ext = os.path.splitext(path)[1].lower()
        same = {".wav": "wav24" if src["subtype"] in ("PCM_24", "PCM_32", "FLOAT", "DOUBLE") else "wav16",
                ".flac": "flac", ".ogg": "ogg", ".oga": "ogg", ".mp3": "mp3"}
        fmt = same.get(ext, "wav24")
        if fmt == "wav24" and src["subtype"] in ("FLOAT", "DOUBLE"):
            fmt = "wav32f"
    if fmt not in EXPORT_FORMATS:
        raise ValueError("unknown format")
    ext, container, subtype = EXPORT_FORMATS[fmt]
    if container not in sf.available_formats():
        raise ValueError(f"{container} can't be written here")
    if container == "OGG" and sr > 192000:
        raise ValueError("Ogg Vorbis can't take this sample rate")
    stem = "".join(c if c not in '<>:"/\\|?*' and ord(c) >= 32 else "_" for c in name).strip(" .") or "region"
    out = os.path.join(dest_dir, stem + ext)
    k = 2
    while os.path.exists(out):
        out = os.path.join(dest_dir, f"{stem}_{k}{ext}")
        k += 1
    sf.write(out, data, sr, format=container, subtype=subtype)
    return out

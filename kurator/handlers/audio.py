"""Audio: sound effects and music.

  * decoding with libsndfile (soundfile, BSD + LGPL library): WAV/BWF, AIFF, FLAC, OGG/Vorbis, Opus,
    MP3, CAF, W64/RF64; M4A/AAC/WMA through ffmpeg if it is installed
  * tags with tinytag (MIT): title, artist, album, genre, comment, BPM...
  * Broadcast WAV 'bext' chunk (description, originator, origination date - used by sound libraries)
  * UCS CatID from file names (Universal Category System: "DOORWood_Creak_..." -> DOORWood)
  * peak / RMS level, and a waveform drawn as the thumbnail, so audio browses like images
"""

from __future__ import annotations

import io
import math
import re
import shutil
import struct
import subprocess
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from . import Handler, Loaded, register

PEAKS = 600
_UCS = re.compile(r"^([A-Z]{2,7}[A-Z][a-z]+)(?:[-_ ]|$)")


def _bext(data: bytes) -> dict:
    """Parse the Broadcast WAV extension chunk (and note iXML), walking RIFF chunks."""
    out: dict = {}
    if data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        return out
    pos, n = 12, len(data)
    while pos + 8 <= n:
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + min(size, 1 << 20)]
        if cid == b"bext" and len(body) >= 346:
            def txt(a, b):
                return body[a:b].split(b"\x00")[0].decode("latin-1", "replace").strip()
            out = {"description": txt(0, 256), "originator": txt(256, 288), "originator_reference": txt(288, 320),
                   "origination_date": txt(320, 330), "origination_time": txt(330, 338)}
            out = {k: v for k, v in out.items() if v}
        elif cid == b"iXML":
            out["ixml"] = body[:16384].decode("utf-8", "replace")
        pos += 8 + size + (size & 1)
        if size == 0xFFFFFFFF:
            break
    return out


def _decode(data: bytes, ext: str):
    """-> (numpy float32 array frames x channels, samplerate) or (None, None)."""
    import numpy as np
    import soundfile as sf
    if ext not in (".m4a", ".aac", ".wma", ".mp4"):
        try:
            with sf.SoundFile(io.BytesIO(data)) as f:
                sr, frames = f.samplerate, f.frames
                step = max(1, frames // 4_000_000)  # long music: read in blocks, keep enough for peaks
                chunks = []
                for block in f.blocks(blocksize=262144, dtype="float32", always_2d=True):
                    chunks.append(block[::step] if step > 1 else block)
                return (np.concatenate(chunks) if chunks else np.zeros((0, 1), "float32")), sr / step, f.channels, frames / sr
        except Exception:
            pass
    ff = shutil.which("ffmpeg")
    if ff:
        p = subprocess.run([ff, "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", "11025", "-f", "f32le", "pipe:1"],
                           input=data, capture_output=True, timeout=120)
        if p.returncode == 0 and p.stdout:
            a = np.frombuffer(p.stdout, dtype="<f4").reshape(-1, 1)
            return a, 11025, None, len(a) / 11025
    return None, None, None, None


LONG_SOUND = 30.0   # seconds: longer sounds (music, ambiences) get warm orange waveforms, effects stay blue
_BLUE = ((70, 98, 168), (140, 176, 255), (122, 162, 255))
_ORANGE = ((168, 92, 36), (255, 172, 92), (250, 150, 70))


def _waveform(peaks: list[float], duration: float | None, size: int = 512, rms: list[float] | None = None) -> Image.Image:
    w, h = size, size // 2
    img = Image.new("RGB", (w, h), (28, 31, 38))
    d = ImageDraw.Draw(img)
    mid = h / 2
    d.line([(0, mid), (w, mid)], fill=(55, 60, 72))
    pal = _ORANGE if (duration or 0) >= LONG_SOUND else _BLUE
    if peaks:
        n = len(peaks)
        for x in range(w):
            k = min(n - 1, int(x * n / w))
            y = max(1.0, peaks[k] * (h * 0.42))
            d.line([(x, mid - y), (x, mid + y)], fill=pal[0] if rms else pal[2])
            if rms:  # loudness body inside the peak outline, like most audio editors
                r = max(1.0, rms[min(len(rms) - 1, k)] * (h * 0.42))
                d.line([(x, mid - r), (x, mid + r)], fill=pal[1])
    if duration:
        m, s = divmod(int(round(duration)), 60)
        label = f"{m}:{s:02d}" if duration >= 1 else f"{duration:.2f}s"
        try:
            font = ImageFont.load_default(size=22)
        except TypeError:
            font = ImageFont.load_default()
        d.rectangle((w - 78, h - 32, w - 6, h - 6), fill=(0, 0, 0))
        d.text((w - 42, h - 19), label, fill=(230, 235, 245), font=font, anchor="mm")
    return img


def load(data: bytes, path: str, target: int | None) -> Loaded:
    import numpy as np
    ext = "." + path.rsplit(".", 1)[-1].lower()
    meta: dict = {"format": ext[1:].upper(), "audio": True}
    found: dict = {}
    try:
        from tinytag import TinyTag
        t = TinyTag.get(file_obj=io.BytesIO(data), filename=path)
        for k in ("title", "artist", "album", "albumartist", "genre", "comment", "composer", "year",
                  "samplerate", "channels", "bitrate", "bitdepth", "duration"):
            v = getattr(t, k, None)
            if v not in (None, "", 0):
                meta[k] = round(v, 3) if isinstance(v, float) else v
        extra = getattr(t, "other", None) or getattr(t, "extra", None) or {}
        for k in ("bpm", "key", "initial_key", "copyright", "publisher", "isrc"):
            v = extra.get(k)
            if v:
                meta[k] = v[0] if isinstance(v, list) else v
    except Exception:
        pass
    b = _bext(data[:8 * 1024 * 1024] if len(data) > 8 * 1024 * 1024 else data)
    if b:
        meta["bwf"] = {k: v for k, v in b.items() if k != "ixml"}
        if "ixml" in b:
            meta["ixml"] = b["ixml"]
        dt = b.get("origination_date")
        if dt and re.match(r"\d{4}[-:/]\d{2}[-:/]\d{2}", dt):
            tm = b.get("origination_time", "00:00:00").replace("-", ":").replace(".", ":")
            try:
                found["document"] = datetime.strptime(f"{dt[:4]}-{dt[5:7]}-{dt[8:10]} {tm[:8]}", "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                pass
    m = _UCS.match(path.replace("\\", "/").rsplit("/", 1)[-1])
    if m:
        meta["ucs_catid"] = m.group(1)

    samples, sr, channels, duration = _decode(data, ext)
    peaks: list[float] = []
    if samples is not None and len(samples):
        mono = np.abs(samples).max(axis=1)
        peak = float(mono.max())
        rms = float(np.sqrt(np.mean(np.square(samples, dtype="float64"))))
        meta["peak_dbfs"] = round(20 * math.log10(peak), 1) if peak > 0 else None
        meta["rms_dbfs"] = round(20 * math.log10(rms), 1) if rms > 0 else None
        bins = np.array_split(mono, min(PEAKS, len(mono)))
        peaks = [float(x.max()) if len(x) else 0.0 for x in bins]
        rmss = [float(np.sqrt(np.mean(np.square(x, dtype="float64")))) if len(x) else 0.0 for x in bins]
        top = max(peaks) or 1.0
        peaks = [round(p / top, 3) for p in peaks]
        meta["rms_env"] = [round(r / top, 3) for r in rmss]
        if channels and "channels" not in meta:
            meta["channels"] = channels
        if duration and "duration" not in meta:
            meta["duration"] = round(duration, 3)
        if sr and "samplerate" not in meta and channels:
            meta["samplerate"] = int(sr)
    meta["peaks"] = peaks
    img = _waveform(peaks, meta.get("duration"), rms=meta.get("rms_env"))
    if (meta.get("duration") or 0) >= LONG_SOUND:
        meta["long"] = True
    return Loaded(img, meta, found, None)


_PLAYABLE = (".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac")
_OTHER = (".aif", ".aiff", ".aifc", ".caf", ".w64", ".rf64", ".wma")
register(Handler("audio", _PLAYABLE, load, browser_viewable=True, group="audio"))
register(Handler("audio", _OTHER, load, browser_viewable=False, group="audio"))

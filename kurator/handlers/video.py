"""Video: a frame as the thumbnail, a strip of frames for hover scrubbing, and the facts.

Uses ffmpeg/ffprobe when they are installed (PATH, or the folder in KURATOR_FFMPEG). Without them,
Windows still gives Explorer's video thumbnail, and MP4/MOV files still get their length, size and
recording date from Kurator's own reader - so phone videos keep their real date either way.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from . import Handler, Loaded, register
from .common import placeholder, shell_thumbnail

STRIP = 1              # thumbs.db "size" key of the hover-scrub strip
STRIP_FRAMES = 8
STRIP_HEIGHT = 144
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # no console window flashing up on Windows


def tool(name: str) -> str | None:
    d = os.environ.get("KURATOR_FFMPEG")
    if d == "off":
        return None
    if d:
        for cand in (os.path.join(d, name), os.path.join(d, name + ".exe")):
            if os.path.isfile(cand):
                return cand
    return shutil.which(name)


def _run(args: list[str], timeout: float = 30) -> bytes:
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                       timeout=timeout, check=False, creationflags=_NO_WINDOW)
    return r.stdout


def probe(path: str) -> dict | None:
    exe = tool("ffprobe")
    if not exe:
        return None
    try:
        out = _run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "--", path], 30)
        return json.loads(out or b"{}") or None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def frame(path: str, t: float, height: int | None = None) -> Image.Image | None:
    """One frame at t seconds (fast seek), optionally scaled to a height."""
    exe = tool("ffmpeg")
    if not exe:
        return None
    vf = ["-vf", f"scale=-2:min(ih\\,{height})"] if height else []   # never upscale
    try:
        out = _run([exe, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1", *vf,
                    "-f", "image2pipe", "-c:v", "png", "-"], 60)
        if not out:
            return None
        img = Image.open(io.BytesIO(out))
        img.load()
        return img.convert("RGB")
    except (OSError, subprocess.TimeoutExpired):
        return None


def _info_from_probe(p: dict) -> tuple[dict, dict]:
    fmt = p.get("format", {})
    streams = p.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    info: dict = {}
    dates: dict = {}
    try:
        info["duration"] = round(float(fmt.get("duration") or (v or {}).get("duration") or 0), 3) or None
    except ValueError:
        pass
    info["container"] = fmt.get("format_long_name") or fmt.get("format_name")
    if fmt.get("bit_rate"):
        info["bitrate_kbps"] = round(int(fmt["bit_rate"]) / 1000)
    if v:
        w, h = v.get("width"), v.get("height")
        rot = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = int((v.get("tags") or {}).get("rotate", rot) or 0)
        if abs(rot) % 180 == 90 and w and h:
            w, h = h, w
        info.update(width=w, height=h, video_codec=v.get("codec_name"), pixel_format=v.get("pix_fmt"))
        rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or ""
        if "/" in rate:
            n, d = rate.split("/")
            if float(d or 0):
                info["fps"] = round(float(n) / float(d), 3)
        if (v.get("tags") or {}).get("timecode"):
            info["timecode"] = v["tags"]["timecode"]
    if a:
        info.update(audio_codec=a.get("codec_name"), audio_channels=a.get("channels"),
                    audio_rate=int(a["sample_rate"]) if a.get("sample_rate") else None)
    tags = {k.lower(): v_ for k, v_ in (fmt.get("tags") or {}).items()}
    for k in ("title", "artist", "comment", "encoder", "com.apple.quicktime.model", "com.apple.quicktime.make",
              "com.android.manufacturer", "com.android.model", "location", "com.apple.quicktime.location.iso6709"):
        if tags.get(k):
            info[k.replace("com.apple.quicktime.", "").replace("com.android.", "")] = tags[k]
    ct = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if ct:
        try:
            dates["document"] = datetime.fromisoformat(ct.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return {k: v_ for k, v_ in info.items() if v_ not in (None, "", 0)}, dates


# ---------------------------------------------------------------- MP4 / MOV without ffmpeg
_EPOCH_1904 = datetime(1904, 1, 1, tzinfo=timezone.utc).timestamp()


def mp4_info(path: str) -> tuple[dict, dict]:
    """Length, picture size and recording time from the 'moov' box (MP4, MOV, M4V, 3GP)."""
    info, dates = {}, {}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        def boxes(start: int, end: int):
            pos = start
            while pos + 8 <= end:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    return
                n, typ = struct.unpack(">I4s", hdr[:8])
                head = 8
                if n == 1:
                    n = struct.unpack(">Q", hdr[8:16])[0]
                    head = 16
                elif n == 0:
                    n = end - pos
                if n < head:
                    return
                yield typ, pos + head, pos + n
                pos += n

        def read(start, count):
            f.seek(start)
            return f.read(count)

        for typ, b, e in boxes(0, size):
            if typ != b"moov":
                continue
            for t2, b2, e2 in boxes(b, e):
                if t2 == b"mvhd":
                    d = read(b2, 32)
                    if d[0] == 1:
                        created, _, scale, dur = struct.unpack(">QQIQ", d[4:32])
                    else:
                        created, _, scale, dur = struct.unpack(">IIII", d[4:20])
                    if scale:
                        info["duration"] = round(dur / scale, 3)
                    if created > 0:
                        ts = _EPOCH_1904 + created
                        if ts > 946684800:   # after 2000: some cameras write 0 or garbage
                            dates["document"] = ts
                elif t2 == b"trak":
                    for t3, b3, e3 in boxes(b2, e2):
                        if t3 == b"tkhd":
                            d = read(b3, 96)
                            off = 88 if d[0] == 1 else 76
                            w, h = struct.unpack(">II", d[off:off + 8])
                            if w and h and "width" not in info:
                                info["width"], info["height"] = w >> 16, h >> 16
            break
    return info, dates


def _label(img: Image.Image, text: str) -> Image.Image:
    img = img.copy()
    d = ImageDraw.Draw(img)
    s = max(14, img.height // 12)
    try:
        font = ImageFont.load_default(size=s)
    except TypeError:
        font = ImageFont.load_default()
    w = d.textlength(text, font=font)
    pad = s // 3
    d.rectangle((img.width - w - 3 * pad, img.height - s - 3 * pad, img.width - pad, img.height - pad), fill=(0, 0, 0))
    d.text((img.width - w - 2 * pad, img.height - s - 2 * pad), text, fill=(235, 238, 245), font=font)
    return img


def fmt_duration(sec: float | None) -> str:
    if not sec:
        return ""
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def load(data: bytes | None, path: str, target: int | None) -> Loaded:
    ext = os.path.splitext(path)[1].lower()
    meta: dict = {"format": ext[1:].upper(), "video": True}
    info, dates = {}, {}
    p = probe(path)
    if p:
        info, dates = _info_from_probe(p)
    elif ext in (".mp4", ".m4v", ".mov", ".3gp", ".3g2"):
        try:
            info, dates = mp4_info(path)
        except (OSError, struct.error):
            pass
    meta["video_info"] = info
    dur = info.get("duration") or 0
    size = (info["width"], info["height"]) if info.get("width") and info.get("height") else None
    extra = {}
    img = None
    if p:
        img = frame(path, min(dur * 0.1, 5.0) if dur else 0, 720)
        if img is not None and dur > 0.5:
            frames = [frame(path, dur * (k + 0.5) / STRIP_FRAMES, STRIP_HEIGHT) for k in range(STRIP_FRAMES)]
            frames = [f for f in frames if f is not None]
            if len(frames) >= 2:
                w = max(f.width for f in frames)
                strip = Image.new("RGB", (w * len(frames), STRIP_HEIGHT))
                for k, f in enumerate(frames):
                    strip.paste(f.resize((w, STRIP_HEIGHT)), (k * w, 0))
                extra[STRIP] = strip
                meta["strip"] = len(frames)
        if img is not None:
            meta["preview"] = "frame"
    if img is None:
        img = shell_thumbnail(path)
        if img is not None:
            meta["preview"] = "windows"
    if img is None:
        meta["placeholder"] = True
        meta["preview_note"] = "install ffmpeg for video previews"
        img = placeholder(ext, fmt_duration(dur) or "video")
    elif dur:
        img = _label(img.convert("RGB"), fmt_duration(dur))
    return Loaded(img, meta, dates, size, extra)


PLAYABLE = (".mp4", ".m4v", ".webm", ".ogv", ".mov")
OTHER = (".mkv", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".m2ts", ".mts", ".ts", ".3gp", ".3g2", ".mxf", ".vob")

register(Handler("video", PLAYABLE, load, browser_viewable=True, group="video", streams=True))
register(Handler("video", OTHER, load, group="video", streams=True))

"""Sample sounds for v0.17 (rows for long sounds, library-wide Playhead analysis): long recordings with
events at known times, and a few short effects of different character.

    python tools/make_v17_samples.py LIBRARY_FOLDER

  long/field_12min.flac     12 minutes, mono 22.05 kHz: quiet room tone, a knock every 37 s, a 3 s
                            whistle at 5:00 and 9:30 (longer than Playhead's first 10 minutes)
  long/talk_14min.m4a       14 minutes, AAC (only ffmpeg reads it): a tone that changes every minute
  long/drone_3min.wav       3 minutes, stereo: a slow drone, louder in the middle
  sfx/glass_tap.wav         a short, bright ring (glass-like decay)
  sfx/rubber_thud.wav       a short, dull thud (damped)
  sfx/rain_bursty.wav       clusters of drops (bursty)
  sfx/riser.wav             noise that swells up (swell-like)
"""

import os
import shutil
import subprocess
import sys

import numpy as np
import soundfile as sf

lib = sys.argv[1]
for d in ("long", "sfx"):
    os.makedirs(os.path.join(lib, d), exist_ok=True)
rng = np.random.default_rng(17)


def t(sec, sr):
    return np.arange(int(sec * sr)) / sr


# 12 minutes of room tone with knocks every 37 s and two whistles
SR = 22050
x = t(720, SR)
room = rng.normal(0, 0.004, len(x)).astype(np.float32)
for k in np.arange(10, 720, 37):
    i = int(k * SR)
    n = int(0.25 * SR)
    room[i:i + n] += (0.7 * np.exp(-np.arange(n) / (0.03 * SR)) * np.sin(2 * np.pi * 180 * np.arange(n) / SR)).astype(np.float32)
for k in (300, 570):
    i, n = int(k * SR), int(3 * SR)
    w = np.sin(np.pi * np.arange(n) / n) ** 2
    room[i:i + n] += (0.3 * w * np.sin(2 * np.pi * (1800 + 400 * np.sin(2 * np.pi * 0.7 * np.arange(n) / SR)) * np.arange(n) / SR)).astype(np.float32)
sf.write(os.path.join(lib, "long", "field_12min.flac"), np.clip(room, -1, 1), SR, subtype="PCM_16")

# 14 minutes of AAC: a tone that steps every minute (ffmpeg decodes it)
ff = shutil.which("ffmpeg")
if ff:
    SR = 22050
    x = t(840, SR)
    f = 220 * 2 ** (np.floor(x / 60) / 12)
    tone = (0.25 * np.sin(2 * np.pi * np.cumsum(f) / SR)).astype(np.float32)
    tmp = os.path.join(lib, "long", "_talk.wav")
    sf.write(tmp, tone, SR, subtype="PCM_16")
    subprocess.run([ff, "-v", "error", "-y", "-i", tmp, "-c:a", "aac", "-b:a", "48k", os.path.join(lib, "long", "talk_14min.m4a")], check=True)
    os.remove(tmp)

# 3 minutes of drone, stereo, louder in the middle
SR = 44100
x = t(180, SR)
env = 0.15 + 0.6 * np.sin(np.pi * x / 180) ** 2
dr = env * (np.sin(2 * np.pi * 55 * x) + 0.4 * np.sin(2 * np.pi * 82.5 * x + np.sin(2 * np.pi * 0.05 * x)))
sf.write(os.path.join(lib, "long", "drone_3min.wav"), (0.5 * np.stack([dr, np.roll(dr, 400)], 1)).astype(np.float32), SR, subtype="PCM_16")

# short effects of different character
x = t(2.5, SR)
glass = sum(a * np.exp(-x / d) * np.sin(2 * np.pi * f * x) for f, a, d in [(2093, 0.5, 1.2), (5240, 0.3, 0.9), (8370, 0.15, 0.6)])
glass[:60] += rng.normal(0, 0.4, 60)
sf.write(os.path.join(lib, "sfx", "glass_tap.wav"), (0.8 * glass / np.abs(glass).max()).astype(np.float32), SR, subtype="PCM_16")
x = t(0.8, SR)
thud = np.exp(-x / 0.025) * np.sin(2 * np.pi * 70 * x) + 0.3 * np.exp(-x / 0.01) * rng.normal(0, 1, len(x))
sf.write(os.path.join(lib, "sfx", "rubber_thud.wav"), (0.8 * thud / np.abs(thud).max()).astype(np.float32), SR, subtype="PCM_16")
x = t(4.0, SR)
rain = np.zeros_like(x)
for c in (0.3, 1.6, 2.2, 3.4):
    for k in rng.uniform(c, c + 0.25, 30):
        i = int(k * SR)
        n = min(300, len(rain) - i)
        rain[i:i + n] += rng.normal(0, 0.4, n) * np.exp(-np.arange(n) / 40)
sf.write(os.path.join(lib, "sfx", "rain_bursty.wav"), np.clip(rain, -1, 1).astype(np.float32), SR, subtype="PCM_16")
x = t(3.0, SR)
riser = rng.normal(0, 1, len(x)) * (x / 3) ** 3
riser[-200:] *= np.linspace(1, 0, 200)
sf.write(os.path.join(lib, "sfx", "riser.wav"), (0.8 * riser / np.abs(riser).max()).astype(np.float32), SR, subtype="PCM_16")
print("samples in", lib)

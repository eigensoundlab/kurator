"""Sample sounds for v0.16 (Playhead analysis): short effects and a long, sectioned piece of music.

    python tools/make_v16_samples.py LIBRARY_FOLDER
"""

import os
import sys

import numpy as np
import soundfile as sf

lib = sys.argv[1]
os.makedirs(os.path.join(lib, "sfx"), exist_ok=True)
os.makedirs(os.path.join(lib, "music"), exist_ok=True)
SR = 44100
rng = np.random.default_rng(16)


def t(sec):
    return np.arange(int(sec * SR)) / SR


# a metal hit: a click and inharmonic partials ringing out, with one long overtone
x = t(2.0)
hit = sum(a * np.exp(-x / d) * np.sin(2 * np.pi * f * x) for f, a, d in
          [(523, 0.5, 0.5), (1371, 0.3, 0.3), (2589, 0.2, 0.8), (4120, 0.1, 0.15)])
hit[:200] += rng.normal(0, 0.6, 200) * np.linspace(1, 0, 200)
hit = 0.8 * hit / np.abs(hit).max()
sf.write(os.path.join(lib, "sfx", "metal_hit.wav"), np.stack([hit, hit * 0.9], 1).astype(np.float32), SR, subtype="PCM_24")

# crackle: bursts of clicks
x = t(3.0)
crk = np.zeros_like(x)
for c in (0.2, 1.1, 2.3):
    for k in rng.uniform(c, c + 0.35, 40):
        i = int(k * SR)
        crk[i:i + 60] += rng.normal(0, 0.5, len(crk[i:i + 60])) * np.exp(-np.arange(len(crk[i:i + 60])) / 12)
sf.write(os.path.join(lib, "sfx", "crackle.wav"), np.clip(crk, -1, 1).astype(np.float32), SR, subtype="PCM_16")

# a swell (the hit backwards), as AIFF: decoded by Kurator, browsers can't all read AIFF
sf.write(os.path.join(lib, "sfx", "swell.aiff"), hit[::-1].astype(np.float32), SR, subtype="PCM_16")

# music, 2.5 minutes at 120 BPM: A (beat), B (beat + chords), A, C (pad only, no beat), A
beat = 0.5
dur = 150.0
x = t(dur)
music = np.zeros_like(x)
kick = np.sin(2 * np.pi * 55 * np.arange(int(0.25 * SR)) / SR * (1 + 2 * np.exp(-np.arange(int(0.25 * SR)) / 800))) \
    * np.exp(-np.arange(int(0.25 * SR)) / 3000)
hat = rng.normal(0, 1, int(0.05 * SR)) * np.exp(-np.arange(int(0.05 * SR)) / 300) * 0.25
sections = [(0, 30, "A"), (30, 60, "B"), (60, 90, "A"), (90, 120, "C"), (120, 150, "A")]
for s0, s1, kind in sections:
    if kind in "AB":
        for k in np.arange(s0, s1, beat):
            i = int(k * SR)
            music[i:i + len(kick)] += kick[: len(music[i:i + len(kick)])] * 0.7
            j = int((k + beat / 2) * SR)
            music[j:j + len(hat)] += hat[: len(music[j:j + len(hat)])]
    if kind in "BC":
        seg = (x >= s0) & (x < s1)
        for f in (220, 277.2, 329.6) if kind == "B" else (174.6, 220, 261.6, 349.2):
            music[seg] += 0.08 * np.sin(2 * np.pi * f * x[seg]) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * x[seg]))
music = 0.9 * music / np.abs(music).max()
sf.write(os.path.join(lib, "music", "sections_120bpm.wav"), np.stack([music, music], 1).astype(np.float32), SR, subtype="PCM_16")
print("made", lib)

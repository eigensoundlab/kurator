"""Sample library for tools/ui_check_v13.py: compressed glTF, binary USD (when the 3D add-on is
installed), a brick ball for turntables, a music loop and an animated sprite.

    python tools/make_v13_samples.py FOLDER
"""

import importlib.util
import os
import sys

import numpy as np
import soundfile as sf
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
sys.path.insert(0, os.path.dirname(__file__))
import compressed_samples as C  # noqa: E402
from make_v12_samples import brick_ball  # noqa: E402

if __name__ == "__main__":
    root = sys.argv[1]
    models, sounds, sprites = (os.path.join(root, d) for d in ("models", "sounds", "sprites"))
    for d in (models, sounds, sprites):
        os.makedirs(d, exist_ok=True)
    brick_ball(os.path.join(models, "bricks_ball.glb"))
    open(os.path.join(models, "quad_meshopt.glb"), "wb").write(C.meshopt_glb())
    if importlib.util.find_spec("DracoPy"):
        open(os.path.join(models, "quad_draco.glb"), "wb").write(C.draco_glb())
    if importlib.util.find_spec("pxr"):
        C.usd_files(models)
    sr = 44100
    t = np.arange(sr * 12) / sr
    x = 0.2 * np.sin(2 * np.pi * 220 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.5 * t))
    for k in range(24):
        s = int(k * 0.5 * sr)
        d = np.arange(min(3000, len(x) - s))
        x[s:s + len(d)] += 0.7 * np.exp(-d / 400) * np.sin(2 * np.pi * 1500 * d / sr)
    sf.write(os.path.join(sounds, "loop_120.wav"), np.stack([x, x * 0.9], 1), sr, subtype="PCM_24")
    frames = [Image.new("RGBA", (48, 48), (k * 40, 120, 220 - k * 30, 255)) for k in range(6)]
    frames[0].save(os.path.join(sprites, "run.gif"), save_all=True, append_images=frames[1:], duration=90, loop=0)

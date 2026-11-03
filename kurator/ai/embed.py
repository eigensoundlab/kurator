"""Image/text embeddings with open_clip (CLIP and SigLIP models) - optional, needs: pip install ".[ai]".

An embedding is a list of numbers describing what an image (or a text) shows. Similar images have
similar embeddings, and an image is close to texts that describe it - that powers "find similar",
search by description, and choosing tags from a pool.
"""

from __future__ import annotations

import io
import threading
import time

MODELS = {
    # name: (open_clip model, pretrained tag, label)
    "fast": ("ViT-B-16-SigLIP2-256", "webli", "SigLIP2 Base - fast, ~0.4 GB"),
    "better": ("ViT-L-16-SigLIP2-384", "webli", "SigLIP2 Large 384 - more accurate, ~1.8 GB"),
    "best": ("ViT-SO400M-14-SigLIP2-378", "webli", "SigLIP2 So400m - most accurate, ~4.5 GB"),
}
TEMPLATES = ["a drawing of {}.", "an illustration of {}.", "a sketch of {}.", "concept art of {}.",
             "a picture of {}.", "a photo of {}.", "an icon of {}."]
IDLE_UNLOAD = 600  # seconds: give the GPU memory back (e.g. to Ollama) when not used


class AIUnavailable(RuntimeError):
    pass


def status() -> dict:
    try:
        import open_clip  # noqa: F401
        import torch
    except Exception as e:
        return {"installed": False, "error": f"{type(e).__name__}: {e}"}
    cuda = torch.cuda.is_available()
    mps = not cuda and getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available()
    dev = {"installed": True, "torch": torch.__version__, "cuda": cuda, "device": "cuda" if cuda else "mps" if mps else "cpu"}
    if cuda:
        try:
            dev["gpu"] = torch.cuda.get_device_name(0)
            dev["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1024 ** 3, 1)
        except Exception:
            pass
    return dev


class Embedder:
    def __init__(self, model: str, pretrained: str | None):
        self.model_name, self.pretrained = model, pretrained or None
        self.key = f"{model}/{pretrained or 'random'}"  # embeddings are only comparable within one model
        self._m = None
        self._lock = threading.RLock()
        self._last = time.time()
        self._timer: threading.Timer | None = None

    def _load(self):
        if self._m is not None:
            return
        try:
            import open_clip
            import torch
        except Exception as e:
            raise AIUnavailable("AI add-on not installed - run install.bat ai (or ./install.sh ai)") from e
        dev = "cuda" if torch.cuda.is_available() else (
            "mps" if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available() else "cpu")
        model, _, preprocess = open_clip.create_model_and_transforms(self.model_name, pretrained=self.pretrained, device=dev)
        model.eval()
        if dev == "cuda":
            model = model.half()
        self._m = model
        self._pre = preprocess
        self._tok = open_clip.get_tokenizer(self.model_name)
        self._torch = torch
        self.device = dev
        bias = getattr(model, "logit_bias", None)
        self.has_bias = bias is not None
        self.scale = float(model.logit_scale.exp().item())
        self.bias = float(bias.item()) if bias is not None else 0.0

    def _touch(self):
        self._last = time.time()
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(IDLE_UNLOAD, self.unload)
        self._timer.daemon = True
        self._timer.start()

    def unload(self):
        with self._lock:
            if self._m is None:
                return
            self._m = None
            try:
                if self._torch.cuda.is_available():
                    self._torch.cuda.empty_cache()
            except Exception:
                pass

    def encode_images(self, images) -> "np.ndarray":  # noqa: F821
        import numpy as np
        with self._lock:
            self._load()
            torch = self._torch
            batch = torch.stack([self._pre(im.convert("RGB")) for im in images]).to(self.device)
            if self.device == "cuda":
                batch = batch.half()
            with torch.no_grad():
                f = self._m.encode_image(batch)
                f = f / f.norm(dim=-1, keepdim=True)
            self._touch()
            return f.float().cpu().numpy().astype(np.float32)

    def encode_texts(self, texts: list[str]) -> "np.ndarray":  # noqa: F821
        import numpy as np
        with self._lock:
            self._load()
            torch = self._torch
            tokens = self._tok(texts).to(self.device)
            with torch.no_grad():
                f = self._m.encode_text(tokens)
                f = f / f.norm(dim=-1, keepdim=True)
            self._touch()
            return f.float().cpu().numpy().astype(np.float32)

    def tag_vectors(self, prompts: list[list[str]]) -> "np.ndarray":  # noqa: F821
        """One vector per tag: the normalised mean of its prompt variants."""
        import numpy as np
        flat = [p for ps in prompts for p in ps]
        enc = self.encode_texts(flat)
        out, i = [], 0
        for ps in prompts:
            v = enc[i:i + len(ps)].mean(axis=0)
            out.append(v / (np.linalg.norm(v) or 1))
            i += len(ps)
        return np.stack(out)

    def logits(self, img_vecs, tag_vecs):
        self._load()
        return (img_vecs @ tag_vecs.T) * self.scale + self.bias


def prompts_for(tag_name: str, ai_prompt: str | None) -> list[str]:
    """Your own description wins ("a robot or android character; a mech"); otherwise art-friendly templates."""
    if ai_prompt and ai_prompt.strip():
        return [p.strip() for p in ai_prompt.split(";") if p.strip()]
    leaf = tag_name.split("/")[-1].replace("_", " ")
    return [t.format(leaf) for t in TEMPLATES]


def image_from_bytes(b: bytes):
    from PIL import Image
    im = Image.open(io.BytesIO(b))
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB")

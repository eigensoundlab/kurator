"""Text-to-sound search with CLAP (contrastive language-audio pretraining), part of the AI add-on.

LAION's CLAP turns sounds and texts into vectors in one space: "door creak", "rain on a tin roof",
"808 kick", "sad piano" find the sounds that match, and "find similar" works on sounds too. Runs
locally through Hugging Face transformers; the model (about 0.8 GB, safetensors) is downloaded once.
"""

from __future__ import annotations

import threading
import time

import numpy as np

SOUND_MODELS = {
    # name: (Hugging Face repository, label)
    "clap": ("laion/larger_clap_general", "LAION CLAP (general sounds and music) - ~0.8 GB"),
    "clap-music": ("laion/larger_clap_music_and_speech", "LAION CLAP (music and speech) - ~0.8 GB"),
}
RATE = 48000
CLIP_SECONDS = 10
IDLE_UNLOAD = 600


class SoundUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import transformers  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def _resample(x: np.ndarray, sr: int, rate: int = RATE) -> np.ndarray:
    if sr == rate or not len(x):
        return x.astype(np.float32)
    n = int(round(len(x) * rate / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def clips(path: str, count: int = 3, seconds: float = CLIP_SECONDS) -> list[np.ndarray]:
    """Up to `count` mono 48 kHz windows of a sound: the start, and further in for longer ones."""
    import soundfile as sf
    try:
        f = sf.SoundFile(path)
    except Exception:
        f = None
    out = []
    if f is not None:
        with f:
            sr, total = f.samplerate, f.frames
            win = int(seconds * sr)
            starts = [0] if total <= win * 1.5 else [int(k * (total - win) / max(1, count - 1)) for k in range(count)]
            for s in starts:
                f.seek(s)
                a = f.read(win, dtype="float32", always_2d=True)
                if len(a):
                    out.append(_resample(a.mean(axis=1), sr))
        return out
    from ..audio_analysis import read_region   # M4A/AAC/WMA through ffmpeg
    a, sr, _ = read_region(path, 0, seconds)
    return [_resample(a.mean(axis=1), sr)]


class SoundEmbedder:
    def __init__(self, repo: str):
        self.repo = repo
        self.key = f"clap:{repo}"
        self.model = None
        self.processor = None
        self.device = "cpu"
        self._lock = threading.Lock()
        self._used = 0.0
        self._timer = None

    def _load(self):
        if self.model is not None:
            return
        try:
            import torch
            from transformers import ClapModel, ClapProcessor
        except Exception as e:
            raise SoundUnavailable(f"text-to-sound search needs the AI add-on with transformers (install.bat ai): {e}") from e
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = ClapProcessor.from_pretrained(self.repo)
        model = ClapModel.from_pretrained(self.repo)
        if self.device == "cuda":
            model = model.half()
        self.model = model.to(self.device).eval()

    def _touch(self):
        self._used = time.time()
        if self._timer is None:
            def check():
                self._timer = None
                if time.time() - self._used >= IDLE_UNLOAD:
                    self.unload()
                else:
                    self._touch()
            self._timer = threading.Timer(IDLE_UNLOAD, check)
            self._timer.daemon = True
            self._timer.start()

    def unload(self):
        with self._lock:
            self.model = None
            self.processor = None
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    @staticmethod
    def _vec(out):
        v = getattr(out, "pooler_output", out)
        v = v.float()
        return (v / v.norm(dim=-1, keepdim=True).clamp_min(1e-8)).cpu().numpy()

    def encode_audio(self, clips_: list[np.ndarray]) -> np.ndarray:
        import torch
        with self._lock:
            self._load()
            fe = getattr(self.processor, "feature_extractor", self.processor)
            inp = fe(clips_, sampling_rate=RATE, return_tensors="pt", truncation="rand_trunc", padding="repeatpad")
            inp = {k: (v.to(self.device).half() if v.dtype == torch.float32 and self.device == "cuda" else v.to(self.device)) for k, v in inp.items()}
            with torch.no_grad():
                out = self._vec(self.model.get_audio_features(**inp))
        self._touch()
        return out

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        import torch
        with self._lock:
            self._load()
            tok = getattr(self.processor, "tokenizer", self.processor)
            inp = tok(texts, padding=True, truncation=True, return_tensors="pt")
            inp = {k: v.to(self.device) for k, v in inp.items() if k in ("input_ids", "attention_mask")}
            with torch.no_grad():
                out = self._vec(self.model.get_text_features(**inp))
        self._touch()
        return out

    def embed_file(self, path: str) -> np.ndarray | None:
        """One vector per sound: the mean of its windows, normalised."""
        cs = [c for c in clips(path) if len(c) > RATE // 10]
        if not cs:
            return None
        v = self.encode_audio(cs).mean(axis=0)
        return v / max(float(np.linalg.norm(v)), 1e-8)

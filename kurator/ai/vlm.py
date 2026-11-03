"""Vision-language model through Ollama (e.g. Gemma) - runs on your machine, nothing is uploaded.

Kurator sends one image plus the allowed tags and asks for JSON that must match a schema with an
'enum' of exactly those tags (Ollama "structured outputs"), so the model cannot invent tags. It can
also write a one-sentence caption, e.g. for training datasets.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request

NONE = "none of these"


class VLMError(RuntimeError):
    pass


class Ollama:
    def __init__(self, url: str = "http://127.0.0.1:11434"):
        self.url = url.rstrip("/")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _req(self, path: str, body: dict | None = None, timeout: float = 600) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", str(e))
            except Exception:
                msg = str(e)
            raise VLMError(f"Ollama: {msg}") from None
        except (urllib.error.URLError, OSError) as e:
            raise VLMError(f"Ollama is not running at {self.url} (install from ollama.com, then e.g. 'ollama pull gemma3:12b')") from e

    def models(self) -> list[str]:
        return sorted(m["name"] for m in self._req("/api/tags", timeout=10).get("models", []))

    def classify(self, model: str, image_jpeg: bytes, tags: list[tuple[str, str]], single: bool,
                 caption: bool = False) -> dict:
        """tags: [(name, description)]. Returns {'tags': [...], 'caption': str|None}."""
        names = [n for n, _ in tags]
        listing = "\n".join(f"- {n}: {d}" if d and d != n else f"- {n}" for n, d in tags)
        if single:
            ask = f'Choose the ONE label that best describes the main subject of the image, or "{NONE}" if none fits.'
            props = {"tag": {"type": "string", "enum": names + [NONE]}}
            req = ["tag"]
        else:
            ask = "Choose ALL labels that clearly apply to the image (an empty list if none fits)."
            props = {"tags": {"type": "array", "items": {"type": "string", "enum": names}}}
            req = ["tags"]
        if caption:
            ask += " Also write a short, factual one-sentence caption of the image (subject, style, colours)."
            props["caption"] = {"type": "string"}
            req.append("caption")
        prompt = ("You are sorting images in an artist's asset library.\n"
                  f"Allowed labels:\n{listing}\n\n{ask} Answer only with JSON.")
        body = {"model": model, "stream": False, "options": {"temperature": 0},
                "format": {"type": "object", "properties": props, "required": req},
                "messages": [{"role": "user", "content": prompt,
                              "images": [base64.b64encode(image_jpeg).decode()]}]}
        r = self._req("/api/chat", body)
        try:
            out = json.loads(r["message"]["content"])
        except (KeyError, ValueError, TypeError) as e:
            raise VLMError(f"Unexpected answer from the model: {str(r)[:200]}") from e
        chosen = [out["tag"]] if single else list(out.get("tags") or [])
        chosen = [c for c in chosen if c in names]  # belt and braces
        return {"tags": chosen, "caption": (out.get("caption") or "").strip() or None}


def jpeg_for_vlm(img, max_side: int = 896) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=90)
    return b.getvalue()

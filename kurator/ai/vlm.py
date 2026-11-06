"""Vision-language models for tag suggestions and captions.

Two kinds of server, both usually running on your own machine:

* Ollama (e.g. Gemma 3): its own API, with "structured outputs".
* Any OpenAI-compatible server: llama.cpp's llama-server, LM Studio, vLLM, LocalAI, Jan... (the
  /v1/chat/completions API with an image). A cloud URL works too, but then the pictures are sent
  to that service.

Kurator sends one image plus the allowed tags and asks for JSON that must match a schema with an
'enum' of exactly those tags, so the model cannot invent tags (and answers are filtered again).
It can also write a one-sentence caption, e.g. for training datasets.
"""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.parse
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
        prompt, schema, names = _ask(tags, single, caption)
        body = {"model": model, "stream": False, "options": {"temperature": 0}, "format": schema,
                "messages": [{"role": "user", "content": prompt,
                              "images": [base64.b64encode(image_jpeg).decode()]}]}
        r = self._req("/api/chat", body)
        try:
            out = json.loads(r["message"]["content"])
        except (KeyError, ValueError, TypeError) as e:
            raise VLMError(f"Unexpected answer from the model: {str(r)[:200]}") from e
        return _answer(out, names, single)


def _ask(tags: list[tuple[str, str]], single: bool, caption: bool) -> tuple[str, dict, list[str]]:
    """(prompt, JSON schema, allowed names)."""
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
    return prompt, {"type": "object", "properties": props, "required": req, "additionalProperties": False}, names


def _answer(out, names: list[str], single: bool) -> dict:
    if not isinstance(out, dict):
        raise VLMError(f"Unexpected answer from the model: {str(out)[:200]}")
    chosen = [out.get("tag")] if single else list(out.get("tags") or [])
    chosen = [c for c in chosen if isinstance(c, str) and c in names]  # belt and braces
    cap = out.get("caption")
    return {"tags": chosen, "caption": (cap.strip() if isinstance(cap, str) else "") or None}


def _local(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


class OpenAICompatible:
    """A server with the OpenAI chat-completions API (llama.cpp, LM Studio, vLLM, LocalAI, Jan, ...)."""

    def __init__(self, url: str = "http://127.0.0.1:8080/v1", key: str = ""):
        url = (url or "").strip().rstrip("/")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise VLMError("The server address should look like http://127.0.0.1:8080/v1")
        if parts.path in ("", "/"):
            url += "/v1"
        self.url = url
        self.key = (key or "").strip()
        # the machine's proxy settings are for the internet, not for a server on this machine
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if _local(url) \
            else urllib.request.build_opener()

    def _req(self, path: str, body: dict | None = None, timeout: float = 600) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.url + path, data=data, method="POST" if body is not None else "GET",
                                     headers=headers)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
                msg = err.get("error", err)
                msg = msg.get("message", msg) if isinstance(msg, dict) else msg
            except Exception:
                msg = str(e)
            raise VLMError(f"{self.url}: {e.code} {str(msg)[:300]}") from None
        except (urllib.error.URLError, OSError) as e:
            raise VLMError(f"No OpenAI-compatible server answers at {self.url} (start llama-server, LM Studio's "
                           "local server, or vLLM - or check the address)") from e

    def models(self) -> list[str]:
        r = self._req("/models", timeout=10)
        return sorted(str(m.get("id")) for m in r.get("data", []) if isinstance(m, dict) and m.get("id"))

    def classify(self, model: str, image_jpeg: bytes, tags: list[tuple[str, str]], single: bool,
                 caption: bool = False) -> dict:
        prompt, schema, names = _ask(tags, single, caption)
        image = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
        body = {"model": model, "temperature": 0, "max_tokens": 400,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                          {"type": "image_url", "image_url": {"url": image}}]}],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "labels", "strict": True, "schema": schema}}}
        try:
            r = self._req("/chat/completions", body)
        except VLMError as e:
            if "response_format" not in str(e) and "json_schema" not in str(e) and "schema" not in str(e):
                raise
            body.pop("response_format")            # an older server: ask for plain JSON in the text
            r = self._req("/chat/completions", body)
        try:
            text = r["choices"][0]["message"]["content"]
            if isinstance(text, list):             # content parts
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            out = _json_in(text)
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise VLMError(f"Unexpected answer from the model: {str(r)[:200]}") from e
        return _answer(out, names, single)


def _json_in(text: str):
    """The JSON object in a model's answer (also inside ```json fences or after a few words)."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def jpeg_for_vlm(img, max_side: int = 896) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=90)
    return b.getvalue()

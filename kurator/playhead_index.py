"""Playhead analysis across the library.

Playhead is optional (Settings → Sounds) and Kurator never re-implements its analysis: the documents come
from Playhead itself, either from the browser (static/js/playhead.js runs it in a worker) or from the
`playhead` command line when one is installed (on the PATH, or KURATOR_PLAYHEAD), fed with samples Kurator
decodes. Only documents of schema playhead-analysis version 1 are read.

This module keeps:
  - the document store (cache/playhead/<sha1 of path, mtime_ns, Playhead version>.json),
  - a summary of each sound's latest document in the catalog (table ph_summary), for the Look filter's
    sound character and the sort menu,
  - the background job that analyses many sounds with the command line.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

SCHEMA = "playhead-analysis"
VERSION_RE = re.compile(r"[0-9A-Za-z.+\-]{1,32}")
DOC_MAX = 8 * 1024 * 1024              # one document
STORE_MAX = 2 * 1024 ** 3              # the whole store; the oldest documents go first
SHORT = 120                            # seconds: as the browser, the file's own rate up to here, 22.05 kHz mono above
MAX_SECONDS = 600                      # analysed from the start of a sound
MATERIALS = {"metal": "metal-like", "glass": "glass-like", "damped": "damped (rubber, wood)"}

# Summary columns: what the filters and sorts read from a document
COLUMNS = ["version", "profile", "asset_class", "analysed", "peak_lufs", "lra", "attack", "harsh", "kurt", "events",
           "event_rate", "burst", "arrow", "t60", "log_tau", "material", "mat_ok", "tonal", "rep_hz", "tempo",
           "tempo_ok", "sections", "form"]

MIGRATION = [
    f"""CREATE TABLE ph_summary (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        mtime_ns INTEGER,
        {', '.join(c + (' TEXT' if c in ('version', 'profile', 'asset_class', 'material', 'form') else ' REAL') for c in COLUMNS)},
        updated REAL
    )""",
]

# The Look filter's sound character. Tokens in one group are alternatives (OR); groups combine (AND).
# Harsh and impulsive are relative: the top quarter of the analysed sounds (the curves are on fixed scales,
# but there is no agreed line for "harsh"); the others use the signs and units Playhead documents.
FILTERS = {
    "hit": ("shape", "Hit-like", "Starts strong and dies away (time arrow T20 above 0.3)", "p.arrow > 0.3"),
    "swell": ("shape", "Swell-like", "Grows and stops (time arrow T20 below −0.3)", "p.arrow < -0.3"),
    "bursty": ("timing", "Bursty", "Events come in clusters (burstiness above 0.1)", "p.burst > 0.1"),
    "regular": ("timing", "Regular", "Events come at even intervals (burstiness below −0.1)", "p.burst < -0.1"),
    "metal": ("material", "Metal-like", "Rings like metal (Playhead's decay and material, where reliable)",
              "p.mat_ok = 1 AND p.material = 'metal-like'"),
    "glass": ("material", "Glass-like", "Rings like glass (where reliable)", "p.mat_ok = 1 AND p.material = 'glass-like'"),
    "damped": ("material", "Damped", "Damped like rubber or wood (where reliable)",
               "p.mat_ok = 1 AND p.material = 'damped (rubber, wood)'"),
    "ring": ("ring", "Long ring", "Rings out for a second or more (T60 ≥ 1 s, where reliable)", "p.mat_ok = 1 AND p.t60 >= 1"),
    "dry": ("ring", "Short decay", "Dies away within 0.3 s (T60 < 0.3 s, where reliable)", "p.mat_ok = 1 AND p.t60 < 0.3"),
    "tonal": ("tone", "Tonal", "A clear pitch or tone (phase coherence ≥ 0.5)", "p.tonal >= 0.5"),
    "noisy": ("tone", "Noisy", "Noise-like, no clear tone (phase coherence ≤ 0.1)", "p.tonal <= 0.1"),
    "harsh": ("harsh", "Harsh", "The harshest quarter of the analysed sounds", None),
    "impulsive": ("impulsive", "Impulsive", "The most impulsive quarter of the analysed sounds (spectral kurtosis)", None),
    "beat": ("music", "Steady beat", "A tempo Playhead is sure of", "p.tempo_ok = 1"),
    "sections": ("music", "Sections", "Two or more sections (music)", "p.sections >= 2"),
    "analysed": ("state", "Analysed", "Sounds with a Playhead analysis of the file as it is now", "1"),
    "unanalysed": ("state", "Not analysed", "Sounds without an analysis yet (or changed since)", None),
}
RELATIVE = {"harsh": "harsh", "impulsive": "kurt"}

# Sort menu entries (sounds without a value go last either way; ring time only where Playhead trusts it)
SORTS = {"harshness": "p.harsh", "impulsiveness": "p.kurt", "ring": "CASE WHEN p.mat_ok = 1 THEN p.t60 END",
         "burstiness": "p.burst", "dynamics": "p.lra"}

_VALID = "p.file_id = files.id AND p.mtime_ns = files.mtime_ns"


# ------------------------------------------------------------------------------------------- documents
def check(body: bytes, version: str | None = None) -> dict:
    """The parsed document, or ValueError with a short reason."""
    if len(body) > DOC_MAX:
        raise ValueError("Playhead document too big")
    try:
        doc = json.loads(body)
    except ValueError:
        raise ValueError("not JSON") from None
    if not (isinstance(doc, dict) and doc.get("schema") == SCHEMA and doc.get("schemaVersion") == 1):
        raise ValueError("not a Playhead analysis (schema playhead-analysis, version 1)")
    gen = doc.get("generator")
    v = gen.get("version") if isinstance(gen, dict) else None
    if not (isinstance(v, str) and VERSION_RE.fullmatch(v)):
        raise ValueError("the document has no Playhead version")
    if version is not None and v != version:
        raise ValueError("the document's Playhead version doesn't match")
    return doc


def doc_file(ph_dir: str, path: str, mtime_ns: int, version: str) -> str:
    key = hashlib.sha1(f"{path}\n{mtime_ns}\n{version}".encode("utf-8", "surrogatepass")).hexdigest()
    return os.path.join(ph_dir, key + ".json")


_saves = 0


def save(ph_dir: str, f: str, body: bytes) -> None:
    """Write a document atomically; now and then drop the oldest ones to keep the store under STORE_MAX."""
    global _saves
    os.makedirs(ph_dir, exist_ok=True)
    tmp = f"{f}.{threading.get_ident()}.part"
    with open(tmp, "wb") as fh:
        fh.write(body)
    os.replace(tmp, f)
    _saves += 1
    if _saves % 64 == 1:
        prune(ph_dir)


def prune(ph_dir: str, limit: int = STORE_MAX) -> int:
    try:
        files = [(e.stat().st_mtime, e.stat().st_size, e.path) for e in os.scandir(ph_dir) if e.name.endswith(".json")]
    except OSError:
        return 0
    total = sum(s for _, s, _ in files)
    gone = 0
    if total > limit:
        for _, size, path in sorted(files):
            if total <= limit * 0.9:
                break
            try:
                os.remove(path)
                total -= size
                gone += 1
            except OSError:
                pass
    return gone


def thin(doc: dict, points: int = 320) -> dict:
    """A lighter copy of a document for grid thumbnails: curves, waveform and spectrogram with at most
    `points` columns over the sound (means, outer min / max, loudest band), everything else as it is.
    Playhead still draws it (drawStrip maps curves by their rate, the rest by its column count)."""
    import base64

    import numpy as np
    out = dict(doc)
    dur = _num(_get(doc, "source", "duration")) or 0

    def groups(n):
        return np.linspace(0, n, min(points, n) + 1).astype(np.int64)[:-1]

    curves = doc.get("curves")
    rate = _num(curves.get("rate")) if isinstance(curves, dict) else None
    if rate and dur > 0:
        lens = [len(v) for v in curves.values() if isinstance(v, list)]
        L = max(set(lens), key=lens.count) if lens else 0
        if L > points:
            g = groups(L)
            c2 = dict(curves)
            for k, v in curves.items():
                if not (isinstance(v, list) and len(v) == L):
                    continue
                a = np.array([x if isinstance(x, (int, float)) and not isinstance(x, bool) else np.nan for x in v], np.float64)
                ok = np.isfinite(a)
                tot = np.add.reduceat(np.where(ok, a, 0.0), g)
                cnt = np.add.reduceat(ok.astype(np.int64), g)
                c2[k] = [round(float(t / c), 4) if c else None for t, c in zip(tot, cnt)]
            c2["rate"] = len(g) * rate / L
            out["curves"] = c2
    wave = doc.get("wave")
    if isinstance(wave, dict) and isinstance(wave.get("columns"), int) and wave["columns"] > points:
        try:
            C = wave["columns"]
            g = groups(C)
            mn = np.minimum.reduceat(np.asarray(wave["min"][:C], np.float64), g)
            mx = np.maximum.reduceat(np.asarray(wave["max"][:C], np.float64), g)
            rm = np.maximum.reduceat(np.asarray(wave["rms"][:C], np.float64), g)
            out["wave"] = {**wave, "columns": len(g), "min": [round(float(x), 4) for x in mn],
                           "max": [round(float(x), 4) for x in mx], "rms": [round(float(x), 4) for x in rm]}
        except (TypeError, ValueError, KeyError):
            out["wave"] = wave
    sp = doc.get("spectrum")
    if isinstance(sp, dict) and sp.get("encoding") == "base64-u8" and isinstance(sp.get("columns"), int) and sp["columns"] > points:
        try:
            B, C = int(sp["bands"]), int(sp["columns"])
            u8 = np.frombuffer(base64.b64decode(sp["data"]), np.uint8)[: B * C].reshape(C, B)
            red = np.maximum.reduceat(u8, groups(C), axis=0)
            out["spectrum"] = {**sp, "columns": int(red.shape[0]), "data": base64.b64encode(red.tobytes()).decode("ascii")}
        except (TypeError, ValueError, KeyError):
            out["spectrum"] = sp
    return out


# ------------------------------------------------------------------------------------------- summaries
def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _get(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def summarize(doc: dict) -> dict:
    """The summary row of a document: the values the filters and sorts use (None where Playhead has none)."""
    D = doc.get("descriptors") if isinstance(doc.get("descriptors"), dict) else {}
    curves = doc.get("curves") if isinstance(doc.get("curves"), dict) else {}
    h = sorted(x for x in (curves.get("harshness") or []) if _num(x) is not None)
    dec = D.get("decay") if isinstance(D.get("decay"), dict) else {}
    rel = dec.get("reliable")
    mat = dec.get("material")
    rhythm = doc.get("rhythm") if isinstance(doc.get("rhythm"), dict) else None
    sections = doc.get("sections") if isinstance(doc.get("sections"), list) else None
    events = doc.get("events") if isinstance(doc.get("events"), list) else None
    form = "".join(str(s.get("label", ""))[:2] for s in sections or [] if isinstance(s, dict))[:64]
    text = lambda v, n=40: v[:n] if isinstance(v, str) and v else None  # noqa: E731
    return {
        "version": text(_get(doc, "generator", "version"), 32),
        "profile": text(doc.get("profile")),
        "asset_class": text(doc.get("assetClass")),
        "analysed": _num(_get(doc, "source", "duration")),
        "peak_lufs": _num(_get(doc, "summary", "peakLufs")),
        "lra": _num(_get(doc, "summary", "loudnessRange")),
        "attack": _num(_get(doc, "summary", "attack")),
        "harsh": h[int(0.9 * (len(h) - 1))] if h else None,      # how harsh it gets: the 90th percentile
        "kurt": _num(_get(D, "kurtosis", "K")),
        "events": len(events) if events is not None else None,
        "event_rate": _num(_get(D, "events", "rate")),
        "burst": _num(_get(D, "events", "burstiness")),
        "arrow": _num(_get(D, "arrow", "T20")),
        "t60": _num(dec.get("t60")),
        "log_tau": _num(dec.get("logTauD")),
        "material": text(mat) if mat in MATERIALS.values() or mat == "not modal" else None,
        "mat_ok": 1.0 if rel is True else 0.0 if rel is False else None,
        "tonal": _num(_get(D, "periodicity", "coherence")),
        "rep_hz": _num(_get(D, "repetition", "hz")),
        "tempo": _num(rhythm.get("tempo")) if rhythm else None,
        "tempo_ok": (1.0 if rhythm.get("reliable") is True else 0.0) if rhythm else None,
        "sections": float(len(sections)) if sections is not None else None,
        "form": form or None,
    }


def index(db, file_id: int, mtime_ns: int, doc: dict) -> None:
    """Remember the summary of a sound's document (the latest one stored wins)."""
    s = summarize(doc)
    cols = ["file_id", "mtime_ns", *COLUMNS, "updated"]
    vals = [file_id, mtime_ns, *(s[c] for c in COLUMNS), time.time()]
    with db.write_lock:
        db.cat.execute(f"INSERT OR REPLACE INTO ph_summary({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)


def backfill(db, ph_dir: str) -> int:
    """Summaries for documents stored before the catalog kept them (Kurator 0.16): matched to the sounds
    of the catalog by the store's key (path, mtime_ns, version)."""
    try:
        names = {e.name[:-5]: e.path for e in os.scandir(ph_dir) if e.name.endswith(".json")}
    except OSError:
        return 0
    versions = set()
    pat = re.compile(rb'"generator"\s*:\s*\{[^}]*?"version"\s*:\s*"([0-9A-Za-z.+\-]{1,32})"')
    for path in names.values():
        try:
            with open(path, "rb") as fh:
                m = pat.search(fh.read(4096))
        except OSError:
            continue
        if m:
            versions.add(m.group(1).decode())
    n = 0
    rows = db.cat.execute("SELECT id, path, mtime_ns FROM files WHERE kind = 'audio' AND id NOT IN "
                          "(SELECT file_id FROM ph_summary)").fetchall()
    for fid, path, mt in rows:
        for v in versions:
            key = os.path.basename(doc_file(ph_dir, path, mt, v))[:-5]
            if key in names:
                try:
                    with open(names[key], "rb") as fh:
                        index(db, fid, mt, check(fh.read(), v))
                    n += 1
                except (OSError, ValueError):
                    pass
                break
    return n


# ------------------------------------------------------------------------------------- filters, sorts
def tokens(ph: str | None) -> list[str]:
    return [t for t in dict.fromkeys(x.strip().lower() for x in (ph or "").split(",")) if t in FILTERS]


def _quartile(db, col: str) -> float | None:
    """The value above which the top quarter of the analysed sounds lie."""
    n = db.cat.execute(f"SELECT COUNT(*) FROM ph_summary p JOIN files ON {_VALID} WHERE p.{col} IS NOT NULL").fetchone()[0]
    if not n:
        return None
    row = db.cat.execute(f"SELECT p.{col} FROM ph_summary p JOIN files ON {_VALID} WHERE p.{col} IS NOT NULL "
                         f"ORDER BY p.{col} LIMIT 1 OFFSET ?", (n - math.ceil(n / 4),)).fetchone()
    return row[0] if row else None


def filter_clause(db, ph: str | None) -> tuple[str, list] | None:
    """SQL (on files) for the chosen sound character, or None when nothing is chosen."""
    ts = tokens(ph)
    if not ts:
        return None
    groups: dict[str, list[tuple[str, list]]] = {}
    for t in ts:
        g, _, _, cond = FILTERS[t]
        args: list = []
        if t == "unanalysed":
            groups.setdefault(g, []).append((f"(files.kind = 'audio' AND NOT EXISTS (SELECT 1 FROM ph_summary p WHERE {_VALID}))", []))
            continue
        if t in RELATIVE:
            q = _quartile(db, RELATIVE[t])
            cond, args = ("0", []) if q is None else (f"p.{RELATIVE[t]} >= ?", [q])
        groups.setdefault(g, []).append((f"EXISTS (SELECT 1 FROM ph_summary p WHERE {_VALID} AND {cond})", args))
    sql, args = [], []
    for alts in groups.values():
        sql.append("(" + " OR ".join(s for s, _ in alts) + ")")
        for _, a in alts:
            args += a
    return " AND ".join(sql), args


def sort_sql(key: str) -> str | None:
    """ORDER BY for a Playhead sort ({o}: ASC / DESC); sounds without a value last."""
    col = SORTS.get(key)
    if not col:
        return None
    sub = f"(SELECT {col} FROM ph_summary p WHERE {_VALID})"
    return f"{sub} IS NULL, {sub} {{o}}, name COLLATE NOCASE"


def stamps(db) -> dict[int, float]:
    """file id -> when its summary was filed, for sounds analysed as they are now (grid strips use it to
    know which sounds have a document, and when it changed)."""
    return dict(db.cat.execute(f"SELECT p.file_id, p.updated FROM ph_summary p JOIN files ON {_VALID}").fetchall())


def counts(db) -> dict:
    """Sounds in the library and how many have an analysis of the file as it is now."""
    lib = "root_id IN (SELECT id FROM roots WHERE kind = 'library') AND kind = 'audio'"
    total = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE {lib}").fetchone()[0]
    done = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE {lib} AND EXISTS (SELECT 1 FROM ph_summary p WHERE {_VALID})").fetchone()[0]
    return {"sounds": total, "analysed": done, "missing": total - done}


def todo(db, scope: str = "missing", ids: list[int] | None = None, limit: int = 200_000) -> list[tuple]:
    """(id, path, mtime_ns, duration, name) of the sounds to analyse: not analysed yet, all, or the given ids."""
    sel = "SELECT id, path, mtime_ns, json_extract(meta, '$.duration'), name FROM files WHERE kind = 'audio'"
    if scope == "ids":
        out = []
        ids = [int(i) for i in ids or []][:limit]
        for k in range(0, len(ids), 900):
            chunk = ids[k:k + 900]
            out += db.cat.execute(f"{sel} AND id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        order = {i: n for n, i in enumerate(ids)}
        return sorted((tuple(r) for r in out), key=lambda r: order.get(r[0], 0))
    sel += " AND root_id IN (SELECT id FROM roots WHERE kind = 'library')"
    if scope != "all":
        sel += f" AND NOT EXISTS (SELECT 1 FROM ph_summary p WHERE {_VALID})"
    return [tuple(r) for r in db.cat.execute(sel + " ORDER BY dir, name LIMIT ?", (limit,)).fetchall()]


# ----------------------------------------------------------------------------------- the command line
def find_cli() -> list[str] | None:
    """How to run the Playhead command line: KURATOR_PLAYHEAD (an executable, or a .js file run with
    node), else `playhead` on the PATH. None when there is none."""
    p = os.environ.get("KURATOR_PLAYHEAD", "").strip()
    if p:
        if p.lower().endswith((".js", ".mjs", ".cjs")):
            node = shutil.which("node")
            return [node, p] if node and os.path.isfile(p) else None
        exe = p if os.path.isfile(p) and os.access(p, os.X_OK) else shutil.which(p)
        return [exe] if exe else None
    exe = shutil.which("playhead")
    return [exe] if exe else None


_info_cache: dict = {}


def cli_info() -> dict | None:
    """{"path", "version"} of the command line, or None."""
    cmd = find_cli()
    if not cmd:
        return None
    try:
        key = (tuple(cmd), os.path.getmtime(cmd[-1]))
    except OSError:
        key = (tuple(cmd), 0)
    if key not in _info_cache:
        v = None
        try:
            r = subprocess.run([*cmd, "version"], capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
            out = r.stdout.decode("utf-8", "replace").strip().splitlines()
            if r.returncode == 0 and out and VERSION_RE.fullmatch(out[-1].strip()):
                v = out[-1].strip()
        except (OSError, subprocess.SubprocessError):
            return None
        _info_cache.clear()
        _info_cache[key] = {"path": " ".join(cmd), "version": v}
    return dict(_info_cache[key])


def analyse(cmd: list[str], path: str, duration: float | None, name: str, profile: str = "auto",
            timeout: float = 900) -> tuple[dict, bytes]:
    """Analyse one sound with the command line: samples decoded by Kurator (the same ones the browser
    gets), raw float32 on stdin, the document on stdout. Returns (document, its JSON)."""
    import numpy as np

    from . import audio_analysis
    short = bool(duration) and 0 < duration <= SHORT
    info, chunks = audio_analysis.samples(path, 0 if short else 22050, not short, MAX_SECONDS)
    if not info["frames"]:
        raise ValueError("the sound is empty")
    sr = info["samplerate"]
    args = [*cmd, "analyze", "-", "--sr", str(sr),
            "--channels", str(info["channels"]), "--profile", profile, "--name", os.path.basename(name or path)]
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = [], []
    readers = [threading.Thread(target=lambda s, b: b.append(s.read()), args=(p.stdout, out), daemon=True),
               threading.Thread(target=lambda s, b: b.append(s.read()), args=(p.stderr, err), daemon=True)]
    for r in readers:
        r.start()
    try:
        left = info["frames"] * info["channels"] * 4
        for c in chunks:
            b = np.ascontiguousarray(c, dtype="<f4").tobytes()[:left]
            p.stdin.write(b)
            left -= len(b)
            if left <= 0:
                break
        if left > 0:                       # shorter than expected: pad with silence, as the samples route does
            p.stdin.write(bytes(left))
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        raise ValueError("Playhead took too long") from None
    for r in readers:
        r.join(5)
    body = (out[0] if out else b"").strip()
    if p.returncode != 0:
        msg = (err[0] if err else b"").decode("utf-8", "replace").strip().splitlines()
        raise ValueError((msg[-1] if msg else f"Playhead stopped (exit {p.returncode})")[:300])
    return check(body), body


class LibraryJob:
    """Analyse many sounds with the command line in the background, a few at a time."""

    def __init__(self, db, ph_dir: str):
        self.db = db
        self.ph_dir = ph_dir
        self.state: dict = {"running": False}
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    def start(self, items: list[tuple], workers: int | None = None) -> dict:
        cmd = find_cli()
        if not cmd:
            raise ValueError("The Playhead command line isn't installed (playhead on the PATH, or KURATOR_PLAYHEAD)")
        with self._lock:
            if self.state.get("running"):
                raise ValueError("Playhead is already analysing sounds")
            if not items:
                raise ValueError("No sounds to analyse")
            workers = workers or max(1, min(4, (os.cpu_count() or 2) // 2))
            self._cancel.clear()
            self.state = {"running": True, "done": 0, "failed": 0, "total": len(items), "current": [], "errors": [],
                          "started": time.time(), "finished": None, "workers": workers, "cancelled": False}
        threading.Thread(target=self._run, args=(cmd, items, workers), name="kurator-playhead", daemon=True).start()
        return self.status()

    def cancel(self) -> None:
        self._cancel.set()

    def status(self) -> dict:
        with self._lock:
            s = dict(self.state)
            s["current"] = list(s.get("current") or [])
            s["errors"] = list(s.get("errors") or [])
            return s

    def _one(self, cmd, item) -> None:
        fid, path, mtime_ns, duration, name = item
        if self._cancel.is_set():
            return
        with self._lock:
            self.state["current"].append(name)
        try:
            st = os.stat(path)
            if st.st_mtime_ns != mtime_ns:
                raise ValueError("the file changed since it was indexed (rescan first)")
            doc, body = analyse(cmd, path, duration, name)
            v = doc["generator"]["version"]
            save(self.ph_dir, doc_file(self.ph_dir, path, mtime_ns, v), body)
            index(self.db, fid, mtime_ns, doc)
            ok = True
        except Exception as e:  # noqa: BLE001 - one sound failing must not stop the rest
            ok = False
            msg = f"{name}: {e}"
        with self._lock:
            self.state["current"].remove(name)
            if ok:
                self.state["done"] += 1
            else:
                self.state["failed"] += 1
                self.state["errors"] = (self.state["errors"] + [msg])[-20:]

    def _run(self, cmd, items, workers) -> None:
        try:
            with ThreadPoolExecutor(workers, thread_name_prefix="kurator-playhead") as ex:
                for _ in ex.map(lambda it: self._one(cmd, it), items):
                    pass
        finally:
            with self._lock:
                self.state.update(running=False, finished=time.time(), cancelled=self._cancel.is_set(), current=[])

"""AI features: embeddings, similar/text search, tag pools (profiles), suggestions, training, captions.

Nothing here changes your tags on its own: every result is a *suggestion* until you accept it.
Accepted suggestions become tags with source 'ai' (+ confidence), so human and AI tags stay apart.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time

from .. import tags as tagmod
from ..db import Database
from ..processing import process_file
from .embed import MODELS, AIUnavailable, Embedder, image_from_bytes, prompts_for, status
from .vlm import NONE, Ollama, VLMError, jpeg_for_vlm

# files that only have a generic 'no preview' card are not worth an embedding
REAL_PREVIEW = "(meta IS NULL OR json_extract(meta, '$.placeholder') IS NULL)"
BATCH = 32
DEFAULTS = {"model": "fast", "custom_model": None, "custom_pretrained": None, "ollama_url": "http://127.0.0.1:11434",
            "vlm_model": ""}


class AIError(ValueError):
    pass


def _sigmoid(x):
    import numpy as np
    return 1 / (1 + np.exp(-x))


def _softmax(x):
    import numpy as np
    e = np.exp(x - x.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


class AIService:
    def __init__(self, db: Database):
        self.db = db
        self.path = os.path.join(db.data_dir, "embeddings.db")
        self._local = threading.local()
        with self._con() as c:
            c.execute("CREATE TABLE IF NOT EXISTS emb (hash TEXT NOT NULL, model TEXT NOT NULL, vec BLOB NOT NULL, "
                      "PRIMARY KEY (hash, model)) WITHOUT ROWID")
        self._embedder: Embedder | None = None
        self._matrix = None  # (model_key, hashes, np.ndarray)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.state: dict = {"running": False}

    # -- settings / status ---------------------------------------------------
    def settings(self) -> dict:
        return {**DEFAULTS, **(self.db.get_setting("ai", {}) or {})}

    def save_settings(self, patch: dict) -> dict:
        s = self.settings()
        for k in DEFAULTS:
            if k in patch and patch[k] is not None:
                s[k] = patch[k]
        if s["model"] not in MODELS and s["model"] != "custom":
            raise AIError("Unknown model")
        self.db.set_setting("ai", s)
        if self._embedder and self._embedder.key != self._model_key(s):
            self._embedder.unload()
            self._embedder = None
            self._matrix = None
        return s

    def _model_key(self, s=None) -> str:
        s = s or self.settings()
        if s["model"] == "custom":
            return f"{s['custom_model']}/{s['custom_pretrained'] or 'random'}"
        m, p, _ = MODELS[s["model"]]
        return f"{m}/{p}"

    def embedder(self) -> Embedder:
        s = self.settings()
        if self._embedder is None or self._embedder.key != self._model_key(s):
            if s["model"] == "custom":
                self._embedder = Embedder(s["custom_model"], s["custom_pretrained"])
            else:
                m, p, _ = MODELS[s["model"]]
                self._embedder = Embedder(m, p)
        return self._embedder

    def info(self) -> dict:
        key = self._model_key()
        with self._con() as c:
            n = c.execute("SELECT COUNT(*) FROM emb WHERE model = ?", (key,)).fetchone()[0]
        images = self.db.cat.execute("SELECT COUNT(DISTINCT hash) FROM files WHERE status = 1 AND kind IN ('image', 'model', 'video') AND hash IS NOT NULL "
                                     "AND root_id IN (SELECT id FROM roots WHERE kind='library') AND " + REAL_PREVIEW).fetchone()[0]
        pending = self.db.cat.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0]
        return {"status": status(), "settings": self.settings(), "models": {k: v[2] for k, v in MODELS.items()},
                "model_key": key, "embedded": n, "images": images, "suggestions": pending, "job": dict(self.state)}

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._local.con = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            con.execute("PRAGMA journal_mode=WAL")
        return con

    # -- jobs ------------------------------------------------------------------
    def _start(self, kind: str, fn, *args) -> dict:
        if self._thread and self._thread.is_alive():
            raise AIError("Another AI task is running")
        self._cancel.clear()
        self.state = {"running": True, "kind": kind, "done": 0, "total": 0, "started": time.time(), "errors": [], "result": None}

        def run():
            try:
                self.state["result"] = fn(*args)
            except (AIUnavailable, VLMError, AIError) as e:
                self.state["errors"].append(str(e))
            except Exception as e:
                self.state["errors"].append(f"{type(e).__name__}: {e}")
            finally:
                self.state["running"] = False
                self.state["finished"] = time.time()

        self._thread = threading.Thread(target=run, name=f"kurator-ai-{kind}", daemon=True)
        self._thread.start()
        return dict(self.state)

    def cancel(self):
        self._cancel.set()

    def wait(self, timeout: float = 600) -> dict:
        if self._thread:
            self._thread.join(timeout)
        return dict(self.state)

    # -- images -----------------------------------------------------------------
    def _thumb(self, file_id: int, h: str, path: str, size: int = 512):
        data = self.db.get_thumb(h, size)
        if data is None:
            r = process_file(path, (size,))
            if "error" in r:
                return None
            self.db.put_thumbs(r["hash"], r["thumbs"])
            data = r["thumbs"][size]
        return image_from_bytes(data)

    def _image_rows(self, ids: list[int] | None) -> list[tuple]:
        sql = ("SELECT id, hash, path FROM files WHERE status = 1 AND kind IN ('image', 'model', 'video') AND hash IS NOT NULL "
               "AND root_id IN (SELECT id FROM roots WHERE kind='library') AND " + REAL_PREVIEW)
        if ids is None:
            return self.db.cat.execute(sql).fetchall()
        out = []
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            out += self.db.cat.execute(sql + f" AND id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        return out

    # -- embeddings -----------------------------------------------------------
    def start_embed(self, ids: list[int] | None = None) -> dict:
        return self._start("embed", self.embed, ids)

    def embed(self, ids: list[int] | None = None) -> dict:
        import numpy as np
        emb = self.embedder()
        key = emb.key
        rows = self._image_rows(ids)
        by_hash: dict[str, tuple] = {}
        for fid, h, path in rows:
            by_hash.setdefault(h, (fid, path))
        con = self._con()
        have = set()
        hashes = list(by_hash)
        for i in range(0, len(hashes), 900):
            chunk = hashes[i:i + 900]
            have.update(r[0] for r in con.execute(
                f"SELECT hash FROM emb WHERE model = ? AND hash IN ({','.join('?' * len(chunk))})", (key, *chunk)))
        todo = [h for h in hashes if h not in have]
        self.state["total"] = len(todo)
        done = 0
        for i in range(0, len(todo), BATCH):
            if self._cancel.is_set():
                break
            batch = todo[i:i + BATCH]
            imgs, ok = [], []
            for h in batch:
                fid, path = by_hash[h]
                try:
                    im = self._thumb(fid, h, path)
                except Exception:
                    im = None
                if im is not None:
                    imgs.append(im)
                    ok.append(h)
            if imgs:
                vecs = emb.encode_images(imgs).astype(np.float16)
                con.executemany("INSERT OR REPLACE INTO emb(hash, model, vec) VALUES (?, ?, ?)",
                                [(h, key, v.tobytes()) for h, v in zip(ok, vecs)])
                con.commit()
            done += len(batch)
            self.state["done"] = done
        self._matrix = None
        return {"embedded": done, "skipped_existing": len(have)}

    def _load_matrix(self):
        import numpy as np
        key = self._model_key()
        if self._matrix is not None and self._matrix[0] == key:
            return self._matrix
        rows = self._con().execute("SELECT hash, vec FROM emb WHERE model = ?", (key,)).fetchall()
        if not rows:
            self._matrix = (key, [], np.zeros((0, 1), np.float32))
        else:
            self._matrix = (key, [r[0] for r in rows], np.stack([np.frombuffer(r[1], np.float16) for r in rows]).astype(np.float32))
        return self._matrix

    def _vectors_for(self, file_rows: list[tuple]):
        """file_rows [(id, hash)] -> (ids, matrix) for the ones that have an embedding."""
        import numpy as np
        _, hashes, M = self._load_matrix()
        pos = {h: i for i, h in enumerate(hashes)}
        ids, idx = [], []
        for fid, h in file_rows:
            if h in pos:
                ids.append(fid)
                idx.append(pos[h])
        return ids, (M[idx] if idx else np.zeros((0, M.shape[1] if M.ndim == 2 else 1), np.float32))

    def _rank(self, query_vec, limit: int) -> list[tuple[int, float]]:
        import numpy as np
        _, hashes, M = self._load_matrix()
        if not hashes:
            raise AIError("No embeddings yet - compute them first (AI ▸ Prepare images)")
        sims = M @ query_vec
        order = np.argsort(-sims)[: limit * 3]
        top = {hashes[i]: float(sims[i]) for i in order}
        out = []
        q = list(top)
        for i in range(0, len(q), 900):
            chunk = q[i:i + 900]
            for fid, h in self.db.cat.execute(
                    f"SELECT id, hash FROM files WHERE hash IN ({','.join('?' * len(chunk))}) "
                    "AND root_id IN (SELECT id FROM roots WHERE kind='library')", chunk):
                out.append((fid, top[h]))
        out.sort(key=lambda x: -x[1])
        return out[:limit]

    def search_text(self, text: str, limit: int = 500) -> list[tuple[int, float]]:
        v = self.embedder().encode_texts([text])[0]
        return self._rank(v, limit)

    def similar(self, file_id: int, limit: int = 500) -> list[tuple[int, float]]:
        row = self.db.cat.execute(f"SELECT hash, kind IN ('image', 'model', 'video') AND {REAL_PREVIEW} FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            raise AIError("No such file")
        if not row[1]:
            raise AIError("Find similar works on images (this file has no image preview)")
        _, hashes, M = self._load_matrix()
        try:
            v = M[hashes.index(row[0])]
        except ValueError:
            raise AIError("This image has no embedding yet - run AI ▸ Prepare images") from None
        ranked = self._rank(v, limit + 1)
        # the image you started from comes first, then the most similar ones
        return [(file_id, 1.0)] + [r for r in ranked if r[0] != file_id][:limit - 1]

    # -- profiles (tag pools) --------------------------------------------------
    def profiles(self) -> list[dict]:
        out = []
        for r in self.db.cat.execute("SELECT * FROM ai_profiles ORDER BY name COLLATE NOCASE"):
            d = dict(r)
            d["tag_ids"] = json.loads(d["tag_ids"])
            tr = json.loads(d["trained"]) if d["trained"] else None
            d["trained"] = {k: v for k, v in tr.items() if k not in ("W", "b")} if tr else None
            d["last_result"] = json.loads(d["last_result"]) if d["last_result"] else None
            out.append(d)
        return out

    def save_profile(self, data: dict, pid: int | None = None) -> dict:
        name = str(data.get("name") or "").strip() or "Tag pool"
        tag_ids = [int(t) for t in data.get("tag_ids") or []]
        known = {r[0] for r in self.db.cat.execute("SELECT id FROM tags")}
        tag_ids = [t for t in dict.fromkeys(tag_ids) if t in known]
        if len(tag_ids) < 2:
            raise AIError("Choose at least two tags for the pool")
        mode = data.get("mode", "single")
        method = data.get("method", "zeroshot")
        if mode not in ("single", "multi") or method not in ("zeroshot", "trained", "vlm"):
            raise AIError("Invalid mode or method")
        thr = float(data.get("threshold", 0.5))
        vals = (name, json.dumps(tag_ids), mode, method, max(0.0, min(1.0, thr)), data.get("vlm_model") or None,
                int(bool(data.get("captions"))))
        with self.db.write_lock:
            c = self.db.cat
            if pid is None:
                pid = c.execute("INSERT INTO ai_profiles(name, tag_ids, mode, method, threshold, vlm_model, captions, created) "
                                "VALUES (?,?,?,?,?,?,?,?)", (*vals, time.time())).lastrowid
            else:
                old = c.execute("SELECT tag_ids FROM ai_profiles WHERE id = ?", (pid,)).fetchone()
                if not old:
                    raise AIError("No such tag pool")
                c.execute("UPDATE ai_profiles SET name=?, tag_ids=?, mode=?, method=?, threshold=?, vlm_model=?, captions=? WHERE id=?",
                          (*vals, pid))
                if json.loads(old[0]) != tag_ids:
                    c.execute("UPDATE ai_profiles SET trained = NULL WHERE id = ?", (pid,))  # pool changed: retrain
        return next(p for p in self.profiles() if p["id"] == pid)

    def delete_profile(self, pid: int) -> None:
        with self.db.write_lock:
            self.db.cat.execute("DELETE FROM ai_profiles WHERE id = ?", (pid,))

    def _profile(self, pid: int) -> dict:
        r = self.db.cat.execute("SELECT * FROM ai_profiles WHERE id = ?", (pid,)).fetchone()
        if not r:
            raise AIError("No such tag pool")
        d = dict(r)
        d["tag_ids"] = json.loads(d["tag_ids"])
        d["trained"] = json.loads(d["trained"]) if d["trained"] else None
        return d

    def _pool(self, p: dict) -> list[dict]:
        rows = {r[0]: dict(r) for r in self.db.cat.execute(
            f"SELECT * FROM tags WHERE id IN ({','.join('?' * len(p['tag_ids']))})", p["tag_ids"])}
        return [rows[t] for t in p["tag_ids"] if t in rows]

    def set_prompt(self, tag_id: int, prompt: str | None) -> None:
        with self.db.write_lock:
            self.db.cat.execute("UPDATE tags SET ai_prompt = ? WHERE id = ?", ((prompt or "").strip() or None, tag_id))

    # -- labels for training ---------------------------------------------------
    def _labels(self, pool: list[dict]) -> dict[int, set[int]]:
        """file_id -> pool tag ids it carries (a sub-tag counts for its pool parent)."""
        names = {t["id"]: t["name"].lower() for t in pool}
        all_tags = {r[0]: r[1].lower() for r in self.db.cat.execute("SELECT id, name FROM tags")}
        maps: dict[int, int] = {}
        for tid, n in all_tags.items():
            for pid, pn in names.items():
                if n == pn or n.startswith(pn + "/"):
                    maps[tid] = pid
        out: dict[int, set[int]] = {}
        if not maps:
            return out
        q = ",".join("?" * len(maps))
        for fid, tid in self.db.cat.execute(f"SELECT file_id, tag_id FROM file_tags WHERE tag_id IN ({q})", list(maps)):
            out.setdefault(fid, set()).add(maps[tid])
        return out

    def train(self, pid: int) -> dict:
        """Learn the pool's tags from images you already tagged (plus your rejections as counter-examples)."""
        import numpy as np
        p = self._profile(pid)
        pool = self._pool(p)
        labels = self._labels(pool)
        rows = self.db.cat.execute(
            f"SELECT id, hash FROM files WHERE id IN ({','.join('?' * len(labels))})", list(labels)).fetchall() if labels else []
        ids, X = self._vectors_for(rows)
        if len(ids) < 10:
            raise AIError(f"Only {len(ids)} tagged images with embeddings - tag at least ~10 per tag (and run Prepare images) first")
        rej: dict[int, set[int]] = {}
        for fid, tid in self.db.cat.execute("SELECT file_id, tag_id FROM rejections"):
            rej.setdefault(tid, set()).add(fid)
        tag_ids = [t["id"] for t in pool]
        counts = {t: sum(1 for f in ids if t in labels.get(f, ())) for t in tag_ids}
        weak = [next(x["name"] for x in pool if x["id"] == t) for t, n in counts.items() if n < 5]
        if len([t for t in tag_ids if counts[t] >= 5]) < 2:
            raise AIError("Need at least 5 tagged examples for at least two tags of the pool")
        rnd = random.Random(42)
        order = list(range(len(ids)))
        rnd.shuffle(order)
        cut = max(1, len(order) // 5)
        test, trainset = order[:cut], order[cut:]
        W, B = [], []
        Xtr = X[trainset]
        for t in tag_ids:
            y = np.array([1.0 if t in labels.get(ids[i], ()) else 0.0 for i in trainset])
            w, b = self._fit(Xtr, y)
            W.append(w)
            B.append(b)
        W, B = np.stack(W), np.array(B)
        P = _sigmoid(X[test] @ W.T + B)
        if p["mode"] == "single":
            pred = P.argmax(axis=1)
            truth = [labels.get(ids[i], set()) for i in test]
            acc = float(np.mean([tag_ids[k] in tr for k, tr in zip(pred, truth)]))
        else:
            yt = np.array([[1.0 if t in labels.get(ids[i], ()) else 0.0 for t in tag_ids] for i in test])
            pr = P >= 0.5
            tp = float((pr & (yt > 0)).sum())
            prec = tp / max(1.0, pr.sum())
            rec = tp / max(1.0, (yt > 0).sum())
            acc = 2 * prec * rec / max(1e-9, prec + rec)
        # final model on everything
        W2, B2 = [], []
        for t in tag_ids:
            y = np.array([1.0 if t in labels.get(f, ()) else 0.0 for f in ids])
            w, b = self._fit(X, y)
            W2.append(w.tolist())
            B2.append(float(b))
        result = {"model": self._model_key(), "examples": len(ids), "per_tag": {str(t): counts[t] for t in tag_ids},
                  "score": round(acc, 3), "metric": "accuracy" if p["mode"] == "single" else "F1", "weak": weak,
                  "trained_at": time.time(), "rejections_used": sum(len(v) for v in rej.values()),
                  "W": W2, "b": B2, "tag_ids": tag_ids}
        with self.db.write_lock:
            self.db.cat.execute("UPDATE ai_profiles SET trained = ? WHERE id = ?", (json.dumps(result), pid))
        return {k: v for k, v in result.items() if k not in ("W", "b")}

    @staticmethod
    def _fit(X, y, iters: int = 400, lr: float = 2.0, l2: float = 1e-3):
        """Balanced logistic regression by gradient descent (no extra dependencies)."""
        import numpy as np
        n, d = X.shape
        pos = max(1.0, y.sum())
        neg = max(1.0, n - y.sum())
        sw = np.where(y > 0, n / (2 * pos), n / (2 * neg))
        w = np.zeros(d, np.float64)
        b = 0.0
        Xd = X.astype(np.float64)
        for _ in range(iters):
            p = _sigmoid(Xd @ w + b)
            g = (p - y) * sw
            w -= lr * (Xd.T @ g / n + l2 * w)
            b -= lr * float(g.mean())
        return w, b

    # -- running a pool ----------------------------------------------------------
    def start_run(self, pid: int, ids: list[int]) -> dict:
        p = self._profile(pid)
        return self._start("vlm" if p["method"] == "vlm" else "suggest", self.run_profile, pid, ids)

    def run_profile(self, pid: int, ids: list[int]) -> dict:
        p = self._profile(pid)
        pool = self._pool(p)
        if len(pool) < 2:
            raise AIError("The pool needs at least two existing tags")
        rows = self._image_rows(list(dict.fromkeys(int(i) for i in ids)))
        labels = self._labels(pool)
        rejected = {(r[0], r[1]) for r in self.db.cat.execute("SELECT file_id, tag_id FROM rejections")}
        if p["mode"] == "single":  # already sorted into one of the pool's tags: leave alone
            rows = [r for r in rows if not labels.get(r[0])]
        self.state["total"] = len(rows)
        if p["method"] == "vlm":
            return self._run_vlm(p, pool, rows, labels, rejected)
        import numpy as np
        if p["method"] == "trained":
            tr = p["trained"]
            if not tr:
                raise AIError("This pool isn't trained yet - press Train first")
            if tr.get("model") != self._model_key():
                raise AIError("The pool was trained with another AI model - train it again")
            W, B = np.array(tr["W"], np.float32), np.array(tr["b"], np.float32)
            order = tr["tag_ids"]
        else:
            emb = self.embedder()
            tv = emb.tag_vectors([prompts_for(t["name"], t.get("ai_prompt")) for t in pool])
            order = [t["id"] for t in pool]
        known = set(self._load_matrix()[1])
        missing = [r for r in rows if r[1] not in known]
        if missing:  # embed what's missing first
            self.embed([r[0] for r in missing])
            self.state["total"] = len(rows)
        ids_ok, X = self._vectors_for([(r[0], r[1]) for r in rows])
        if not ids_ok:
            return {"suggested": 0, "files": 0}
        if p["method"] == "trained":
            P = _sigmoid(X @ W.T + B)
        else:
            L = emb.logits(X, tv)
            P = _softmax(L) if (p["mode"] == "single" or not emb.has_bias) else _sigmoid(L)
        now = time.time()
        sugg = []
        for i, fid in enumerate(ids_ok):
            have = labels.get(fid, set())
            if p["mode"] == "single":
                k = int(P[i].argmax())
                cands = [(k, float(P[i, k]))]
            else:
                cands = [(k, float(P[i, k])) for k in range(len(order))]
            for k, score in cands:
                t = order[k]
                if score >= p["threshold"] and t not in have and (fid, t) not in rejected:
                    sugg.append((fid, t, round(score, 4), pid, p["method"], now))
            self.state["done"] = i + 1
        with self.db.write_lock:
            c = self.db.cat
            c.execute("BEGIN")
            c.executemany("INSERT OR REPLACE INTO suggestions(file_id, tag_id, score, profile_id, method, created) VALUES (?,?,?,?,?,?)", sugg)
            c.execute("UPDATE ai_profiles SET last_run = ?, last_result = ? WHERE id = ?",
                      (now, json.dumps({"files": len(ids_ok), "suggested": len(sugg)}), pid))
            c.execute("COMMIT")
        return {"files": len(ids_ok), "suggested": len(sugg)}

    def _run_vlm(self, p, pool, rows, labels, rejected) -> dict:
        s = self.settings()
        model = p.get("vlm_model") or s.get("vlm_model")
        if not model:
            raise AIError("Choose a vision model (e.g. gemma3:12b) for this pool")
        if self._embedder:
            self._embedder.unload()  # leave the GPU to Ollama
        ol = Ollama(s["ollama_url"])
        tags = [(t["name"].split("/")[-1], (t.get("ai_prompt") or "").strip()) for t in pool]
        by_leaf = {t["name"].split("/")[-1]: t["id"] for t in pool}
        n_sugg = n_cap = 0
        for i, (fid, h, path) in enumerate(rows):
            if self._cancel.is_set():
                break
            try:
                im = self._thumb(fid, h, path, 1600)
                r = ol.classify(model, jpeg_for_vlm(im), tags, p["mode"] == "single", bool(p["captions"]))
            except VLMError:
                raise
            except Exception as e:
                self.state["errors"].append(f"{path}: {e}")
                continue
            now = time.time()
            with self.db.write_lock:
                for name in r["tags"]:
                    t = by_leaf.get(name)
                    if t and t not in labels.get(fid, set()) and (fid, t) not in rejected and name != NONE:
                        self.db.cat.execute("INSERT OR REPLACE INTO suggestions VALUES (?,?,?,?,?,?)",
                                            (fid, t, 1.0, p["id"], "vlm", now))
                        n_sugg += 1
                if r["caption"]:
                    self.db.cat.execute("INSERT OR REPLACE INTO captions VALUES (?,?,?,?)", (fid, model, r["caption"], now))
                    n_cap += 1
            self.state["done"] = i + 1
        with self.db.write_lock:
            self.db.cat.execute("UPDATE ai_profiles SET last_run = ?, last_result = ? WHERE id = ?",
                                (time.time(), json.dumps({"files": len(rows), "suggested": n_sugg, "captions": n_cap}), p["id"]))
        return {"files": len(rows), "suggested": n_sugg, "captions": n_cap}

    # -- reviewing suggestions --------------------------------------------------
    def suggestions_by_file(self) -> dict[int, list]:
        out: dict[int, list] = {}
        for fid, tid, score in self.db.cat.execute("SELECT file_id, tag_id, score FROM suggestions"):
            out.setdefault(fid, []).append([tid, round(score or 0, 3)])
        return out

    def _select(self, ids, tag_id, min_score, profile_id):
        where, args = [], []
        if ids is not None:
            if not ids:
                return []
            where.append(f"file_id IN ({','.join('?' * len(ids))})")
            args += ids
        if tag_id is not None:
            where.append("tag_id = ?")
            args.append(tag_id)
        if min_score is not None:
            where.append("score >= ?")
            args.append(min_score)
        if profile_id is not None:
            where.append("profile_id = ?")
            args.append(profile_id)
        sql = "SELECT file_id, tag_id, score FROM suggestions" + (" WHERE " + " AND ".join(where) if where else "")
        return self.db.cat.execute(sql, args).fetchall()

    def accept(self, ids=None, tag_id=None, min_score=None, profile_id=None) -> dict:
        rows = self._select(ids, tag_id, min_score, profile_id)
        by_tag: dict[int, list] = {}
        for fid, tid, score in rows:
            by_tag.setdefault(tid, []).append((fid, score))
        changes = []
        for tid, items in by_tag.items():
            r = tagmod.apply(self.db, [f for f, _ in items], tid, "add", source="ai")
            with self.db.write_lock:
                c = self.db.cat
                c.executemany("UPDATE file_tags SET confidence = ? WHERE file_id = ? AND tag_id = ?",
                              [(s, f, tid) for f, s in items])
                c.executemany("DELETE FROM suggestions WHERE file_id = ? AND tag_id = ?", [(f, tid) for f, _ in items])
            changes.append({"tag_id": tid, "added": r["added"], "also_removed": r["also_removed"]})
        return {"accepted": len(rows), "changes": changes}

    def reject(self, ids=None, tag_id=None, min_score=None, profile_id=None) -> dict:
        rows = self._select(ids, tag_id, min_score, profile_id)
        now = time.time()
        with self.db.write_lock:
            c = self.db.cat
            c.executemany("INSERT OR IGNORE INTO rejections VALUES (?,?,?)", [(f, t, now) for f, t, _ in rows])
            c.executemany("DELETE FROM suggestions WHERE file_id = ? AND tag_id = ?", [(f, t) for f, t, _ in rows])
        return {"rejected": len(rows)}

    def caption(self, file_id: int) -> list[dict]:
        return [{"model": m, "text": t} for m, t in self.db.cat.execute(
            "SELECT model, text FROM captions WHERE file_id = ? ORDER BY created DESC", (file_id,))]

    def ollama_models(self) -> list[str]:
        return Ollama(self.settings()["ollama_url"]).models()

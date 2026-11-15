"""Recording plans: what to record for a project, made here or in Ballistic Foley Lab, taken to a phone.

A plan has a name, a project, quick tags and shots (each with tags, how many takes, an optional UCS
category). It goes to a phone as a link to Ballistic Foley Lab's Record view with the plan in the part
of the address after "#plan=" - browsers never send that part to the web server - or as the QR code of
that link. The encoding is Ballistic's own (packPlan / unpackPlan in its src/engine.js): base64url of
compact JSON with short keys, so a plan of a few dozen shots still fits one QR code.

Takes come back in recording sessions (sessions.py); each one is counted against its shot, so the plan
shows what is still missing. A session from a plan Kurator does not know adds the plan.
"""

from __future__ import annotations

import base64
import json
import re
import time

from .db import Database

DEFAULT_URL = "https://eigensoundlab.github.io/ballistic/"
MAX_PLANS = 200
MAX_SHOTS = 100


class PlanError(ValueError):
    pass


def _str(v, n: int) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _tags(v) -> list[str]:
    if not isinstance(v, list):
        return []
    out = []
    for t in v:
        if isinstance(t, str) and t.strip():
            out.append("/".join(p.strip() for p in t.replace("\\", "/").split("/") if p.strip())[:100])
    return [t for t in dict.fromkeys(out) if t][:20]


def clean(p: dict) -> dict:
    """A plan as stored and linked: checked, with sensible limits (the same as Ballistic's)."""
    if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not isinstance(p.get("shots"), list):
        raise PlanError("A plan needs a name and a list of shots")
    pid = _str(p.get("id"), 40) or "plan-" + format(int(time.time() * 1000), "x")
    if not re.fullmatch(r"[\w.-]+", pid):
        raise PlanError("A plan id may hold letters, digits, - _ and . only")
    shots, ids = [], set()
    for k, s in enumerate(p["shots"][:MAX_SHOTS]):
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip():
            continue
        sid = _str(s.get("id"), 40) or f"s{k + 1}"
        while sid in ids:
            sid += "x"
        ids.add(sid)
        try:
            takes = max(1, min(99, int(s.get("takes") or 1)))
        except (TypeError, ValueError):
            takes = 1
        shots.append({"id": sid, "name": _str(s["name"], 100), "tags": _tags(s.get("tags")), "takes": takes,
                      "ucs": re.sub(r"[^A-Za-z]", "", _str(s.get("ucs"), 40))[:20], "notes": _str(s.get("notes"), 300)})
    return {"id": pid, "name": _str(p["name"], 100) or "Plan", "project": _str(p.get("project"), 100),
            "chips": _tags(p.get("chips")), "shots": shots}


def pack(p: dict) -> str:
    """The plan as Ballistic's #plan= value."""
    o: dict = {"v": 1, "i": p["id"], "n": p["name"]}
    if p.get("project"):
        o["p"] = p["project"]
    if p.get("chips"):
        o["c"] = p["chips"]
    o["s"] = []
    for s in p["shots"]:
        x: dict = {"i": s["id"], "n": s["name"]}
        if s.get("tags"):
            x["t"] = s["tags"]
        if s.get("takes", 1) > 1:
            x["k"] = s["takes"]
        if s.get("ucs"):
            x["u"] = s["ucs"]
        if s.get("notes"):
            x["o"] = s["notes"]
        o["s"].append(x)
    raw = json.dumps(o, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unpack(s: str) -> dict:
    try:
        o = json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise PlanError("Not a plan link") from e
    if not isinstance(o, dict) or o.get("v") != 1:
        raise PlanError("Not a plan link")
    return clean({"id": o.get("i"), "name": o.get("n"), "project": o.get("p", ""), "chips": o.get("c", []),
                  "shots": [{"id": x.get("i"), "name": x.get("n"), "tags": x.get("t", []), "takes": x.get("k", 1),
                             "ucs": x.get("u", ""), "notes": x.get("o", "")} for x in (o.get("s") or []) if isinstance(x, dict)]})


def address(db: Database) -> str:
    return db.get_setting("ballistic_url", None) or DEFAULT_URL


def link(db: Database, p: dict) -> str:
    return address(db).split("#")[0] + "#plan=" + pack(p)


# ---------------------------------------------------------------- storage
def _all(db: Database) -> list[dict]:
    v = db.get_setting("plans", [])
    return v if isinstance(v, list) else []


def _takes(db: Database) -> dict:
    v = db.get_setting("plan_takes", {})
    return v if isinstance(v, dict) else {}


def get(db: Database, pid: str) -> dict | None:
    return next((p for p in _all(db) if p.get("id") == pid), None)


def save(db: Database, p: dict) -> dict:
    p = clean(p)
    plans = [x for x in _all(db) if x.get("id") != p["id"]]
    if len(plans) >= MAX_PLANS:
        raise PlanError(f"Kurator keeps up to {MAX_PLANS} plans: delete one first")
    p["changed"] = time.time()
    plans.append(p)
    db.set_setting("plans", plans)
    return p


def delete(db: Database, pid: str) -> bool:
    plans = _all(db)
    keep = [x for x in plans if x.get("id") != pid]
    db.set_setting("plans", keep)
    t = _takes(db)
    if t.pop(pid, None) is not None:
        db.set_setting("plan_takes", t)
    return len(keep) != len(plans)


def add_takes(db: Database, pid: str, by_shot: dict[str, list[int]]) -> None:
    """Takes that arrived in a session, counted against their shots."""
    t = _takes(db)
    shots = t.setdefault(pid, {})
    for sid, ids in by_shot.items():
        shots[sid] = list(dict.fromkeys([*shots.get(sid, []), *ids]))[-1000:]
    db.set_setting("plan_takes", t)


def progress(db: Database, p: dict) -> dict[str, int]:
    """Takes per shot that are still in the catalog."""
    shots = _takes(db).get(p["id"], {})
    out = {}
    for s in p["shots"]:
        ids = [int(i) for i in shots.get(s["id"], [])][:1000]
        if not ids:
            out[s["id"]] = 0
            continue
        q = ",".join("?" * len(ids))
        out[s["id"]] = db.cat.execute(f"SELECT COUNT(*) FROM files WHERE id IN ({q})", ids).fetchone()[0]
    return out


def listing(db: Database) -> list[dict]:
    out = []
    for p in sorted(_all(db), key=lambda x: -(x.get("changed") or 0)):
        try:
            c = clean(p)
        except PlanError:
            continue
        c["progress"] = progress(db, c)
        c["link"] = link(db, c)
        out.append(c)
    return out

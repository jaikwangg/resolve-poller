"""Replay every real record the server has stored through every strategy and flag
anomalies. No ground truth exists for real data, so this flags shapes that are
suspicious, not errors:

  premature_100    claimed done / 100% and later the same job+stage fell below 100 or its clip
                   count changed (the 2026-10-14 TEST pattern)
  drop             value fell > 1 pt between consecutive sent values
  unexplained_drop drop with no reason code (strategies that give reasons)
  jump             value rose > 25 pts in one poll
  switch           project/timeline changed under the same job+stage between polls

    python -m sim.replay [DIR ...]      (default: <repo>/server/data*, <repo>/server/tests/fixtures/replay)

Reads JSONL read-only. Records without host/ts or without timeline counts become
ok:false observations (they still count as polls).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import pathlib

from . import strategies

REPO = pathlib.Path(__file__).resolve().parents[2]
JUMP = 25.0
FLAGS = ("premature_100", "drop", "unexplained_drop", "jump", "switch")


def default_dirs():
    ds = [pathlib.Path(p) for p in sorted(glob.glob(str(REPO / "server" / "data*")))]
    ds.append(REPO / "server" / "tests" / "fixtures" / "replay")
    return [d for d in ds if d.is_dir()]


def _dt(s):
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else None
    except Exception:
        return None


def load_records(dirs):
    recs, seen = [], set()
    for d in dirs:
        for f in sorted(pathlib.Path(d).glob("*.jsonl")):
            for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if not isinstance(r, dict) or not r.get("host") or _dt(r.get("ts")) is None:
                    continue
                k = (r["host"], r["ts"])
                if k in seen:                 # same record in data/ and fixtures/
                    continue
                seen.add(k)
                recs.append(r)
    recs.sort(key=lambda r: (_dt(r["ts"]), r["host"]))
    return recs


def to_obs(r, t0):
    src = r.get("source") or {}
    tl = src.get("timeline") or {}
    job = r.get("job") or {}
    t = (_dt(r["ts"]) - t0).total_seconds() / 60
    o = {"t": round(t, 2), "ts": r["ts"], "host": r["host"], "stage": r.get("stage") or "color",
         "booking": {"project_no": job.get("job_id") or src.get("project"),
                     "project_name": job.get("title") or job.get("job_id")},
         "external": {"scope": {}, "pms_task": None}}
    if src.get("ok") is False or tl.get("video_clips") is None:
        return {**o, "ok": False, "reason": src.get("reason") or "no_timeline"}
    return {**o, "ok": True, "project": src.get("project"), "timeline": tl.get("name"),
            "timeline_count": src.get("timeline_count"), "video_clips": tl.get("video_clips"),
            "graded_clips": tl.get("graded_clips"), "duration_sec": tl.get("duration_sec"),
            "offline_target_sec": src.get("offline_target_sec"), "markers": tl.get("markers"),
            "shots": src.get("shots") or [], "grade_pending": (tl.get("grade") or {}).get("pending") or 0,
            "recorded_pct": (r.get("progress") or {}).get("pct")}


def flag(obs, outs):
    """-> list of anomalies {flag, key, t, ts, host, from, to, reasons}"""
    found = []
    last = {}            # key -> (pct, obs)
    hundred = {}         # key -> obs where 100/done was first claimed (cleared when it falls)
    for o, out in zip(obs, outs):
        if not out["send"] or out["pct"] is None:
            continue
        k, p = out["key"], out["pct"]
        prev = last.get(k)
        base = {"key": k, "t": o["t"], "ts": o["ts"], "host": o["host"], "reasons": out["reasons"]}
        if prev:
            pp, po = prev
            if p < pp - 1.0:
                found.append({**base, "flag": "drop", "from": pp, "to": p})
                if not set(out["reasons"]) - {"scope_unconfirmed", "no_identity", "awaiting_stability"}:
                    found.append({**base, "flag": "unexplained_drop", "from": pp, "to": p})
            if p > pp + JUMP:
                found.append({**base, "flag": "jump", "from": pp, "to": p})
            if (po.get("project"), po.get("timeline")) != (o.get("project"), o.get("timeline")):
                found.append({**base, "flag": "switch", "from": f"{po.get('project')}/{po.get('timeline')}",
                              "to": f"{o.get('project')}/{o.get('timeline')}"})
        h = hundred.get(k)
        if h is not None and (p < 100.0 or o.get("video_clips") != h.get("video_clips")):
            found.append({**base, "flag": "premature_100", "from": f"100 at {h['ts']}",
                          "to": f"{p} ({o.get('graded_clips')}/{o.get('video_clips')} clips)"})
            hundred.pop(k)
        if out["done"] or p >= 100.0:
            hundred.setdefault(k, o)
        last[k] = (p, o)
    return found


def run(dirs=None):
    dirs = dirs or default_dirs()
    recs = load_records(dirs)
    if not recs:
        return {"dirs": [str(d) for d in dirs], "records": 0, "by_strategy": {}, "anomalies": {}}
    t0 = _dt(recs[0]["ts"])
    obs = [to_obs(r, t0) for r in recs]
    out = {"dirs": [str(d) for d in dirs], "records": len(recs),
           "keys": sorted({strategies.key_of(o) for o in obs if o.get("ok")}), "by_strategy": {}, "anomalies": {}}
    for cls in strategies.ALL:
        outs = strategies.run(cls, obs)
        an = flag(obs, outs)
        out["anomalies"][cls.name] = an
        out["by_strategy"][cls.name] = {f: sum(1 for a in an if a["flag"] == f) for f in FLAGS}
        out["by_strategy"][cls.name]["sent_values"] = sum(1 for x in outs if x["send"])
    return out


def _shown(d):
    """Repo-relative path when inside the repo, so generated docs do not depend on the checkout location."""
    p = pathlib.Path(d).resolve()
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def markdown(res, detail=("S0", "C3")):
    lines = [f"Replay of {res['records']} real records from {', '.join(_shown(d) for d in res['dirs'])}", ""]
    if not res["records"]:
        return "\n".join(lines + ["(no records)"])
    lines += ["| strategy | " + " | ".join(FLAGS) + " | sent |", "|---" * (len(FLAGS) + 2) + "|"]
    for name, c in res["by_strategy"].items():
        lines.append(f"| {name} | " + " | ".join(str(c[f]) for f in FLAGS) + f" | {c['sent_values']} |")
    for name in detail:
        an = [a for a in res["anomalies"].get(name, []) if a["flag"] != "switch"]
        if an:
            lines += ["", f"{name} anomalies (first 12):"]
            for a in an[:12]:
                lines.append(f"- {a['flag']} {a['key']} {a['ts']} {a['host']}: {a['from']} -> {a['to']} {a['reasons']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="replay real editortrack records through every strategy")
    ap.add_argument("dirs", nargs="*")
    a = ap.parse_args(argv)
    print(markdown(run([pathlib.Path(d) for d in a.dirs] or None)))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""progress_v2 - what editortrack sends to PMS for one (project_no, stage): PMS contract v2.

Implements steps 1-2 of the recommended strategy (docs/research/STRATEGY_COMPARISON.md, C3 rollout):

  step 1 (Q1, server only)
    - never 100 / done unless the scope is confirmed and the value has held still for the
      stability window; otherwise provisional and capped (default 99, never above 99.99)
    - hold the previous value (send nothing) while the grade detector warms up
      (timeline.grade.pending > 0), while an offline/reference timeline is open, while the
      Resolve project does not match the booking (job guard), and on records without data
    - a record older than the newest one already seen from its host (offline-queue backlog,
      clock jump) never moves the value (late_record)
    - every drop of more than 1 point carries a reason code
  step 2 (PMS contract v2, ~/github/pms docs/integrations/editortrack.md section 3.1, PMS 9e67f60)
    - each value carries provisional, total_confirmed, done, done_count, total, work_done
      (monotonic) and reasons; seal() refuses anything PMS would answer 422 to

Owner decisions D1-D8 are still open; the study's proposed defaults are used and each is an env
setting (read once by Config.from_env):

  D1 EDITORTRACK_SCOPE_SOURCE   none (default: no confirmed scope -> never done) | lock
                                lock = a human-set shot count per (project_no, stage) in
                                <store>/scope_locks.json (see scope_locks() below). Precedence list,
                                comma separated, first source that has a scope wins.
  D2 EDITORTRACK_PROGRESS_DONE  advisory (default: done may be sent, PMS treats it as advisory) | never
  D3 unit = clips on video tracks as counted by the poller today (titles/generators/adjustment/
     disabled cannot be told apart without per-item data: that is step 3)
  D4 EDITORTRACK_MASS_GUARD     on (default) | off - many clips turning graded in one poll
                                (a look/LUT on everything) blocks done until the scope is re-locked
  D5 EDITORTRACK_JOB_GUARD      withhold (default: Resolve project must contain the booking's
                                project_no or name, else nothing is sent) | off
  D6 EDITORTRACK_PROGRESS_CAP   99 (default; clamped to 0..99.99)
     EDITORTRACK_STABLE_MIN     30 (minutes the confirmed 100% must hold, same counts)
     EDITORTRACK_STABLE_GAP_MIN 10 (a gap between polls longer than this restarts the window)
  D7 multi-room: most recent change wins (only meaningful with per-item data, step 3)
  D8 EDITORTRACK_OFFLINE_MATCH  same variable and default as agent/resolve_poller.py

Stdlib only, no FastAPI: research/sim imports this module to score it on the worst-case library
(strategies SV and SVL), so the code that ships is the code that is tested.
"""
from __future__ import annotations

import datetime
import json
import os
import re

STAGES = ("data", "conform", "color", "subtitle", "master")
DEFAULT_OFFLINE_MATCH = r"offline|editorial|_ref\b|reference|picture\s*lock|ผ่านตัด"
MAX_PCT_PROVISIONAL = 99.99           # PMS: a v2 record at 100 needs a confirmed, non-provisional scope
MASS_MIN = 4                          # mass-grade guard: >= this many clips ...
MASS_FRAC = 0.5                       # ... and >= this share of the clips turning graded in one poll ...
MASS_WINDOW_MIN = 2.0                 # ... at most this many minutes after the previous poll of that timeline
DONE_OPEN = {"TODO", "IN_PROGRESS", "REVISION"}
CLOCK_AHEAD_SEC = 300                 # a record stamped this far after the server received it is not trusted
HOLDS = ("job_mismatch", "detector_pending", "reference_timeline", "late_record", "no_data")
EXPLAINING = {"scope_change", "scope_shrink", "rework", "regression", "replaced", "reopened",
              "scope_mismatch", "job_mismatch"}
REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,39}")
MAX_REASONS = 16
MAX_COUNT = 2 ** 31 - 1


# ---------------------------------------------------------------- config
class Config:
    def __init__(self, scope_source=(), send_done=True, cap=99.0, stable_min=30.0, stable_gap_min=10.0,
                 job_guard=True, mass_guard=True, offline_match=DEFAULT_OFFLINE_MATCH):
        self.scope_source = tuple(s for s in scope_source if s and s != "none")
        self.send_done = bool(send_done)
        self.cap = max(0.0, min(MAX_PCT_PROVISIONAL, float(cap)))
        self.stable_min = max(0.0, float(stable_min))
        self.stable_gap_min = max(0.0, float(stable_gap_min))
        self.job_guard = bool(job_guard)
        self.mass_guard = bool(mass_guard)
        self.offline_match = offline_match or DEFAULT_OFFLINE_MATCH

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env

        def num(name, default):
            try:
                return float(env.get(name, default))
            except (TypeError, ValueError):
                return float(default)          # a typo never widens what is sent: defaults are the strict ones
        src = [s.strip().lower() for s in str(env.get("EDITORTRACK_SCOPE_SOURCE", "none")).split(",")]
        return cls(scope_source=[s for s in src if s in ("lock",)],
                   send_done=str(env.get("EDITORTRACK_PROGRESS_DONE", "advisory")).strip().lower() == "advisory",
                   cap=num("EDITORTRACK_PROGRESS_CAP", 99), stable_min=num("EDITORTRACK_STABLE_MIN", 30),
                   stable_gap_min=num("EDITORTRACK_STABLE_GAP_MIN", 10),
                   job_guard=str(env.get("EDITORTRACK_JOB_GUARD", "withhold")).strip().lower() != "off",
                   mass_guard=str(env.get("EDITORTRACK_MASS_GUARD", "on")).strip().lower() != "off",
                   offline_match=env.get("EDITORTRACK_OFFLINE_MATCH") or DEFAULT_OFFLINE_MATCH)

    def describe(self):
        return {"scope_source": list(self.scope_source) or ["none"], "send_done": self.send_done, "cap": self.cap,
                "stable_min": self.stable_min, "stable_gap_min": self.stable_gap_min, "job_guard": self.job_guard,
                "mass_guard": self.mass_guard, "offline_match": self.offline_match}


# ---------------------------------------------------------------- helpers
def _epoch(ts):
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d.timestamp() if d.tzinfo else None


def _norm(s):
    return re.sub(r"[^0-9a-z]+", "", str(s or "").lower())


def _r2(x):
    return None if x is None else round(max(0.0, min(100.0, float(x))), 2)


def _int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= MAX_COUNT else None


def job_matches(project, project_no, project_name):
    """Resolve project name vs the booking (D5): contains the project_no, or the names contain each other.
    An empty Resolve project name cannot be checked and passes (it carries no counts anyway)."""
    p = _norm(project)
    if not p:
        return True
    pno, pname = _norm(project_no), _norm(project_name)
    return bool((pno and pno in p) or (pname and (pname in p or p in pname)))


def out(key, pct=None, done=False, total=None, done_count=None, confirmed=False, provisional=True,
        reasons=(), work=None, send=True):
    return {"key": key, "pct": _r2(pct), "done": bool(done), "total": total, "done_count": done_count,
            "total_confirmed": bool(confirmed), "provisional": bool(provisional),
            "reasons": sorted(set(reasons)), "work_done": work, "send": bool(send)}


def seal(o, cap=99.0):
    """Last gate before anything leaves: enforce the PMS v2 rules (9e67f60 _check_v2) and the study's
    invariants INV1/INV2/INV5 on the output itself. Returns the output, possibly corrected toward
    *less* certainty, and with send=False when it cannot be made valid. Never makes a claim stronger."""
    o = dict(o)
    cap = max(0.0, min(MAX_PCT_PROVISIONAL, cap))
    confirmed = o["total_confirmed"] and not o["provisional"]
    if o["done"] and not (confirmed and o["pct"] == 100.0 and o["done_count"] is not None
                          and o["done_count"] == o["total"]):
        o["done"] = False
        o["provisional"] = True
    if not o["done"]:
        o["provisional"] = True if not confirmed else o["provisional"]
        if o["pct"] is not None and o["pct"] > cap:
            o["pct"] = round(cap, 2)
    for k in ("done_count", "total", "work_done"):
        if o.get(k) is not None and _int(o[k]) is None:
            o[k] = None
    if o["done_count"] is not None and o["total"] is not None and o["done_count"] > o["total"]:
        o["done_count"] = o["total"]
    rs = [r for r in o["reasons"] if REASON_CODE.fullmatch(r)]
    o["reasons"] = sorted(set(rs))[:MAX_REASONS]
    if o["pct"] is None or not (0.0 <= o["pct"] <= 100.0):
        o["send"] = False
    return o


# ---------------------------------------------------------------- engine
class Engine:
    """Stateful per (project_no, stage). step(obs) -> output dict (see out()).

    obs is the research harness's observation format (research/sim/engine.py), which the server builds
    from an ingested record with obs_from_record():
      t (minutes, monotonic per host clock), ts, host, stage, ok, project, timeline,
      video_clips, graded_clips, duration_sec, offline_target_sec, grade_pending,
      booking {project_no, project_name} (None/no project_no = unbound: computed, never sent),
      external {scope: {stage: {total}}, pms_task}, agent {pct, done, total} (stages other than color/conform)
    """

    def __init__(self, cfg=None, state=None):
        self.cfg = cfg or Config()
        st = state or {}
        self.keys = st.get("keys", {})         # key -> per-key state
        self.host_t = st.get("host_t", {})     # host -> newest record time (epoch s) seen
        self.last = st.get("last", {})         # key -> last output (sent or not)

    def state(self):
        return {"v": 1, "keys": self.keys, "host_t": self.host_t, "last": self.last}

    # ---- holds: previous value kept, nothing sent
    def _hold(self, key, reason, st=None):
        prev = self.last.get(key)
        o = dict(prev) if prev else out(key)
        o["reasons"] = sorted((set(o["reasons"]) - set(HOLDS)) | {reason})
        o["send"] = False
        if st is not None and reason != "late_record":
            st["hi_since"] = None              # unknown state: the stability window starts again
        return o

    def step(self, obs):
        b = obs.get("booking") or {}
        stage = obs.get("stage")
        bound = bool(b.get("project_no"))
        key = f"{b.get('project_no') or ('unbound:' + str(obs.get('project')))}|{stage}"
        st = self.keys.setdefault(key, {})
        host = str(obs.get("host"))
        tsec = _epoch(obs.get("ts"))
        if tsec is not None and host in self.host_t and tsec < self.host_t[host]:
            return self._hold(key, "late_record")
        if tsec is not None:
            self.host_t[host] = max(tsec, self.host_t.get(host, tsec))
        if not obs.get("ok", True):
            return self._hold(key, "no_data", st)
        if self.cfg.job_guard and not job_matches(obs.get("project"), b.get("project_no"), b.get("project_name")):
            return self._hold(key, "job_mismatch", st)
        if obs.get("grade_pending"):
            return self._hold(key, "detector_pending", st)
        if re.search(self.cfg.offline_match, str(obs.get("timeline") or ""), re.I):
            return self._hold(key, "reference_timeline", st)
        o = self._value(key, obs, st, stage)
        if o is None:
            return self._hold(key, "no_data", st)
        o["send"] = bound
        o = seal(o, self.cfg.cap)
        self.last[key] = o
        return o

    def _base(self, obs, stage):
        """-> (pct, done_count, total) from today's counts (same formula as agent/scope.py)."""
        if stage == "color":
            v, g = obs.get("video_clips"), obs.get("graded_clips")
            if not v or g is None:
                return None, None, None
            g = min(g, v)
            return 100.0 * g / v, g, v
        if stage == "conform":
            tgt, d = obs.get("offline_target_sec"), obs.get("duration_sec")
            if tgt and d is not None:
                return 100.0 * min(1.0, d / tgt), None, None
        ag = obs.get("agent") or {}
        if ag.get("pct") is None:
            return None, None, None
        return float(ag["pct"]), _int(ag.get("done")), _int(ag.get("total"))

    def _value(self, key, obs, st, stage):
        pct, dc, total = self._base(obs, stage)
        if pct is None:
            return None
        t = float(obs.get("t") or 0.0)
        reasons = set()
        view = "\x1f".join(str(obs.get(k)) for k in ("host", "project", "timeline"))
        v, g = obs.get("video_clips"), obs.get("graded_clips")

        # work_done: monotonic count of clips that turned graded (approximate without per-item data)
        views = st.setdefault("views", {})
        pv = views.get(view)
        if stage == "color" and g is not None:
            if pv is None:
                if not views:
                    st["work"] = st.get("work", 0) + g      # first timeline of this job: what is graded counts
            elif pv.get("g") is not None and g > pv["g"]:
                st["work"] = st.get("work", 0) + (g - pv["g"])
        work = st.get("work") if stage == "color" else None

        # mass-grade guard (D4): many clips graded at once on one timeline (look/LUT on everything)
        if (self.cfg.mass_guard and stage == "color" and pv is not None and g is not None
                and pv.get("g") is not None and t - pv.get("t", -1e9) <= MASS_WINDOW_MIN):
            jump = g - pv["g"]
            if jump >= max(MASS_MIN, MASS_FRAC * (v or 0)):
                st["mass_at"] = t
                reasons.add("mass_grade")
        views[view] = {"g": g, "v": v, "t": t}

        # confirmed scope (D1)
        confirmed, sc = False, None
        if "lock" in self.cfg.scope_source and stage == "color":
            sc = ((obs.get("external") or {}).get("scope") or {}).get(stage) or None
            total_lock = _int((sc or {}).get("total")) or (len((sc or {}).get("shots") or []) or None)
            if total_lock:
                confirmed = True
                if st.get("lock_total") != total_lock:      # a new or changed lock counts as set now
                    st["lock_total"], st["lock_seen_t"] = total_lock, t
                lock_t = (sc or {}).get("locked_t", st["lock_seen_t"])
                if st.get("mass_at") is not None and (lock_t is None or lock_t <= st["mass_at"]):
                    reasons.add("mass_grade")            # not cleared until the scope is locked again
                if v is not None and v > total_lock:
                    # more clips than the lock: the lock no longer describes the timeline. Report the
                    # counts as seen, unconfirmed, and say why (PMS shows "scope grew")
                    confirmed = False
                    reasons |= {"scope_grew_after_lock", "scope_change"}
                else:
                    total = total_lock
                    dc = min(g or 0, total)
                    if v is not None and v < total:
                        reasons.add("scope_mismatch")     # clips missing vs the lock
                    pct = 100.0 * dc / total
        if not confirmed:
            reasons.add("scope_unconfirmed")

        # reason for a drop (every drop > 1 pt is labelled)
        last = st.get("last")
        if last and last.get("pct") is not None and pct < last["pct"] - 1.0:
            if v is not None and last.get("v") is not None and v > last["v"]:
                reasons.add("scope_change")
            elif g is not None and last.get("g") is not None and g < last["g"]:
                reasons.add("regression")
            elif view != last.get("view"):
                reasons.add("scope_change")               # another timeline/project of the same job
            elif stage == "conform":
                if (obs.get("offline_target_sec") or 0) > (last.get("tgt") or 0):
                    reasons.add("scope_change")
                else:
                    reasons.add("scope_shrink")
            elif total is not None and last.get("total") is not None and total > last["total"]:
                reasons.add("scope_change")
            elif dc is not None and last.get("dc") is not None and dc < last["dc"]:
                reasons.add("regression")
            if not reasons & EXPLAINING:
                reasons.add("unexplained_drop")
        st["last"] = {"pct": pct, "v": v, "g": g, "view": view, "tgt": obs.get("offline_target_sec"),
                      "total": total, "dc": dc}

        # done (D2, D6): confirmed, complete, no blocker, stable for the window, PMS task not reopened
        task = (obs.get("external") or {}).get("pms_task")
        blockers = reasons & {"scope_mismatch", "mass_grade", "scope_grew_after_lock"}
        complete = confirmed and dc is not None and total and dc >= total and not blockers
        sig = [total, dc, v, view]
        prev_t = st.get("seen_t")
        if complete:
            gap = prev_t is not None and t - prev_t > self.cfg.stable_gap_min
            if st.get("hi_since") is None or st.get("hi_sig") != sig or gap:
                st["hi_since"], st["hi_sig"] = t, sig
        else:
            st["hi_since"] = None
        st["seen_t"] = t
        if task in DONE_OPEN and st.get("was_done"):
            reasons.add("reopened")
            st["hi_since"] = None
        if task == "REVISION":
            reasons.add("rework")
        stable = st.get("hi_since") is not None and t - st["hi_since"] >= self.cfg.stable_min
        done = bool(complete and stable and self.cfg.send_done and st.get("hi_since") is not None)
        if complete and not done:
            reasons.add("awaiting_stability")
        provisional = not (confirmed and not blockers)
        if done:
            pct = 100.0
        st["was_done"] = done or bool(st.get("was_done") and task not in DONE_OPEN)
        return out(key, pct, done=done, total=total, done_count=dc, confirmed=confirmed,
                   provisional=provisional, reasons=reasons, work=work)


# ---------------------------------------------------------------- server glue (records -> obs -> payload)
def obs_from_record(rec, eff, scope=None):
    """One ingested record (schema 3) + the effective job (dashboard_api._eff_job) -> observation.
    Only an ERP booking (eff source "erp") binds the value to a PMS project_no; anything else is
    computed for state but never sent (PMS answers 422 to Resolve project names)."""
    src = rec.get("source") or {}
    tl = src.get("timeline") or {}
    prog = rec.get("progress") or {}
    tsec = _epoch(rec.get("ts"))
    stage = eff.get("stage") or rec.get("stage")
    bound = eff.get("source") == "erp" and eff.get("job_id")
    o = {"t": (tsec or 0.0) / 60.0, "ts": rec.get("ts"), "host": rec.get("host"), "stage": stage,
         "booking": {"project_no": eff.get("job_id") if bound else None,
                     "project_name": eff.get("title") if bound else None},
         "external": {"scope": {stage: scope} if scope else {}, "pms_task": None}}
    rx = _epoch((rec.get("_rx") or {}).get("at"))
    if tsec is not None and rx is not None and tsec > rx + CLOCK_AHEAD_SEC:
        # host clock ahead of the server: its time would make every later record look late (and could
        # stretch a stability window), so it neither moves the value nor the host's newest-time mark
        return {**o, "ts": None, "ok": False}
    if src.get("ok") is False or not prog:
        return {**o, "ok": False}
    if stage in ("color", "conform") and tl.get("video_clips") is None and prog.get("pct") is None:
        return {**o, "ok": False}
    return {**o, "ok": True, "project": src.get("project"), "timeline": tl.get("name"),
            "video_clips": tl.get("video_clips"), "graded_clips": tl.get("graded_clips"),
            "duration_sec": tl.get("duration_sec"), "offline_target_sec": src.get("offline_target_sec"),
            "grade_pending": (tl.get("grade") or {}).get("pending") or 0,
            "agent": {"pct": prog.get("pct"), "done": prog.get("done"), "total": prog.get("total")}}


def payload(o, project_no, stage, observed_at, host):
    """output -> the POST body of /api/integrations/editortrack/progress (contract v2, PMS 9e67f60).
    Field names and types exactly as kpp-pms-api ProgressRecord; optional counts left out when unknown."""
    p = {"project_no": project_no, "stage": stage, "progress_pct": o["pct"], "observed_at": observed_at,
         "host": host, "provisional": bool(o["provisional"]), "total_confirmed": bool(o["total_confirmed"]),
         "done": bool(o["done"]), "reasons": list(o["reasons"])}
    for k in ("done_count", "total", "work_done"):
        if o.get(k) is not None:
            p[k] = int(o[k])
    return p


def pms_check(p):
    """Python port of PMS 9e67f60 services/integrations/workstation.py _check_v2 + the pct rules:
    returns the 422 reason PMS would give, or None. Used as a fail-closed gate before sending and in tests."""
    pct = p.get("progress_pct")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not (0 <= pct <= 100) or round(pct, 2) != pct:
        return "progress_pct must be 0-100 with at most two decimals"
    if p.get("stage") not in STAGES or not p.get("project_no"):
        return "project_no/stage"
    for name in ("done_count", "total", "work_done"):
        v = p.get(name)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not (0 <= v <= MAX_COUNT)):
            return f"{name} out of range"
    for name in ("provisional", "total_confirmed", "done"):
        if p.get(name) is not None and not isinstance(p[name], bool):
            return f"{name} must be bool"
    rs = p.get("reasons")
    if rs is not None:
        if not isinstance(rs, list) or len(rs) > MAX_REASONS or not all(isinstance(c, str) and REASON_CODE.fullmatch(c) for c in rs):
            return "reasons"
    confirmed = p.get("total_confirmed") is True and p.get("provisional") is not True
    if p.get("done") is True and not confirmed:
        return "done needs total_confirmed=true and provisional not true"
    if p.get("done") is True and pct != 100:
        return "done needs progress_pct 100"
    if p.get("done") is True and p.get("done_count") is not None and p.get("total") is not None \
            and p["done_count"] != p["total"]:
        return "done needs done_count equal to total"
    v2 = any(p.get(f) is not None for f in ("provisional", "total_confirmed", "done", "done_count", "total",
                                             "work_done", "reasons"))
    if v2 and pct >= 100 and not confirmed:
        return "100 needs total_confirmed=true and provisional not true"
    # editortrack's own rule (zero-incident): never 100 without done, never done without the counts
    if pct >= 100 and p.get("done") is not True:
        return "editortrack: 100 only with done"
    return None


def load_scope_locks(path):
    """<store>/scope_locks.json: {"<project_no>|<stage>": {"total": int, "locked_at": iso, "by": text}}.
    A human sets a lock to confirm the scope (D1 = lock). Unreadable or malformed -> no locks (fail closed:
    without a lock nothing is ever done)."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    locks = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict) and _int(v.get("total")) and _epoch(v.get("locked_at")) is not None:
                locks[str(k)] = {"total": v["total"], "locked_t": _epoch(v["locked_at"]) / 60.0}
    return locks


def lock_for(locks, key, ts):
    """the lock of a key, if it was set at or before the record's time (replays stay deterministic)."""
    lk = locks.get(key)
    t = _epoch(ts)
    if not lk or t is None or lk["locked_t"] > t / 60.0:
        return None
    return lk


if __name__ == "__main__":
    import sys
    # set or clear a scope lock:  python3 progress_v2.py lock <store> <project_no> <stage> <total>|clear
    if len(sys.argv) >= 6 and sys.argv[1] == "lock":
        _, _, store, pno, stg, total = sys.argv[:6]
        path = os.path.join(store, "scope_locks.json")
        try:
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
        except (OSError, ValueError):
            cur = {}
        k = f"{pno}|{stg}"
        if total == "clear":
            cur.pop(k, None)
        else:
            cur[k] = {"total": int(total), "locked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      "by": os.environ.get("USER", "")}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        print(json.dumps(cur.get(k) or {"cleared": k}, ensure_ascii=False))
    else:
        print(json.dumps(Config.from_env().describe(), ensure_ascii=False, indent=1))

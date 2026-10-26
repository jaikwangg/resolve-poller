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
  D3 unit = with per-item data (step 3): shots, excluding titles, generators, adjustment layers,
     audio and disabled clips (NON_GRADABLE); without it: clips on video tracks as the poller counts them
  step 3 (identity ledger, class Ledger): when a record carries source.items (unique id, clip name,
     kind, track, start/end, graded, grade signature, media version, online) the value comes from one
     shot list per job and stage across rooms, timelines and projects. TODAY'S POLLER DOES NOT SEND
     ITEMS: the agent change (core getters only, behind a flag) needs owner sign-off for real rooms
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
import math
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
                 job_guard=True, mass_guard=True, offline_match=DEFAULT_OFFLINE_MATCH, lock_counts=False):
        def finite(x, default):
            try:
                x = float(x)
            except (TypeError, ValueError):
                return default
            return x if math.isfinite(x) else default
        self.scope_source = tuple(s for s in scope_source if s and s != "none")
        self.send_done = bool(send_done)
        self.cap = max(0.0, min(MAX_PCT_PROVISIONAL, finite(cap, 99.0)))
        self.stable_min = max(0.0, finite(stable_min, 30.0))
        self.stable_gap_min = max(0.0, finite(stable_gap_min, 10.0))
        # D1 = lock on today's counts (no per-item data) cannot see Replace Clip, another reel with the same
        # clip count, or a look applied in steps: done from counts needs this second, explicit opt-in
        self.lock_counts = bool(lock_counts)
        self.job_guard = bool(job_guard)
        self.mass_guard = bool(mass_guard)
        self.offline_match = offline_match or DEFAULT_OFFLINE_MATCH

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env

        def num(name, default, stricter):
            """env can only make a setting stricter than the study default; a typo, nan/inf or a looser
            value falls back to the default (lowering the window or raising the gap needs a code change)"""
            try:
                x = float(env.get(name, default))
            except (TypeError, ValueError):
                return float(default)
            return x if math.isfinite(x) and stricter(x) else float(default)
        src = [s.strip().lower() for s in str(env.get("EDITORTRACK_SCOPE_SOURCE", "none")).split(",")]
        return cls(scope_source=[s for s in src if s in ("lock",)],
                   send_done=str(env.get("EDITORTRACK_PROGRESS_DONE", "advisory")).strip().lower() == "advisory",
                   cap=num("EDITORTRACK_PROGRESS_CAP", 99, lambda x: 0 <= x <= MAX_PCT_PROVISIONAL),
                   stable_min=num("EDITORTRACK_STABLE_MIN", 30, lambda x: x >= 30),
                   stable_gap_min=num("EDITORTRACK_STABLE_GAP_MIN", 10, lambda x: 0 < x <= 10),
                   lock_counts=str(env.get("EDITORTRACK_LOCK_COUNTS", "0")).strip() == "1",
                   job_guard=str(env.get("EDITORTRACK_JOB_GUARD", "withhold")).strip().lower() != "off",
                   mass_guard=str(env.get("EDITORTRACK_MASS_GUARD", "on")).strip().lower() != "off",
                   offline_match=env.get("EDITORTRACK_OFFLINE_MATCH") or DEFAULT_OFFLINE_MATCH)

    def describe(self):
        return {"scope_source": list(self.scope_source) or ["none"], "send_done": self.send_done, "cap": self.cap,
                "stable_min": self.stable_min, "stable_gap_min": self.stable_gap_min, "job_guard": self.job_guard,
                "mass_guard": self.mass_guard, "offline_match": self.offline_match, "lock_counts": self.lock_counts}


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


NAME_MIN = 6                          # a name match needs at least this many letters/digits (no "test" in everything)


def _bounded(needle, hay):
    """needle's alphanumeric runs appear in hay in order, separators optional, and not inside a longer
    number or word: KPP-1 is not in KPP-10_x, KPP-2026-0005 is in KPP20260005BrandX."""
    runs = re.findall(r"[0-9a-z]+", str(needle or "").lower())
    if not runs:
        return False
    pat = r"[^0-9a-z]*".join(re.escape(r) for r in runs)
    first, last = runs[0][0], runs[-1][-1]
    before = r"(?<![0-9])" if first.isdigit() else r"(?<![a-z])"
    after = r"(?![0-9])" if last.isdigit() else r"(?![a-z])"
    return re.search(before + pat + after, str(hay or "").lower()) is not None


def job_matches(project, project_no, project_name):
    """Resolve project name vs the booking (D5): contains the project_no (bounded), or contains the booking
    name / is contained in it (bounded, at least NAME_MIN letters/digits). An empty Resolve project name
    cannot be checked and passes (it carries no counts anyway)."""
    p = _norm(project)
    if not p:
        return True
    if project_no and _bounded(project_no, project):
        return True
    pname = _norm(project_name)
    if len(pname) >= NAME_MIN and _bounded(project_name, project):
        return True
    return bool(len(p) >= NAME_MIN and project_name and _bounded(project, project_name))


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


# ---------------------------------------------------------------- step 3: per-clip identity ledger
NON_GRADABLE = {"title", "generator", "adjustment", "audio", "disabled"}   # D3: not units of color work


def _norm_code(s):
    """clip name -> shot code: extension and a trailing _vNNN version stripped, punctuation dropped."""
    s = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", str(s or ""))
    s = re.sub(r"[_\-. ]?v\d+$", "", s, flags=re.I)
    return re.sub(r"[^0-9A-Za-z]+", "", s).upper()


def _unit_done(item, stage):
    if stage == "conform":
        return bool(item.get("online")) and item.get("kind") != "placeholder"
    return bool(item.get("graded"))


def identities(items):
    """items -> {ident: [items]} for gradable items. ident = normalised clip name (sid) when the poller
    sends one, else the Resolve unique id. Same-name items that touch on one track (a blade) share one
    ident; a non-adjacent reuse of one source gets #2, #3."""
    groups, by_name = {}, {}
    its = [i for i in items or [] if isinstance(i, dict) and i.get("kind") not in NON_GRADABLE]
    for i in sorted(its, key=lambda x: (x.get("track", 1), x.get("start", 0))):
        base = _norm_code(i["sid"]) if i.get("sid") else "UID:" + str(i.get("id"))
        by_name.setdefault(base, []).append(i)
    for base, lst in by_name.items():
        n, prev = 1, None
        for i in lst:
            if prev is not None and not (prev.get("track") == i.get("track")
                                         and abs((prev.get("end") or 0) - (i.get("start") or 0)) < 1e-6):
                n += 1
            groups.setdefault(base if n == 1 else f"{base}#{n}", []).append(i)
            prev = i
    return groups


class Ledger:
    """One shot list per (job, stage) across hosts, projects and timelines (C3 rollout step 3).

    Each shot keeps one state per view (host, project, timeline) it appears in. A shot counts as done
    when, in the most recently *changed* view holding its newest media version, it is done (graded /
    online), its grade was not merely carried onto new media (Replace Clip), and it was not part of a
    mass apply (D4). A view seen for the first time with nothing changed never overrides work done
    elsewhere (D7: most recent change wins). State is plain JSON (persisted with the engine)."""

    def __init__(self, st, mass_guard=True):
        self.st, self.mass_guard = st, mass_guard
        st.setdefault("views", {})      # view -> [idents]
        st.setdefault("view_t", {})     # view -> t of its last poll
        st.setdefault("shots", {})      # ident -> {view: {done, ver, sig, replaced, mass, changed_t, seen_ver}}
        st.setdefault("work", {})       # "ident|sig-or-version" -> 1 : monotonic work counter

    @staticmethod
    def _ok(vs):
        return vs["done"] and not vs["replaced"] and not vs["mass"]

    def shot_done(self, ident):
        views = self.st["shots"].get(ident) or {}
        if not views:
            return False
        top = max(v["ver"] for v in views.values())
        latest = max((v for v in views.values() if v["ver"] == top),
                     key=lambda v: float("-inf") if v["changed_t"] is None else v["changed_t"])
        return self._ok(latest)

    def _work(self, ident, what):
        self.st["work"][f"{ident}|{what}"] = 1

    def update(self, obs, t):
        stage = obs.get("stage")
        S = self.st
        view = "\x1f".join(str(obs.get(k)) for k in ("host", "project", "timeline"))
        groups = identities(obs.get("items"))
        reasons = set()
        prev = S["views"].get(view)
        cur = set(groups)
        was_done = {i: self.shot_done(i) for i in S["shots"]}
        before = set(S["shots"])
        if prev is not None:
            for gid in set(prev) - cur:
                vs = S["shots"].get(gid, {})
                vs.pop(view, None)
                if not vs:
                    S["shots"].pop(gid, None)
                    reasons.add("scope_shrink")
        newly, fresh = [], []
        for ident, its in groups.items():
            done = all(_unit_done(i, stage) for i in its)
            ver = max(int(i.get("version") or 0) for i in its)
            sig = sorted({str(i.get("sig")) for i in its})           # blade halves share one grade
            views = S["shots"].setdefault(ident, {})
            s = views.get(view)
            if s is None:
                top = max([v["ver"] for v in views.values()] or [ver])
                carried = any(v["sig"] == sig and v["ver"] < ver for v in views.values())
                views[view] = {"done": done, "ver": ver, "sig": sig,
                               "replaced": bool(stage == "color" and done and ver > top and carried), "mass": False,
                               "changed_t": None if len(views) else t, "seen_ver": ver}
                if done and prev is not None:
                    newly.append(ident)
                elif done and len(views) == 1:
                    fresh.append(ident)          # never seen anywhere and already graded at first sight
                if done:
                    self._work(ident, "|".join(sig) if stage == "color" else ver)
                continue
            if ver != s["ver"]:
                reasons.add("replaced")
                if stage == "color" and done and sig == s["sig"]:
                    s["replaced"] = True                             # grade carried onto new media
                s["ver"] = ver
                if stage == "conform" and done:
                    self._work(ident, ver)
            if stage == "color" and done and sig != s["sig"]:
                if s["done"] and not s["mass"]:
                    reasons.add("rework")
                s["replaced"] = False
                if s["done"]:
                    s["mass"] = False                                # touched again after a mass apply
                self._work(ident, "|".join(sig))
            if done and not s["done"]:
                self._work(ident, "|".join(sig) if stage == "color" else ver)
                newly.append(ident)
            if not done:
                s["mass"] = False
            if (done, sig) != (s["done"], s["sig"]) or ver != s.get("seen_ver", ver):
                s["changed_t"] = t
            s["seen_ver"] = ver
            s["done"], s["sig"] = done, sig
        dt = t - S["view_t"].get(view, -1e18)
        S["view_t"][view] = t
        if (self.mass_guard and stage == "color" and dt <= MASS_WINDOW_MIN
                and len(newly) >= max(MASS_MIN, MASS_FRAC * len(S["shots"]))):
            for ident in newly:
                S["shots"][ident][view]["mass"] = True
            reasons.add("mass_grade")
        if (self.mass_guard and stage == "color" and fresh
                and len(fresh) >= max(MASS_MIN, MASS_FRAC * len(S["shots"]))):
            # a timeline first seen with most shots already "graded" (template node tree, look on everything,
            # agent installed mid-job): not counted until each shot is touched again
            for ident in fresh:
                S["shots"][ident][view]["mass"] = True
            reasons.add("mass_grade")
        if before and set(S["shots"]) - before:
            reasons.add("scope_change")
        if any(was_done.get(i) and not self.shot_done(i) for i in S["shots"]):
            reasons.add("regression")
        S["views"][view] = sorted(cur)
        total = len(S["shots"])
        dc = sum(1 for i in S["shots"] if self.shot_done(i))
        return total, dc, reasons

    def bases(self):
        return {i.split("#")[0] for i in self.st["shots"]}

    def work_count(self):
        return len(self.st["work"])

    def done_of(self, codes):
        idx = {}
        for ident in self.st["shots"]:
            idx.setdefault(ident.split("#")[0], []).append(ident)
        return sum(1 for c in codes if idx.get(_norm_code(c)) and all(self.shot_done(i) for i in idx[_norm_code(c)]))

    def unknown_vs(self, codes):
        want = {_norm_code(c) for c in codes}
        return {i for i in self.st["shots"] if i.split("#")[0] not in want}


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
        st = state if valid_state(state) else {}
        self.keys = st.get("keys", {})         # key -> per-key state
        self.host_t = st.get("host_t", {})     # host -> newest record time (epoch s) seen
        self.last = st.get("last", {})         # key -> last output (sent or not)
        self.hints = st.get("hints", {})       # job -> [shot codes] from offline timelines / the conform ledger

    def state(self):
        return {"v": 1, "keys": self.keys, "host_t": self.host_t, "last": self.last, "hints": self.hints}

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
        st = self.keys.setdefault(key, {}) if bound else {}      # unbound keys are never sent: keep no state
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
        if bound:
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
        use_items = isinstance(obs.get("items"), list) and stage in ("color", "conform")
        if pct is None and not use_items:
            return None
        t = float(obs.get("t") or 0.0)
        reasons = set()
        view = "\x1f".join(str(obs.get(k)) for k in ("host", "project", "timeline"))
        v, g = obs.get("video_clips"), obs.get("graded_clips")
        job = key.rsplit("|", 1)[0]
        book = None
        if use_items:
            # step 3: one shot list per job and stage across rooms, timelines and projects
            book = Ledger(st.setdefault("book", {}), self.cfg.mass_guard)
            total, dc, r = book.update(obs, t)
            reasons |= r
            work = book.work_count()
            hint = set(self.hints.get(job) or [])
            hint |= {_norm_code(x) for x in obs.get("offline_shots") or []}
            if stage == "conform":
                hint |= book.bases()
            self.hints[job] = sorted(hint)
            extra = hint - book.bases() if stage == "color" or obs.get("offline_shots") else set()
            if extra:
                total += len(extra)                          # shots known elsewhere for this job, not here yet
                reasons.add("scope_hint")
            pct = 100.0 * dc / total if total else None
            if pct is None:
                return None
            cmp_v, cmp_g = total, dc
        else:
            # counts only (today's poller): work_done = clips that turned graded (approximate)
            views = st.setdefault("views", {})
            pv = views.get(view)
            if stage == "color" and g is not None:
                if pv is None:
                    if not views:
                        st["work"] = st.get("work", 0) + g   # first timeline of this job: what is graded counts
                elif pv.get("g") is not None and g > pv["g"]:
                    st["work"] = st.get("work", 0) + (g - pv["g"])
            work = st.get("work") if stage == "color" else None
            # mass-grade guard (D4) from counts: many clips turning graded between two successful polls of
            # one timeline (no time limit: scans abort while the user works), or a timeline first seen with
            # most clips already graded
            if self.cfg.mass_guard and stage == "color" and g is not None:
                base_g = pv.get("g") if pv is not None else 0
                if base_g is not None and g - base_g >= max(MASS_MIN, MASS_FRAC * (v or 0)):
                    st["mass_at"] = t
                    if "lock" in self.cfg.scope_source:
                        reasons.add("mass_grade")    # only a label that matters with a lock: not shown otherwise
            views[view] = {"g": g, "v": v, "t": t}
            cmp_v, cmp_g = v, g

        # confirmed scope (D1)
        confirmed = False
        if "lock" in self.cfg.scope_source:
            sc = ((obs.get("external") or {}).get("scope") or {}).get(stage) or None
            shots = (sc or {}).get("shots") or None
            total_lock = _int((sc or {}).get("total")) or (len(shots) if shots else None)
            if total_lock and (book is not None or stage == "color"):
                if st.get("lock_total") != total_lock:      # a new or changed lock counts as set now
                    st["lock_total"], st["lock_seen_t"] = total_lock, t
                lock_t = (sc or {}).get("locked_t", st["lock_seen_t"])
                if book is not None and shots:
                    confirmed = True                         # a shot list: matched shot by shot
                    total, dc = len(shots), book.done_of(shots)
                    if book.unknown_vs(shots):
                        reasons.add("scope_mismatch")
                    pct = 100.0 * dc / total
                elif cmp_v is not None and cmp_v > total_lock:
                    # more units than the lock: the lock no longer describes the work. Report what is
                    # seen, unconfirmed, and say why (PMS shows "scope grew")
                    reasons |= {"scope_grew_after_lock", "scope_change"}
                else:
                    confirmed = True
                    if book is None and st.get("mass_at") is not None and (lock_t is None or lock_t <= st["mass_at"]):
                        reasons.add("mass_grade")            # counts: not cleared until the scope is locked again
                    if book is None and not self.cfg.lock_counts:
                        reasons.add("awaiting_identity")     # counts alone may not end in done (Config.lock_counts)
                    if cmp_v is not None and cmp_v < total_lock:
                        reasons.add("scope_mismatch")         # units missing vs the lock
                    total = total_lock
                    dc = min(cmp_g or 0, total)
                    pct = 100.0 * dc / total
        if not confirmed:
            reasons.add("scope_unconfirmed")

        # reason for a drop (every drop > 1 pt is labelled)
        last = st.get("last")
        if last and last.get("pct") is not None and pct < last["pct"] - 1.0:
            if view != last.get("view") and book is None:
                # another room/timeline/project of the same job: its counts are not comparable with the last
                # one (fuzzer find, now WC44), so the drop is a change of view, never a regression
                reasons.add("scope_change")
            elif cmp_v is not None and last.get("v") is not None and cmp_v > last["v"]:
                reasons.add("scope_change")
            elif cmp_g is not None and last.get("g") is not None and cmp_g < last["g"]:
                reasons.add("regression")
            elif stage == "conform" and book is None:
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
        st["last"] = {"pct": pct, "v": cmp_v, "g": cmp_g, "view": view, "tgt": obs.get("offline_target_sec"),
                      "total": total, "dc": dc}

        # done (D2, D6): confirmed, complete, no blocker, stable for the window, PMS task not reopened
        task = (obs.get("external") or {}).get("pms_task")
        blockers = reasons & {"scope_mismatch", "mass_grade", "scope_grew_after_lock", "awaiting_identity"}
        complete = bool(confirmed and dc is not None and total and dc >= total and not blockers)
        sig = [total, dc] if book is not None else [total, dc, v, view]
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
        done = bool(complete and stable and self.cfg.send_done)
        if complete and not done:
            reasons.add("awaiting_stability")
        provisional = not (confirmed and not blockers)
        if done:
            pct = 100.0
        st["was_done"] = done or bool(st.get("was_done") and task not in DONE_OPEN)
        return out(key, pct, done=done, total=total, done_count=dc, confirmed=confirmed,
                   provisional=provisional, reasons=reasons, work=work)


def valid_state(st):
    """shape check of a persisted state (a hand-edited or truncated file must not break every ingest)"""
    return (isinstance(st, dict) and st.get("v") == 1
            and all(isinstance(st.get(k, {}), dict) for k in ("keys", "host_t", "last", "hints"))
            and all(isinstance(v, dict) for v in st.get("keys", {}).values())
            and all(isinstance(v, (int, float)) for v in st.get("host_t", {}).values())
            and all(isinstance(v, dict) for v in st.get("last", {}).values()))


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
            "agent": {"pct": prog.get("pct"), "done": prog.get("done"), "total": prog.get("total")},
            **({"items": src["items"]} if isinstance(src.get("items"), list) else {}),
            **({"offline_shots": src["offline_shots"]} if isinstance(src.get("offline_shots"), list) else {})}


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
    if p.get("stage") not in STAGES or not isinstance(p.get("project_no"), str) or not p["project_no"]:
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

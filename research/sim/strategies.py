"""Candidate progress strategies. Each turns the observation stream into what
editortrack would send to PMS for one (job, stage).

Every strategy is a class with a fresh state per run; `step(obs)` returns

    {"key": "<project_no>|<stage>",
     "pct": float | None,          # progress_pct sent to PMS (None = nothing to send)
     "done": bool,                 # the stage is claimed complete
     "total": int | None,          # denominator (units of scope)
     "done_count": int | None,     # numerator
     "total_confirmed": bool,      # denominator comes from a confirmed scope source
     "provisional": bool,          # value may still move either way; PMS must not treat it as done
     "reasons": [str],             # scope_change, scope_shrink, rework, regression, replaced,
                                   # scope_mismatch, job_mismatch, reopened, scope_unconfirmed,
                                   # awaiting_stability, debounced_drop, no_data, no_identity
     "work_done": int | None,      # monotonic count of grade/conform events (S5 and combos)
     "send": bool}                 # PMS push would happen (booking-bound and data present)

Today PMS receives only progress_pct; `done` for strategies that do not set it
is read as pct >= 100, which is how a person reading the PMS project page reads it.

Data each strategy reads is listed in NEEDS. Fields marked (ext) are not sent by
today's poller (agent/resolve_poller.py) and need a poller change.
"""
from __future__ import annotations

import re

from .engine import OFFLINE_MATCH

STABLE_MIN = 30          # minutes the 100% state must hold, same scope, before done
DEBOUNCE_POLLS = 3       # S4: polls a drop must persist before it is reported
PROVISIONAL_CAP = 99.0   # provisional values never reach 100
NON_GRADABLE = {"title", "generator", "adjustment", "audio", "disabled"}
DONE_OPEN = {"TODO", "IN_PROGRESS", "REVISION"}
MASS_MIN = 4             # C3 mass-grade guard: >= this many shots ...
MASS_FRAC = 0.5          # ... and >= this share of the scope turning graded in one poll ...
MASS_WINDOW = 2          # ... that came <= this many minutes after the previous poll of the same timeline


def _r(x):
    return None if x is None else round(max(0.0, min(100.0, x)), 2)


def _norm_code(s):
    s = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", str(s or ""))
    s = re.sub(r"[_\-. ]?v\d+$", "", s, flags=re.I)
    return re.sub(r"[^0-9A-Za-z]+", "", s).upper()


def _norm_proj(s):
    return re.sub(r"[^0-9a-z]+", "", str(s or "").lower())


def key_of(obs):
    b = obs.get("booking") or {}
    job = b.get("project_no") or ("unbound:" + str(obs.get("project")))
    return f"{job}|{obs.get('stage')}"


def _out(key, pct=None, done=None, total=None, done_count=None, confirmed=False, provisional=False,
         reasons=(), work=None, send=True):
    pct = _r(pct)
    if done is None:
        done = pct is not None and pct >= 100.0
    return {"key": key, "pct": pct, "done": bool(done), "total": total, "done_count": done_count,
            "total_confirmed": bool(confirmed), "provisional": bool(provisional),
            "reasons": sorted(set(reasons)), "work_done": work, "send": bool(send)}


class Strategy:
    name = "?"
    label = "?"
    needs = ()
    cost = "?"
    human = "none"

    ignore_late = True       # a record older than the newest one already seen from its host
                             # (offline-queue backlog, clock jump) does not move the current value

    def __init__(self):
        self.st = {}
        self.last = {}
        self.host_ts = {}

    def step(self, obs):
        key = key_of(obs)
        bound = bool((obs.get("booking") or {}).get("project_no"))
        h, ts = obs.get("host"), obs.get("ts") or ""
        if self.ignore_late and h in self.host_ts and ts < self.host_ts[h]:
            prev = self.last.get(key)
            o = dict(prev) if prev else _out(key)
            o["reasons"] = sorted(set(o["reasons"]) | {"late_record"})
            o["send"] = False
            return o
        self.host_ts[h] = max(ts, self.host_ts.get(h, ""))
        if not obs.get("ok", True) or (obs.get("stage") == "color" and obs.get("video_clips") is None):
            prev = self.last.get(key)
            o = dict(prev) if prev else _out(key)
            o["reasons"] = sorted(set(o["reasons"]) | {"no_data"})
            o["send"] = False
            return o
        o = self._step(key, obs, self.st.setdefault(key, {}))
        o["send"] = o["send"] and bound and o["pct"] is not None
        self.last[key] = o
        return o

    def _step(self, key, obs, st):
        raise NotImplementedError


# ---------------- helpers shared by strategies ----------------
def s0_pct(obs):
    if obs.get("stage") == "conform":
        tgt = obs.get("offline_target_sec")
        d = obs.get("duration_sec")
        return None if not tgt or d is None else 100.0 * min(1.0, d / tgt)
    v, g = obs.get("video_clips"), obs.get("graded_clips")
    return None if not v or g is None else 100.0 * g / v


def unit_done(item, stage):
    if stage == "conform":
        return bool(item.get("online")) and item.get("kind") != "placeholder"
    return bool(item.get("graded"))


def identities(obs):
    """items -> {ident: [items]} for gradable items. ident = normalized clip name (sid) when the
    poller can read one, else the Resolve unique id. Same-name items that are timeline-adjacent
    (a blade) share one ident; non-adjacent reuse of one source gets #2, #3."""
    groups = {}
    items = [i for i in obs.get("items") or [] if i.get("kind") not in NON_GRADABLE]
    by_name = {}
    for i in sorted(items, key=lambda x: (x.get("track", 1), x.get("start", 0))):
        base = _norm_code(i["sid"]) if i.get("sid") else "UID:" + str(i["id"])
        by_name.setdefault(base, []).append(i)
    for base, its in by_name.items():
        n, prev = 1, None
        for i in its:
            if prev is not None and not (prev.get("track") == i.get("track") and abs(prev["end"] - i["start"]) < 1e-6):
                n += 1
            groups.setdefault(base if n == 1 else f"{base}#{n}", []).append(i)
            prev = i
    return groups


class ShotBook:
    """Per (job, stage) shot ledger across hosts, projects and timelines (used by S3/C2/C3).

    Each shot keeps one state per view (host, project, timeline) it appears in. A shot counts
    as done when, in some view holding its newest media version, it is done (graded / online),
    its grade was not merely carried onto new media (replaced), and it was not part of a mass
    apply (C3 guard). So re-opening an older timeline, a duplicate, or another room's copy does
    not undo work done elsewhere, while a new VFX version needs work on that version."""

    def __init__(self, mass_guard=False):
        self.mass_guard = mass_guard
        self.views = {}       # view -> set(ident)
        self.view_t = {}      # view -> t of last poll
        self.shots = {}       # ident -> {view: {"done","ver","sig","replaced","mass"}}
        self.work = set()     # (ident, sig/version) events: monotonic work counter

    @staticmethod
    def _ok(vs):
        return vs["done"] and not vs["replaced"] and not vs["mass"]

    def shot_done(self, ident):
        """state of the most recently CHANGED view among those holding the newest version
        (a view seen for the first time with nothing changed never overrides work elsewhere)"""
        views = self.shots.get(ident) or {}
        if not views:
            return False
        top = max(v["ver"] for v in views.values())
        latest = max((v for v in views.values() if v["ver"] == top), key=lambda v: v["changed_t"])
        return self._ok(latest)

    def update(self, obs):
        stage = obs.get("stage")
        view = (obs.get("host"), obs.get("project"), obs.get("timeline"))
        groups = identities(obs)
        reasons = set()
        prev = self.views.get(view)
        cur = set(groups)
        was_done = {i: self.shot_done(i) for i in self.shots}
        before = set(self.shots)
        if prev is not None:
            for g in prev - cur:
                vs = self.shots.get(g, {})
                vs.pop(view, None)
                if not vs:
                    self.shots.pop(g, None)
                    reasons.add("scope_shrink")
        newly = []
        for ident, its in groups.items():
            done = all(unit_done(i, stage) for i in its)
            ver = max(i.get("version") or 0 for i in its)
            sig = tuple(sorted({str(i.get("sig")) for i in its}))   # blade halves share one grade
            views = self.shots.setdefault(ident, {})
            s = views.get(view)
            if s is None:
                # first sight in this view: carried-over grade on a newer version than seen elsewhere = replaced
                top = max([v["ver"] for v in views.values()] or [ver])
                carried = any(v["sig"] == sig and v["ver"] < ver for v in views.values())
                views[view] = {"done": done, "ver": ver, "sig": sig,
                               "replaced": bool(stage == "color" and done and ver > top and carried), "mass": False,
                               "changed_t": float("-inf") if len(views) else obs.get("t", 0), "seen_ver": ver}
                if done and prev is not None:
                    newly.append(ident)
                if done:
                    self.work.add((ident, sig if stage == "color" else ver))
                continue
            if ver != s["ver"]:
                reasons.add("replaced")
                if stage == "color" and done and sig == s["sig"]:
                    s["replaced"] = True          # grade carried onto new media: needs a look
                s["ver"] = ver
                if stage == "conform" and done:
                    self.work.add((ident, ver))
            if stage == "color" and done and sig != s["sig"]:
                if s["done"] and not s["mass"]:
                    reasons.add("rework")
                s["replaced"] = False
                if s["done"]:
                    s["mass"] = False             # touched again individually after a mass apply
                self.work.add((ident, sig))
            if done and not s["done"]:
                self.work.add((ident, sig if stage == "color" else ver))
                newly.append(ident)
            if not done:
                s["mass"] = False
            if (done, sig) != (s["done"], s["sig"]) or ver != s.get("seen_ver", ver):
                s["changed_t"] = obs.get("t", 0)
            s["seen_ver"] = ver
            s["done"], s["sig"] = done, sig
        dt = obs.get("t", 0) - self.view_t.get(view, -1e9)
        self.view_t[view] = obs.get("t", 0)
        if (self.mass_guard and stage == "color" and dt <= MASS_WINDOW
                and len(newly) >= max(MASS_MIN, MASS_FRAC * len(self.shots))):
            for ident in newly:
                self.shots[ident][view]["mass"] = True
            reasons.add("mass_grade")
        if before and set(self.shots) - before:      # first poll ever is not a change
            reasons.add("scope_change")
        if any(was_done.get(i) and not self.shot_done(i) for i in self.shots):
            reasons.add("regression")
        self.views[view] = cur
        total = len(self.shots)
        dc = sum(1 for i in self.shots if self.shot_done(i))
        return total, dc, reasons

    def idents(self):
        return set(self.shots)

    def done_of(self, codes):
        idx = {}
        for ident in self.shots:
            idx.setdefault(ident.split("#")[0], []).append(ident)
        n = 0
        for c in codes:
            ids = idx.get(_norm_code(c))
            if ids and all(self.shot_done(i) for i in ids):
                n += 1
        return n

    def unknown_vs(self, codes):
        want = {_norm_code(c) for c in codes}
        return {i for i in self.shots if i.split("#")[0] not in want}


# ---------------- strategies ----------------
class S0Current(Strategy):
    name, label = "S0", "today: graded / current clips (conform: duration / offline)"
    ignore_late = False      # today's server pushes every record as it arrives
    needs = ("video_clips", "graded_clips", "duration_sec", "offline_target_sec")
    cost = "0 (exists)"

    def _step(self, key, obs, st):
        return _out(key, s0_pct(obs))


class S1HighWater(Strategy):
    name, label = "S1", "high-water-mark denominator (max clips / max offline target ever seen)"
    needs = ("video_clips", "graded_clips", "duration_sec", "offline_target_sec")
    cost = "S (server only)"

    def _step(self, key, obs, st):
        reasons = set()
        if obs.get("stage") == "conform":
            tgt = obs.get("offline_target_sec")
            if tgt:
                if st.get("hwm") and tgt > st["hwm"]:
                    reasons.add("scope_change")
                st["hwm"] = max(st.get("hwm") or 0, tgt)
            hwm, d = st.get("hwm"), obs.get("duration_sec")
            pct = None if not hwm or d is None else 100.0 * min(1.0, d / hwm)
            return _out(key, pct, reasons=reasons)
        v, g = obs.get("video_clips") or 0, obs.get("graded_clips") or 0
        if st.get("hwm") is not None and v > st["hwm"]:
            reasons.add("scope_change")
        if st.get("hwm") is not None and v < st["hwm"]:
            reasons.add("scope_shrink_ignored")
        st["hwm"] = max(st.get("hwm") or 0, v)
        pct = None if not st["hwm"] else 100.0 * g / st["hwm"]
        return _out(key, pct, total=st["hwm"], done_count=g, reasons=reasons)


class S2Confirmed(Strategy):
    name, label = "S2", "confirmed external scope; provisional (capped 99) until confirmed"
    needs = ("external.scope (PMS/offline EDL/conform shot list)", "graded_clips", "items.sid (ext, for shot match)")
    cost = "M (PMS field or shot-list import + server)"
    human = "someone records the scope (shot count/list or runtime) per job"

    def __init__(self):
        super().__init__()
        self.book = {}

    def _step(self, key, obs, st):
        stage = obs.get("stage")
        sc = ((obs.get("external") or {}).get("scope") or {}).get(stage)
        if not sc:
            p = s0_pct(obs)
            return _out(key, None if p is None else min(p, PROVISIONAL_CAP), done=False, provisional=True,
                        reasons={"scope_unconfirmed"})
        reasons = set()
        if stage == "conform" and sc.get("target_sec") and not sc.get("shots"):
            d = obs.get("duration_sec") or 0
            pct = 100.0 * min(1.0, d / sc["target_sec"])
            return _out(key, pct, confirmed=True)
        total = sc.get("total") or len(sc.get("shots") or [])
        if sc.get("shots") and obs.get("items") is not None:
            book = self.book.setdefault(key, ShotBook())
            book.update(obs)
            dc = book.done_of(sc["shots"])
            if book.unknown_vs(sc["shots"]):
                reasons.add("scope_mismatch")
        else:
            dc = min(obs.get("graded_clips") or 0, total)
            if (obs.get("video_clips") or 0) != total:
                reasons.add("scope_mismatch")
        pct = 100.0 * dc / total if total else None
        return _out(key, pct, total=total, done_count=dc, confirmed=True, reasons=reasons)


class S3ShotState(Strategy):
    name, label = "S3", "per-shot identity ledger across rooms/timelines (rework/replace aware)"
    needs = ("items: sid or unique id, kind, graded, sig, version (ext)",)
    cost = "M-L (poller sends per-item identity + server ledger)"

    def __init__(self):
        super().__init__()
        self.books = {}
        self.fallback = S1HighWater()

    def _step(self, key, obs, st):
        if obs.get("items") is None:
            o = self.fallback._step(key, obs, st.setdefault("fb", {}))
            o["reasons"] = sorted(set(o["reasons"]) | {"no_identity"})
            return o
        book = self.books.setdefault(key, ShotBook())
        total, dc, reasons = book.update(obs)
        pct = 100.0 * dc / total if total else None
        return _out(key, pct, total=total, done_count=dc, reasons=reasons, work=len(book.work))


class S4Stable(Strategy):
    """Hysteresis wrapper: 100%/done only after the base value held 100 with the same total
    for STABLE_MIN minutes; drops reported only after DEBOUNCE_POLLS consecutive polls."""
    name, label = "S4", "S0 + hysteresis: 100% only after stable 30 min, drops debounced 3 polls"
    needs = ("same as base",)
    cost = "S (server only)"
    base_cls = S0Current

    def __init__(self):
        super().__init__()
        self.base = self.base_cls()

    def _step(self, key, obs, st):
        b = self.base.step(obs)
        reasons = set(b["reasons"])
        pct = b["pct"]
        t = obs.get("t", 0)
        sig = (b["total"], obs.get("video_clips"))
        if pct is not None and pct >= 100.0:
            if st.get("hi_since") is None or st.get("hi_sig") != sig:
                st["hi_since"], st["hi_sig"] = t, sig
            stable = t - st["hi_since"] >= STABLE_MIN
        else:
            st["hi_since"] = None
            stable = False
        done = stable and not b["provisional"]
        if pct is not None and pct >= 100.0 and not done:
            pct = PROVISIONAL_CAP
            reasons.add("awaiting_stability")
        last = st.get("last")
        if pct is not None and last is not None and pct < last - 1.0:
            st["drops"] = st.get("drops", 0) + 1
            if st["drops"] < DEBOUNCE_POLLS:
                pct = last
                reasons.add("debounced_drop")
            else:
                st["drops"] = 0
        else:
            st["drops"] = 0
        st["last"] = pct
        return _out(key, pct, done=done, total=b["total"], done_count=b["done_count"],
                    confirmed=b["total_confirmed"], provisional=b["provisional"] or not done,
                    reasons=reasons, work=b["work_done"])


class S5WorkDone(Strategy):
    name, label = "S5", "S0 current % plus a separate monotonic work-done counter"
    needs = ("graded_clips per poll", "items.sig (ext) for exact counting")
    cost = "S-M (server; exact with item sigs)"

    def _step(self, key, obs, st):
        pct = s0_pct(obs)
        if obs.get("items") is not None:
            ev = st.setdefault("ev", set())
            for i in obs["items"]:
                if unit_done(i, obs.get("stage")):
                    ev.add((i.get("sid") or i["id"], i.get("sig"), i.get("version")))
            work = len(ev)
        else:
            vk = (obs.get("host"), obs.get("project"), obs.get("timeline"))
            prev = st.setdefault("prev", {}).get(vk)
            g = obs.get("graded_clips") or 0
            if prev is not None and g > prev:
                st["work"] = st.get("work", 0) + g - prev
            elif prev is None:
                st["work"] = st.get("work", 0) + g
            st["prev"][vk] = g
            work = st.get("work", 0)
        return _out(key, pct, work=work)


class S6BookingTime(Strategy):
    name, label = "S6", "straw man: booked time elapsed / booking length"
    needs = ("booking start/end",)
    cost = "S"

    def _step(self, key, obs, st):
        b = obs.get("booking") or {}
        s, e = b.get("start_t"), b.get("end_t")
        if s is None or e is None or e <= s:
            return _out(key, None)
        return _out(key, 100.0 * (obs.get("t", 0) - s) / (e - s))


class C1HighWaterStable(S4Stable):
    name, label = "C1", "S1 + S4 (high-water mark with hysteresis)"
    base_cls = S1HighWater


class C2ShotStable(S4Stable):
    name, label = "C2", "S3 + S4 (shot ledger with hysteresis)"
    base_cls = S3ShotState
    needs = S3ShotState.needs
    cost = "M-L"


class C3Recommended(Strategy):
    """S2 + S3 + S4 + S5, plus job guard, detector-pending hold and PMS task reopen.

    - scope = confirmed external shot list (or count) when present; otherwise the union of the
      shot ledger and the automatic scope hints (offline-timeline shots seen by the conform room,
      the conform ledger of the same job) -> provisional
    - provisional values are capped at 99 and never done
    - done only when confirmed, every confirmed shot done on its newest version, no unknown shots,
      and that state held STABLE_MIN minutes; a PMS task back in TODO/IN_PROGRESS/REVISION reopens
    - drops are reported at once, always with a reason code (unexplained_drop if none fits)
    - work_done is monotonic (grade/conform events): effort on rework is visible even when % is flat
    - Resolve project that does not match the booking -> job_mismatch: not sent, ledger untouched
    - grade detector still has pending clips (grade.pending > 0) -> previous value held
    """
    name, label = "C3", "S2+S3+S4+S5 + job guard: confirmed scope, shot ledger, stable done, work counter"
    needs = ("items (ext)", "external.scope (confirmed shot list)", "booking project_no/name",
             "grade.pending (exists)", "offline_shots (ext)", "PMS task status (optional)")
    cost = "L (poller items + server ledger + PMS fields)"
    human = "scope confirmation per job (shot list/count) - or owner accepts the offline timeline as scope (C4)"
    auto_confirm = False

    def __init__(self):
        super().__init__()
        self.books = {}
        self.hints = {}       # job -> set(idents) from offline timelines / conform ledger
        self.fb = S1HighWater()

    def _hold(self, key, reason):
        prev = self.last.get(key)
        o = dict(prev) if prev else _out(key, provisional=True)
        o["reasons"] = sorted((set(o["reasons"]) - {"job_mismatch", "detector_pending", "reference_timeline"}) | {reason})
        o["send"] = False
        return o

    def _step(self, key, obs, st):
        stage = obs.get("stage")
        job = key.split("|")[0]
        reasons = set()
        b = obs.get("booking") or {}
        proj = _norm_proj(obs.get("project"))
        pno, pname = _norm_proj(b.get("project_no")), _norm_proj(b.get("project_name"))
        if proj and not ((pno and pno in proj) or (pname and (pname in proj or proj in pname))):
            return self._hold(key, "job_mismatch")
        if obs.get("grade_pending"):
            return self._hold(key, "detector_pending")
        if re.search(OFFLINE_MATCH, str(obs.get("timeline") or ""), re.I):
            return self._hold(key, "reference_timeline")     # offline/editorial reference is never the deliverable
        sc = ((obs.get("external") or {}).get("scope") or {}).get(stage)
        task = (obs.get("external") or {}).get("pms_task")
        work = None
        if obs.get("items") is None:
            fb = self.fb._step(key, obs, st.setdefault("fb", {}))
            total, dc = fb["total"], fb["done_count"]
            reasons |= set(fb["reasons"]) | {"no_identity"}
            if stage == "conform":
                pct = fb["pct"]
                total = dc = None
            else:
                pct = None if not total else 100.0 * (dc or 0) / total
            book = None
        else:
            book = self.books.setdefault(key, ShotBook(mass_guard=True))
            total, dc, r = book.update(obs)
            reasons |= r
            work = len(book.work)
            hint = self.hints.setdefault(job, set())
            hint |= {_norm_code(x) for x in obs.get("offline_shots") or []}
            if stage == "conform":
                hint |= {i.split("#")[0] for i in book.idents()}
            extra = hint - {i.split("#")[0] for i in book.idents()} if stage == "color" or obs.get("offline_shots") else set()
            if extra:
                total += len(extra)
                reasons.add("scope_hint")
            pct = 100.0 * dc / total if total else None
        confirmed = False
        if sc and sc.get("shots") and book is not None:
            confirmed = True
            total = len(sc["shots"])
            dc = book.done_of(sc["shots"])
            if book.unknown_vs(sc["shots"]):
                reasons.add("scope_mismatch")
            pct = 100.0 * dc / total
        elif sc and sc.get("total") and stage == "color":
            confirmed = True
            total = sc["total"]
            dc = min(dc or 0, total)
            pct = 100.0 * dc / total
        elif self.auto_confirm and book is not None and obs.get("offline_shots"):
            confirmed = True
            want = sorted({_norm_code(x) for x in obs["offline_shots"]})
            total, dc = len(want), book.done_of(want)
            if book.unknown_vs(want):
                reasons.add("scope_mismatch")
            pct = 100.0 * dc / total
        last = st.get("last")
        if pct is not None and last is not None and last["pct"] is not None and pct < last["pct"] - 1.0:
            if total and last["total"] and total > last["total"]:
                reasons.add("scope_change")
            elif dc is not None and last["dc"] is not None and dc < last["dc"]:
                reasons.add("regression")
            if not reasons & {"scope_change", "scope_shrink", "rework", "regression", "replaced", "scope_mismatch"}:
                reasons.add("unexplained_drop")
        complete = pct is not None and pct >= 100.0
        blockers = reasons & {"scope_mismatch"}
        sig = (total, dc)
        if complete and confirmed and not blockers:
            if st.get("hi_since") is None or st.get("hi_sig") != sig:
                st["hi_since"], st["hi_sig"] = obs.get("t", 0), sig
        else:
            st["hi_since"] = None
        stable = st.get("hi_since") is not None and obs.get("t", 0) - st["hi_since"] >= STABLE_MIN
        if task in DONE_OPEN and st.get("was_done"):
            reasons.add("reopened")
            stable = False
            st["hi_since"] = None
        if task == "REVISION":
            reasons.add("rework")
        done = bool(complete and confirmed and not blockers and stable)
        provisional = not (confirmed and not blockers)
        if not confirmed:
            reasons.add("scope_unconfirmed")
        if complete and not done:
            pct = PROVISIONAL_CAP
            if confirmed and not blockers:
                reasons.add("awaiting_stability")
        st["was_done"] = done or bool(st.get("was_done") and task not in DONE_OPEN)
        st["last"] = {"pct": pct, "total": total, "dc": dc}
        return _out(key, pct, done=done, total=total, done_count=dc, confirmed=confirmed,
                    provisional=provisional, reasons=reasons, work=work)


class C4AutoScope(C3Recommended):
    name, label = "C4", "C3, but the offline/editorial timeline's shot list counts as confirmed scope (no human input)"
    human = "none (trusts the offline timeline found by name)"
    auto_confirm = True


class Q1QuickFix(Strategy):
    """What can ship this week on the server alone, no poller or PMS change: today's value, but
    never 100 / done (capped 99, provisional), held while the grade detector is still pending,
    while an offline/reference timeline is open, or while the Resolve project does not match the
    booking; late (backlog) records do not move the value; drops get a reason code from counts."""
    name, label = "Q1", "quick fix: S0 capped at 99 + holds (pending, reference timeline, job mismatch, late) + count reasons"
    needs = ("same as S0 + grade.pending + booking project_no/name",)
    cost = "S (server only, this week)"

    def _step(self, key, obs, st):
        b = obs.get("booking") or {}
        proj = _norm_proj(obs.get("project"))
        pno, pname = _norm_proj(b.get("project_no")), _norm_proj(b.get("project_name"))
        hold = None
        if proj and not ((pno and pno in proj) or (pname and (pname in proj or proj in pname))):
            hold = "job_mismatch"
        elif obs.get("grade_pending"):
            hold = "detector_pending"
        elif re.search(OFFLINE_MATCH, str(obs.get("timeline") or ""), re.I):
            hold = "reference_timeline"
        if hold:
            prev = self.last.get(key)
            o = dict(prev) if prev else _out(key, provisional=True)
            o["reasons"] = sorted({r for r in o["reasons"] if r not in ("job_mismatch", "detector_pending",
                                                                         "reference_timeline")} | {hold})
            o["send"] = False
            return o
        pct = s0_pct(obs)
        reasons = {"scope_unconfirmed"}
        v, g = obs.get("video_clips"), obs.get("graded_clips")
        last = st.get("last")
        if last and pct is not None and last["pct"] is not None and pct < last["pct"] - 1.0:
            if v is not None and last["v"] is not None and v > last["v"]:
                reasons.add("scope_change")
            elif g is not None and last["g"] is not None and g < last["g"]:
                reasons.add("regression")
            elif (obs.get("project"), obs.get("timeline")) != last["view"]:
                reasons.add("scope_change")
            else:
                reasons.add("unexplained_drop")
        st["last"] = {"pct": pct, "v": v, "g": g, "view": (obs.get("project"), obs.get("timeline"))}
        return _out(key, None if pct is None else min(pct, PROVISIONAL_CAP), done=False, provisional=True,
                    reasons=reasons)


ALL = [S0Current, S1HighWater, S2Confirmed, S3ShotState, S4Stable, S5WorkDone, S6BookingTime,
       C1HighWaterStable, C2ShotStable, C3Recommended, C4AutoScope, Q1QuickFix]
BY_NAME = {c.name: c for c in ALL}
RECOMMENDED = "C3"


def run(strategy_cls, observations):
    s = strategy_cls()
    return [s.step(o) for o in observations]

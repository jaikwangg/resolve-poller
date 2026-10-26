"""Scenario engine: expands a scenario (JSON) into the observation stream the
editortrack server would receive, plus the ground truth at each observation.

Two scenario kinds (see research/sim/README.md for the full format):

  "steps"         a story told as edit operations (add / grade / replace / cut ...).
                  The engine keeps a model of every Resolve project, timeline and
                  host, and after each step emits one poll per polling host.
  "observations"  recorded observations given as-is (replay of real data).
                  Each must carry a "truth" block written by whoever recorded it.

An observation is a superset of today's record (schema 3): the counts the
poller sends today (video_clips, graded_clips, duration_sec, markers, shots,
offline_target_sec) plus fields a richer poller could send (items with
identity, grade signature, media version, online state). Strategies declare
which fields they read; see strategies.py.

Stdlib only. Pure: same scenario in -> same observations out.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re

BASE_TS = dt.datetime(2026, 11, 2, 2, 0, tzinfo=dt.timezone.utc)   # 09:00 Bangkok, an arbitrary Monday
# same default as agent/resolve_poller.py OFFLINE_MATCH
OFFLINE_MATCH = r"offline|editorial|_ref\b|reference|picture\s*lock|ผ่านตัด"
STAGES = ("data", "conform", "color", "subtitle", "master")
# items that sit on a video track and are counted in video_clips today
VIDEO_KINDS = {"video", "vfx", "adjustment", "title", "generator", "compound", "still", "placeholder", "multicam",
               "disabled"}


class ScenarioError(ValueError):
    pass


def _iso(t_min, skew_sec=0):
    return (BASE_TS + dt.timedelta(minutes=t_min, seconds=skew_sec)).isoformat()


class _Item:
    __slots__ = ("id", "shot", "kind", "track", "start", "dur", "graded", "sig", "media", "version",
                 "online", "ident", "src_in", "job", "sid_override")

    def to_obs(self):
        sid = None
        if self.ident in ("marker", "name"):
            sid = self.media if self.ident == "name" else self.shot
        if self.sid_override is not None:
            sid = self.sid_override
        return {"id": self.id, "kind": self.kind, "track": self.track, "start": self.start,
                "end": round(self.start + self.dur, 3), "graded": self.graded, "sig": self.sig,
                "media": self.media, "version": self.version, "online": self.online,
                "sid": sid, "src_in": self.src_in}


class World:
    def __init__(self, sc):
        self.sc = sc
        self.job = sc.get("job", "KPP-2026-0001")
        self.projects = {}            # name -> {"timelines": {name: [items]}}
        self.hosts = {}
        self.truth = {}               # job -> stage -> shot -> bool
        self.scope = {}               # job -> stage -> [shots]
        self.timeline_job = {}        # (project, timeline) -> job
        self.external = {"scope": {}, "pms_task": None}
        self.seq = 0
        self.ident_default = sc.get("identity", "name")
        default_booking = sc.get("booking", {"booking_id": "B-1", "project_no": self.job,
                                             "project_name": sc.get("project_name", self.job),
                                             "activity": "Grading", "start_t": 0, "end_t": 480})
        for h, cfg in (sc.get("hosts") or {"grade-01": {"stage": sc.get("stage", "color")}}).items():
            self.hosts[h] = {"stage": cfg.get("stage", sc.get("stage", "color")), "project": cfg.get("project"),
                             "timeline": cfg.get("timeline"),
                             "booking": copy.deepcopy(cfg["booking"] if "booking" in cfg else default_booking),
                             "skew": cfg.get("skew", 0), "up": True}
        for job, stages in (sc.get("truth") or {}).items():
            for st, shots in stages.items():
                self.scope.setdefault(job, {})[st] = list(shots)

    # ---- helpers ----
    def _tl(self, host=None, project=None, timeline=None):
        h = self.hosts[host] if host else None
        p = project or (h and h["project"])
        t = timeline or (h and h["timeline"])
        if not p or not t:
            raise ScenarioError(f"no open project/timeline (host={host})")
        return self.projects.setdefault(p, {"timelines": {}})["timelines"].setdefault(t, [])

    def _job_of(self, project, timeline):
        return self.timeline_job.get((project, timeline), self.job)

    def _set_truth(self, job, stage, shots, value):
        d = self.truth.setdefault(job, {}).setdefault(stage, {})
        for s in shots:
            d[s] = value

    def _items(self, host, a):
        return self._tl(host, a.get("project"), a.get("timeline"))

    def _newsig(self):
        self.seq += 1
        return f"g{self.seq}"

    # ---- operations ----
    def op(self, host, name, a):
        a = a if a is not None else {}
        h = self.hosts.get(host)
        if name == "open":
            h["project"], h["timeline"] = a["project"], a["timeline"]
            self._tl(host)
        elif name == "add":
            items = self._items(host, a)
            track = a.get("track", 1)
            end = max([i.start + i.dur for i in items if i.track == track] or [a.get("at", 0.0)])
            if "at" in a:
                end = a["at"]
            for s in a["shots"]:
                it = _Item()
                self.seq += 1
                it.id = f"u{self.seq}"
                it.shot, it.kind, it.track = s, a.get("kind", "video"), track
                it.start, it.dur = end, float(a.get("dur", 4.0))
                end += it.dur
                it.graded = bool(a.get("graded", False))
                it.sig = self._newsig() if it.graded else None
                it.version = a.get("version", 1)
                it.media = a.get("media", "{shot}_v{version:03d}").format(shot=s, version=it.version)
                it.online = bool(a.get("online", True))
                it.ident = a.get("ident", self.ident_default)
                it.src_in = float(a.get("src_in", 0.0))
                it.job = None
                it.sid_override = (a.get("sid") or {}).get(s) if isinstance(a.get("sid"), dict) else a.get("sid")
                items.append(it)
                if a.get("done"):
                    self._set_truth(self._job_of(a.get("project") or h["project"], a.get("timeline") or h["timeline"]),
                                    a.get("stage", h["stage"] if h else "color"), [s], True)
        elif name == "blade":
            items = self._items(host, a)
            for idx, it in enumerate(items):
                if it.shot == a["shot"]:
                    half = it.dur / 2
                    b = copy.copy(it)
                    self.seq += 1
                    b.id = f"u{self.seq}"
                    b.start, b.dur, b.src_in = it.start + half, half, it.src_in + half
                    it.dur = half
                    items.insert(idx + 1, b)
                    break
        elif name == "remove":
            items = self._items(host, a)
            items[:] = [i for i in items if i.shot not in set(a["shots"])]
            if a.get("close_gap", True):
                for tr in {i.track for i in items}:
                    pos = 0.0
                    for i in sorted([i for i in items if i.track == tr], key=lambda x: x.start):
                        i.start, pos = pos, pos + i.dur
        elif name == "replace":
            items = self._items(host, a)
            for i in items:
                if i.shot == a["shot"]:
                    self.seq += 1
                    if a.get("new_item", False):
                        i.id = f"u{self.seq}"
                    i.version = a.get("version", i.version + 1)
                    i.media = a.get("media", "{shot}_v{version:03d}").format(shot=i.shot, version=i.version)
                    i.online = a.get("online", True)
                    if "kind" in a:
                        i.kind = a["kind"]
                    if not a.get("keep_grade", True):
                        i.graded, i.sig = False, None
            job = self._job_of(h["project"], h["timeline"])
            for st, v in (a.get("truth") or {"color": False}).items():
                self._set_truth(job, st, [a["shot"]], v)
        elif name in ("grade", "ungrade", "notes", "crash_revert"):
            shots = set(a.get("shots") or [])
            job = self._job_of(h["project"], h["timeline"])
            items = self._items(host, a)
            visible = a.get("visible", True)
            if name in ("ungrade", "crash_revert"):
                # resetting a clip that is not graded here changes nothing in the deliverable
                shots = {i.shot for i in items if i.shot in shots and i.graded}
            for i in items:
                if i.shot in shots and visible:
                    if name == "grade":
                        i.graded, i.sig = True, self._newsig()
                    elif name in ("ungrade", "crash_revert"):
                        i.graded, i.sig = False, None
            if name == "grade":
                self._set_truth(job, "color", shots, bool(a.get("true", True)))
            else:
                self._set_truth(job, "color", shots, False)
        elif name == "rename_sid":
            for i in self._items(host, a):
                if i.shot == a["shot"]:
                    i.sid_override = a["sid"]
        elif name == "lut_all":
            # one transform applied to every clip at once (look / CST / show LUT)
            if a.get("level", "clip") == "clip":
                for i in self._items(host, a):
                    if i.kind in VIDEO_KINDS:
                        i.graded, i.sig = True, self._newsig()
            # truth unchanged: a look on every clip is not a finished grade
        elif name == "conform":
            job = self._job_of(h["project"], h["timeline"])
            shots = set(a["shots"])
            for i in self._items(host, a):
                if i.shot in shots:
                    i.online = True
                    if i.kind == "placeholder":
                        i.kind = "vfx"
            self._set_truth(job, "conform", shots, bool(a.get("true", True)))
        elif name == "copy_timeline":
            src = self._tl(None, a.get("from_project") or h["project"], a["from"])
            dst_p = a.get("to_project") or h["project"]
            dst = self.projects.setdefault(dst_p, {"timelines": {}})["timelines"].setdefault(a["to"], [])
            for i in src:
                c = copy.copy(i)
                self.seq += 1
                c.id = f"u{self.seq}"
                dst.append(c)
            if a.get("open", True):
                h["project"], h["timeline"] = dst_p, a["to"]
        elif name == "new_timeline":
            p = a.get("project") or h["project"]
            self.projects.setdefault(p, {"timelines": {}})["timelines"].setdefault(a["name"], [])
            if a.get("open"):
                h["project"], h["timeline"] = p, a["name"]
        elif name == "booking":
            h["booking"] = copy.deepcopy(a) if a else None
        elif name == "confirm_scope":
            self.external["scope"][a.get("stage", "color")] = {k: v for k, v in a.items() if k != "stage"}
        elif name == "pms_task":
            self.external["pms_task"] = a.get("status")
        elif name == "skew":
            h["skew"] = a.get("sec", 0)
        elif name == "down":
            h["up"] = False
        elif name == "up":
            h["up"] = True
        elif name == "truth_scope":
            self.scope.setdefault(a.get("job", self.job), {})[a.get("stage", "color")] = list(a["shots"])
        elif name == "truth_done":
            self._set_truth(a.get("job", self.job), a.get("stage", "color"), a["shots"], bool(a.get("value", True)))
        elif name == "timeline_job":
            self.timeline_job[(a.get("project") or h["project"], a["timeline"])] = a["job"]
        elif name == "set_stage":
            h["stage"] = a["stage"]
        else:
            raise ScenarioError(f"unknown op {name!r}")

    # ---- observation ----
    def truth_for(self, job, stage):
        scope = (self.scope.get(job) or {}).get(stage)
        if not scope:
            return {"pct": None, "done": False, "scope": 0}
        d = (self.truth.get(job) or {}).get(stage) or {}
        n = sum(1 for s in scope if d.get(s))
        pct = round(100.0 * n / len(scope), 2)
        return {"pct": pct, "done": n == len(scope), "scope": len(scope)}

    def observe(self, host, t):
        h = self.hosts[host]
        base = {"t": t, "ts": _iso(t, h["skew"]), "host": host, "stage": h["stage"],
                "booking": copy.deepcopy(h["booking"]),
                "external": copy.deepcopy(self.external)}
        if not h["up"]:
            return {**base, "ok": False, "reason": "resolve_not_running"}
        if not h["project"] or not h["timeline"]:
            return {**base, "ok": False, "reason": "no_project_open"}
        proj = self.projects[h["project"]]
        items = proj["timelines"][h["timeline"]]
        vids = [i for i in items if i.kind in VIDEO_KINDS]
        dur = max([i.start + i.dur for i in items] or [0.0])
        offline, offline_shots = None, None
        for name, tl in proj["timelines"].items():
            if name == h["timeline"]:
                continue
            if re.search(OFFLINE_MATCH, name, re.I):
                offline = round(max([i.start + i.dur for i in tl] or [0.0]), 2) or None
                offline_shots = [x["sid"] for x in (i.to_obs() for i in tl)
                                 if x["sid"] and x["kind"] in ("video", "vfx", "placeholder", "compound")]
                break
        markers = [i for i in vids if i.ident == "marker"]
        shots_seen = {}
        for i in markers:
            code = i.sid_override if i.sid_override is not None else i.shot
            shots_seen.setdefault(code, i.graded)
        job = self._job_of(h["project"], h["timeline"])
        return {**base, "ok": True, "project": h["project"], "timeline": h["timeline"],
                "timeline_count": len(proj["timelines"]),
                "video_clips": len(vids), "graded_clips": sum(1 for i in vids if i.graded),
                "duration_sec": round(dur, 2), "offline_target_sec": offline,
                "offline_shots": offline_shots, "grade_pending": 0,
                "markers": len(shots_seen),
                "shots": [{"code": c, "graded": g} for c, g in shots_seen.items()],
                "items": [i.to_obs() for i in items],
                "true_job": job,
                "truth": self.truth_for(job, h["stage"]),
                "truth_by_job": {j: self.truth_for(j, h["stage"]) for j in sorted(set(self.scope) | {job})}}


def expand(sc):
    """scenario dict -> list of observations (each with truth)."""
    if "observations" in sc:
        out = []
        for o in sc["observations"]:
            o = copy.deepcopy(o)
            o.setdefault("ts", _iso(o.get("t", 0)))
            o.setdefault("ok", True)
            o.setdefault("external", {"scope": {}, "pms_task": None})
            o.setdefault("true_job", (o.get("booking") or {}).get("project_no") or sc.get("job"))
            out.append(o)
        return out
    w = World(sc)
    obs = []
    default_host = next(iter(w.hosts))
    for si, step in enumerate(sc.get("steps", [])):
        host = step.get("host", default_host)
        for op in step.get("ops", []):
            if isinstance(op, str):
                name, args = op, {}
            else:
                name, args = op[0], (op[1] if len(op) > 1 else {})
            w.op(host, name, args)
        poll = step.get("poll", True)
        if poll is False:
            continue
        hosts = [host] if poll is True else (list(w.hosts) if poll == "all" else list(poll))
        for ph in hosts:
            o = w.observe(ph, step["t"])
            o["step"] = si
            obs.append(o)
    return obs


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

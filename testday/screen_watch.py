#!/usr/bin/env python3
"""Screen-change watcher for the N-hour soaks (WORKSTATION_SAFETY.md section 7, S1-S3).

Question it answers: did anything on screen change at an agent turn while nobody touched the machine?

    # on the test workstation, during the soak (stdlib + OS screenshot tool, nothing to install):
    python3 testday/screen_watch.py capture --out ~/ET_TESTDAY/S3_grade01 --hours 8 --interval 2 \
        [--mask 0.88,0,0.12,0.03 ...]          # mask = x,y,w,h fractions to ignore (e.g. the menu-bar clock)
    # afterwards (on the workstation or any machine with the run folder copied):
    python3 testday/screen_watch.py analyze --run ~/ET_TESTDAY/S3_grade01 \
        --agent-log ~/.editortrack/agent.log [--records 2026-11-02.jsonl --host grade-01] [--no-operator]
    # re-run the diff over saved frames (tests, or frames exported from another recorder):
    python3 testday/screen_watch.py replay --list frames.csv --out RUN      # csv: epoch,idle_sec,path

capture: every --interval s it grabs the screen (macOS `screencapture`, every display, + `sips`; Linux X11
`ffmpeg -f x11grab` or ImageMagick `import`), downsizes to --width, compares with the previous frame (exact, row
by row; masked areas ignored) and writes one CSV row: time, OS idle seconds, changed?, bounding box. Frames are
kept only for changes nobody caused (before + after) and once every --keyframe-min minutes, within a disk budget
(--max-gb, --min-free-gb); past it the CSV continues without frames. A failed grab is a CSV row, never the end
of the run. It asks once that the first frame shows Resolve (not wallpaper / lock screen).

analyze: a change is "user" when the OS idle time shows input since the previous frame; otherwise it is
unexplained. Verdicts:
  FAIL        an unexplained change (or one with unknown idle) overlapping an agent turn (turn start - 2 s ..
              end + 2 s; turn ends from the records' turn_ms), or a forbidden / suspect / tripped record
  INCOMPLETE  no server records for the host, fewer than --min-scans full scans, capture shorter than planned,
              > 1 % failed grabs, an agent turn inside a capture gap, frames stopped by the disk budget
  PENDING     unexplained changes away from turns that the test lead still has to explain
  PASS        none of the above (exit 0 only for PASS)
Agent.log turns that printed None (silent) are not evidence. User changes that overlap a turn are listed as
REVIEW. With --no-operator (S3 idle soak) every change counts as unexplained, whatever the idle reading says.

The screen content is evidence: run this only with DUMMY projects on screen; close mail/chat first.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import imgdiff  # noqa: E402

FIELDS = ["epoch", "local", "idle_sec", "changed", "rows", "bbox", "area", "w", "h", "file", "cap_ms", "note"]


# ---------------------------------------------------------------- OS idle (no Resolve, no extra packages)
def idle_seconds():
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True,
                                 timeout=5).stdout
            for line in out.splitlines():
                if '"HIDIdleTime"' in line:
                    return int(line.split("=")[-1].strip()) / 1e9
            return None
        if sys.platform.startswith("linux"):
            return _x11_idle()
    except Exception:
        return None
    return None


def _x11_idle():
    import ctypes

    class XSS(ctypes.Structure):
        _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                    ("since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("event_mask", ctypes.c_ulong)]

    x11 = ctypes.CDLL("libX11.so.6")
    xss = ctypes.CDLL("libXss.so.1")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XFree.argtypes = [ctypes.c_void_p]
    xss.XScreenSaverAllocInfo.restype = ctypes.c_void_p
    xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return None
    try:
        info = xss.XScreenSaverAllocInfo()
        xss.XScreenSaverQueryInfo(dpy, x11.XDefaultRootWindow(dpy), info)
        idle = ctypes.cast(info, ctypes.POINTER(XSS)).contents.idle / 1000.0
        x11.XFree(info)
        return idle
    finally:
        x11.XCloseDisplay(dpy)


# ---------------------------------------------------------------- capture backends
class Grabber(object):
    """grab() -> (Frame, path of a full-resolution PNG or None). Temp files live in a private temp dir."""

    def __init__(self, backend, width, display=None):
        self.width = width
        self.display = display
        self.tmp = tempfile.mkdtemp(prefix="et-screen-")
        self.backend = backend if backend != "auto" else self._auto()

    def _auto(self):
        if sys.platform == "darwin":
            if not (shutil.which("screencapture") and shutil.which("sips")):
                common.die("macOS screencapture/sips not found")
            return "mac"
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            common.die("Wayland session: X11 is required (Resolve rooms run X11; see NEXT_STEPS)")
        if not os.environ.get("DISPLAY"):
            common.die("no DISPLAY: run inside the operator's X11 session")
        if shutil.which("ffmpeg"):
            return "ffmpeg"
        if shutil.which("import"):
            return "import"
        common.die("need ffmpeg (x11grab) or ImageMagick 'import' on Linux")

    def grab(self):
        """-> (Frame for diffing, list of full-resolution files or [])."""
        if self.backend == "mac":
            # one file per display: screencapture writes display 1 to the first name, 2 to the second, ...
            names = [os.path.join(self.tmp, "full%d.png" % i) for i in range(1, 4)]
            for n in names:
                if os.path.exists(n):
                    os.remove(n)
            cmd = ["screencapture", "-x", "-t", "png"]
            if self.display:
                cmd += ["-D", str(self.display)]
                names = names[:1]
            subprocess.run(cmd + names, check=True, timeout=30)
            fulls = [n for n in names if os.path.exists(n)]
            if not fulls:
                raise OSError("screencapture wrote nothing")
            frames = []
            for i, n in enumerate(fulls):
                small = os.path.join(self.tmp, "small%d.bmp" % i)
                subprocess.run(["sips", "-s", "format", "bmp", "--resampleWidth", str(self.width), n, "--out", small],
                               check=True, timeout=30, capture_output=True)
                with open(small, "rb") as f:
                    frames.append(imgdiff.read_bmp(f.read()))
            return stack(frames), fulls
        if self.backend == "ffmpeg":
            disp = os.environ.get("DISPLAY", ":0")
            cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "x11grab", "-i", disp,
                   "-vf", "scale=%d:-1:flags=area" % self.width, "-frames:v", "1", "-f", "image2pipe",
                   "-c:v", "bmp", "-"]
            r = subprocess.run(cmd, check=True, timeout=30, capture_output=True)
            return imgdiff.read_bmp(r.stdout), []
        if self.backend == "import":
            r = subprocess.run(["import", "-silent", "-window", "root", "-resize", "%dx" % self.width, "bmp3:-"],
                               check=True, timeout=30, capture_output=True)
            return imgdiff.read_bmp(r.stdout), []
        raise ValueError(self.backend)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def stack(frames):
    """Displays one above the other (same width after resampling) so one diff covers all of them."""
    if len(frames) == 1:
        return frames[0]
    w, bpp = frames[0].w, frames[0].bpp
    rows = []
    for f in frames:
        if f.w != w or f.bpp != bpp:
            raise ValueError("displays resampled to different widths")
        rows.extend(f.rows)
    return imgdiff.Frame(w, len(rows), bpp, rows)


class Keeper(object):
    """Writes evidence frames under run/frames with a disk budget: past --max-gb, or below --min-free-gb free on
    that disk, nothing more is kept (the CSV goes on) so a soak can never fill the workstation's disk."""

    def __init__(self, run, max_gb, min_free_gb):
        self.dir = os.path.join(run, "frames")
        self.max_bytes = max_gb * 1e9
        self.min_free = min_free_gb * 1e9
        self.used = 0
        self.stopped = None

    def ok(self):
        if self.stopped:
            return False
        if self.used >= self.max_bytes:
            self.stopped = "disk_stop:max_gb"
        elif shutil.disk_usage(self.dir).free < self.min_free:
            self.stopped = "disk_stop:min_free"
        return not self.stopped

    def keep(self, epoch, frame, fulls, tag):
        if not self.ok():
            return ""
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(epoch)) + "-%03d" % int((epoch % 1) * 1000)
        out = []
        if fulls:
            for i, src in enumerate(fulls, 1):
                name = "%s_%s_d%d.png" % (stamp, tag, i)
                shutil.copyfile(src, os.path.join(self.dir, name))
                self.used += os.path.getsize(os.path.join(self.dir, name))
                out.append(os.path.join("frames", name))
        else:
            name = "%s_%s.png" % (stamp, tag)
            data = imgdiff.write_png(frame)
            with open(os.path.join(self.dir, name), "wb") as f:
                f.write(data)
            self.used += len(data)
            out.append(os.path.join("frames", name))
        return ";".join(out)


def user_input_since(idle, gap, slack=0.5):
    """True when the OS idle time shows input since the previous frame."""
    return idle is not None and idle < gap + slack


def capture(args):
    run = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.join(run, "frames"), exist_ok=True)
    csv_path = os.path.join(run, "capture.csv")
    if os.path.exists(csv_path) and not args.append:
        common.die("%s already has a capture.csv; use a new --out per run (or --append)" % run)
    masks = [imgdiff.parse_rect(m) for m in args.mask]
    try:
        os.nice(10)
    except Exception:
        pass
    g = Grabber(args.backend, args.width, args.display)
    keeper = Keeper(run, args.max_gb, args.min_free_gb)
    meta = {"kit_version": common.KIT_VERSION, "host": os.uname()[1], "backend": g.backend, "width": args.width,
            "interval": args.interval, "masks": args.mask, "started": common.iso_local(time.time()),
            "hours": args.hours, "label": args.label, "first_frame_confirmed": None}
    common.write_json(os.path.join(run, "capture_meta.json"), meta)
    print("capturing (%s, every %ss) -> %s ; Ctrl-C to stop. DUMMY projects only on screen." %
          (g.backend, args.interval, run), flush=True)
    end = time.time() + args.hours * 3600 if args.hours else None
    prev = None
    prev_fulls = []
    prev_kept = False
    prev_t = None
    last_key = 0.0
    n = changes = 0
    try:
        while end is None or time.time() < end:
            t0 = time.time()
            row = {"epoch": "%.3f" % t0, "local": common.iso_local(t0), "note": ""}
            try:
                idle = idle_seconds()
                frame, fulls = g.grab()
                d = imgdiff.diff(prev, frame, masks)
                row.update({"idle_sec": "" if idle is None else "%.1f" % idle, "changed": int(d["changed"]),
                            "rows": d["rows"], "bbox": "" if not d["bbox"] else "%d,%d,%d,%d" % d["bbox"],
                            "area": d["area"], "w": frame.w, "h": frame.h, "file": ""})
                gap = (t0 - prev_t) if prev_t else args.interval
                if d["changed"]:
                    changes += 1
                    if not user_input_since(idle, gap):     # keep evidence only for changes nobody caused
                        if not prev_kept and prev is not None:
                            keeper.keep(prev_t, prev, prev_fulls, "before")
                        row["file"] = keeper.keep(t0, frame, fulls, "change")
                        prev_kept = True
                    else:
                        prev_kept = False
                else:
                    prev_kept = False
                if t0 - last_key >= args.keyframe_min * 60:
                    row["file"] = row["file"] or keeper.keep(t0, frame, [], "key")
                    last_key = t0
                    if n == 0 and args.confirm and sys.stdin.isatty():
                        ans = common.ask("Open %s now: does it show the Resolve UI (not wallpaper, lock screen or "
                                         "a black frame)? y/n" % os.path.join(run, row["file"] or "frames"), "n")
                        meta["first_frame_confirmed"] = ans.lower().startswith("y")
                        common.write_json(os.path.join(run, "capture_meta.json"), meta)
                        if not meta["first_frame_confirmed"]:
                            common.die("first frame does not show Resolve: fix Screen Recording permission / "
                                       "display / lock screen, then start again with a new --out")
                # keep the full frames of this capture as the next "before" (mac only)
                keep_dir = os.path.join(g.tmp, "prev")
                shutil.rmtree(keep_dir, ignore_errors=True)
                prev_fulls = []
                if fulls:
                    os.makedirs(keep_dir)
                    for i, fsrc in enumerate(fulls):
                        dst = os.path.join(keep_dir, "p%d.png" % i)
                        os.replace(fsrc, dst)
                        prev_fulls.append(dst)
                prev, prev_t = frame, t0
                n += 1
                if keeper.stopped and keeper.stopped not in (meta.get("disk_stop") or ""):
                    meta["disk_stop"] = keeper.stopped
                    common.write_json(os.path.join(run, "capture_meta.json"), meta)
                    row["note"] = keeper.stopped
                    print("WARNING: %s - frames are no longer kept (the CSV continues)" % keeper.stopped, flush=True)
            except SystemExit:
                raise
            except Exception as e:          # one failed capture is logged, never fatal
                row["note"] = "grab_error:%s" % type(e).__name__
            row["cap_ms"] = int((time.time() - t0) * 1000)
            try:
                common.append_csv(csv_path, row, FIELDS)
            except OSError:
                pass
            time.sleep(max(0.0, args.interval - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        g.close()
        meta["stopped"] = common.iso_local(time.time())
        try:
            common.write_json(os.path.join(run, "capture_meta.json"), meta)
        except OSError:
            pass
    print("stopped: %d frames, %d changes. Next: screen_watch.py analyze --run %s" % (n, changes, run))
    return 0


def replay(args):
    """Same diff pipeline over saved frames listed in a CSV (epoch,idle_sec,path)."""
    run = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(run, exist_ok=True)
    csv_path = os.path.join(run, "capture.csv")
    if os.path.exists(csv_path):
        os.remove(csv_path)
    masks = [imgdiff.parse_rect(m) for m in args.mask]
    base = os.path.dirname(os.path.abspath(args.list))
    prev = None
    for r in common.read_csv(args.list):
        t = float(r["epoch"])
        p = r["path"] if os.path.isabs(r["path"]) else os.path.join(base, r["path"])
        frame = imgdiff.read_image(p)
        d = imgdiff.diff(prev, frame, masks, tol=args.tol)
        common.append_csv(csv_path, {
            "epoch": "%.3f" % t, "local": common.iso_local(t), "idle_sec": r.get("idle_sec", ""),
            "changed": int(d["changed"]), "rows": d["rows"],
            "bbox": "" if not d["bbox"] else "%d,%d,%d,%d" % d["bbox"], "area": d["area"],
            "w": frame.w, "h": frame.h, "file": p, "cap_ms": "", "note": "replay"}, FIELDS)
        prev = frame
    print("replayed -> %s" % csv_path)
    return 0


# ---------------------------------------------------------------- analysis
def classify(rows, turns, window=2.0, no_operator=False, ignore=(), slack=0.5):
    """rows: capture.csv dicts. Returns (events, stats). Pure function (unit-tested).
    Verdicts: FAIL (unexplained change at a turn), CHECK (idle unknown at a turn), EXPLAIN (unexplained change away
    from turns), REVIEW (a user change that overlaps a turn: the test lead looks at it), ok."""
    events = []
    prev_t = None
    frames = unknown_idle = errors = 0
    for r in rows:
        if not r.get("epoch"):
            continue
        note = r.get("note") or ""
        if "error" in note:
            errors += 1
            continue
        t = float(r["epoch"])
        frames += 1
        idle = r.get("idle_sec")
        idle = float(idle) if idle not in (None, "") else None
        if idle is None:
            unknown_idle += 1
        if str(r.get("changed")) != "1" or prev_t is None:
            prev_t = t
            continue
        bbox = tuple(int(x) for x in r["bbox"].split(",")) if r.get("bbox") else None
        w, h = int(r.get("w") or 0), int(r.get("h") or 0)
        if bbox and w and any(imgdiff.bbox_inside(bbox, rect, w, h) for rect in ignore):
            prev_t = t
            continue
        gap = t - prev_t
        if no_operator:
            kind = "unexplained"
        elif idle is None:
            kind = "unknown_idle"
        elif user_input_since(idle, gap, slack):
            kind = "user"
        else:
            kind = "unexplained"
        hit = [tu for tu in turns if prev_t <= tu["end"] + window and t >= tu["start"] - window]
        ev = {"epoch": t, "local": r.get("local"), "kind": kind, "idle_sec": idle, "gap": round(gap, 2),
              "bbox": r.get("bbox"), "area": r.get("area"), "file": r.get("file"), "at_turn": bool(hit),
              "turn": (hit[0].get("source", "") + ":" + str(hit[0].get("reason") or hit[0].get("result") or ""))
              if hit else ""}
        if kind == "unexplained":
            ev["verdict"] = "FAIL" if hit else "EXPLAIN"
        elif kind == "unknown_idle":
            ev["verdict"] = "CHECK" if hit else "EXPLAIN"
        else:
            ev["verdict"] = "REVIEW" if hit else "ok"
        events.append(ev)
        prev_t = t
    stats = {"frames": frames, "errors": errors, "unknown_idle_frames": unknown_idle, "changes": len(events)}
    for k, v in (("fail", "FAIL"), ("check", "CHECK"), ("explain", "EXPLAIN"), ("review", "REVIEW"), ("user", "ok")):
        stats[k] = sum(1 for e in events if e["verdict"] == v)
    return events, stats


def coverage(rows, turns, meta, max_gap=None):
    """Problems that make a run unusable as evidence: short run, many grab errors, turns not seen on screen."""
    good = [float(r["epoch"]) for r in rows if r.get("epoch") and "error" not in (r.get("note") or "")]
    problems = []
    if not good:
        return ["no frames"]
    interval = float(meta.get("interval") or 2.0)
    max_gap = max_gap or max(3 * interval, 10.0)
    span = good[-1] - good[0]
    hours = float(meta.get("hours") or 0)
    if hours and span < 0.95 * hours * 3600:
        problems.append("capture covers %.2f h of the planned %.2f h" % (span / 3600.0, hours))
    errs = len(rows) - len(good)
    if rows and errs > 0.01 * len(rows):
        problems.append("%d of %d captures failed" % (errs, len(rows)))
    gaps = [(a, b) for a, b in zip(good, good[1:]) if b - a > max_gap]
    unseen = [tu for tu in turns if any(tu["start"] < b and tu["end"] > a for a, b in gaps)]   # any overlap
    if unseen:
        problems.append("%d agent turn(s) fell in capture gaps > %.0f s" % (len(unseen), max_gap))
    if meta.get("first_frame_confirmed") is not True:
        problems.append("nobody confirmed the first frame shows Resolve (not wallpaper / lock screen): look at the "
                        "first key frame and re-run analyze with --first-frame-ok")
    if not hours:
        problems.append("no planned length (capture_meta.json missing or --hours 0): pass --planned-hours")
    if meta.get("disk_stop"):
        problems.append("evidence frames stopped (%s): unexplained changes after that have no frame" % meta["disk_stop"])
    return problems


def hotspots(events, top=8):
    """Most frequent change boxes (helps choose --mask / --ignore from the agent-OFF baseline)."""
    c = {}
    for e in events:
        if e["bbox"]:
            c[e["bbox"]] = c.get(e["bbox"], 0) + 1
    return sorted(c.items(), key=lambda kv: -kv[1])[:top]


def analyze(args):
    run = os.path.abspath(os.path.expanduser(args.run))
    rows = common.read_csv(os.path.join(run, "capture.csv"))
    if not rows:
        common.die("empty capture.csv")
    try:
        meta = prefs_load(os.path.join(run, "capture_meta.json"))
    except (OSError, ValueError):
        meta = {}
    if args.first_frame_ok:
        meta["first_frame_confirmed"] = True
    if args.planned_hours:
        meta["hours"] = args.planned_hours
    good = [r for r in rows if r.get("epoch") and "error" not in (r.get("note") or "")]
    if not good:
        common.die("no successful captures in %s" % run)
    t_first, t_last = float(good[0]["epoch"]), float(good[-1]["epoch"])
    turns = []
    log_turns = []
    if args.agent_log:
        log_turns = [t for t in common.agent_log_turns(args.agent_log, args.tz_offset)
                     if t_first - 60 <= t["start"] <= t_last + 60]
        # a turn that printed None stayed silent (kill switch / no lease / server unreachable): not evidence
        turns += [t for t in log_turns if t.get("result", "").strip() != "None"]
    recs = []
    if args.records:
        if not args.host:
            common.die("--records needs --host")
        recs = common.load_records(args.records, args.host, t_first - 120, t_last + 120)
        turns += common.record_turns(recs)
    ignore = [imgdiff.parse_rect(m) for m in args.ignore]
    events, st = classify(rows, turns, args.window, args.no_operator, ignore)
    bad_recs = [r for r in recs if common.reason_of(r) == "forbidden_call"
                or (common.reason_of(r) == "state_changed_during_scan" and (r.get("source") or {}).get("suspect_agent"))
                or (r.get("agent") or {}).get("tripped")]
    scans = sum(1 for r in recs if common.reason_of(r) == "scan" and t_first <= r["_t"] <= t_last)
    cov = coverage(rows, turns, meta)
    incomplete = list(cov)
    if not recs:
        incomplete.append("no server records for this host (--records/--host): the agent's activity is unproven")
    elif scans < args.min_scans:
        incomplete.append("only %d full scans in the period (need >= %d): the agent was not really exercised"
                          % (scans, args.min_scans))
    if st["fail"] or st["check"] or bad_recs:
        verdict = "FAIL"
    elif incomplete:
        verdict = "INCOMPLETE"
    elif st["explain"]:
        verdict = "PENDING"
    else:
        verdict = "PASS"
    hours = (t_last - t_first) / 3600.0
    silent = len(log_turns) - len([t for t in log_turns if t.get("result", "").strip() != "None"])
    lines = ["# Screen-change analysis: %s" % os.path.basename(run), "",
             "**%s**  (S1/S2/S3: 0 unexplained screen changes at agent turns; no breaker / forbidden / suspect "
             "records; the agent really scanned; the whole period was captured)" % verdict, "",
             "- period: %s -> %s (%.2f h), %d frames (%d capture errors, %d with unknown idle), mode: %s"
             % (good[0]["local"], good[-1]["local"], hours, st["frames"], st["errors"], st["unknown_idle_frames"],
                "no-operator (idle soak)" if args.no_operator else "operator working"),
             "- agent turns used: %d (agent.log non-silent %d, silent 'None' %d ignored; records %d, full scans %d)"
             % (len(turns), len(log_turns) - silent, silent, len(recs), scans),
             "- changes: %d -> FAIL %d, CHECK %d, EXPLAIN %d, REVIEW %d, user %d"
             % (st["changes"], st["fail"], st["check"], st["explain"], st["review"], st["user"]),
             "- records with forbidden_call / suspect state change / breaker tripped: %d" % len(bad_recs)]
    for p in incomplete:
        lines.append("- INCOMPLETE: %s" % p)
    if st["explain"]:
        lines.append("- PENDING: each EXPLAIN change must be explained by the test lead (Resolve autosave, "
                     "notification, clock...) and compared with the agent-OFF baseline; the step passes only then.")
    if st["review"]:
        lines.append("- REVIEW: user-caused changes that overlap an agent turn; the test lead looks at the frames "
                     "and the operator's account (detection is weak while someone works: see CHECKLIST S1/S2).")
    lines += ["", "| time | verdict | kind | idle s | gap s | bbox | at turn | frame |", "|---|---|---|---|---|---|---|---|"]
    for e in events:
        if e["verdict"] != "ok" or args.all:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (e["local"], e["verdict"], e["kind"],
                                                                     e["idle_sec"], e["gap"], e["bbox"], e["turn"],
                                                                     e["file"]))
    hs = hotspots([e for e in events if e["verdict"] != "ok"])
    if hs:
        lines += ["", "Most frequent change boxes (x0,y0,x1,y1 px at the capture width): " +
                  "; ".join("%s x%d" % kv for kv in hs)]
    if bad_recs:
        lines += ["", "Bad records:"] + ["- %s %s" % (r.get("ts"), common.reason_of(r)) for r in bad_recs[:20]]
    report = "\n".join(lines) + "\n"
    with open(os.path.join(run, "analysis.md"), "w", encoding="utf-8") as f:
        f.write(report)
    common.write_json(os.path.join(run, "analysis.json"), {"verdict": verdict, "ok": verdict == "PASS", "stats": st,
                                                           "bad_records": len(bad_recs), "scans": scans,
                                                           "incomplete": incomplete, "turns": len(turns),
                                                           "events": events})
    print(report)
    return 0 if verdict == "PASS" else 1


def prefs_load(p):
    import json
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("capture")
    c.add_argument("--out", required=True)
    c.add_argument("--hours", type=float, default=0, help="stop after N hours (0 = until Ctrl-C)")
    c.add_argument("--interval", type=float, default=2.0)
    c.add_argument("--width", type=int, default=1920, help="compare at this width (full frames are kept too)")
    c.add_argument("--mask", action="append", default=[], help="x,y,w,h fractions to ignore (repeatable)")
    c.add_argument("--keyframe-min", type=float, default=10.0)
    c.add_argument("--backend", default="auto", choices=["auto", "mac", "ffmpeg", "import"])
    c.add_argument("--display", help="macOS screencapture -D <n> (main display = 1)")
    c.add_argument("--label", default="")
    c.add_argument("--max-gb", type=float, default=5.0, help="stop keeping frames past this many GB")
    c.add_argument("--min-free-gb", type=float, default=20.0, help="stop keeping frames below this free space")
    c.add_argument("--no-confirm", dest="confirm", action="store_false",
                   help="skip the 'first frame shows Resolve' question")
    c.add_argument("--append", action="store_true", help="continue an existing run folder")
    r = sub.add_parser("replay")
    r.add_argument("--list", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--mask", action="append", default=[])
    r.add_argument("--tol", type=int, default=0)
    a = sub.add_parser("analyze")
    a.add_argument("--run", required=True)
    a.add_argument("--agent-log")
    a.add_argument("--records", nargs="*", default=[])
    a.add_argument("--host")
    a.add_argument("--tz-offset", type=float, default=None, help="agent.log UTC offset in hours if analysed elsewhere")
    a.add_argument("--window", type=float, default=2.0)
    a.add_argument("--no-operator", action="store_true", help="idle soak: nobody touches the machine")
    a.add_argument("--ignore", action="append", default=[], help="x,y,w,h fractions: drop changes fully inside")
    a.add_argument("--all", action="store_true", help="also list user changes")
    a.add_argument("--min-scans", type=int, default=10, help="full agent scans required in the period")
    a.add_argument("--first-frame-ok", action="store_true",
                   help="the test lead looked at the first key frame and it shows Resolve")
    a.add_argument("--planned-hours", type=float, default=0, help="planned length when capture had no --hours")
    args = ap.parse_args(argv)
    if args.cmd == "capture":
        return capture(args)
    if args.cmd == "replay":
        return replay(args)
    if args.cmd == "analyze":
        return analyze(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

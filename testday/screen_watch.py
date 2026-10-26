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

capture: every --interval s it grabs the screen (macOS `screencapture` + `sips`; Linux X11 `ffmpeg -f x11grab`
or ImageMagick `import`), downsizes to --width, compares with the previous frame (exact, row by row; masked
areas ignored) and writes one CSV row: time, OS idle seconds, changed?, bounding box. Full frames are kept
only around a change (before + after) and once every --keyframe-min minutes, so an 8 h soak stays small.
It runs at low priority and writes only inside --out.

analyze: a change is "user" when the OS idle time shows input since the previous frame; otherwise it is
UNEXPLAINED. An unexplained change overlapping an agent turn window (turn start - 2 s .. turn end + 2 s; turns
from agent.log and/or the server records) is a FAIL. Unexplained changes away from turns are listed for the
operator to explain (compare with the agent-OFF baseline run). With --no-operator (S3 idle soak: nobody touches
the machine) every change counts as unexplained, whatever the idle reading says.

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
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True,
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
        full = os.path.join(self.tmp, "full.png")
        if self.backend == "mac":
            cmd = ["screencapture", "-x", "-t", "png"]
            if self.display:
                cmd += ["-D", str(self.display)]
            subprocess.run(cmd + [full], check=True, timeout=30)
            small = os.path.join(self.tmp, "small.bmp")
            subprocess.run(["sips", "-s", "format", "bmp", "--resampleWidth", str(self.width), full, "--out", small],
                           check=True, timeout=30, capture_output=True)
            with open(small, "rb") as f:
                return imgdiff.read_bmp(f.read()), full
        if self.backend == "ffmpeg":
            disp = os.environ.get("DISPLAY", ":0")
            cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "x11grab", "-i", disp,
                   "-frames:v", "1", "-y", full,
                   "-vf", "scale=%d:-1:flags=area" % self.width, "-frames:v", "1", "-f", "image2pipe",
                   "-c:v", "bmp", "-"]
            r = subprocess.run(cmd, check=True, timeout=30, capture_output=True)
            return imgdiff.read_bmp(r.stdout), full
        if self.backend == "import":
            r = subprocess.run(["import", "-silent", "-window", "root", "-resize", "%dx" % self.width, "bmp3:-"],
                               check=True, timeout=30, capture_output=True)
            return imgdiff.read_bmp(r.stdout), None
        raise ValueError(self.backend)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _keep(run, epoch, frame, full, tag):
    name = "%s_%s.png" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(epoch)), tag)
    dst = os.path.join(run, "frames", name)
    if full and os.path.exists(full):
        shutil.copyfile(full, dst)
    else:
        with open(dst, "wb") as f:
            f.write(imgdiff.write_png(frame))
    return os.path.join("frames", name)


def capture(args):
    run = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.join(run, "frames"), exist_ok=True)
    csv_path = os.path.join(run, "capture.csv")
    masks = [imgdiff.parse_rect(m) for m in args.mask]
    try:
        os.nice(10)
    except Exception:
        pass
    g = Grabber(args.backend, args.width, args.display)
    common.write_json(os.path.join(run, "capture_meta.json"), {
        "kit_version": common.KIT_VERSION, "host": os.uname()[1], "backend": g.backend, "width": args.width,
        "interval": args.interval, "masks": args.mask, "started": common.iso_local(time.time()),
        "hours": args.hours, "label": args.label})
    print("capturing (%s, every %ss) -> %s ; Ctrl-C to stop. DUMMY projects only on screen." %
          (g.backend, args.interval, run), flush=True)
    end = time.time() + args.hours * 3600 if args.hours else None
    prev = prev_full_kept = None
    prev_tmp = os.path.join(g.tmp, "prev.png")
    last_key = 0.0
    n = changes = 0
    try:
        while end is None or time.time() < end:
            t0 = time.time()
            idle = idle_seconds()
            try:
                frame, full = g.grab()
            except Exception as e:      # one failed grab is logged, never fatal
                common.append_csv(csv_path, {"epoch": "%.3f" % t0, "local": common.iso_local(t0),
                                             "note": "grab_error:%s" % type(e).__name__}, FIELDS)
                time.sleep(args.interval)
                continue
            d = imgdiff.diff(prev, frame, masks)
            row = {"epoch": "%.3f" % t0, "local": common.iso_local(t0),
                   "idle_sec": "" if idle is None else "%.1f" % idle, "changed": int(d["changed"]),
                   "rows": d["rows"], "bbox": "" if not d["bbox"] else "%d,%d,%d,%d" % d["bbox"],
                   "area": d["area"], "w": frame.w, "h": frame.h, "file": "", "note": ""}
            if d["changed"]:
                changes += 1
                if prev_full_kept is None and prev is not None:   # keep the "before" frame once
                    src = prev_tmp if os.path.exists(prev_tmp) else None
                    _keep(run, t0 - args.interval, prev, src, "before")
                row["file"] = _keep(run, t0, frame, full, "change")
                prev_full_kept = True
            else:
                prev_full_kept = None
            if t0 - last_key >= args.keyframe_min * 60:
                row["file"] = row["file"] or _keep(run, t0, frame, full, "key")
                last_key = t0
            if full and os.path.exists(full):
                os.replace(full, prev_tmp)
            row["cap_ms"] = int((time.time() - t0) * 1000)
            common.append_csv(csv_path, row, FIELDS)
            prev = frame
            n += 1
            time.sleep(max(0.0, args.interval - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        g.close()
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
    """rows: capture.csv dicts. Returns (events, stats). Pure function (unit-tested)."""
    events = []
    prev_t = None
    frames = 0
    for r in rows:
        if not r.get("epoch") or (r.get("note") or "").startswith("grab_error"):
            continue
        t = float(r["epoch"])
        frames += 1
        if str(r.get("changed")) != "1" or prev_t is None:
            prev_t = t
            continue
        bbox = tuple(int(x) for x in r["bbox"].split(",")) if r.get("bbox") else None
        w, h = int(r.get("w") or 0), int(r.get("h") or 0)
        if bbox and w and any(imgdiff.bbox_inside(bbox, rect, w, h) for rect in ignore):
            prev_t = t
            continue
        idle = r.get("idle_sec")
        idle = float(idle) if idle not in (None, "") else None
        gap = t - prev_t
        if no_operator:
            kind = "unexplained"
        elif idle is None:
            kind = "unknown_idle"
        elif idle < gap + slack:
            kind = "user"
        else:
            kind = "unexplained"
        hit = [tu for tu in turns if prev_t <= tu["end"] + window and t >= tu["start"] - window]
        ev = {"epoch": t, "local": r.get("local"), "kind": kind, "idle_sec": idle, "gap": round(gap, 2),
              "bbox": r.get("bbox"), "area": r.get("area"), "file": r.get("file"),
              "at_turn": bool(hit), "turn": (hit[0].get("source", "") + ":" + str(hit[0].get("reason") or
                                                                                 hit[0].get("result") or ""))
              if hit else ""}
        if kind in ("unexplained", "unknown_idle") and hit:
            ev["verdict"] = "FAIL" if kind == "unexplained" else "CHECK"
        elif kind in ("unexplained", "unknown_idle"):
            ev["verdict"] = "EXPLAIN"
        else:
            ev["verdict"] = "ok"
        events.append(ev)
        prev_t = t
    stats = {"frames": frames, "changes": len(events),
             "fail": sum(1 for e in events if e["verdict"] == "FAIL"),
             "check": sum(1 for e in events if e["verdict"] == "CHECK"),
             "explain": sum(1 for e in events if e["verdict"] == "EXPLAIN"),
             "user": sum(1 for e in events if e["verdict"] == "ok")}
    return events, stats


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
    t_first = float(rows[0]["epoch"])
    t_last = float(rows[-1]["epoch"])
    turns = []
    if args.agent_log:
        turns += [t for t in common.agent_log_turns(args.agent_log, args.tz_offset)
                  if t_first - 60 <= t["start"] <= t_last + 60]
    recs = []
    if args.records:
        recs = common.load_records(args.records, args.host, t_first - 120, t_last + 120)
        turns += common.record_turns(recs)
    ignore = [imgdiff.parse_rect(m) for m in args.ignore]
    events, st = classify(rows, turns, args.window, args.no_operator, ignore)
    bad_recs = [r for r in recs if common.reason_of(r) == "forbidden_call"
                or (common.reason_of(r) == "state_changed_during_scan" and (r.get("source") or {}).get("suspect_agent"))
                or (r.get("agent") or {}).get("tripped")]
    scans = sum(1 for r in recs if common.reason_of(r) == "scan")
    hours = (t_last - t_first) / 3600.0
    ok = st["fail"] == 0 and not bad_recs and (st["check"] == 0)
    lines = ["# Screen-change analysis: %s" % os.path.basename(run), "",
             "- period: %s -> %s (%.2f h), %d frames, mode: %s" % (rows[0]["local"], rows[-1]["local"], hours,
                                                                    st["frames"], "no-operator (idle soak)"
                                                                    if args.no_operator else "operator working"),
             "- agent turns seen: %d (agent.log %s, records %d, of which full scans %d)"
             % (len(turns), "yes" if args.agent_log else "no", len(recs), scans),
             "- changes: %d (user %d, unexplained away from turns %d, unexplained AT a turn %d, idle unknown at a "
             "turn %d)" % (st["changes"], st["user"], st["explain"], st["fail"], st["check"]),
             "- records with forbidden_call / suspect state change / breaker tripped: %d" % len(bad_recs),
             "", "**%s**" % common.verdict_line(ok, "0 unexplained screen changes at agent turns; no breaker/forbidden"
                                                  " records (S1/S2/S3 threshold)"), ""]
    if not turns:
        lines.append("WARNING: no agent turns found in the period - was the agent on? (pass --agent-log/--records)")
        ok = False
    if st["explain"]:
        lines.append("Unexplained changes away from agent turns must each be explained by the test lead "
                     "(Resolve autosave, notification, clock...) and compared with the agent-OFF baseline.")
    lines += ["", "| time | verdict | kind | idle s | gap s | bbox | at turn | frame |", "|---|---|---|---|---|---|---|---|"]
    for e in events:
        if e["verdict"] != "ok" or args.all:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (e["local"], e["verdict"], e["kind"],
                                                                     e["idle_sec"], e["gap"], e["bbox"], e["turn"],
                                                                     e["file"]))
    hs = hotspots([e for e in events if e["verdict"] != "ok"])
    if hs:
        lines += ["", "Most frequent unexplained change boxes (x0,y0,x1,y1 px at the capture width): " +
                  "; ".join("%s x%d" % kv for kv in hs)]
    if bad_recs:
        lines += ["", "Bad records:"] + ["- %s %s" % (r.get("ts"), common.reason_of(r)) for r in bad_recs[:20]]
    report = "\n".join(lines) + "\n"
    with open(os.path.join(run, "analysis.md"), "w", encoding="utf-8") as f:
        f.write(report)
    common.write_json(os.path.join(run, "analysis.json"), {"ok": ok, "stats": st, "bad_records": len(bad_recs),
                                                           "turns": len(turns), "events": events})
    print(report)
    return 0 if ok else 1


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

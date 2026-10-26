"""Shared helpers for the phase-3 test-day kit (stdlib only, Python >= 3.8).

The kit runs only on a TEST workstation, by a person, on the test day, with DUMMY projects.
Nothing in testday/ talks to DaVinci Resolve's scripting API (a test enforces this): every
Resolve action on the test day is done by the operator in the Resolve UI.
"""
import csv
import datetime as _dt
import json
import math
import os
import re
import sys
import time

KIT_VERSION = "testday-1"


def now_epoch():
    return time.time()


def iso_local(epoch):
    return _dt.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="milliseconds")


def parse_iso(s):
    """ISO 8601 (with or without tz; naive = local time) -> epoch seconds, or None."""
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        return time.mktime(d.timetuple()) + d.microsecond / 1e6
    return d.timestamp()


# ---------- agent turns ----------
_POLL_RE = re.compile(r"^--- (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) poll ---")


def agent_log_turns(path, tz_offset_h=None):
    """Turn start times from ~/.editortrack/agent.log (run-agent.sh writes '--- <local time> poll ---').
    The log is in the WORKSTATION's local time; pass tz_offset_h (e.g. 7) when analysing elsewhere."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    for i, line in enumerate(lines):
        m = _POLL_RE.match(line)
        if not m:
            continue
        d = _dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        if tz_offset_h is None:
            t = time.mktime(d.timetuple())
        else:
            t = d.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=tz_offset_h))).timestamp()
        result = lines[i + 1].strip() if i + 1 < len(lines) else ""
        out.append({"start": t, "end": t + 10.0, "source": "agent.log", "result": result})
    return out


def load_records(paths, host=None, t0=None, t1=None):
    """Server records (server data/<day>.jsonl, copied read-only from the server) for one host."""
    recs = []
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or (host and r.get("host") != host):
                    continue
                t = parse_iso(r.get("ts"))
                if t is None or (t0 is not None and t < t0) or (t1 is not None and t > t1):
                    continue
                r["_t"] = t
                r["_bytes"] = len(line.encode("utf-8"))
                recs.append(r)
    recs.sort(key=lambda r: r["_t"])
    return recs


def record_turns(recs):
    """Turn windows from server records: [ts - turn_ms - 2 s, ts + 2 s] (ts is set after the scan)."""
    out = []
    for r in recs:
        a = r.get("agent") or {}
        turn_s = (a.get("turn_ms") or 0) / 1000.0
        src = r.get("source") or {}
        g = (a.get("guard") or src.get("guard") or {})
        out.append({"start": r["_t"] - turn_s, "end": r["_t"], "source": "record",
                    "reason": src.get("reason") or ("ok" if src.get("ok") else None),
                    "calls": g.get("calls"), "guard_ms": g.get("ms")})
    return out


def guard_of(r):
    a = r.get("agent") or {}
    src = r.get("source") or {}
    return a.get("guard") or src.get("guard") or {}


def reason_of(r):
    src = r.get("source") or {}
    if src.get("ok"):
        return "scan"
    return src.get("reason") or "unknown"


# ---------- stats ----------
def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    xs = list(xs)
    if len(xs) < 2:
        return 0.0 if xs else None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def percentile(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


# ---------- output ----------
def append_csv(path, row, fields):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def verdict_line(ok, label):
    return "%s  %s" % ("PASS" if ok else "FAIL", label)


def die(msg, code=2):
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(code)


def ask(prompt, default=None):
    """Interactive prompt (operator on the test day). Empty input -> default."""
    try:
        s = input("%s%s: " % (prompt, " [%s]" % default if default is not None else ""))
    except EOFError:
        s = ""
    return s.strip() or (default if default is not None else "")

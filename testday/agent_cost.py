#!/usr/bin/env python3
"""P4 agent cost sampler: CPU time of the agent turns and the size of ~/.editortrack, every 5 s for 1 h.

    python3 testday/agent_cost.py --minutes 60 --csv ~/ET_TESTDAY/P4_grade01.csv

Each agent turn is a short-lived process, so sampling `ps` would miss most of them. Instead:
  Linux : systemd writes "editortrack-agent.service: Consumed <t> CPU time" to the user journal when each
          turn ends; the sampler sums those lines since it started. (No such lines? use `pidstat -C python 5`.)
  macOS : the launchd KeepAlive loop (deploy/run-loop.sh) is the parent of every turn; `ps -S` adds the CPU time
          of exited children to the parent, so its growth is the agent's CPU.
RSS per turn comes from the heartbeat (`turn_stats.py`, rss_mb), network from record sizes (turn_stats).
PASS (with turn_stats): average CPU < 0.5 % of one core, every turn < 2 s CPU, ~/.editortrack < 15 MB.
Read-only: it runs `journalctl --user` / `ps` and walks ~/.editortrack; nothing else.
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

FIELDS = ["epoch", "local", "cpu_total_s", "turns", "last_turn_cpu_s", "state_bytes", "note"]


def dir_bytes(d):
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def parse_ps_time(s):
    """ps TIME: [[dd-]hh:]mm:ss[.cc] -> seconds."""
    s = s.strip()
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [float(x) for x in s.split(":")]
    v = 0.0
    for p in parts:
        v = v * 60 + p
    return days * 86400 + v


def parse_timespan(s):
    """systemd timespan as in 'Consumed 1min 2.345s CPU time' -> seconds ('312ms', '1.2s', '45us', '1h 2min')."""
    units = {"us": 1e-6, "ms": 1e-3, "s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0}
    total = 0.0
    for tok in s.split():
        num = tok.rstrip("abcdefghijklmnopqrstuvwxyz")
        unit = tok[len(num):]
        if not num or unit not in units:
            return None
        total += float(num) * units[unit]
    return total


def parse_consumed(journal_text):
    """CPU seconds per finished run from systemd's 'editortrack-agent.service: Consumed X CPU time' lines."""
    out = []
    for line in journal_text.splitlines():
        if "Consumed" not in line or "CPU time" not in line:
            continue
        span = line.split("Consumed", 1)[1].split("CPU time", 1)[0]
        v = parse_timespan(span)
        if v is not None:
            out.append(v)
    return out


class LinuxProbe(object):
    """systemd logs 'Consumed <t> CPU time' when each oneshot turn ends (user journal)."""

    def __init__(self):
        self.since = time.strftime("%Y-%m-%d %H:%M:%S")

    def sample(self):
        out = subprocess.run(["journalctl", "--user", "-u", "editortrack-agent.service", "-o", "cat",
                              "--since", self.since, "--no-pager"], capture_output=True, text=True, timeout=20).stdout
        runs = parse_consumed(out)
        return sum(runs), len(runs), (runs[-1] if runs else None)


class MacProbe(object):
    def __init__(self):
        self.base = None
        self.last = 0.0
        self.turns = 0
        self.prev = None

    def sample(self):
        out = subprocess.run(["ps", "-S", "-A", "-o", "pid=,time=,command="], capture_output=True, text=True,
                             timeout=10).stdout
        cpu = None
        for line in out.splitlines():
            if "deploy/run-loop.sh" in line:
                parts = line.split(None, 2)
                cpu = (cpu or 0.0) + parse_ps_time(parts[1])
        if cpu is None:
            return None, self.turns, None
        if self.base is None:
            self.base = cpu
        total = cpu - self.base
        if self.prev is not None and total > self.prev:
            self.turns += 1
            self.last = total - self.prev
        self.prev = total
        return total, self.turns, self.last


def summarize(rows, minutes):
    cpu = [float(r["cpu_total_s"]) for r in rows if r.get("cpu_total_s") not in (None, "")]
    per = [float(r["last_turn_cpu_s"]) for r in rows if r.get("last_turn_cpu_s") not in (None, "")]
    st = [int(r["state_bytes"]) for r in rows if r.get("state_bytes") not in (None, "")]
    total = (cpu[-1] - cpu[0]) if len(cpu) > 1 else None
    avg_pct = (100.0 * total / (minutes * 60.0)) if total is not None and minutes else None
    checks = {"avg_cpu_below_0.5%_of_one_core": avg_pct is not None and avg_pct < 0.5,
              "every_turn_below_2s_cpu": bool(per) and max(per) < 2.0,
              "state_dir_below_15MB": bool(st) and max(st) < 15 * 1024 * 1024}
    return {"cpu_s": total, "avg_cpu_pct": avg_pct, "max_turn_cpu_s": max(per) if per else None,
            "max_state_bytes": max(st) if st else None, "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--every", type=float, default=5)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--state-dir", default="~/.editortrack")
    a = ap.parse_args(argv)
    state = os.path.expanduser(a.state_dir)
    probe = MacProbe() if sys.platform == "darwin" else LinuxProbe()
    end = time.time() + a.minutes * 60
    rows = []
    print("sampling every %ss for %s min -> %s" % (a.every, a.minutes, a.csv))
    try:
        while time.time() < end:
            t = time.time()
            try:
                total, turns, last = probe.sample()
                note = ""
            except Exception as e:
                total, turns, last, note = None, None, None, "probe_error:%s" % type(e).__name__
            row = {"epoch": "%.3f" % t, "local": common.iso_local(t),
                   "cpu_total_s": "" if total is None else "%.3f" % total, "turns": turns,
                   "last_turn_cpu_s": "" if last is None else "%.3f" % last,
                   "state_bytes": dir_bytes(state) if os.path.isdir(state) else 0, "note": note}
            rows.append(row)
            common.append_csv(a.csv, row, FIELDS)
            time.sleep(max(0.0, a.every - (time.time() - t)))
    except KeyboardInterrupt:
        pass
    minutes = (float(rows[-1]["epoch"]) - float(rows[0]["epoch"])) / 60.0 if len(rows) > 1 else 0
    s = summarize(rows, minutes)
    print("agent CPU %.2f s over %.1f min = %.3f %% of one core; max per turn %s s; ~/.editortrack max %s bytes"
          % (s["cpu_s"] or 0, minutes, s["avg_cpu_pct"] or 0, s["max_turn_cpu_s"], s["max_state_bytes"]))
    for k, v in s["checks"].items():
        print(common.verdict_line(v, k))
    return 0 if all(s["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())

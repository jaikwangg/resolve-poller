#!/usr/bin/env python3
"""P3 render time with vs without the agent (WORKSTATION_SAFETY.md 7 P3). Read-only: it only stat()s the
render output folder; it never talks to Resolve.

    # before pressing "Render All" in Resolve (one fresh output folder per run):
    python3 testday/render_timer.py watch --dir ~/ET_DUMMY_TESTDAY/renders/off1 --label off1 --agent off \
        --csv ~/ET_TESTDAY/P3_grade01.csv
    # after 3 x off and 3 x on (alternate: off on off on off on):
    python3 testday/render_timer.py analyze --csv ~/ET_TESTDAY/P3_grade01.csv [--records day.jsonl --host grade-01]

Timing = first appearance of a new file in --dir -> the last time any new file changed, both on this machine's
clock, once the folder has been stable for --settle seconds. The operator also types the render time Resolve shows for the job (Render Queue),
which is kept beside it as a cross-check.

PASS: >= 3 runs each; mean(on) - mean(off) <= 1 % of mean(off) AND |mean(on) - mean(off)| <= 2 sd(off);
every agent turn during an ON render is a skip (rendering / page_deliver), never a scan; at least one turn
in every ON render and none in any OFF render (records are required).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

FIELDS = ["label", "agent", "dir", "files", "bytes", "first_seen", "last_write", "seconds", "resolve_reported",
          "start_local", "notes"]
RENDER_SKIPS = ("rendering", "page_deliver", "user_active", "resolve_busy")


def snapshot(d):
    out = {}
    for root, _dirs, files in os.walk(d):
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[p] = (st.st_size, st.st_mtime)
    return out


def watch_dir(d, settle=10.0, poll=0.5, timeout=6 * 3600, clock=time.time, sleep=time.sleep, snap=snapshot):
    """Wait for new files in d, then until they stop changing for `settle` s. Returns timing dict."""
    before = snap(d)
    first = None
    last_change = None
    seen = {}
    t_end = clock() + timeout
    while clock() < t_end:
        now = clock()
        cur = snap(d)
        new = {p: v for p, v in cur.items() if p not in before or before[p] != v}
        if new and first is None:
            first = now
        if new and new != seen:
            seen = new
            last_change = now
        if first is not None and last_change is not None and now - last_change >= settle:
            # both ends on this machine's clock (file mtimes come from the file server's clock on a SAN/NFS)
            return {"files": len(seen), "bytes": sum(v[0] for v in seen.values()), "first_seen": first,
                    "last_write": last_change, "seconds": round(last_change - first, 2)}
        sleep(poll)
    return None


def watch(a):
    d = os.path.abspath(os.path.expanduser(a.dir))
    os.makedirs(d, exist_ok=True)
    print("watching %s - start the render in Resolve now (output to this folder). DUMMY project only." % d)
    r = watch_dir(d, a.settle)
    if not r:
        common.die("no render output seen before the timeout")
    print("render finished: %.1f s (%d file(s), %.1f MB)" % (r["seconds"], r["files"], r["bytes"] / 1e6))
    rep = common.ask("render time shown by Resolve for this job (e.g. 00:10:12), or Enter to skip", "")
    notes = common.ask("notes", "")
    common.append_csv(a.csv, dict(r, label=a.label, agent=a.agent, dir=d, resolve_reported=rep,
                                  start_local=common.iso_local(r["first_seen"]), notes=notes,
                                  first_seen="%.3f" % r["first_seen"], last_write="%.3f" % r["last_write"]), FIELDS)
    return 0


def _hms(s):
    try:
        parts = [float(x) for x in str(s).strip().split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    v = 0.0
    for p in parts:
        v = v * 60 + p
    return v


def evaluate(rows, recs=()):
    off = [float(r["seconds"]) for r in rows if r["agent"] == "off"]
    on = [float(r["seconds"]) for r in rows if r["agent"] == "on"]
    mo, mn, so = common.mean(off), common.mean(on), common.stdev(off)
    delta = (mn - mo) if (mo is not None and mn is not None) else None
    bad, on_without, off_with = [], [], []
    for r in rows:
        s, e = float(r["first_seen"]), float(r["last_write"])
        inside = [rec for rec in recs if s <= rec["_t"] <= e]
        if r["agent"] == "off":
            if inside:
                off_with.append(r.get("label"))
            continue
        if not inside:
            on_without.append(r.get("label"))
        for rec in inside:
            if common.reason_of(rec) not in RENDER_SKIPS:
                bad.append((rec.get("ts"), common.reason_of(rec)))
    checks = {
        "at_least_3_runs_each": len(off) >= 3 and len(on) >= 3,
        "mean_delta_<=1%": delta is not None and mo and delta / mo <= 0.01,
        "within_2_sd_of_off_runs": delta is not None and so is not None and abs(delta) <= 2 * so,
        "agent_skips_during_on_renders": not bad,
        "agent_turns_seen_in_every_on_render": bool(on) and not on_without,
        "no_agent_turns_in_off_renders": not off_with,
    }
    resolve = [(r["agent"], _hms(r.get("resolve_reported"))) for r in rows if _hms(r.get("resolve_reported"))]
    return {"off": off, "on": on, "mean_off": mo, "mean_on": mn, "sd_off": so, "delta_s": delta,
            "delta_pct": (100.0 * delta / mo) if (delta is not None and mo) else None, "bad_turns": bad,
            "resolve_reported": resolve, "checks": checks, "pass": all(bool(v) for v in checks.values())}


def analyze(a):
    rows = common.read_csv(a.csv)
    t0 = min(float(r["first_seen"]) for r in rows) - 60
    t1 = max(float(r["last_write"]) for r in rows) + 60
    recs = common.load_records(a.records, a.host, t0, t1)
    if not recs:
        common.die("no records for host %s during the renders: agent state unproven (check --host)" % a.host)
    res = evaluate(rows, recs)
    print("off runs %s  mean %.2f s  sd %.2f" % (res["off"], res["mean_off"] or 0, res["sd_off"] or 0))
    print("on  runs %s  mean %.2f s" % (res["on"], res["mean_on"] or 0))
    if res["delta_s"] is not None:
        print("delta %+.2f s (%+.2f %%)" % (res["delta_s"], res["delta_pct"]))
    for k, v in res["checks"].items():
        print(common.verdict_line(bool(v), k))
    for b in res["bad_turns"]:
        print("  bad turn during ON render: %s %s" % b)
    if a.json:
        common.write_json(a.json, res)
    return 0 if res["pass"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("watch")
    w.add_argument("--dir", required=True)
    w.add_argument("--label", required=True)
    w.add_argument("--agent", required=True, choices=["on", "off"])
    w.add_argument("--csv", required=True)
    w.add_argument("--settle", type=float, default=10.0)
    an = sub.add_parser("analyze")
    an.add_argument("--csv", required=True)
    an.add_argument("--records", nargs="+", required=True)
    an.add_argument("--host", required=True)
    an.add_argument("--json")
    a = ap.parse_args(argv)
    if a.cmd == "watch":
        return watch(a)
    if a.cmd == "analyze":
        return analyze(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

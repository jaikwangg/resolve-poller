#!/usr/bin/env python3
"""Per-host agent turn statistics from the server records (read-only copy of server data/<day>.jsonl).

    python3 testday/turn_stats.py --records 2026-11-02.jsonl --host conform-01 [--from ISO --to ISO] \
        [--budget-ms 1500] [--json out.json]

Used for P2 (large timeline: p95 guard.ms, calls, scan_ms over >= 50 turns), P4 (RSS per turn, network per
minute from record sizes) and as a sanity check of every step (skip reasons, breaker, forbidden calls).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

HTTP_OVERHEAD_BYTES = 350   # request line + headers per POST (approx.); added to the JSON size


def stats(recs, budget_ms=1500):
    reasons = {}
    for r in recs:
        k = common.reason_of(r)
        reasons[k] = reasons.get(k, 0) + 1
    scans = [r for r in recs if common.reason_of(r) == "scan"]
    gms = [common.guard_of(r).get("ms") for r in scans if common.guard_of(r).get("ms") is not None]
    calls = [common.guard_of(r).get("calls") for r in scans if common.guard_of(r).get("calls") is not None]
    scan_ms = [((r.get("source") or {}).get("timeline") or {}).get("scan_ms") for r in scans]
    scan_ms = [x for x in scan_ms if x is not None]
    clips = [((r.get("source") or {}).get("timeline") or {}).get("video_clips") for r in scans]
    clips = [x for x in clips if x is not None]
    ag = [r.get("agent") or {} for r in recs]
    rss = [a.get("rss_mb") for a in ag if a.get("rss_mb") is not None]
    turn = [a.get("turn_ms") for a in ag if a.get("turn_ms") is not None]
    t0 = recs[0]["_t"] if recs else None
    t1 = recs[-1]["_t"] if recs else None
    minutes = max(1.0, (t1 - t0) / 60.0) if recs else None
    net = sum(r["_bytes"] + HTTP_OVERHEAD_BYTES for r in recs)
    bad = [r for r in recs if common.reason_of(r) == "forbidden_call" or (r.get("agent") or {}).get("tripped")
           or (common.reason_of(r) == "state_changed_during_scan" and (r.get("source") or {}).get("suspect_agent"))]
    skip_calls = [common.guard_of(r).get("calls") for r in recs
                  if common.reason_of(r) not in ("scan",) and common.guard_of(r).get("calls") is not None]
    out = {
        "records": len(recs), "scans": len(scans), "reasons": reasons,
        "from": common.iso_local(t0) if t0 else None, "to": common.iso_local(t1) if t1 else None,
        "guard_ms_p50": common.percentile(gms, 50), "guard_ms_p95": common.percentile(gms, 95),
        "guard_ms_max": max(gms) if gms else None,
        "calls_p95": common.percentile(calls, 95), "calls_max": max(calls) if calls else None,
        "scan_ms_p95": common.percentile(scan_ms, 95), "clips_max": max(clips) if clips else None,
        "skip_calls_max": max(skip_calls) if skip_calls else None,
        "rss_mb_max": max(rss) if rss else None, "turn_ms_max": max(turn) if turn else None,
        "net_bytes_per_min": round(net / minutes, 1) if minutes else None,
        "bad_records": len(bad), "budget_ms": budget_ms,
    }
    out["checks"] = {
        "P2_p95_guard_ms_within_budget": (out["guard_ms_p95"] is not None and out["guard_ms_p95"] <= budget_ms),
        "P2_at_least_50_scans": len(scans) >= 50,
        "P4_rss_below_100mb": (out["rss_mb_max"] is not None and out["rss_mb_max"] < 100),
        "P4_network_below_5kb_per_min": (out["net_bytes_per_min"] is not None and out["net_bytes_per_min"] < 5120),
        "no_forbidden_trip_or_suspect": not bad,
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", nargs="+", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--budget-ms", type=float, default=1500)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    recs = common.load_records(a.records, a.host, common.parse_iso(a.t0), common.parse_iso(a.t1))
    s = stats(recs, a.budget_ms)
    for k in ("records", "scans", "from", "to", "guard_ms_p50", "guard_ms_p95", "guard_ms_max", "calls_p95",
              "calls_max", "scan_ms_p95", "clips_max", "skip_calls_max", "rss_mb_max", "turn_ms_max",
              "net_bytes_per_min", "bad_records"):
        print("%-18s %s" % (k, s[k]))
    print("reasons            " + ", ".join("%s=%d" % kv for kv in sorted(s["reasons"].items())))
    for k, v in s["checks"].items():
        print(common.verdict_line(v, k))
    if a.json:
        common.write_json(a.json, s)
    return 0 if s["checks"]["no_forbidden_trip_or_suspect"] else 1


if __name__ == "__main__":
    sys.exit(main())

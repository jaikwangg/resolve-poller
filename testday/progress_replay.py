#!/usr/bin/env python3
"""V (progress v2 validation): replay the test day's records through the progress-v2 engine, offline.

During phase 3 nothing is pushed to PMS, and the server only runs the progress-v2 engine when a PMS progress path
is configured. So the values PMS *would* have received are produced afterwards from the stored records, with a
fake PMS that only collects the request bodies (no network):

    python3 testday/progress_replay.py --server-dir ~/github/editortrack/server \
        --records 2026-11-03.jsonl --host conform-01 [--job ET_DUMMY] [--csv $R/V_conform01.csv]

--server-dir is a checkout that has server/progress_v2.py (branch night/progress-v2 or main once merged); its
engine config is read from the EDITORTRACK_* environment exactly as on the server. Output: one line per value
that would have been sent (time, pct, provisional, done, done_count/total, reasons) plus the withheld counts,
and the checks below. Match the times to the R0-R9 steps written in the results.

PASS: no body with done or pct >= 100 unless total_confirmed and not provisional; every drop carries at least one
explaining reason; the engine raised no error.
"""
import argparse
import importlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

FIELDS = ["observed_at", "host", "progress_pct", "provisional", "total_confirmed", "done", "done_count", "total",
          "work_done", "reasons"]


class FakePMS(object):
    """Stands in for erp: progress_on_ingest only needs these attributes and _post_auth. Nothing leaves the
    process."""
    ERP_BASE = "http://pms.invalid"
    ERP_PROGRESS_PATH = "/api/integrations/editortrack/progress"
    ERP_PROGRESS_METHOD = "POST"

    def __init__(self):
        self.bodies = []

    def _post_auth(self, url, body, method="POST"):
        self.bodies.append(dict(body))
        return {"data": {"stored": True}}


def load_engine(server_dir):
    server_dir = os.path.abspath(os.path.expanduser(server_dir))
    if not os.path.exists(os.path.join(server_dir, "progress_v2.py")):
        common.die("%s has no progress_v2.py (use a night/progress-v2 or merged checkout)" % server_dir)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    pms_sync = importlib.import_module("pms_sync")
    pv = importlib.import_module("progress_v2")
    return pms_sync, pv


def replay(recs, pms_sync, pv, job="ET_DUMMY"):
    store = tempfile.mkdtemp(prefix="et-v-replay-")
    try:
        pms_sync._pg.update({"engine": None, "pushed": {}, "store": None})
        for k in ("sent", "rejected", "refused_locally", "errors"):
            pms_sync.progress_status[k] = 0
        pms_sync.progress_status["withheld"], pms_sync.progress_status["last"] = {}, {}
        pms = FakePMS()

        def eff(rec):
            stage = rec.get("stage") or (rec.get("progress") or {}).get("stage") or "color"
            return {"job_id": job, "title": job, "stage": stage, "source": "erp"}

        for r in recs:
            r = {k: v for k, v in r.items() if not k.startswith("_") or k == "_rx"}
            pms_sync.progress_on_ingest(r, eff, pms, store)
        status = {k: pms_sync.progress_status.get(k) for k in ("sent", "rejected", "refused_locally", "errors",
                                                               "withheld", "last_error")}
        return pms.bodies, status
    finally:
        shutil.rmtree(store, ignore_errors=True)


def check(bodies, status, explaining):
    false_done = [b for b in bodies if (b.get("done") or (b.get("progress_pct") or 0) >= 100)
                  and (b.get("provisional") or not b.get("total_confirmed"))]
    silent_drops = []
    prev = None
    for b in bodies:
        p = b.get("progress_pct")
        if prev is not None and p is not None and p < prev and not (set(b.get("reasons") or []) & set(explaining)):
            silent_drops.append(b)
        if p is not None:
            prev = p
    checks = {"values_produced": bool(bodies),
              "never_done_or_100_without_confirmed_scope": not false_done,
              "every_drop_has_an_explaining_reason": not silent_drops,
              "engine_no_errors": not status.get("errors")}
    return checks, false_done, silent_drops


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server-dir", required=True)
    ap.add_argument("--records", nargs="+", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--job", default="ET_DUMMY")
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--csv")
    a = ap.parse_args(argv)
    pms_sync, pv = load_engine(a.server_dir)
    recs = common.load_records(a.records, a.host, common.parse_iso(a.t0), common.parse_iso(a.t1))
    if not recs:
        common.die("no records for host %s (check --host: macOS may report name.local)" % a.host)
    bodies, status = replay(recs, pms_sync, pv, a.job)
    checks, false_done, silent = check(bodies, status, getattr(pv, "EXPLAINING", ()))
    print("%d records -> %d values that would be sent; withheld %s; errors %s %s" % (
        len(recs), len(bodies), status.get("withheld"), status.get("errors"), status.get("last_error") or ""))
    for b in bodies:
        print("  %s  %6.2f %%  prov=%s done=%s  %s/%s  %s" % (
            b.get("observed_at"), b.get("progress_pct") or 0, b.get("provisional"), b.get("done"),
            b.get("done_count"), b.get("total"), ",".join(b.get("reasons") or [])))
        if a.csv:
            row = dict(b, reasons=" ".join(b.get("reasons") or []))
            common.append_csv(a.csv, row, FIELDS)
    for k, v in checks.items():
        print(common.verdict_line(v, k))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

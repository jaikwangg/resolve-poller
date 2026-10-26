"""Scoring of strategy outputs against ground truth, and the invariants the
progress figure sent to PMS must keep.

score(observations, outputs) -> dict of metrics (see METRICS for meanings)
check_invariants(observations, outputs) -> [violation dict]
"""
from __future__ import annotations

EXPLAINING = {"scope_change", "scope_shrink", "rework", "regression", "replaced", "reopened",
              "scope_mismatch", "job_mismatch"}
TOL = 10.0          # points: |error| within this counts as "correct" for time-to-correct

METRICS = {
    "false_completion": "claimed done (done flag, or pct >= 100 which PMS reads as done) while truth not done",
    "fc_polls": "number of polls with a false completion",
    "fr_unexplained": "reported pct fell > 1 pt while truth did not fall, and no reason code explains it",
    "fr_explained": "same drop but labelled (scope_change, rework, ...) - PMS can show it as such",
    "max_err": "max |reported pct - true pct| over sent polls",
    "mean_err": "mean |reported - true|",
    "ttc_min": "longest episode (minutes) of error > 10 pts or false completion before it was corrected",
    "uncorrected": "an error episode was still open at the end of the scenario",
    "final_err": "|reported - true| at the last sent poll",
    "final_done_ok": "done claim at the end equals truth at the end",
    "missed_completion": "truth done at the end but strategy never claims done",
    "wrong_job": "polls attributed to a job other than the one actually worked on",
    "withheld": "polls with Resolve data but nothing sent",
    "pms_stale": "polls where the value PMS shows (latest observed_at per project+stage) is not the value just sent",
    "pms_max_err": "max |value PMS shows - true pct|",
}


def _claims_done(o):
    return bool(o["done"]) or (o["pct"] is not None and o["pct"] >= 100.0)


def score(obs, outs):
    m = {"false_completion": False, "fc_polls": 0, "fr_unexplained": 0, "fr_explained": 0,
         "max_err": 0.0, "mean_err": 0.0, "ttc_min": 0, "uncorrected": False, "final_err": None,
         "final_done_ok": None, "missed_completion": False, "wrong_job": 0, "withheld": 0, "polls": 0,
         "pms_stale": 0, "pms_max_err": 0.0}
    shown = {}               # key -> (observed_at, pct) PMS displays: DISTINCT ON stage ORDER BY observed_at DESC
    errs = []
    prev = {}                # key -> (pct, truth_pct)
    episode = None
    last_sent = None
    for o_, out in zip(obs, outs):
        if o_.get("ok", True) and not out["send"]:
            m["withheld"] += 1
        if not out["send"]:
            continue
        # judge against the truth of the job PMS was told about (wrong-job polls hurt that job)
        tbj = o_.get("truth_by_job")
        if tbj is not None:
            truth = tbj.get(out["key"].split("|")[0]) or {"pct": None, "done": False}
        else:
            truth = o_.get("truth") or {}
        tp = truth.get("pct")
        m["polls"] += 1
        if out["pct"] is not None:
            cur = shown.get(out["key"])
            ts = o_.get("ts") or ""
            if cur is None or ts >= cur[0]:
                shown[out["key"]] = (ts, out["pct"])
            if shown[out["key"]][1] != out["pct"]:
                m["pms_stale"] += 1
            if tp is not None:
                m["pms_max_err"] = round(max(m["pms_max_err"], abs(shown[out["key"]][1] - tp)), 2)
        tj = o_.get("true_job")
        if tj and out["key"].split("|")[0] != tj:
            m["wrong_job"] += 1
        fc = _claims_done(out) and not truth.get("done", False) and tp is not None
        if fc:
            m["false_completion"] = True
            m["fc_polls"] += 1
        err = None
        if tp is not None and out["pct"] is not None:
            err = abs(out["pct"] - tp)
            errs.append(err)
        p = prev.get(out["key"])
        if p is not None and out["pct"] is not None and p[0] is not None and out["pct"] < p[0] - 1.0:
            truth_fell = tp is not None and p[1] is not None and tp < p[1] - 0.5
            if not truth_fell:
                if set(out["reasons"]) & EXPLAINING:
                    m["fr_explained"] += 1
                else:
                    m["fr_unexplained"] += 1
        prev[out["key"]] = (out["pct"], tp)
        bad = fc or (err is not None and err > TOL)
        t = o_.get("t", 0)
        if bad and episode is None:
            episode = t
        elif not bad and episode is not None:
            m["ttc_min"] = max(m["ttc_min"], t - episode)
            episode = None
        last_sent = (o_, out, err)
    if episode is not None:
        m["uncorrected"] = True
        m["ttc_min"] = max(m["ttc_min"], (obs[-1].get("t", 0) if obs else 0) - episode)
    if errs:
        m["max_err"] = round(max(errs), 2)
        m["mean_err"] = round(sum(errs) / len(errs), 2)
    if last_sent:
        o_, out, err = last_sent
        truth = o_.get("truth") or {}
        m["final_err"] = None if err is None else round(err, 2)
        if truth.get("pct") is not None:
            m["final_done_ok"] = _claims_done(out) == bool(truth.get("done"))
            m["missed_completion"] = bool(truth.get("done")) and not _claims_done(out)
    return m


# ---------------- invariants ----------------
INVARIANTS = {
    "INV1_bounded": "pct is None or 0 <= pct <= 100 with at most 2 decimals (PMS NUMERIC(5,2))",
    "INV2_no_unconfirmed_done": "done or pct >= 100 only with a confirmed scope and not provisional",
    "INV3_drop_explained": "a drop > 1 pt always carries a reason code; never 'unexplained_drop'",
    "INV4_work_monotonic": "work_done never decreases for a key",
    "INV5_provisional_not_done": "provisional implies not done",
    "INV6_growth_not_regression": "a poll whose only change is added clips never carries 'regression'",
    "INV7_done_is_true": "done implies truth done (only for scenarios whose ops are all observable)",
}


def check_invariants(obs, outs, observable=False, growth_only_idx=()):
    v = []
    prev, work = {}, {}
    for idx, (o_, out) in enumerate(zip(obs, outs)):
        pct = out["pct"]
        if pct is not None and (pct < 0 or pct > 100 or round(pct, 2) != pct):
            v.append({"inv": "INV1_bounded", "i": idx, "pct": pct})
        if _claims_done(out) and (not out["total_confirmed"] or out["provisional"]):
            v.append({"inv": "INV2_no_unconfirmed_done", "i": idx, "pct": pct})
        p = prev.get(out["key"])
        if out["send"] and p is not None and pct is not None and pct < p - 1.0:
            if not set(out["reasons"]) & EXPLAINING:
                v.append({"inv": "INV3_drop_explained", "i": idx, "from": p, "to": pct, "reasons": out["reasons"]})
        if out["send"] and pct is not None:
            prev[out["key"]] = pct
        w = out.get("work_done")
        if w is not None:
            if w < work.get(out["key"], 0):
                v.append({"inv": "INV4_work_monotonic", "i": idx, "from": work[out["key"]], "to": w})
            work[out["key"]] = max(w, work.get(out["key"], 0))
        if out["provisional"] and out["done"]:
            v.append({"inv": "INV5_provisional_not_done", "i": idx})
        if idx in growth_only_idx and "regression" in out["reasons"]:
            v.append({"inv": "INV6_growth_not_regression", "i": idx})
        if observable and out["send"] and out["done"] and "truth" in o_ and not o_["truth"].get("done"):
            v.append({"inv": "INV7_done_is_true", "i": idx})
    return v

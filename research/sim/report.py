"""Tables shared by run_all (summary report) and docs (the research documents)."""
from __future__ import annotations

import statistics

from . import library, strategies

# Data each strategy depends on and whether it exists today (checked 2026-10-26 against
# agent/resolve_poller.py, server/*.py and ~/github/pms kpp-pms-api).
DATA = {
    "S0": ("clip counts, graded count, duration, offline timeline", "yes (poller today)"),
    "S1": ("same as S0 + server state", "yes"),
    "S2": ("confirmed scope per job (shot list or count / runtime)", "NO: PMS has no shot/runtime field; deliverables = name/format/quantity only"),
    "S3": ("per-item identity (clip name / unique id), kind, grade signature, media version", "partly: poller reads uid + node/LUT signature in grade_detect but does not send them; names only with EXTRACT_CLIP_META (off)"),
    "S4": ("same as S0 + server state", "yes"),
    "S5": ("graded count deltas (exact: per-item signatures)", "yes (approx) / partly (exact)"),
    "S6": ("booking start/end", "yes (PMS schedule)"),
    "C1": ("as S1", "yes"),
    "C2": ("as S3", "partly"),
    "C3": ("as S3 + confirmed scope + booking name + grade.pending + PMS task status", "partly: grade.pending yes; items need poller change; scope needs PMS field or EDL import; task status not in /schedule"),
    "Q1": ("same as S0 + grade.pending (sent today) + booking project_no/name", "yes"),
    "C4": ("as S3 + offline timeline item list", "partly: offline timeline is read today for duration only"),
    "SV": ("same as Q1 (counts + grade.pending + booking project_no/name)", "yes (shipped on night/progress-v2)"),
    "SVL": ("as SV + a human scope lock (shot count) per project_no/stage", "yes (scope_locks.json; off by default)"),
}


def evaluate_library(dirs=None, include_new=False):
    rows = []
    for path, sc in library.load_all(dirs):
        if sc.get("status") == "new" and not include_new:
            continue
        obs, res = library.evaluate(sc)
        rows.append((sc, obs, res))
    return rows


def cell(m):
    s = ""
    s += "F" if m["false_completion"] else ""
    s += "R" if m["fr_unexplained"] else ("r" if m["fr_explained"] else "")
    s += "M" if m["missed_completion"] else ""
    s += "W" if m["wrong_job"] else ""
    s += "L" if m["polls"] == 0 else ""
    return f"{s or 'ok'} {m['max_err']:.0f}"


def matrix(rows):
    names = [c.name for c in strategies.ALL]
    out = ["| scenario | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    for sc, obs, res in rows:
        out.append(f"| {sc['id']} | " + " | ".join(cell(res[n][1]) for n in names) + " |")
    return "\n".join(out)


def aggregate(rows):
    agg = {}
    for cls in strategies.ALL:
        n = cls.name
        ms = [res[n][1] for _, _, res in rows]
        inv = sum(len(res[n][2]) for _, _, res in rows)
        errs = [m["max_err"] for m in ms if m["polls"]]
        agg[n] = {
            "label": cls.label,
            "fc_scen": sum(m["false_completion"] for m in ms),
            "fc_polls": sum(m["fc_polls"] for m in ms),
            "fr_unexpl": sum(1 for m in ms if m["fr_unexplained"]),
            "fr_expl": sum(1 for m in ms if m["fr_explained"]),
            "missed": sum(m["missed_completion"] for m in ms),
            "wrong_job": sum(1 for m in ms if m["wrong_job"]),
            "silent": sum(1 for m in ms if m["polls"] == 0),
            "max_err_median": round(statistics.median(errs), 1) if errs else None,
            "max_err_worst": round(max(errs), 1) if errs else None,
            "uncorrected": sum(m["uncorrected"] for m in ms),
            "ttc_worst": max([m["ttc_min"] for m in ms] or [0]),
            "pms_stale": sum(m["pms_stale"] for m in ms),
            "inv_violations": inv,
            "needs": DATA[n][0], "exists": DATA[n][1], "human": cls.human, "cost": cls.cost,
        }
    return agg


def aggregate_table(agg, n_scen):
    out = [f"Over {n_scen} library scenarios. FC = scenarios with a false completion (done or 100% while not "
           "done); FR = scenarios with a false regression without reason code (with reason code in brackets); "
           "missed = truth done at the end but never claimed; err = max |reported - truth| per scenario (median / "
           "worst); uncorr = error episodes still open at the end; ttc = longest wrong episode (min); inv = "
           "invariant violations.", "",
           "| strategy | FC | FC polls | FR (expl.) | missed | wrong job | silent | err med/worst | uncorr | ttc | PMS stale | inv |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, a in agg.items():
        out.append(f"| {n} | {a['fc_scen']} | {a['fc_polls']} | {a['fr_unexpl']} ({a['fr_expl']}) | {a['missed']} | "
                   f"{a['wrong_job']} | {a['silent']} | {a['max_err_median']}/{a['max_err_worst']} | {a['uncorrected']} | "
                   f"{a['ttc_worst']} | {a['pms_stale']} | {a['inv_violations']} |")
    return "\n".join(out)


def strategy_table(agg):
    out = ["| strategy | what it does | data it depends on | exists today? | human input | cost |",
           "|---|---|---|---|---|---|"]
    for n, a in agg.items():
        out.append(f"| {n} | {a['label']} | {a['needs']} | {a['exists']} | {a['human']} | {a['cost']} |")
    return "\n".join(out)


def with_confirmed_scope(sc):
    """Copy of a scenario in which an authoritative shot list (the ground-truth scope of the main job)
    is known from the first poll - what S2/C3 can do once PMS or the offline EDL supplies scope."""
    import copy
    sc = copy.deepcopy(sc)
    job = sc.get("job")
    stage = sc.get("stage", "color")
    shots = ((sc.get("truth") or {}).get(job) or {}).get(stage)
    if "steps" in sc and shots:
        sc["steps"][0]["ops"].append(["confirm_scope", {"stage": stage, "source": "oracle", "shots": shots}])
    elif "observations" in sc:
        for o in sc["observations"]:
            n = (o.get("truth") or {}).get("scope")
            if n:
                o.setdefault("external", {"scope": {}, "pms_task": None})["scope"] = {stage: {"total": n}}
    return sc


def confirmed_table(dirs=None, names=("S0", "S2", "C3", "C4", "SVL")):
    rows = []
    for _, sc in library.load_all(dirs):
        if sc.get("status") == "new":
            continue
        obs, res = library.evaluate(with_confirmed_scope(sc), list(names))
        rows.append((sc, obs, res))
    out = ["Same library, but every scenario gets an authoritative shot list for its main job at the first poll "
           "(upper bound of what a confirmed scope source buys).", "",
           "| strategy | FC | FR (expl.) | missed | err med/worst | uncorr |", "|---|---|---|---|---|---|"]
    for n in names:
        ms = [res[n][1] for _, _, res in rows]
        errs = [m["max_err"] for m in ms if m["polls"]]
        out.append(f"| {n} | {sum(m['false_completion'] for m in ms)} | "
                   f"{sum(1 for m in ms if m['fr_unexplained'])} ({sum(1 for m in ms if m['fr_explained'])}) | "
                   f"{sum(m['missed_completion'] for m in ms)} | "
                   f"{round(statistics.median(errs), 1) if errs else None}/{round(max(errs), 1) if errs else None} | "
                   f"{sum(m['uncorrected'] for m in ms)} |")
    fc = [sc["id"] for sc, _, res in rows if res["C3"][1]["false_completion"]]
    missed = [sc["id"] for sc, _, res in rows if res["C3"][1]["missed_completion"]]
    out += ["", f"C3 false completions with a confirmed list: {', '.join(fc) or 'none'}.",
            f"C3 still never done with a confirmed list: {', '.join(missed) or 'none'}."]
    if "SVL" in names:
        fcl = [sc["id"] for sc, _, res in rows if res["SVL"][1]["false_completion"]]
        out.append(f"SVL (shipped server path, D1 = lock, counts only) false completions with a confirmed list: "
                   f"{', '.join(fcl) or 'none'}.")
    return "\n".join(out)

"""Scenario library: load, validate, evaluate every scenario against every strategy,
and check each scenario's `expect` block.

expect format (per strategy name):
    {"S0": {"false_completion": true}, "C3": {"false_completion": false, "max_final_err": 10}}
  <metric>: value      exact match (bools, ints)
  max_<metric>: n      metric <= n
  min_<metric>: n      metric >= n
Metric names are in metrics.METRICS (plus "polls").

status: "active" (asserted), "new" (found by the fuzzer, not yet triaged: reported, not asserted),
        "known_limitation" (asserted as written: the expectation documents the limitation).
"""
from __future__ import annotations

import json
import pathlib

from . import engine, metrics, strategies

HERE = pathlib.Path(__file__).parent
LIB_DIRS = [HERE / "scenarios" / "library", HERE / "scenarios" / "found"]
REQUIRED = ("id", "title", "category", "severity", "status")
STATUSES = {"active", "new", "known_limitation", "fixed"}


def paths(dirs=None):
    out = []
    for d in dirs or LIB_DIRS:
        out += sorted(pathlib.Path(d).glob("*.json"))
    return out


def load_all(dirs=None):
    return [(p, engine.load(p)) for p in paths(dirs)]


def validate(sc):
    errs = [f"missing {k}" for k in REQUIRED if k not in sc]
    if sc.get("status") not in STATUSES:
        errs.append(f"status must be one of {sorted(STATUSES)}")
    if ("steps" in sc) == ("observations" in sc):
        errs.append("exactly one of steps / observations")
    for name in (sc.get("expect") or {}):
        if name not in strategies.BY_NAME:
            errs.append(f"expect names unknown strategy {name}")
        for k in sc["expect"][name]:
            if k == "invariants":
                continue
            base = k[4:] if k.startswith(("max_", "min_")) else k
            if base not in metrics.METRICS and base != "polls":
                errs.append(f"expect {name}.{k}: unknown metric")
    return errs


def evaluate(sc, names=None):
    """-> (observations, {strategy: (outputs, metrics, invariant violations)})"""
    obs = engine.expand(sc)
    steps = sc.get("steps") or []
    growth = {i for i, o in enumerate(obs) if "step" in o and steps[o["step"]].get("growth_only")}
    observable = bool(sc.get("observable"))
    res = {}
    for cls in strategies.ALL:
        if names and cls.name not in names:
            continue
        outs = strategies.run(cls, obs)
        res[cls.name] = (outs, metrics.score(obs, outs),
                         metrics.check_invariants(obs, outs, observable=observable, growth_only_idx=growth))
    return obs, res


def check_expect(sc, res):
    fails = []
    for name, exp in (sc.get("expect") or {}).items():
        m = res[name][1]
        for k, want in exp.items():
            if k == "invariants":
                got = sorted({x["inv"] for x in res[name][2]} & set(want))
                if got:
                    fails.append(f"{sc['id']}: {name} violates {got}")
                continue
            if k.startswith("max_"):
                got = m.get(k[4:])
                ok = got is not None and got <= want
            elif k.startswith("min_"):
                got = m.get(k[4:])
                ok = got is not None and got >= want
            else:
                got = m.get(k)
                ok = got == want
            if not ok:
                fails.append(f"{sc['id']}: {name}.{k} expected {want!r} got {got!r}")
    return fails


def pms_sequence(obs, outs):
    """What PMS would store for the strategy: (t, host, pct) each time the sent value changes."""
    seq, last = [], {}
    for o, out in zip(obs, outs):
        if out["send"] and out["pct"] is not None and last.get(out["key"]) != out["pct"]:
            seq.append((o.get("t"), o.get("host"), out["pct"], out["done"], out["reasons"]))
            last[out["key"]] = out["pct"]
    return seq


if __name__ == "__main__":
    import sys
    bad = 0
    for p, sc in load_all():
        v = validate(sc)
        if v:
            print(p.name, v)
            bad += 1
    print("invalid:", bad)
    sys.exit(1 if bad else 0)

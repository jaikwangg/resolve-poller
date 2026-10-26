"""Generative testing: compose random edit histories, check the invariants, shrink
failures to a minimal scenario and save it into the library (scenarios/found/).

A history is a list of genes (op, a, b): op from OPS, a and b small ints. compose()
turns genes into an ordinary steps-scenario, so a found failure is replayed by the
same engine as hand-written scenarios. hypothesis (tests/test_generative.py) draws the
same genes, so both shrink the same representation.

    python -m sim.fuzz --n 300 --seed 7 [--strategy C3] [--save] [--unobservable]

Exit code 1 when a new failure was found (CI / scheduled run can alert on it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random

from . import library, metrics

FOUND = pathlib.Path(__file__).parent / "scenarios" / "found"
JOB = "KPP-2026-0901"
PROJ = "Fuzz_Job_grade"
OBSERVABLE_OPS = ["grade", "grade", "grade", "ungrade", "add", "cut", "replace", "blade", "dup", "peek_old",
                  "gap", "down", "room2", "confirm", "wait", "other_project"]
UNOBSERVABLE_OPS = ["lut_all", "notes"]
OPS = OBSERVABLE_OPS + UNOBSERVABLE_OPS


def compose(genes, n0=6, observable_only=True, settle_tail=0):
    """genes -> scenario dict (steps kind). Deterministic. settle_tail = that many extra one-minute polls at
    the end (real agents poll every 60 s), so time-window rules such as a stability window get exercised."""
    shots = [f"S{i:03d}" for i in range(1, n0 + 1)]
    live = list(shots)                 # deliverable timeline shot list
    next_id = n0 + 1
    tl, tl_n = "R1", 1
    t = 0
    steps = [{"t": 0, "host": "grade-01", "ops": [["open", {"project": PROJ, "timeline": tl}],
                                                    ["add", {"shots": list(shots)}]]}]
    confirms = []
    room2 = False
    observable = True
    for op, a, b in genes:
        if observable_only and op in UNOBSERVABLE_OPS:
            continue
        t += 1 + (b % 30)
        st = {"t": t, "host": "grade-01", "ops": []}
        pick = live[a % len(live)] if live else None
        if op == "grade" and live:
            st["ops"].append(["grade", {"shots": live[a % len(live): a % len(live) + 1 + b % 3]}])
        elif op == "ungrade" and pick:
            st["ops"].append(["ungrade", {"shots": [pick]}])
        elif op == "add":
            new = [f"S{next_id + i:03d}" for i in range(1 + a % 3)]
            next_id += len(new)
            live += new
            st["ops"].append(["add", {"shots": new, "kind": ["video", "vfx", "placeholder"][b % 3]}])
            st["growth_only"] = True
        elif op == "cut" and len(live) > 1:
            live.remove(pick)
            st["ops"].append(["remove", {"shots": [pick]}])
        elif op == "replace" and pick:
            st["ops"].append(["replace", {"shot": pick, "keep_grade": bool(b % 2), "kind": "vfx"}])
        elif op == "blade" and pick:
            st["ops"].append(["blade", {"shot": pick}])
        elif op == "dup":
            tl_n += 1
            new_tl = f"R1_v{tl_n}"
            st["ops"].append(["copy_timeline", {"from": tl, "to": new_tl}])
            tl = new_tl
        elif op == "peek_old" and tl_n > 1:
            st["ops"].append(["open", {"project": PROJ, "timeline": "R1"}])
            steps.append(st)
            t += 1
            st = {"t": t, "host": "grade-01", "ops": [["open", {"project": PROJ, "timeline": tl}]]}
        elif op == "gap":
            st["poll"] = False
            if live:
                st["ops"].append(["grade", {"shots": [pick]}])
        elif op == "down":
            st["ops"].append(["down"])
            steps.append(st)
            t += 1 + a
            st = {"t": t, "host": "grade-01", "ops": [["up"]]}
        elif op == "room2" and live:
            if not room2:
                st2 = {"t": t, "host": "grade-02", "ops": [["copy_timeline", {"from_project": PROJ, "from": tl,
                                                                              "to_project": PROJ + "_room2",
                                                                              "to": "R1"}]]}
                steps.append(st2)
                room2 = True
            st = {"t": t, "host": "grade-02", "ops": [["grade", {"shots": [pick]}]]}
        elif op == "confirm":
            st["ops"].append(["confirm_scope", {"stage": "color", "source": "fuzz", "shots": None}])
            confirms.append(st)
        elif op == "other_project":
            st["ops"].append(["open", {"project": "Other_Job_grade", "timeline": "X"}])
            steps.append(st)
            t += 1
            st = {"t": t, "host": "grade-01", "ops": [["open", {"project": PROJ, "timeline": tl}]]}
        elif op == "lut_all":
            observable = False
            st["ops"].append(["lut_all", {}])
        elif op == "notes" and pick:
            observable = False
            st["ops"].append(["notes", {"shots": [pick]}])
        # "wait" and no-op fallbacks: a plain poll
        steps.append(st)
    for st in steps:
        for o in st["ops"]:
            if o[0] == "confirm_scope":
                o[1]["shots"] = list(live)
    # the other job's project must exist before it is opened
    steps[0]["ops"].append(["new_timeline", {"project": "Other_Job_grade", "name": "X"}])
    steps[0]["ops"].append(["add", {"project": "Other_Job_grade", "timeline": "X",
                                     "shots": ["O001", "O002", "O003"], "graded": True}])
    steps.append({"t": t + 60, "host": "grade-01", "ops": []})
    for k in range(1, settle_tail + 1):
        steps.append({"t": t + 60 + k, "host": "grade-01", "ops": []})
    return {"id": "FZ", "title": "generated", "category": "generated", "severity": [], "status": "new",
            "stage": "color", "job": JOB, "project_name": "Fuzz Job", "observable": observable,
            "hosts": {"grade-01": {"stage": "color"}, "grade-02": {"stage": "color"}},
            "steps": steps, "truth": {JOB: {"color": list(live)}}, "genes": [list(g) for g in genes]}


def violations(sc, strategy="C3"):
    _, res = library.evaluate(sc, [strategy])
    return res[strategy][2]


def random_genes(rng, n=None, unobservable=False):
    ops = OPS if unobservable else OBSERVABLE_OPS
    return [(rng.choice(ops), rng.randrange(10), rng.randrange(1, 40)) for _ in range(n or rng.randrange(3, 25))]


def shrink(genes, fails, observable_only=True):
    """Greedy delta debugging: drop genes, then shrink their ints, while `fails(genes)` stays true."""
    genes = [tuple(g) for g in genes]
    changed = True
    while changed:
        changed = False
        for i in range(len(genes) - 1, -1, -1):
            cand = genes[:i] + genes[i + 1:]
            if fails(cand):
                genes, changed = cand, True
        for i, (op, a, b) in enumerate(genes):
            for na, nb in ((0, b), (a, 1), (0, 1)):
                if (na, nb) != (a, b):
                    cand = genes[:i] + [(op, na, nb)] + genes[i + 1:]
                    if fails(cand):
                        genes, changed = cand, True
                        op, a, b = genes[i]
    return genes


def save(sc, inv, strategy, seed=None):
    FOUND.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(json.dumps(sc["genes"]).encode()).hexdigest()[:8]
    sc = dict(sc)
    sc["id"] = f"FZ_{inv}_{h}"
    sc["title"] = f"Fuzzer: {inv} for {strategy} (shrunk, {len(sc['genes'])} genes)"
    sc["category"] = "generated"
    sc["severity"] = [inv]
    sc["status"] = "new"
    sc["story"] = ("Found by sim.fuzz and shrunk to a minimal edit history. Triage: fix the strategy (status -> "
                   "active) or document the limitation (status -> known_limitation with known_limits).")
    sc["found_by"] = {"strategy": strategy, "invariant": inv, "seed": seed}
    sc["expect"] = {strategy: {"invariants": [inv]}}
    path = FOUND / f"{sc['id']}.json"
    if not path.exists():
        path.write_text(json.dumps(sc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def run(n=200, seed=0, strategy="C3", do_save=False, unobservable=False, max_saves=5):
    rng = random.Random(seed)
    found = {}
    for k in range(n):
        genes = random_genes(rng, unobservable=unobservable)
        sc = compose(genes, observable_only=not unobservable)
        v = violations(sc, strategy)
        if not v:
            continue
        inv = v[0]["inv"]

        def fails(g, inv=inv):
            return any(x["inv"] == inv for x in violations(compose(g, observable_only=not unobservable), strategy))
        small = shrink(genes, fails, observable_only=not unobservable)
        key = (inv, json.dumps(small))
        if key in found:
            continue
        found[key] = compose(small, observable_only=not unobservable)
        if len(found) >= max_saves:
            break
    paths = []
    if do_save:
        for (inv, _), sc in found.items():
            paths.append(save(sc, inv, strategy, seed))
    return found, paths


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--strategy", default="C3")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--unobservable", action="store_true", help="also compose LUT-all / client notes ops")
    a = ap.parse_args(argv)
    found, paths = run(a.n, a.seed, a.strategy, a.save, a.unobservable)
    print(f"fuzz: {a.n} histories, seed {a.seed}, strategy {a.strategy}: {len(found)} distinct minimal failures")
    for (inv, genes), _ in found.items():
        print(f"  {inv}: {genes}")
    for p in paths:
        print("  saved", p)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())

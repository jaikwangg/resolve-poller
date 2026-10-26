"""One command for CI and the scheduled run.

    python -m sim.run_all [--fuzz N] [--seed S] [--save-found] [--data DIR ...] [--out FILE]

1. validates every scenario file (library + found)
2. evaluates every scenario x strategy, checks `expect` blocks (status active/known_limitation)
3. checks the invariants of the recommended strategy on every scenario
4. fuzzes N random edit histories against the recommended strategy (shrinks + optionally saves failures)
5. replays all real recorded data and flags anomalies per strategy
6. prints a short summary (and writes it to --out)

Exit 0 = all green. Exit 1 = an expectation failed, a scenario is invalid, the recommended
strategy broke an invariant, or the fuzzer found a new failure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time

from . import fuzz, library, replay, report, strategies


def main(argv=None):
    ap = argparse.ArgumentParser(description="run the whole progress-strategy test system")
    ap.add_argument("--fuzz", type=int, default=150, help="random histories (0 = skip)")
    ap.add_argument("--seed", type=int, default=None, help="default: day of year (a different stream daily)")
    ap.add_argument("--save-found", action="store_true", help="write shrunk failures to scenarios/found/")
    ap.add_argument("--data", nargs="*", default=None, help="dirs of real JSONL (default server/data* + replay fixture)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    t0 = time.time()
    rec = strategies.RECOMMENDED
    seed = a.seed if a.seed is not None else dt.date.today().timetuple().tm_yday
    lines = [f"# editortrack progress test system - {dt.datetime.now().isoformat(timespec='seconds')}", ""]
    bad = False

    invalid = [(p.name, e) for p, sc in library.load_all() for e in [library.validate(sc)] if e]
    lines.append(f"- scenarios invalid: {len(invalid)}")
    for n, e in invalid:
        lines.append(f"  - {n}: {e}")
    bad |= bool(invalid)

    rows = report.evaluate_library(include_new=True)
    asserted = [r for r in rows if r[0].get("status") in ("active", "known_limitation", "fixed")]
    new = [r for r in rows if r[0].get("status") == "new"]
    fails = [f for sc, _, res in asserted for f in library.check_expect(sc, res)]
    lines.append(f"- scenarios: {len(rows)} ({len(asserted)} asserted, {len(new)} new/untriaged)")
    lines.append(f"- expectation failures: {len(fails)}")
    lines += [f"  - {f}" for f in fails]
    bad |= bool(fails)

    inv = [(sc["id"], v) for sc, _, res in asserted for v in res[rec][2]]
    lines.append(f"- {rec} invariant violations on asserted scenarios: {len(inv)}")
    lines += [f"  - {i}: {v}" for i, v in inv[:20]]
    bad |= bool(inv)
    for sc, _, res in new:
        lines.append(f"  - untriaged {sc['id']}: {rec} violations now {len(res[rec][2])}")

    agg = report.aggregate(report.evaluate_library())
    lines += ["", "## Strategies over the library", "", report.aggregate_table(agg, len(asserted)), ""]

    if a.fuzz:
        found, paths = fuzz.run(a.fuzz, seed, rec, a.save_found)
        lines.append(f"- fuzz: {a.fuzz} histories, seed {seed}: {len(found)} new minimal failures for {rec}")
        for (i, g), _ in found.items():
            lines.append(f"  - {i}: {g}")
        lines += [f"  - saved {p}" for p in paths]
        bad |= bool(found)

    try:
        from . import docs
        stale = [str(pth) for pth, body in docs.build().items()
                 if not pth.exists() or pth.read_text(encoding="utf-8") != body]
    except SystemExit as e:           # a marker is missing
        stale = [str(e)]
    lines.append(f"- research docs out of date (run python -m sim.docs): {stale}" if stale
                 else "- research docs up to date")
    rp = replay.run([pathlib.Path(d) for d in a.data] if a.data else None)
    lines += ["", "## Replay of real data", "", replay.markdown(rp), ""]
    lines.append(f"- result: {'FAIL' if bad else 'OK'} in {time.time() - t0:.1f} s")
    text = "\n".join(lines)
    print(text)
    if a.out:
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out).write_text(text + "\n", encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

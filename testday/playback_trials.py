#!/usr/bin/env python3
"""P1 playback trials: agent OFF vs ON, alternating, hands off during playback (WORKSTATION_SAFETY.md 7 P1).

    # on the workstation (operator at the keyboard; the script only keeps time and asks questions):
    python3 testday/playback_trials.py run --csv ~/ET_TESTDAY/P1_conform01.csv --timelines PB_4K,PB_8K \
        --trials 10 --seconds 120 [--agent-dir ~/editortrack/agent]
    # afterwards, with the server records for that host copied read-only:
    python3 testday/playback_trials.py analyze --csv ~/ET_TESTDAY/P1_conform01.csv \
        --records 2026-11-02.jsonl --host conform-01

Frame drops are read from Resolve's own viewer readout (the playback frame-rate indicator / dropped-frame
warning of the version under test) by the operator; the script never talks to Resolve. Agent ON/OFF is switched
with the agent's own kill switch (`deploy/killswitch.sh on|off`) when --agent-dir is given, otherwise the operator
switches it and the script checks the kill file.

PASS (per timeline, only valid if every OFF trial is drop-free = the timeline plays in real time):
  0 dropped frames, indicator never red/amber and min fps not below the timeline rate in every ON trial; every
  agent turn inside an ON trial is a skip (playback, timecode_unknown, user_active, rendering, resolve_busy,
  idle_unknown) with guard.calls <= 12, never a full scan; at least one `playback` skip per timeline (the agent
  log must show playback skips, section 7 P1); no record inside OFF trials; the kill file matched the agent state
  in every trial. Any OFF trial with drops / red indicator / low fps makes the timeline INVALID (not real time).
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

FIELDS = ["trial", "timeline", "agent", "start", "end", "start_local", "dropped", "min_fps", "indicator", "notes",
          "kill_file"]
SKIP_OK = ("playback", "timecode_unknown", "user_active", "rendering", "resolve_busy", "idle_unknown")
MAX_SKIP_CALLS = 12


def kill_file_present(state_dir):
    return os.path.exists(os.path.join(state_dir, "DISABLED"))


def set_agent(state, agent_dir):
    cmd = ["sh", os.path.join(agent_dir, "deploy", "killswitch.sh"), "off" if state == "on" else "on"]
    subprocess.run(cmd, check=False)


def schedule(timelines, trials):
    """Alternate OFF/ON, starting OFF, per timeline: OFF ON OFF ON ... (trials per state)."""
    out, n = [], 0
    for tl in timelines:
        for k in range(2 * trials):
            n += 1
            out.append((n, tl, "off" if k % 2 == 0 else "on"))
    return out


def run(a):
    state_dir = os.path.expanduser(a.state_dir)
    done = set()
    if os.path.exists(a.csv):
        done = {int(r["trial"]) for r in common.read_csv(a.csv)}
    plan = schedule(a.timelines.split(","), a.trials)
    print("P1 playback: %d trials of %d s. DUMMY project only. Hands off the keyboard and mouse while playing."
          % (len(plan), a.seconds))
    for n, tl, state in plan:
        if n in done:
            continue
        print("\n=== trial %d/%d  timeline %s  agent %s ===" % (n, len(plan), tl, state.upper()))
        if a.agent_dir:
            set_agent(state, a.agent_dir)
        else:
            common.ask("switch the agent %s (sh deploy/killswitch.sh %s), then Enter" %
                       (state.upper(), "off" if state == "on" else "on"))
        kf = kill_file_present(state_dir)
        if (state == "on") == kf:
            print("WARNING: kill file %s present=%s does not match agent %s" % (state_dir, kf, state))
        if state == "on" and a.warmup:
            print("waiting %d s so at least one agent turn is due..." % a.warmup)
            time.sleep(a.warmup)
        common.ask("Open %s, playhead at the start. Press Enter, then within 5 s press Space in Resolve and take "
                   "your hands off" % tl)
        t0 = time.time()
        for left in range(a.seconds, 0, -10):
            sys.stdout.write("\r%4d s left " % left)
            sys.stdout.flush()
            time.sleep(min(10, left))
        sys.stdout.write("\a\rSTOP playback now (Space), then come back here.\n")
        t1 = time.time()
        dropped = ""
        while not dropped.isdigit():             # no default: the operator must read it off Resolve
            dropped = common.ask("frames dropped during this trial per Resolve's viewer readout (a number)")
        min_fps = ""
        while not _num(min_fps):
            min_fps = common.ask("lowest playback fps shown in the viewer (a number)")
        ind = ""
        while ind not in ("y", "n"):
            ind = common.ask("playback indicator ever red/amber? (y/n)").lower()
        notes = common.ask("notes (anything noticed)", "")
        common.append_csv(a.csv, {"trial": n, "timeline": tl, "agent": state, "start": "%.3f" % t0,
                                  "end": "%.3f" % t1, "start_local": common.iso_local(t0), "dropped": dropped,
                                  "min_fps": min_fps, "indicator": ind, "notes": notes, "kill_file": int(kf)}, FIELDS)
    if a.agent_dir:
        set_agent("off", a.agent_dir)
        print("agent left OFF (kill switch on). Turn it back on deliberately when the next step needs it.")
    return 0


def _num(x):
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def trial_bad(t, rate):
    """Operator-observed trouble in one trial: drops, red/amber indicator, fps below the timeline rate."""
    why = []
    try:
        if int(float(t.get("dropped"))) != 0:
            why.append("dropped=%s" % t.get("dropped"))
    except (TypeError, ValueError):
        why.append("dropped not recorded")
    if (t.get("indicator") or "").lower().startswith("y"):
        why.append("indicator red/amber")
    if _num(t.get("min_fps")) and rate and float(t["min_fps"]) < rate - 0.01:
        why.append("min fps %s < %s" % (t["min_fps"], rate))
    return why


def evaluate(trials, recs, rate=None):
    """Pure function: per timeline verdict. trials = csv dicts, recs = loaded records of the host."""
    by_tl = {}
    for t in trials:
        by_tl.setdefault(t["timeline"], []).append(t)
    res = {}
    for tl, ts in by_tl.items():
        off = [t for t in ts if t["agent"] == "off"]
        on = [t for t in ts if t["agent"] == "on"]

        def in_win(t):
            s, e = float(t["start"]), float(t["end"])
            # any turn overlapping the trial (turn = [ts - turn_ms, ts]) +- 2 s
            return [r for r in recs if r["_t"] >= s - 2
                    and r["_t"] - (((r.get("agent") or {}).get("turn_ms") or 0) / 1000.0) <= e + 2]

        off_bad = [trial_bad(t, rate) for t in off]
        on_bad = [trial_bad(t, rate) for t in on]
        on_recs = [r for t in on for r in in_win(t)]
        off_recs = [r for t in off for r in in_win(t)]
        bad = [r for r in on_recs if common.reason_of(r) not in SKIP_OK
               or (common.guard_of(r).get("calls") or 0) > MAX_SKIP_CALLS]
        mismatch = [t["trial"] for t in ts if str(t.get("kill_file", "")) in ("0", "1")
                    and (t["agent"] == "on") == (str(t["kill_file"]) == "1")]
        baseline_ok = bool(off) and not any(off_bad)
        checks = {
            "baseline_realtime(off trials clean)": baseline_ok,
            "0_dropped_frames_no_red_no_low_fps_agent_on": bool(on) and not any(on_bad),
            "turns_inside_on_trials": len(on_recs) > 0,
            "at_least_one_playback_skip": any(common.reason_of(r) == "playback" for r in on_recs),
            "every_on_turn_is_a_skip_with_<=12_calls": not bad,
            "no_records_inside_off_trials": not off_recs,
            "kill_file_matched_agent_state": not mismatch,
        }
        if not baseline_ok:
            verdict = "INVALID (timeline does not play in real time with the agent off)"
        elif mismatch:
            verdict = "INVALID (agent state not as planned in trials %s)" % ", ".join(mismatch)
        else:
            verdict = "PASS" if all(checks.values()) else "FAIL"
        res[tl] = {"off": len(off), "on": len(on), "off_trouble": off_bad, "on_trouble": on_bad,
                   "on_turns": len(on_recs), "on_reasons": sorted({common.reason_of(r) for r in on_recs}),
                   "bad_turns": [(r.get("ts"), common.reason_of(r), common.guard_of(r).get("calls")) for r in bad],
                   "checks": checks, "verdict": verdict}
    return res


def analyze(a):
    trials = common.read_csv(a.csv)
    if not trials:
        common.die("no trials in %s" % a.csv)
    t0 = min(float(t["start"]) for t in trials) - 300
    t1 = max(float(t["end"]) for t in trials) + 300
    recs = common.load_records(a.records, a.host, t0, t1)
    if not recs:
        common.die("no records for host %s in the trial period: agent behaviour unproven (check --host)" % a.host)
    res = evaluate(trials, recs, a.rate)
    ok = True
    for tl, r in res.items():
        print("\n%s: %s" % (tl, r["verdict"]))
        print("  OFF trials %d trouble %s | ON trials %d trouble %s | turns in ON windows %d reasons %s"
              % (r["off"], r["off_trouble"], r["on"], r["on_trouble"], r["on_turns"], r["on_reasons"]))
        for k, v in r["checks"].items():
            print("  " + common.verdict_line(v, k))
        for b in r["bad_turns"]:
            print("  bad turn: %s reason=%s calls=%s" % b)
        ok = ok and r["verdict"] == "PASS"
    if a.json:
        common.write_json(a.json, res)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--csv", required=True)
    r.add_argument("--timelines", default="PB_4K,PB_8K")
    r.add_argument("--trials", type=int, default=10, help="trials per agent state per timeline")
    r.add_argument("--seconds", type=int, default=120)
    r.add_argument("--agent-dir", help="agent folder; the script then runs killswitch.sh on/off itself")
    r.add_argument("--state-dir", default="~/.editortrack")
    r.add_argument("--warmup", type=int, default=65, help="seconds to wait after switching ON before playing")
    an = sub.add_parser("analyze")
    an.add_argument("--csv", required=True)
    an.add_argument("--records", nargs="+", required=True)
    an.add_argument("--host", required=True)
    an.add_argument("--rate", type=float, required=True, help="timeline frame rate (e.g. 24)")
    an.add_argument("--json")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a)
    if a.cmd == "analyze":
        return analyze(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

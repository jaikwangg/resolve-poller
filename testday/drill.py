#!/usr/bin/env python3
"""Kill-switch + uninstall drill on the TEST workstation (WORKSTATION_SAFETY.md 7 K1, K2, K5, U1, U2).

Every step is timed and appended to --log (CSV). It drives only the agent's own scripts
(deploy/killswitch.sh, deploy/uninstall.sh) and read-only checks; it never talks to Resolve and never uses sudo
itself (it prints the sudo command for the operator; a system-wide install is left to the operator).

    python3 testday/drill.py --agent-dir ~/editortrack/agent --log ~/ET_TESTDAY/drill_grade01.csv before
        # Resolve quit -> U1 snapshot "before" (take it BEFORE install.sh)
    python3 testday/drill.py ... k1          # local kill switch: scheduler stopped at once, no poll for 3 min
    python3 testday/drill.py ... k2          # system kill file: next turn silent (<= 60 s + one turn)
    python3 testday/drill.py ... silence --tag K3              # fleet switch: silent after 60 s
    python3 testday/drill.py ... silence --tag K4 --grace lease  # server unreachable: silent once the lease expires
  "Silent" = the turn printed None AND the queue did not grow (runner prints None also when a POST failed after
  a scan). K1/K2/silence first require the agent to be demonstrably on (scheduler active, a record sent recently).
    python3 testday/drill.py ... uninstall   # K5 stopwatch: owner decides -> agent gone (<= 300 s)  [--purge = U2]
    python3 testday/drill.py ... after       # after reboot, Resolve quit -> snapshot + compare (U1)
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import prefs_snapshot  # noqa: E402

FIELDS = ["step", "start_local", "seconds", "result", "detail", "operator"]
LABEL = "com.kantana.editortrack.agent"


def log(a, step, t0, ok, detail, verdict=None):
    verdict = verdict or ("PASS" if ok else "FAIL")
    common.append_csv(a.log, {"step": step, "start_local": common.iso_local(t0), "seconds": "%.1f" % (time.time() - t0),
                              "result": verdict, "detail": detail, "operator": a.operator}, FIELDS)
    print(common.verdict_line(ok, "%s: %s" % (step, detail)))
    return 0 if ok else 1


def sh(args, timeout=120):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def scheduler_active():
    if sys.platform == "darwin":
        rc, _ = sh(["launchctl", "print", "gui/%d/%s" % (os.getuid(), LABEL)])
        return rc == 0
    rc, out = sh(["systemctl", "--user", "is-active", "editortrack-agent.timer"])
    return out.strip() in ("active", "activating")


def agent_process():
    rc, _ = sh(["pgrep", "-f", "editortrack.*(run-loop|run-agent|runner.py)|deploy/run-loop.sh|deploy/run-agent.sh"])
    return rc == 0


def log_turns_since(state_dir, since):
    p = os.path.join(state_dir, "agent.log")
    if not os.path.exists(p):
        return []
    return [t for t in common.agent_log_turns(p) if t["start"] >= since - 1]


def is_silent(result_line):
    """runner.py prints None when it sent nothing: kill switch / lock / no lease, but ALSO when every POST failed
    (it then scanned Resolve and queued the record). So a silent turn also needs a queue that does not grow."""
    return result_line.strip() == "None"


def queue_lines(state_dir):
    try:
        with open(os.path.join(state_dir, "queue.jsonl"), "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def live_before(a, window=150):
    """The agent must be demonstrably working before a stop is tested: scheduler active and a turn that sent a
    record (not None) in the last `window` s. Otherwise a 'silent' result proves nothing."""
    recent = [t for t in log_turns_since(a.state_dir, time.time() - window) if not is_silent(t["result"])]
    problems = []
    if not scheduler_active():
        problems.append("scheduler not active")
    if not recent:
        problems.append("no turn sent a record in the last %d s" % window)
    return problems


def watch(a, since, minutes, grace):
    """Wait, sampling the queue every 5 s. -> (turns, loud turns after grace, queue growth after grace)."""
    end = since + minutes * 60
    q_prev = queue_lines(a.state_dir)
    grew_late = 0
    while time.time() < end:
        time.sleep(5)
        q = queue_lines(a.state_dir)
        if q > q_prev and time.time() > since + grace:
            grew_late += q - q_prev
        q_prev = q
    turns = log_turns_since(a.state_dir, since)
    loud = [t for t in turns if not is_silent(t["result"]) and t["start"] > since + grace]
    return turns, loud, grew_late


def cmd_before(a):
    t0 = time.time()
    common.ask("Quit DaVinci Resolve completely, then Enter")
    out = os.path.join(a.dir, "U1_before.json")
    snap = prefs_snapshot.snapshot()
    common.write_json(out, snap)
    ok = bool(snap["groups"]["resolve_prefs"])
    return log(a, "U1-before", t0, ok, "%d strict pref files -> %s%s" % (
        len(snap["groups"]["resolve_prefs"]), out, "" if ok else " (NO preference files found: fix paths first)"))


def cmd_k1(a):
    t0 = time.time()
    pre = live_before(a)
    if pre:
        return log(a, "K1", t0, False, "precondition: agent not demonstrably on (%s); turn it on first" % "; ".join(pre))
    rc, out = sh(["sh", os.path.join(a.agent_dir, "deploy", "killswitch.sh"), "on"])
    t_switch = time.time()
    time.sleep(2)
    active = scheduler_active()
    proc = agent_process()
    rc2, status = sh(["sh", os.path.join(a.agent_dir, "deploy", "killswitch.sh"), "status"])
    print("watching agent.log and the queue for %d s (no new turn may appear)..." % a.watch)
    turns, _loud, grew = watch(a, t_switch, a.watch / 60.0, 0)
    ok = rc == 0 and not active and not proc and "DISABLED" in status and not turns and not grew
    return log(a, "K1", t0, ok, "scheduler_active=%s process=%s status=%r new_turns=%d queue_growth=%d" % (
        active, proc, status.replace("\n", " | "), len(turns), grew))


def cmd_k2(a):
    path = "/Library/Application Support/editortrack/DISABLED" if sys.platform == "darwin" else "/etc/editortrack/DISABLED"
    t_pre = time.time()
    if os.path.exists(os.path.join(a.state_dir, "DISABLED")):
        return log(a, "K2", t_pre, False, "precondition: the local kill file is set; run killswitch.sh off first")
    pre = live_before(a)
    if pre:
        return log(a, "K2", t_pre, False, "precondition: agent not demonstrably on (%s)" % "; ".join(pre))
    print("Operator runs:  sudo mkdir -p '%s' && sudo touch '%s'" % (os.path.dirname(path), path))
    common.ask("Enter as soon as the file exists")
    t0 = time.time()
    if not os.path.exists(path):
        return log(a, "K2", t0, False, "kill file %s not found" % path)
    turns, loud, grew = watch(a, t0, a.minutes, 60)
    after = [t for t in turns if t["start"] > t0 + 60]       # silent turns must be seen after the switch
    ok = bool(after) and not loud and not grew
    print("Operator: remove it again with  sudo rm '%s'" % path)
    return log(a, "K2", t0, ok, "turns=%d non-silent-after-60s=%d queue_growth_after_60s=%d (results: %s)" % (
        len(turns), len(loud), grew, ", ".join(t["result"] for t in turns[:6])))


def lease_remaining(state_dir, now):
    import json
    try:
        with open(os.path.join(state_dir, "lease.json"), encoding="utf-8") as f:
            return max(0.0, float(json.load(f).get("until", 0)) - now)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def cmd_silence(a):
    t_pre = time.time()
    pre = live_before(a)
    if pre:
        return log(a, "silence-" + a.tag, t_pre, False, "precondition: agent not demonstrably on (%s)" % "; ".join(pre))
    common.ask("Make the change now (K3: agent_control.json enabled:false · K4: server unreachable), then Enter")
    t0 = time.time()
    if a.grace == "lease":                       # K4: the agent may keep working until its lease runs out
        rem = lease_remaining(a.state_dir, t0)
        if rem is None:
            return log(a, "silence-" + a.tag, t0, False, "no readable lease.json in %s" % a.state_dir)
        grace = rem + 90
    else:
        grace = float(a.grace)
    minutes = max(a.minutes, (grace + 180) / 60.0)
    print("watching %.1f min; must be silent (no record sent, queue not growing) after %.0f s" % (minutes, grace))
    turns, loud, grew = watch(a, t0, minutes, grace)
    after = [t for t in turns if t["start"] > t0 + grace]
    ok = bool(after) and not loud and not grew
    return log(a, "silence-" + a.tag, t0, ok, "grace=%.0fs turns=%d after_grace=%d non-silent=%d queue_growth=%d (%s)" % (
        grace, len(turns), len(after), len(loud), grew, ", ".join(t["result"] for t in turns[:8])))


def system_wide_install():
    if sys.platform == "darwin":
        c = ["/Library/LaunchAgents/%s.plist" % LABEL]
    else:
        c = ["/etc/systemd/user/editortrack-agent.service", "/etc/systemd/user/editortrack-agent.timer"]
    return [p for p in c if os.path.exists(p)]


def cmd_uninstall(a):
    print("K5: the person running this must NOT be the developer, working from the runbook only.")
    common.ask("Owner decides 'remove it now' -> press Enter to start the stopwatch")
    t0 = time.time()
    sysw = system_wide_install()
    if sysw:   # uninstall.sh would call sudo for these; this kit never runs sudo itself
        print("System-wide install found (%s). The operator runs, from the runbook:\n  sudo sh %s%s\nthen:"
              "  sh %s --verify" % (", ".join(sysw), os.path.join(a.agent_dir, "deploy", "uninstall.sh"),
                                    " --purge" if a.purge else "", os.path.join(a.agent_dir, "deploy", "uninstall.sh")))
        return log(a, "U2-purge" if a.purge else "K5-uninstall", t0, False,
                   "system-wide install: run uninstall.sh by hand (sudo) and time it with a stopwatch")
    args = ["sh", os.path.join(a.agent_dir, "deploy", "uninstall.sh")] + (["--purge"] if a.purge else [])
    rc, out = sh(args, timeout=600)
    print(out)
    if a.purge:   # --purge deleted the agent folder (and its uninstall.sh), so verify here
        gone = not os.path.exists(a.agent_dir)
        v_rc = 0 if (gone and not agent_process() and not scheduler_active()
                     and not os.path.exists(a.state_dir)) else 1
        v_out = "agent dir gone=%s" % gone
    else:
        v_rc, v_out = sh(["sh", os.path.join(a.agent_dir, "deploy", "uninstall.sh"), "--verify"])
    secs = time.time() - t0
    ok = rc == 0 and v_rc == 0 and secs <= 300
    step = "U2-purge" if a.purge else "K5-uninstall"
    return log(a, step, t0, ok, "uninstall rc=%d verify rc=%d (%s) in %.0f s (limit 300)" % (
        rc, v_rc, v_out.replace("\n", " "), secs))


def cmd_after(a):
    t0 = time.time()
    common.ask("After the reboot: quit DaVinci Resolve completely, then Enter")
    before_p = os.path.join(a.dir, "U1_before.json")
    after_p = os.path.join(a.dir, "U1_after.json")
    snap = prefs_snapshot.snapshot()
    common.write_json(after_p, snap)
    allow = prefs_snapshot.load(a.allow) if a.allow else []
    res = prefs_snapshot.compare(prefs_snapshot.load(before_p), snap, allow, a.expected)
    common.write_json(os.path.join(a.dir, "U1_compare.json"), res)
    for p in res["strict_differences"]:
        print("  DIFF %s" % p)
    for n in res["notes"]:
        print("  note: %s" % n)
    if res.get("check"):
        print("CHECK (owner decision): " + res["check"])
    return log(a, "U1-after", t0, res["ok"], "; ".join(res["fail"]) or res.get("check")
               or "Resolve prefs byte-identical, nothing left", verdict=res["verdict"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent-dir", default="~/editortrack/agent")
    ap.add_argument("--state-dir", default="~/.editortrack")
    ap.add_argument("--log", required=True, help="drill CSV (kept with the results)")
    ap.add_argument("--operator", default="", help="name of the person running the step")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("before")
    k1 = sub.add_parser("k1")
    k1.add_argument("--watch", type=int, default=180)
    k2 = sub.add_parser("k2")
    k2.add_argument("--minutes", type=float, default=4)
    si = sub.add_parser("silence")
    si.add_argument("--minutes", type=float, default=3)
    si.add_argument("--grace", default="60", help="seconds, or 'lease' (K4: until lease.json expires + 90 s)")
    si.add_argument("--tag", default="K3")
    un = sub.add_parser("uninstall")
    un.add_argument("--purge", action="store_true")
    af = sub.add_parser("after")
    af.add_argument("--allow")
    af.add_argument("--expected", action="append", default=[])
    a = ap.parse_args(argv)
    a.agent_dir = os.path.abspath(os.path.expanduser(a.agent_dir))
    a.state_dir = os.path.expanduser(a.state_dir)
    a.log = os.path.abspath(os.path.expanduser(a.log))
    a.dir = os.path.dirname(a.log)
    os.makedirs(a.dir, exist_ok=True)
    fn = {"before": cmd_before, "k1": cmd_k1, "k2": cmd_k2, "silence": cmd_silence, "uninstall": cmd_uninstall,
          "after": cmd_after}.get(a.cmd)
    if not fn:
        ap.print_help()
        return 2
    if a.cmd in ("k1", "uninstall") and not os.path.isdir(a.agent_dir):
        common.die("agent dir %s not found" % a.agent_dir)
    return fn(a)


if __name__ == "__main__":
    sys.exit(main())

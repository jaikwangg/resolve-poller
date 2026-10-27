#!/usr/bin/env python3
"""Snapshot + compare of DaVinci Resolve preferences and the machine's scheduler/home footprint (U1).

    python3 testday/prefs_snapshot.py snapshot --out ~/ET_TESTDAY/U1_before.json     # Resolve QUIT first
    ... install agent, run, kill-switch drill, uninstall, reboot, quit Resolve ...
    python3 testday/prefs_snapshot.py snapshot --out ~/ET_TESTDAY/U1_after.json
    python3 testday/prefs_snapshot.py compare ~/ET_TESTDAY/U1_before.json ~/ET_TESTDAY/U1_after.json \
        [--allow ~/ET_TESTDAY/control_allow.json]

Groups:
  resolve_prefs   STRICT: every file must be byte-identical (sha256), none added or removed.
                  macOS ~/Library/Preferences/Blackmagic Design, ~/Library/Preferences/com.blackmagic-design.*
                  Linux ~/.local/share/DaVinciResolve/configs, /opt/resolve/configs, ~/.config/Blackmagic Design
  resolve_support REPORT: Resolve's application-support folder without logs (local project database etc.)
  scheduler       launchd plists / systemd user units / crontab: no new entry may remain after uninstall
  footprint       names in ~ and a few config folders; any editortrack remnant = FAIL

Resolve rewrites some of its own files when it runs, independent of the agent. If the strict group differs,
repeat the same day WITHOUT the agent (control run) and pass its differences with --allow (made by
`compare --emit-allow`): files that change in the control run too are listed as Resolve's own churn and the
verdict becomes CHECK (exit 2), not PASS: section 7 says byte-identical, so accepting that churn is the owner's
decision. Unreadable preference files FAIL. Everything is read-only (stat + read for hashing); run it with
Resolve quit.
"""
import argparse
import glob
import hashlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

HASH_LIMIT = 512 * 1024 * 1024    # bigger files: size + mtime only
SKIP_DIRS = {"logs", "Logs", "CacheClip", ".gallery_cache"}


def default_roots(home=None, platform=None):
    home = home or os.path.expanduser("~")
    platform = platform or sys.platform
    if platform == "darwin":
        lib = os.path.join(home, "Library")
        return {
            "resolve_prefs": [os.path.join(lib, "Preferences", "Blackmagic Design")]
            + sorted(glob.glob(os.path.join(lib, "Preferences", "com.blackmagic-design.*"))),
            "resolve_support": [os.path.join(lib, "Application Support", "Blackmagic Design", "DaVinci Resolve"),
                                "/Library/Application Support/Blackmagic Design/DaVinci Resolve"],
            "scheduler": [os.path.join(lib, "LaunchAgents"), "/Library/LaunchAgents", "/Library/LaunchDaemons"],
            "footprint_dirs": [home, lib, os.path.join(lib, "Application Support"), "/Library/Application Support"],
            "editortrack": [os.path.join(home, ".editortrack"), "/Library/Application Support/editortrack"],
        }
    return {
        "resolve_prefs": [os.path.join(home, ".local", "share", "DaVinciResolve", "configs"), "/opt/resolve/configs",
                          os.path.join(home, ".config", "Blackmagic Design")],
        "resolve_support": [os.path.join(home, ".local", "share", "DaVinciResolve")],
        "scheduler": [os.path.join(home, ".config", "systemd", "user"), "/etc/systemd/user"],
        "footprint_dirs": [home, os.path.join(home, ".config"), os.path.join(home, ".local", "share")],
        "editortrack": [os.path.join(home, ".editortrack"), "/etc/editortrack"],
    }


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def file_entry(p):
    st = os.lstat(p)
    e = {"size": st.st_size, "mtime": round(st.st_mtime, 3), "mode": oct(st.st_mode & 0o7777)}
    if os.path.islink(p):
        e["link"] = os.readlink(p)
    elif st.st_size <= HASH_LIMIT:
        try:
            e["sha256"] = sha256(p)
        except OSError as ex:
            e["error"] = type(ex).__name__
    else:
        e["sha256"] = None
    return e


def walk(root, skip=()):
    """{path: entry} for every file under root (or root itself if it is a file)."""
    out = {}
    if os.path.isfile(root) or os.path.islink(root):
        out[root] = file_entry(root)
        return out
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in skip)
        for f in sorted(files):
            p = os.path.join(d, f)
            try:
                out[p] = file_entry(p)
            except OSError as ex:
                out[p] = {"error": type(ex).__name__}
    return out


def _cmd(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def scheduler_lists(platform=None):
    platform = platform or sys.platform
    if platform == "darwin":
        labels = sorted({ln.split()[-1] for ln in _cmd(["launchctl", "list"]).splitlines()[1:] if ln.split()
                         and not ln.split()[-1].startswith("application.")})   # per-launch ids change every boot
        out = {"launchctl_labels": labels}
    else:
        units = sorted(ln.split()[0] for ln in _cmd(["systemctl", "--user", "list-unit-files", "--no-legend",
                                                     "--no-pager"]).splitlines() if ln.split())
        out = {"systemd_user_units": units}
    cron = _cmd(["crontab", "-l"])
    out["crontab_sha256"] = hashlib.sha256(cron.encode()).hexdigest()
    out["crontab_lines"] = [ln for ln in cron.splitlines() if ln.strip() and not ln.startswith("#")]
    return out


def snapshot(roots=None, platform=None, extra=()):
    roots = roots or default_roots(platform=platform)
    snap = {"kit_version": common.KIT_VERSION, "taken": common.iso_local(common.now_epoch()),
            "host": os.uname()[1], "platform": platform or sys.platform, "groups": {}, "roots": roots,
            "missing": [], "lists": scheduler_lists(platform)}
    for group in ("resolve_prefs", "resolve_support", "scheduler"):
        files = {}
        paths = list(roots.get(group, [])) + (list(extra) if group == "resolve_prefs" else [])
        for r in paths:
            if not os.path.exists(r):
                snap["missing"].append(r)
                continue
            skip = SKIP_DIRS | ({"configs"} if group == "resolve_support" else set())
            files.update(walk(r, skip=skip))
        snap["groups"][group] = files
    names = {}
    for d in roots.get("footprint_dirs", []):
        try:
            names[d] = sorted(os.listdir(d))
        except OSError:
            names[d] = None
    snap["footprint"] = names
    snap["editortrack_present"] = [p for p in roots.get("editortrack", []) if os.path.exists(p)]
    return snap


def diff_files(a, b, keys=("sha256", "link", "size")):
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed, touched = [], []
    for p in sorted(set(a) & set(b)):
        if any(a[p].get(k) != b[p].get(k) for k in keys) or a[p].get("mode") != b[p].get("mode"):
            changed.append(p)
        elif a[p].get("mtime") != b[p].get("mtime"):
            touched.append(p)
    return {"added": added, "removed": removed, "changed": changed, "touched": touched}


def compare(before, after, allow=(), expected=()):
    allow = set(allow)
    expected = set(expected)
    res = {"groups": {}, "fail": [], "notes": []}
    for g in ("resolve_prefs", "resolve_support", "scheduler"):
        res["groups"][g] = diff_files(before["groups"].get(g, {}), after["groups"].get(g, {}))
    strict = res["groups"]["resolve_prefs"]
    hard = [p for k in ("added", "removed", "changed") for p in strict[k] if p not in allow]
    churn = [p for k in ("added", "removed", "changed") for p in strict[k] if p in allow]
    unreadable = [p for p, e in after["groups"].get("resolve_prefs", {}).items() if "error" in e]
    unreadable += [p for p, e in before["groups"].get("resolve_prefs", {}).items() if "error" in e]
    if hard:
        res["fail"].append("resolve preferences not byte-identical: %d file(s)" % len(hard))
    if unreadable:
        res["fail"].append("preference file(s) could not be read, so cannot be proven identical: %s"
                           % ", ".join(sorted(set(unreadable))))
    if churn:
        res["check"] = ("%d preference file(s) differ but also differ in the control run without the agent. "
                        "Section 7 asks for byte-identical: accepting Resolve's own churn is an owner decision, and an "
                        "allow-listed file would also hide an agent change to it." % len(churn))
    res["strict_differences"] = hard
    res["control_explained"] = churn
    sched = res["groups"]["scheduler"]
    if sched["added"]:
        res["fail"].append("new scheduler files remain: %s" % ", ".join(sched["added"]))
    lb, la = before.get("lists", {}), after.get("lists", {})
    for k in ("launchctl_labels", "systemd_user_units", "crontab_lines"):
        new = sorted(set(la.get(k) or []) - set(lb.get(k) or []))
        ours = [n for n in new if "editortrack" in n.lower() or "kantana" in n.lower()]
        if ours:
            res["fail"].append("new %s left behind: %s" % (k, ", ".join(ours)))
        if len(new) > len(ours):
            res["notes"].append("other new %s (not ours, check by eye): %s" % (k, ", ".join(n for n in new
                                                                                             if n not in ours)[:400]))
    for d, names in (after.get("footprint") or {}).items():
        old = set((before.get("footprint") or {}).get(d) or [])
        new = sorted(set(names or []) - old)
        if new:
            bad = [n for n in new if "editortrack" in n.lower() and n not in expected]
            if bad:
                res["fail"].append("editortrack remnants in %s: %s" % (d, ", ".join(bad)))
            res["notes"].append("new names in %s: %s" % (d, ", ".join(new[:20])))
    if after.get("editortrack_present"):
        res["fail"].append("editortrack paths still present: %s" % ", ".join(after["editortrack_present"]))
    if before.get("platform") != after.get("platform") or before.get("host") != after.get("host"):
        res["fail"].append("snapshots are from different machines")
    res["ok"] = not res["fail"] and not res.get("check")
    res["verdict"] = "FAIL" if res["fail"] else ("CHECK" if res.get("check") else "PASS")
    return res


def load(p):
    import json
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True)
    s.add_argument("--extra", action="append", default=[], help="more strict preference paths (repeatable)")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--allow", help="JSON list of paths that change in a control run without the agent")
    c.add_argument("--emit-allow", help="write this pair's strict differences as an allow list (control run only)")
    c.add_argument("--expected", action="append", default=[],
                   help="a new name that is expected (e.g. the agent code folder when not purged)")
    c.add_argument("--json")
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        snap = snapshot(extra=a.extra)
        common.write_json(os.path.expanduser(a.out), snap)
        n = sum(len(v) for v in snap["groups"].values())
        print("snapshot: %d files (%d strict preference files), missing roots: %s -> %s"
              % (n, len(snap["groups"]["resolve_prefs"]), ", ".join(snap["missing"]) or "none", a.out))
        if not snap["groups"]["resolve_prefs"]:
            print("WARNING: no Resolve preference files found - check the paths for this Resolve version (--extra)")
        return 0
    if a.cmd == "compare":
        before, after = load(a.before), load(a.after)
        allow = load(a.allow) if a.allow else []
        res = compare(before, after, allow, a.expected)
        if a.emit_allow:
            common.write_json(a.emit_allow, res["strict_differences"] + res["control_explained"])
            print("allow list written (use ONLY from a control run without the agent): %s" % a.emit_allow)
        for g, d in res["groups"].items():
            print("%-16s added %d  removed %d  changed %d  touched(mtime only) %d" %
                  (g, len(d["added"]), len(d["removed"]), len(d["changed"]), len(d["touched"])))
        for p in res["strict_differences"]:
            print("  DIFF  %s" % p)
        for p in res["control_explained"]:
            print("  churn %s" % p)
        for n in res["notes"]:
            print("note: " + n)
        for f in res["fail"]:
            print("FAIL: " + f)
        if res.get("check"):
            print("CHECK: " + res["check"])
        print("%s  U1 Resolve preferences byte-identical, no scheduler/footprint left" % res["verdict"])
        if a.json:
            common.write_json(a.json, res)
        return {"PASS": 0, "FAIL": 1}.get(res["verdict"], 2)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

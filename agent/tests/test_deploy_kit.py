"""SAFETY: deploy kit — uninstall leaves nothing, kill switch works, resource caps present,
no shared secret / default server in the repo, station templates keep the fail-closed gates.

Runs the real shell scripts with stub systemctl/launchctl/pgrep/pkill/sudo on PATH and a fake
HOME, so nothing on this machine is touched. marker: safety
"""
import os, pathlib, stat, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tests.util import agent_env, AGENT            # noqa: E402

SAFETY = True
DEPLOY = AGENT / "deploy"
REPO = AGENT.parent


def stub_env(tmp, uname="Linux"):
    bin_ = tmp / "bin"
    bin_.mkdir()
    log = tmp / "calls.log"
    for name, code in (("systemctl", 0), ("launchctl", 1), ("pkill", 0), ("pgrep", 1), ("sudo", 0), ("uname", 0)):
        body = '#!/bin/sh\necho "%s $*" >> "%s"\n' % (name, log)
        if name == "uname":
            body += 'echo %s\n' % uname
        if name == "systemctl":
            body += 'case "$*" in *is-active*) exit 3;; esac\n'
        if name == "sudo":
            body += '"$@"\n'
        body += "exit %d\n" % code
        p = bin_ / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    home = tmp / "home"
    home.mkdir()
    env = {"PATH": "%s:/usr/bin:/bin" % bin_, "HOME": str(home)}
    return env, home, log


class Uninstall(unittest.TestCase):
    def _installed(self, home):
        u = home / ".config/systemd/user"
        u.mkdir(parents=True)
        (u / "editortrack-agent.service").write_text("x")
        (u / "editortrack-agent.timer").write_text("x")
        st = home / ".editortrack"
        st.mkdir()
        for f in ("queue.jsonl", "grade_cache.json", "agent.log", "lease.json"):
            (st / f).write_text("x")
        other = home / "Resolve Project Library"        # must never be touched
        other.mkdir()
        (other / "keep.db").write_text("client")
        return other

    def test_uninstall_removes_everything_and_verifies(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        env, home, log = stub_env(tmp)
        other = self._installed(home)
        r = subprocess.run(["sh", str(DEPLOY / "uninstall.sh")], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((home / ".editortrack").exists())
        self.assertFalse((home / ".config/systemd/user/editortrack-agent.timer").exists())
        self.assertTrue((other / "keep.db").exists())
        self.assertIn("systemctl --user disable --now editortrack-agent.timer", log.read_text())
        v = subprocess.run(["sh", str(DEPLOY / "uninstall.sh"), "--verify"], env=env, capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stdout)

    def test_verify_detects_remnants(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        env, home, log = stub_env(tmp)
        self._installed(home)
        v = subprocess.run(["sh", str(DEPLOY / "uninstall.sh"), "--verify"], env=env, capture_output=True, text=True)
        self.assertEqual(v.returncode, 1)
        self.assertIn(".editortrack", v.stdout)

    def test_dry_run_changes_nothing(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        env, home, log = stub_env(tmp)
        self._installed(home)
        r = subprocess.run(["sh", str(DEPLOY / "uninstall.sh"), "--dry-run"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertTrue((home / ".editortrack/queue.jsonl").exists())


class KillSwitchScript(unittest.TestCase):
    def test_on_makes_agent_silent_and_off_reenables(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        env, home, log = stub_env(tmp)
        state = tmp / "state"
        env["EDITORTRACK_STATE_DIR"] = str(state)
        r = subprocess.run(["sh", str(DEPLOY / "killswitch.sh"), "on"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("systemctl --user stop", log.read_text())
        with agent_env(EDITORTRACK_STATE_DIR=str(state)) as (m, sd):
            self.assertTrue(m["safety"].kill_switch().startswith("file:"))
        subprocess.run(["sh", str(DEPLOY / "killswitch.sh"), "off"], env=env, check=True, capture_output=True)
        with agent_env(EDITORTRACK_STATE_DIR=str(state)) as (m, sd):
            self.assertIsNone(m["safety"].kill_switch())


class Kit(unittest.TestCase):
    def test_resource_caps_present(self):
        svc = (DEPLOY / "editortrack-agent.service").read_text()
        for k in ("Nice=19", "IOSchedulingClass=idle", "CPUQuota=", "MemoryMax=", "TimeoutStartSec="):
            self.assertIn(k, svc)
        pl = (DEPLOY / "com.kantana.editortrack.agent.plist").read_text()
        self.assertIn("<key>Nice</key><integer>19</integer>", pl)
        self.assertIn("<key>LowPriorityIO</key><true/>", pl)
        self.assertIn("nice -n 19", (DEPLOY / "run-agent.sh").read_text())

    def test_no_shared_token_or_default_server_in_code(self):
        r = subprocess.run(["git", "-C", str(REPO), "grep", "-n", "-I", "-e", "kpp-et-test", "--", ".",
                            ":!*.md", ":!agent/tests/*"], capture_output=True, text=True)
        self.assertEqual(r.stdout, "", "leaked token still in the repo")
        self.assertNotIn("192.168.", (DEPLOY / "make-env.sh").read_text())

    def test_station_templates_keep_gates(self):
        for f in list((DEPLOY / "stations").glob("*.sh")) + [DEPLOY / "env.example"]:
            s = f.read_text()
            self.assertIn("export EDITORTRACK_ALLOW_EXPORT_LUT=0", s, f)
            self.assertIn("export EDITORTRACK_RESOLVE_VERIFIED=", s, f)
            for bad in ("EDITORTRACK_POLL_RENDER=1", "EDITORTRACK_REQUIRE_LEASE", "EDITORTRACK_DISABLE=0"):
                self.assertNotIn(bad, s, f)

    def test_hard_timeout_below_scheduler_timeout(self):
        import re
        svc = (DEPLOY / "editortrack-agent.service").read_text()
        t = int(re.search(r"TimeoutStartSec=(\d+)", svc).group(1))
        with agent_env(EDITORTRACK_HARD_TIMEOUT_SEC=None) as (m, sd):
            self.assertLess(m["safety"].HARD_TIMEOUT_SEC, t)


if __name__ == "__main__":
    unittest.main()

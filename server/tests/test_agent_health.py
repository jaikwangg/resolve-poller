"""SAFETY/MAINTENANCE: server-side lease (fleet kill switch, version pin) and agent health.

Includes an end-to-end check with the real FastAPI app under uvicorn and the real agent
runner against the mock Resolve: flipping agent_control.json stops every Resolve call on the
next turn. marker: safety
run: cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_agent_health -v
"""
import datetime, json, os, pathlib, sys, tempfile, threading, time, unittest

HERE = pathlib.Path(__file__).parent
TMP = tempfile.mkdtemp(prefix="et-health-")
import atexit, shutil                                  # noqa: E402
atexit.register(shutil.rmtree, TMP, True)
os.environ["EDITORTRACK_STORE"] = TMP
for k in [k for k in os.environ if k.startswith("ERP_")]:
    del os.environ[k]
sys.path.insert(0, str(HERE.parent))
from tests import authfix          # noqa: E402
authfix.install(hosts=("x", "e2e"))

import agent_health as ah          # noqa: E402
import health_alert                # noqa: E402

MARKERS = {"safety"}   # shared marker convention: ./run_tests.sh safety
CTRL = pathlib.Path(TMP) / "agent_control.json"
# the agent's own version (the e2e test runs the real agent, which must get a lease)
import re as _re                   # noqa: E402
V = _re.search(r'AGENT_VERSION = "([^"]+)"', (HERE.parents[1] / "agent" / "safety.py").read_text()).group(1)


def write_ctrl(obj):
    CTRL.write_text(json.dumps(obj))


class Control(unittest.TestCase):
    def tearDown(self):
        if CTRL.exists():
            CTRL.unlink()

    def test_fail_closed(self):
        self.assertFalse(ah.control_for(TMP, "grade-01", V)["enabled"])            # no file
        CTRL.write_text("{broken")
        self.assertFalse(ah.control_for(TMP, "grade-01", V)["enabled"])            # corrupt
        write_ctrl({"default": {"enabled": True}})
        self.assertFalse(ah.control_for(TMP, "grade-01", V)["enabled"])            # no version pin
        write_ctrl({"allowed_versions": [V], "default": {"enabled": False}, "hosts": {"grade-01": {"enabled": True}}})
        self.assertFalse(ah.control_for(TMP, "grade-02", V)["enabled"])            # not listed, default off
        self.assertFalse(ah.control_for(TMP, "grade-01", "0.8")["enabled"])        # old version
        c = ah.control_for(TMP, "grade-01", V)
        self.assertTrue(c["enabled"])
        self.assertFalse(c["allow_resolve"])                                        # must be explicit

    def test_http_requires_token(self):
        from fastapi.testclient import TestClient
        import dashboard_api
        cl = TestClient(dashboard_api.app)
        self.assertEqual(cl.get("/agent/control?host=x&v=" + V).status_code, 401)
        write_ctrl({"allowed_versions": [V], "hosts": {"x": {"enabled": True, "allow_resolve": True}}})
        r = cl.get("/agent/control?host=x&v=" + V, headers=authfix.machine("x"))
        self.assertEqual(r.json()["enabled"], True)


def rec(host, minutes_ago, ok=True, **agent):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    a = {"version": V, "rss_mb": 25, "turn_ms": 400, "guard": {"ms": 40}, "tripped": None,
         "backoff_fails": 0, "queue_lines": 0}
    a.update(agent)
    return {"host": host, "ts": t.isoformat(), "source": {"ok": ok} if ok else {"ok": False, "reason": "user_active"},
            "agent": a}


class Health(unittest.TestCase):
    def test_statuses(self):
        ctl = {"allowed_versions": [V]}
        rows = ah.health([rec("a", 1), rec("b", 1, tripped="page_changed_while_idle"), rec("c", 30),
                          rec("d", 1, version="0.1"), rec("e", 1, rss_mb=400), rec("f", 1, queue_lines=5000)],
                         control=ctl)
        self.assertEqual(rows["a"]["status"], "ok")
        self.assertEqual(rows["b"]["status"], "alert")
        self.assertEqual(rows["c"]["status"], "warn")
        self.assertEqual(rows["d"]["status"], "alert")
        self.assertEqual(rows["e"]["status"], "alert")
        self.assertEqual(rows["f"]["status"], "warn")

    def test_rejected_records_reported_by_the_agent(self):
        # ET-5 heartbeat: cumulative "rejected"; the row shows the 24 h increase, an old agent shows nothing
        rows = ah.health([rec("a", 60 * 30, rejected=3), rec("a", 60, rejected=3), rec("a", 1, rejected=5),
                          rec("b", 60, rejected=2), rec("b", 1, rejected=2),
                          rec("c", 1), rec("d", 1, rejected="x"), rec("e", 1, rejected=0, reject_hold=True),
                          rec("f", 60, rejected=7), rec("f", 1, rejected=1)])
        self.assertEqual((rows["a"]["rejected"], rows["a"]["rejected_24h"], rows["a"]["status"]), (5, 2, "warn"))
        self.assertTrue(any("refused 2 record" in w for w in rows["a"]["warnings"]))
        self.assertEqual((rows["b"]["rejected_24h"], rows["b"]["status"]), (0, "ok"))
        self.assertEqual((rows["c"]["rejected"], rows["c"]["rejected_24h"], rows["c"]["status"]), (None, 0, "ok"))
        self.assertEqual((rows["d"]["rejected"], rows["d"]["status"]), (None, "ok"))
        self.assertEqual(rows["e"]["status"], "warn")
        self.assertTrue(any("reject hold" in w for w in rows["e"]["warnings"]))
        self.assertEqual(rows["f"]["rejected_24h"], 1)                  # counter reset (reinstall)

    def test_ongoing_condition_notifies_once_while_counts_change(self):
        # review 2 Medium 1: refused every minute / PMS hanging -> one message at start, one at clear (5-min timer)
        now = datetime.datetime.now(datetime.timezone.utc)
        st, sent = {}, []
        for i in range(4):
            t = (now - datetime.timedelta(minutes=10 - i)).isoformat()
            refused = {"a": {"sent": "a2", "route": "ingest", "count": 7 + 5 * i, "first_at": (now - datetime.timedelta(minutes=30)).isoformat(),
                             "last_at": t}}
            rows = ah.health([rec("a", 60 * (40 - i))], refused=refused, now=now)
            rows[health_alert.SERVER_ROW] = {"status": "alert", "warnings": [], "alerts": [
                "PMS progress: %d step(s) not sent since %s (still queued at shutdown (drain cap 10s), last %s)"
                % (3 + i, now.isoformat(), t)]}
            m, st = health_alert.transitions(st, rows)
            sent += m
        self.assertEqual(len(sent), 2, sent)                 # host "a" once, the server row once
        self.assertTrue(any(s.startswith("🔴 editortrack server (PMS progress): alert") for s in sent), sent)
        rows = ah.health([rec("a", 1)], now=now)
        rows[health_alert.SERVER_ROW] = {"status": "ok", "alerts": [], "warnings": []}
        m, st = health_alert.transitions(st, rows)
        self.assertEqual(len(m), 2)                          # both clear
        self.assertNotIn(health_alert.SERVER_ROW, " ".join(m))

    def test_alerts_only_on_transition(self):
        rows = ah.health([rec("a", 1, tripped="x")])
        m1, st = health_alert.transitions({}, rows)
        self.assertEqual(len(m1), 1)
        m2, st = health_alert.transitions(st, rows)
        self.assertEqual(m2, [])
        m3, st = health_alert.transitions(st, ah.health([rec("a", 1)]))
        self.assertEqual(len(m3), 1)            # recovery message
        self.assertIn("ok", m3[0])


class EndToEnd(unittest.TestCase):
    """real server app + real agent turn: remote kill switch stops Resolve calls next turn."""

    def test_fleet_kill_switch(self):
        import uvicorn, dashboard_api
        cfg = uvicorn.Config(dashboard_api.app, host="127.0.0.1", port=0, log_level="error")
        srv = uvicorn.Server(cfg)
        th = threading.Thread(target=srv.run, daemon=True)
        th.start()
        for _ in range(100):
            if srv.started:
                break
            time.sleep(0.05)
        port = srv.servers[0].sockets[0].getsockname()[1]
        try:
            import importlib.util
            agent = HERE.parents[1] / "agent"

            def load(name):
                spec = importlib.util.spec_from_file_location("agent_" + name, agent / "tests" / (name + ".py"))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
            agent_env = load("util").agent_env
            mr = load("mock_resolve")
            World, loader = mr.World, mr.loader
            write_ctrl({"allowed_versions": [V], "hosts": {"e2e": {"enabled": True, "allow_resolve": True}}})
            env = dict(EDITORTRACK_SERVER="http://127.0.0.1:%d/ingest" % port, EDITORTRACK_TOKEN=authfix.machine_token("e2e"),
                       # agent_env clears EDITORTRACK_*; the in-process server needs its auth files back
                       EDITORTRACK_TOKEN_MAP=os.environ["EDITORTRACK_TOKEN_MAP"],
                       EDITORTRACK_READ_USERS=os.environ["EDITORTRACK_READ_USERS"],
                       EDITORTRACK_REQUIRE_LEASE=None)
            with agent_env(**env) as (m, sd):
                m["runner"].socket.gethostname = lambda: "e2e"
                m["runner"].sample = lambda: {"idle_sec": 30}
                w1 = World()
                m["resolve_poller"]._load_resolve = loader(w1)
                self.assertEqual(m["runner"].main(), 200)
                self.assertTrue(w1.calls)
                # ET-5 against the real /ingest: a queued record the server refuses (400) is dropped and the
                # record behind it is delivered; the next heartbeat carries the count to /api/health
                ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
                # (per-host tokens: the deliverable record carries this machine's host; the refused one keeps a
                # foreign host on purpose - record_problem runs before the host check, so it is a final 400, not a 403)
                m["runner"]._enqueue([json.dumps({"host": "e2e-q", "ts": ts, "agent": "not a dict"}),
                                      json.dumps({"host": "e2e", "ts": ts, "n": 2})])
                self.assertEqual(m["runner"].main(), 200)
                self.assertFalse((sd / "queue.jsonl").exists())
                st = json.loads((sd / "rejected.json").read_text())
                self.assertEqual(st["last"]["status"], 400)
                self.assertIn("bad_type:agent", st["last"]["reason"])     # X-Editortrack-Reject from the real server
                self.assertEqual(m["runner"].main(), 200)
                write_ctrl({"allowed_versions": [V], "hosts": {"e2e": {"enabled": False}}})
                w2 = World()
                m["resolve_poller"]._load_resolve = loader(w2)
                self.assertIsNone(m["runner"].main())
                self.assertEqual(w2.calls, [])
            recs = [json.loads(l) for f in pathlib.Path(TMP).glob("*.jsonl") for l in f.read_text().splitlines()]
            self.assertEqual(ah.health(recs)["e2e"]["version"], V)
            self.assertEqual([r.get("n") for r in recs if "n" in r], [2])
            self.assertFalse([r for r in recs if r["host"] == "e2e-q"])
            self.assertEqual(ah.health(recs)["e2e"]["rejected"], 1)
        finally:
            srv.should_exit = True
            th.join(5)


class RolloutPreflight(unittest.TestCase):
    """review High 1: an agent upgraded before the server lists its version records nothing once its lease ends.
    agent/deploy/preflight.py (run by install.sh) asks the REAL server exactly like the agent and refuses."""

    @classmethod
    def setUpClass(cls):
        import uvicorn, dashboard_api
        cls.srv = uvicorn.Server(uvicorn.Config(dashboard_api.app, host="127.0.0.1", port=0, log_level="error"))
        cls.th = threading.Thread(target=cls.srv.run, daemon=True)
        cls.th.start()
        for _ in range(100):
            if cls.srv.started:
                break
            time.sleep(0.05)
        cls.port = cls.srv.servers[0].sockets[0].getsockname()[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.should_exit = True
        cls.th.join(5)

    def tearDown(self):
        if CTRL.exists():
            CTRL.unlink()

    def run_preflight(self, host="e2e", token=None):
        import subprocess
        tok = token or authfix.machine_token("e2e")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": TMP,
               "EDITORTRACK_SERVER": "http://127.0.0.1:%d/ingest" % self.port,
               "EDITORTRACK_TOKEN": tok, "EDITORTRACK_HOST": host}
        r = subprocess.run([sys.executable, "-B", str(HERE.parents[1] / "agent" / "deploy" / "preflight.py")],
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertNotIn(tok, r.stdout + r.stderr)                 # the token is never printed
        return r

    def test_new_version_refused_until_allowed_versions_lists_it(self):
        old = "0.9.1-safety"
        self.assertNotEqual(V, old)
        write_ctrl({"allowed_versions": [old], "hosts": {"e2e": {"enabled": True}}})     # server not updated yet
        r = self.run_preflight()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PREFLIGHT FAILED", r.stderr)
        self.assertIn("version_not_allowed", r.stderr)
        self.assertIn(V, r.stderr)
        write_ctrl({"allowed_versions": [old, V], "hosts": {"e2e": {"enabled": True}}})  # the documented order
        r = self.run_preflight()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("grants agent %s a lease" % V, r.stdout)

    def test_every_other_refusal_fails_loudly(self):
        write_ctrl({"allowed_versions": [V], "hosts": {"e2e": {"enabled": False}}})
        self.assertIn("host_disabled", self.run_preflight().stderr)
        CTRL.unlink()
        self.assertIn("no_control_file", self.run_preflight().stderr)
        write_ctrl({"allowed_versions": [V], "hosts": {"e2e": {"enabled": True}}})
        r = self.run_preflight(host="editbay-7")
        self.assertEqual(r.returncode, 1)
        self.assertIn("EDITORTRACK_HOST=e2e", r.stderr)
        r = self.run_preflight(token="not-a-real-token-0000000000000000")
        self.assertEqual(r.returncode, 1)
        self.assertIn("401", r.stderr)

    def test_shipped_docs_state_the_order(self):
        root = HERE.parents[1]
        rd = (root / "agent" / "deploy" / "README.md").read_text(encoding="utf-8")
        self.assertIn("Rollout order", rd)
        self.assertIn('"allowed_versions": ["0.9.1-safety", "0.9.2-safety"]', rd)
        self.assertIn("deploy/preflight.py", (root / "agent" / "deploy" / "install.sh").read_text(encoding="utf-8"))
        self.assertIn("single worker", (root / "server" / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

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
os.environ["EDITORTRACK_TOKEN"] = "t"
for k in [k for k in os.environ if k.startswith("ERP_")]:
    del os.environ[k]
sys.path.insert(0, str(HERE.parent))

import agent_health as ah          # noqa: E402
import health_alert                # noqa: E402

MARKERS = {"safety"}   # shared marker convention: ./run_tests.sh safety
CTRL = pathlib.Path(TMP) / "agent_control.json"
V = "0.9.0-safety"


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
        r = cl.get("/agent/control?host=x&v=" + V, headers={"Authorization": "Bearer t"})
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
            env = dict(EDITORTRACK_SERVER="http://127.0.0.1:%d/ingest" % port, EDITORTRACK_TOKEN="t",
                       EDITORTRACK_REQUIRE_LEASE=None)
            with agent_env(**env) as (m, sd):
                m["runner"].socket.gethostname = lambda: "e2e"
                m["runner"].sample = lambda: {"idle_sec": 30}
                w1 = World()
                m["resolve_poller"]._load_resolve = loader(w1)
                self.assertEqual(m["runner"].main(), 200)
                self.assertTrue(w1.calls)
                write_ctrl({"allowed_versions": [V], "hosts": {"e2e": {"enabled": False}}})
                w2 = World()
                m["resolve_poller"]._load_resolve = loader(w2)
                self.assertIsNone(m["runner"].main())
                self.assertEqual(w2.calls, [])
            recs = [json.loads(l) for f in pathlib.Path(TMP).glob("*.jsonl") for l in f.read_text().splitlines()]
            self.assertEqual(ah.health(recs)["e2e"]["version"], V)
        finally:
            srv.should_exit = True
            th.join(5)


if __name__ == "__main__":
    unittest.main()

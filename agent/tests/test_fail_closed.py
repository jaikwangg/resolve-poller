"""SAFETY: on any doubt the agent does nothing that turn (no Resolve call) and stays quiet.

Covers: kill switch (env, local file, agent-dir file, unreadable), lease (missing, expired,
disabled, server down, allow_resolve=false), single-instance lock, circuit breaker, backoff
(no retry storm), and every poller pre-check (Resolve not running, not Studio, unverified
version, no project, loading, Deliver page, rendering, playback, user active, idle unknown),
post-check (page changed during scan), hard watchdog.

marker: safety
"""
import json, os, pathlib, subprocess, sys, time, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tests.util import agent_env, idle, AGENT        # noqa: E402
from tests.mock_resolve import World, loader          # noqa: E402
from tests.fake_server import FakeServer              # noqa: E402

SAFETY = True
PREFLIGHT = {"GetProductName", "GetVersionString", "GetProjectManager", "GetCurrentProject",
             "GetCurrentPage", "IsRenderingInProgress"}


def act(idle_sec=30):
    return lambda: {"idle_sec": idle_sec, "frontmost_name": "DaVinci Resolve", "resolve_frontmost": True}


def turn(m, world, idle_sec=30):
    """one runner turn with the mock Resolve and a fixed idle time"""
    m["resolve_poller"]._load_resolve = loader(world)
    m["runner"].sample = act(idle_sec)
    return m["runner"].main()


class KillSwitch(unittest.TestCase):
    def _assert_silent(self, srv, w, status):
        self.assertIsNone(status)
        self.assertEqual(w.calls, [], "Resolve touched while disabled")
        self.assertEqual(srv.requests, [], "network used while disabled")

    def test_env(self):
        with FakeServer() as srv, agent_env(EDITORTRACK_DISABLE="1", EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            w = World()
            self._assert_silent(srv, w, turn(m, w))

    def test_local_file(self):
        with FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            sd.mkdir(parents=True)
            (sd / "DISABLED").write_text("")
            w = World()
            self._assert_silent(srv, w, turn(m, w))

    def test_unreadable_means_disabled(self):
        with FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            sd.mkdir(parents=True)
            (sd / "DISABLED").write_text("")
            os.chmod(sd, 0o000)                 # stat() -> PermissionError
            try:
                if os.geteuid() == 0:
                    self.skipTest("root ignores permissions")
                self.assertTrue(m["safety"].kill_switch().startswith("unreadable"))
                w = World()
                self._assert_silent(srv, w, turn(m, w))
            finally:
                os.chmod(sd, 0o700)

    def test_falsey_env_values_do_not_disable(self):
        for v in ("0", "false", "no", "off", ""):
            with agent_env(EDITORTRACK_DISABLE=v) as (m, sd):
                self.assertIsNone(m["safety"].kill_switch())


class Lease(unittest.TestCase):
    def _env(self, srv):
        return dict(EDITORTRACK_SERVER=srv.url + "/ingest", EDITORTRACK_REQUIRE_LEASE=None)

    def test_granted_lease_polls(self):
        with FakeServer() as srv, agent_env(**self._env(srv)) as (m, sd):
            w = World()
            self.assertEqual(turn(m, w), 200)
            self.assertTrue(w.calls)
            self.assertTrue(srv.posts()[0]["source"].get("ok"), srv.posts()[0]["source"])

    def test_disabled_by_server(self):
        with FakeServer(control={"enabled": False}) as srv, agent_env(**self._env(srv)) as (m, sd):
            w = World()
            self.assertIsNone(turn(m, w))
            self.assertEqual(w.calls, [])
            self.assertEqual(srv.posts(), [])

    def test_no_control_endpoint_means_no_poll(self):
        with FakeServer(control="down") as srv, agent_env(**self._env(srv)) as (m, sd):
            w = World()
            self.assertIsNone(turn(m, w))
            self.assertEqual(w.calls, [])

    def test_lease_survives_short_outage_then_expires(self):
        with FakeServer(control={"enabled": True, "lease_sec": 2}) as srv, agent_env(**self._env(srv)) as (m, sd):
            w = World()
            self.assertEqual(turn(m, w), 200)
            srv.control = "down"
            self.assertEqual(turn(m, World()), 200)     # still inside the lease
            time.sleep(2.2)
            w3 = World()
            self.assertIsNone(turn(m, w3))              # expired -> silent
            self.assertEqual(w3.calls, [])

    def test_corrupt_lease_file(self):
        with agent_env(EDITORTRACK_REQUIRE_LEASE=None) as (m, sd):
            sd.mkdir(parents=True)
            (sd / "lease.json").write_text("{not json")
            self.assertFalse(m["safety"].lease_ok())

    def test_allow_resolve_false_sends_heartbeat_only(self):
        ctl = {"enabled": True, "lease_sec": 600, "allow_resolve": False}
        with FakeServer(control=ctl) as srv, agent_env(**self._env(srv)) as (m, sd):
            w = World()
            self.assertEqual(turn(m, w), 200)
            self.assertEqual(w.calls, [])
            self.assertEqual(srv.posts()[0]["source"]["reason"], "lease_no_resolve")

    def test_station_templates_never_disable_the_lease(self):
        for f in (AGENT / "deploy").rglob("*"):
            if f.is_file():
                self.assertNotIn("EDITORTRACK_REQUIRE_LEASE", f.read_text(errors="ignore"), f)


class Breaker(unittest.TestCase):
    def test_lock_held_skips(self):
        with agent_env() as (m, sd):
            a = m["safety"].Lock()
            self.assertTrue(a.acquire())
            w = World()
            self.assertIsNone(turn(m, w))
            self.assertEqual(w.calls, [])

    def test_tripped_sends_heartbeat_without_resolve(self):
        with FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            m["safety"].trip("test")
            w = World()
            self.assertEqual(turn(m, w), 200)
            self.assertEqual(w.calls, [])
            p = srv.posts()[0]
            self.assertTrue(p["source"]["reason"].startswith("tripped"))
            self.assertEqual(p["agent"]["tripped"], "test")

    def test_page_change_while_user_idle_trips(self):
        """the user is idle the whole time yet the page changes: assume it was us, stop for good."""
        with FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            w = World(n_clips=10)
            w.on_call = lambda world, name: setattr(world, "page", "edit") if len(world.calls) == 30 else None
            turn(m, w)
            self.assertEqual(m["safety"].tripped(), "page_changed_while_idle")
            w2 = World()
            turn(m, w2)
            self.assertEqual(w2.calls, [])

    def test_page_change_by_active_user_discards_but_does_not_trip(self):
        with agent_env() as (m, sd):
            w = World(n_clips=10)
            w.on_call = lambda world, name: setattr(world, "page", "edit") if len(world.calls) == 30 else None
            seq = iter([30, 0.5, 0.5, 0.5])
            snap = m["resolve_poller"].snapshot(idle_fn=lambda: next(seq), load=loader(w))
            self.assertEqual(snap["reason"], "state_changed_during_scan")
            self.assertFalse(snap["suspect_agent"])
            self.assertNotIn("OpenPage", w.calls)        # never "restores" the page
            self.assertEqual(w.page, "edit")

    def test_forbidden_call_trips(self):
        with FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            rp = m["resolve_poller"]
            rp._job_hint = lambda markers: (_ for _ in ()).throw(__import__("resolve_guard").ForbiddenCall("OpenPage"))
            w = World()
            turn(m, w)
            self.assertEqual(m["safety"].tripped(), "forbidden_call")
            self.assertNotIn("OpenPage", w.calls)

    def test_exception_backs_off_no_retry_storm(self):
        with FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            loads = []

            def broken(ctx=None):
                loads.append(1)
                raise RuntimeError("fusionscript exploded")
            m["runner"].sample = act(30)
            m["resolve_poller"]._load_resolve = broken
            for _ in range(10):
                m["runner"].main()
            self.assertEqual(len(loads), 1, "retried Resolve inside the backoff window")
            b = json.loads((sd / "backoff.json").read_text())
            self.assertEqual(b["fails"], 1)
            self.assertGreaterEqual(b["next_at"] - time.time(), 50)
            self.assertEqual(len(srv.posts()), 10)      # heartbeats keep flowing

    def test_backoff_is_exponential_and_capped(self):
        with agent_env() as (m, sd):
            s = m["safety"]
            waits = [s.backoff_fail("x", now=1000.0) for _ in range(10)]
            self.assertEqual(waits[:4], [60, 120, 240, 480])
            self.assertEqual(max(waits), 3600)


class PreChecks(unittest.TestCase):
    def snap(self, world=None, idle_sec=30, **env):
        with agent_env(**env) as (m, sd):
            w = world or World()
            return m["resolve_poller"].snapshot(idle_fn=idle(idle_sec) if idle_sec is not None else (lambda: None),
                                                load=loader(w)), w

    def test_resolve_not_running(self):
        with agent_env() as (m, sd):
            s = m["resolve_poller"].snapshot(idle_fn=idle(30), load=lambda ctx=None: None)
            self.assertEqual(s["reason"], "resolve_not_running")

    def test_skip_reasons(self):
        cases = [
            (dict(world=World(product="DaVinci Resolve")), "not_studio"),
            (dict(world=World(version="21.0.0")), "resolve_version_unverified"),
            (dict(world=World(), EDITORTRACK_RESOLVE_VERIFIED=None), "resolve_version_unverified"),
            (dict(world=World(page=None)), "resolve_busy"),
            (dict(world=World(page="deliver")), "page_deliver"),
            (dict(world=World(rendering=True)), "rendering"),
            (dict(world=World(playing=True)), "playback"),
            (dict(world=World(), idle_sec=1), "user_active"),
            (dict(world=World(), idle_sec=None), "idle_unknown"),
        ]
        for kw, reason in cases:
            with self.subTest(reason=reason):
                s, w = self.snap(**kw)
                self.assertFalse(s["ok"])
                self.assertEqual(s["reason"], reason)
                # a skipped turn makes only the cheap pre-flight reads (+2 timecode reads for playback)
                self.assertLessEqual(set(w.calls) - PREFLIGHT, {"GetCurrentTimeline", "GetCurrentTimecode"})
                self.assertLessEqual(len(w.calls), 12)

    def test_no_project(self):
        s, w = self.snap(world=World(no_project=True))
        self.assertEqual(s["reason"], "no_project_open")

    def test_version_prefix_match(self):
        with agent_env(EDITORTRACK_RESOLVE_VERIFIED="20.2,19.1.4") as (m, sd):
            v = m["resolve_poller"].version_verified
            self.assertTrue(v("20.2.1.5"))
            self.assertTrue(v("19.1.4"))
            self.assertFalse(v("19.1.3"))
            self.assertFalse(v("20.20.0"))
            self.assertFalse(v(None))


class Watchdog(unittest.TestCase):
    def test_hard_timeout_kills_a_hung_turn(self):
        code = ("import sys,time; sys.path.insert(0, %r); import safety; safety.hard_timeout(1); time.sleep(30)"
                % str(AGENT))
        t0 = time.time()
        r = subprocess.run([sys.executable, "-c", code], timeout=20)
        self.assertEqual(r.returncode, 3)
        self.assertLess(time.time() - t0, 10)


if __name__ == "__main__":
    unittest.main()

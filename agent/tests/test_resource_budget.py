"""SAFETY: bounded cost per poll, bounded local disk, no writes outside the agent's state dir.

Measures a mock 3000-clip timeline in a fresh process (wall time, API calls, peak RSS) and
checks the budgets: calls per clip, deadline abort, MAX_CLIPS counts-only mode, queue caps,
flush caps, and that one full turn writes only under EDITORTRACK_STATE_DIR (+ the agent's
own temp dir for ExportLUT when that gate is on).

`python3 -m tests.test_resource_budget --report` prints the measurement table.
marker: safety
"""
import json, os, pathlib, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tests.util import agent_env, idle, BASE_ENV, AGENT     # noqa: E402
from tests.mock_resolve import World, loader                 # noqa: E402
from tests.fake_server import FakeServer                     # noqa: E402

MARKERS = {"safety"}   # shared marker convention: ./run_tests.sh safety
RUN_TURN = str(pathlib.Path(__file__).with_name("run_turn.py"))


def run_turn(server, n_clips, tracks=4, latency_us=0, scan_only=False, **env):
    tmp = tempfile.mkdtemp(prefix="et-turn-")
    e = {k: v for k, v in os.environ.items() if not k.startswith("EDITORTRACK_")}
    e.update(BASE_ENV)
    e["EDITORTRACK_STATE_DIR"] = os.path.join(tmp, "state")
    e["HOME"] = os.path.join(tmp, "home")
    os.makedirs(e["HOME"])
    e.update({k: str(v) for k, v in env.items()})
    args = [sys.executable, RUN_TURN, server, str(n_clips), str(tracks), str(latency_us)] + (["scan_only"] if scan_only else [])
    r = subprocess.run(args, env=e, cwd=tmp, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-2000:])
    out = json.loads(r.stdout.strip().splitlines()[-1])
    out["tmp"] = tmp
    out["state"] = e["EDITORTRACK_STATE_DIR"]
    return out


class ScanBudget(unittest.TestCase):
    def test_3000_clips_nodes_fast_path(self):
        r = run_turn("http://127.0.0.1:9", 3000, scan_only=True)
        self.assertIsNone(r["reason"])
        self.assertTrue(r["per_clip"])
        calls = r["guard"]["calls"]
        self.assertLessEqual(calls / 3000, 4.1, "calls per clip grew (nodes + shots should be 4)")
        self.assertLess(r["maxrss_mb"], 120)
        self.assertEqual(r["page"], "color")

    def test_deadline_aborts_whole_poll(self):
        # 1 ms per call (slow Resolve): 12k calls would take 12 s -> must abort at the budget
        r = run_turn("http://127.0.0.1:9", 3000, latency_us=1000, scan_only=True, EDITORTRACK_SCAN_BUDGET_MS=800)
        self.assertEqual(r["reason"], "scan_budget")
        self.assertLess(r["guard"]["ms"], 800 + 50)
        self.assertLess(r["wall_ms"], 800 + 300)

    def test_max_calls_aborts(self):
        r = run_turn("http://127.0.0.1:9", 3000, scan_only=True, EDITORTRACK_MAX_CALLS=500)
        self.assertEqual(r["reason"], "scan_budget")
        self.assertLessEqual(r["mock_calls"], 500)

    def test_huge_timeline_counts_only(self):
        r = run_turn("http://127.0.0.1:9", 3000, scan_only=True, EDITORTRACK_MAX_CLIPS=1000)
        self.assertFalse(r["per_clip"])
        self.assertEqual(r["video_clips"], 3000)
        self.assertLess(r["guard"]["calls"], 60)

    def test_budget_abort_backs_off(self):
        with agent_env(EDITORTRACK_MAX_CALLS="50") as (m, sd):
            m["resolve_poller"]._load_resolve = loader(World(n_clips=200))
            m["runner"].sample = lambda: {"idle_sec": 30}
            m["runner"].main()
            self.assertEqual(m["safety"].backoff_state().get("reason"), "scan_budget")


class NoForeignWrites(unittest.TestCase):
    def _check(self, r):
        state = os.path.realpath(r["state"])
        bad = []
        for ev, p in r["writes"]:
            rp = os.path.realpath(p)
            if rp == state or rp.startswith(state + os.sep):
                continue
            bad.append((ev, p))
        self.assertEqual(bad, [], "agent wrote outside its state dir")

    def test_full_turn_writes_only_state_dir(self):
        with FakeServer() as srv:
            r = run_turn(srv.url, 50)
            self.assertEqual(r["status"], 200)
            self._check(r)
            self.assertTrue(r["writes"])            # it did write (lock, queue/last_server) — just only there

    def test_server_down_queue_only_in_state_dir(self):
        r = run_turn("http://127.0.0.1:9", 50)
        self.assertIsNone(r["status"])
        self._check(r)
        self.assertTrue(os.path.exists(os.path.join(r["state"], "queue.jsonl")))

    def test_exportlut_writes_only_state_dir(self):
        with FakeServer() as srv:
            r = run_turn(srv.url, 20, EDITORTRACK_ALLOW_EXPORT_LUT=1, EDITORTRACK_GRADE_METHOD="lut")
            self._check(r)
            self.assertTrue(any("et-grade-" in p for _, p in r["writes"]))
            left = [p for p in pathlib.Path(r["state"], "tmp").glob("et-grade-*")]
            self.assertEqual(left, [], "ExportLUT temp not cleaned")


class QueueBounds(unittest.TestCase):
    def test_queue_capped_by_lines_and_bytes(self):
        with agent_env(EDITORTRACK_QUEUE_MAX_LINES="5000", EDITORTRACK_QUEUE_MAX_BYTES=str(400_000)) as (m, sd):
            r = m["runner"]
            for i in range(60):
                r._enqueue([json.dumps({"i": i * 100 + k, "pad": "x" * 50}) for k in range(100)])
            q = (sd / "queue.jsonl")
            self.assertLessEqual(q.stat().st_size, 400_000)
            lines = q.read_text().splitlines()
            self.assertEqual(json.loads(lines[-1])["i"], 5999)        # newest kept

    def test_flush_is_rate_limited(self):
        with FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest", EDITORTRACK_FLUSH_MAX="300") as (m, sd):
            r = m["runner"]
            r._enqueue([json.dumps({"host": "h", "i": i}) for i in range(1000)])
            r.post({"host": "h", "i": -1})
            self.assertEqual(len(srv.posts()), 301)
            self.assertEqual(len((sd / "queue.jsonl").read_text().splitlines()), 700)

    def test_disk_full_does_not_crash(self):
        with agent_env() as (m, sd):
            sd.mkdir(parents=True)
            (sd / "queue.jsonl").mkdir()          # writes to this path fail
            m["runner"]._enqueue(["{}"])           # must not raise


def report():
    rows = []
    for n, lat in ((300, 0), (3000, 0), (3000, 50), (3000, 130)):
        r = run_turn("http://127.0.0.1:9", n, latency_us=lat, scan_only=True, EDITORTRACK_SCAN_BUDGET_MS=60000)
        rows.append((n, lat, r["guard"]["calls"], r["guard"]["ms"], r["wall_ms"], r["py_heap_peak_mb"], r["maxrss_mb"], r["reason"]))
    print("clips  lat_us/call  api_calls  guard_ms  wall_ms  py_heap_peak_MB  maxrss_MB  reason")
    for row in rows:
        print("%5d  %11d  %9d  %8d  %7.0f  %15.2f  %9.1f  %s" % row)


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        unittest.main()

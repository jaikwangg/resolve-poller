"""SAFETY (review of the 27 Oct merge, Medium 1 + 2): re-sends are harmless, a slow PMS cannot cause them,
and a host-name mismatch fails visibly without losing time.

M1  /ingest is idempotent per (token host, rid) — or (token host, ts) for agents < 0.9.2 — persisted in
    <store>/ingest_ids/, and the PMS progress step runs on a worker thread after the answer.
M2  host names compare case-insensitively without a domain suffix; the record is stored under the token's
    own name; a real mismatch is a 403 that the agent retries (record kept), logs, and /api/health shows.
marker: safety
run: cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_ingest_idempotency -v
"""
import contextlib, datetime, io, json, os, pathlib, shutil, sys, tempfile, threading, time, unittest, uuid

HERE = pathlib.Path(__file__).parent
TMP = tempfile.mkdtemp(prefix="et-idem-")
import atexit                                          # noqa: E402
atexit.register(shutil.rmtree, TMP, True)
os.environ["EDITORTRACK_STORE"] = TMP
for k in [k for k in os.environ if k.startswith("ERP_")]:
    del os.environ[k]                                  # never call a real PMS from a test
sys.path.insert(0, str(HERE.parent))
from tests import authfix          # noqa: E402
authfix.install(hosts=("grade-01", "conform-01", "e2e"))

from fastapi.testclient import TestClient   # noqa: E402
import dashboard_api as api        # noqa: E402
import ingest_ids                  # noqa: E402
import pms_sync                    # noqa: E402
import auth                        # noqa: E402

MARKERS = {"safety"}
STORE = pathlib.Path(TMP)


def rec(host="grade-01", rid="auto", ts=None, **extra):
    r = {"schema": 3, "ts": ts or datetime.datetime.now(datetime.timezone.utc).isoformat(), "host": host,
         "stage": "color", "activity": {"idle_sec": 3.0, "active": True, "resolve_frontmost": True}}
    if rid == "auto":
        r["rid"] = uuid.uuid4().hex
    elif rid is not None:
        r["rid"] = rid
    r.update(extra)
    return r


def stored():
    out = []
    for f in sorted(STORE.glob("*.jsonl")):
        out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    return out


def clean():
    for f in STORE.glob("*.jsonl"):
        f.unlink()
    shutil.rmtree(STORE / "ingest_ids", ignore_errors=True)
    for n in ("host_refused.json",):
        with contextlib.suppress(FileNotFoundError):
            (STORE / n).unlink()
    api._ids = ingest_ids.Index(STORE)


class Base(unittest.TestCase):
    def setUp(self):
        clean()
        self.c = TestClient(api.app)
        self._saved = (pms_sync.progress_on_ingest, pms_sync._progress_enabled)

    def tearDown(self):
        pms_sync.progress_on_ingest, pms_sync._progress_enabled = self._saved
        pms_sync.progress_drain(5)

    def post(self, r, host="grade-01"):
        return self.c.post("/ingest", json=r, headers=authfix.machine(host))


class Idempotent(Base):
    def test_resend_with_rid_is_stored_once(self):
        r = rec()
        a, b, c = self.post(r), self.post(r), self.post(r)
        self.assertEqual([x.status_code for x in (a, b, c)], [200, 200, 200])
        self.assertNotIn("duplicate", a.json())
        self.assertTrue(b.json()["duplicate"])
        self.assertEqual(len(stored()), 1)

    def test_old_agent_without_rid_deduped_on_ts(self):
        r = rec(rid=None)
        self.assertEqual(self.post(r).status_code, 200)
        self.assertTrue(self.post(r).json()["duplicate"])
        self.assertEqual(len(stored()), 1)
        self.assertEqual(self.post(rec(rid=None)).status_code, 200)    # a new ts = a new record
        self.assertEqual(len(stored()), 2)

    def test_distinct_rids_both_stored_and_hosts_do_not_shadow(self):
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.post(rec(ts=ts, rid="a" * 32))
        self.post(rec(ts=ts, rid="b" * 32))
        self.post(rec("conform-01", ts=ts, rid="a" * 32), host="conform-01")   # same rid, other host
        self.assertEqual(len(stored()), 3)

    def test_index_survives_restart_and_late_resend(self):
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)).isoformat()
        r = rec(ts=old)
        self.post(r)
        api._ids = ingest_ids.Index(STORE)              # server restarted: index read back from disk
        self.assertTrue(self.post(r).json()["duplicate"])
        self.assertEqual(len(stored()), 1)
        [f] = list((STORE / "ingest_ids").glob("*.txt"))
        self.assertEqual(f.stat().st_mode & 0o077, 0)

    def test_record_fsynced_before_its_key(self):
        # a power cut between the two writes must never leave a key without its record (review Medium 2)
        events, real_fsync, real_add = [], os.fsync, api._index().add

        def fsync(fd):
            try:
                events.append(("fsync", os.readlink("/proc/self/fd/%d" % fd)))
            except OSError:
                events.append(("fsync", None))
            return real_fsync(fd)

        def add(host, r):
            events.append(("key", r["rid"]))
            return real_add(host, r)
        os.fsync, api._ids.add = fsync, add
        try:
            self.assertEqual(self.post(rec()).status_code, 200)   # first record of the day: file + dir fsynced
        finally:
            os.fsync = real_fsync
            del api._ids.add
        kinds = [k for k, _ in events]
        self.assertEqual(kinds[-1], "key")
        self.assertGreaterEqual(kinds.count("fsync"), 2)          # the record file and the new directory entry
        synced = [v for k, v in events if k == "fsync" and v]
        if synced:                                                # Linux: check WHAT was fsynced
            self.assertTrue(any(s.endswith(".jsonl") for s in synced), synced)
            self.assertIn(str(STORE.resolve()), [str(pathlib.Path(s).resolve()) for s in synced])

    def test_far_future_ts_stored_and_key_filed_under_today(self):
        # a bad workstation clock (2099) must not create an index day file retention never deletes (review Low 6),
        # and the record is still stored (never dropped for its ts)
        r = rec(ts="2099-01-01T00:00:00+00:00")
        self.assertEqual(self.post(r).status_code, 200)
        self.assertTrue(self.post(r).json()["duplicate"])
        self.assertEqual(len(stored()), 1)
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        self.assertEqual([f.name for f in (STORE / "ingest_ids").glob("*.txt")], [today + ".txt"])
        now = datetime.datetime(2026, 10, 28, 20, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(ingest_ids._day("2026-10-29T10:00:00+00:00", now), "2026-10-29")   # < 1 day ahead: own day
        self.assertEqual(ingest_ids._day("2026-10-30T10:00:00+00:00", now), "2026-10-28")   # > 1 day ahead: today
        self.assertEqual(ingest_ids._day("9999-12-31T23:59:59+14:00", now), "2026-10-28")

    def test_bad_rid_is_a_final_400(self):
        for bad in (12345, "short", "x" * 65, "has space here", ""):
            r = self.post(rec(rid=bad))
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("bad_rid", r.headers.get("X-Editortrack-Reject", ""))
        self.assertEqual(stored(), [])

    def test_retention_prunes_index(self):
        import retention
        (STORE / "ingest_ids").mkdir(exist_ok=True)
        (STORE / "ingest_ids" / "2000-01-01.txt").write_text("h\tk\n")
        keep = STORE / "ingest_ids" / (datetime.date.today().isoformat() + ".txt")
        keep.write_text("h\tk\n")
        retention.run(STORE, 90, echo=lambda *_: None)
        self.assertFalse((STORE / "ingest_ids" / "2000-01-01.txt").exists())
        self.assertTrue(keep.exists())


class SlowPms(Base):
    def test_slow_pms_does_not_delay_the_answer(self):
        calls, gate = [], threading.Event()

        def slow(rec_, *a):
            gate.wait(10)                               # PMS hangs until released
            calls.append(rec_["rid"])
            return True
        pms_sync.progress_on_ingest = slow
        pms_sync._progress_enabled = lambda erp: True
        r = rec()
        t0 = time.monotonic()
        res = self.post(r)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(res.json(), {"ok": True, "pushed": "queued"})
        self.assertEqual(len(stored()), 1)              # stored before PMS answered
        self.assertTrue(self.post(r).json()["duplicate"])    # a re-send is not queued for PMS again
        gate.set()
        self.assertTrue(pms_sync.progress_drain(5))
        self.assertEqual(calls, [r["rid"]])

    def test_pms_error_never_fails_ingest(self):
        def boom(*a):
            raise RuntimeError("PMS down")
        pms_sync.progress_on_ingest = boom
        pms_sync._progress_enabled = lambda erp: True
        self.assertEqual(self.post(rec()).status_code, 200)
        self.assertTrue(pms_sync.progress_drain(5))
        self.assertEqual(self.post(rec()).status_code, 200)    # the worker survived
        self.assertTrue(pms_sync.progress_drain(5))
        self.assertEqual(len(stored()), 2)


class HostNames(Base):
    def test_norm(self):
        for a in ("grade-01", "GRADE-01", " Grade-01 ", "grade-01.local", "GRADE-01.corp.example.", "grade-01."):
            self.assertTrue(auth.same_host(a, "grade-01"), a)
        for a in ("grade-02", "grade-01x", "xgrade-01", "", ".", None, 7, "conform-01.grade-01"):
            self.assertFalse(auth.same_host(a, "grade-01"), a)

    def test_variants_accepted_and_stored_under_token_name(self):
        for h in ("GRADE-01", "grade-01.local", "Grade-01.corp.example."):
            self.assertEqual(self.post(rec(h)).status_code, 200, h)
        self.assertEqual({r["host"] for r in stored()}, {"grade-01"})
        ctl = STORE / "agent_control.json"
        ctl.write_text(json.dumps({"allowed_versions": ["x"], "hosts": {"grade-01": {"enabled": True}}}))
        self.addCleanup(ctl.unlink)
        r = self.c.get("/agent/control?host=GRADE-01.local&v=x", headers=authfix.machine("grade-01"))
        self.assertEqual((r.status_code, r.json()["enabled"]), (200, True))     # looked up under the token's name

    def test_real_mismatch_403_visible_and_cleared(self):
        for h in ("grade-02", "conform-01"):            # a token never posts as another (existing) host
            r = self.post(rec(h))
            self.assertEqual(r.status_code, 403, h)
            self.assertEqual(r.headers.get("X-Editortrack-Host-Mismatch"), "grade-01")
            self.assertNotIn("X-Editortrack-Reject", r.headers)   # not final: the agent keeps the record
        self.assertEqual(stored(), [])
        self.assertEqual(self.c.get("/agent/control?host=wrong&v=x", headers=authfix.machine("grade-01")).status_code, 403)
        hl = self.c.get("/api/health", headers=authfix.reader()).json()["hosts"]
        [row] = [x for x in hl if x["host"] == "grade-01"]
        self.assertEqual(row["status"], "alert")
        self.assertEqual(row["host_refused"]["count"], 3)
        self.assertTrue(any("host name mismatch" in a for a in row["alerts"]))
        self.assertIn("host name mismatch", self.c.get("/health", headers=authfix.reader()).text)
        time.sleep(0.01)
        self.assertEqual(self.post(rec("grade-01")).status_code, 200)     # fixed: a newer record arrives
        [row] = [x for x in self.c.get("/api/health", headers=authfix.reader()).json()["hosts"] if x["host"] == "grade-01"]
        self.assertNotIn("host_refused", row)

    def test_refused_entry_cleared_when_accepted_again(self):
        # review Low 5: host_refused.json entries are removed, not only hidden
        import agent_health
        f = STORE / agent_health.HOST_REFUSED_FILE
        self.assertEqual(self.post(rec("grade-02")).status_code, 403)
        self.assertTrue(f.exists())
        self.assertEqual(self.post(rec("grade-01")).status_code, 200)
        self.assertFalse(f.exists())                                       # removed by the accepted /ingest
        self.assertEqual(self.post(rec("grade-02")).status_code, 403)
        r = self.c.get("/agent/control?host=grade-01&v=x", headers=authfix.machine("grade-01"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(f.exists())                                       # ... or by an accepted lease request
        self.assertEqual(self.post(rec("grade-02")).status_code, 403)
        self.assertEqual(self.post(rec("conform-01", rid="c" * 32), host="conform-01").status_code, 200)
        self.assertIn("grade-01", json.loads(f.read_text()))              # another host's success clears nothing

    def test_refused_entry_expires_after_7_days(self):
        import agent_health
        f = STORE / agent_health.HOST_REFUSED_FILE
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)).isoformat()
        f.write_text(json.dumps({"conform-01": {"sent": "x", "route": "ingest", "count": 4, "first_at": old,
                                                "last_at": old}}))
        self.assertEqual(agent_health.load_host_refused(STORE), {})
        hl = self.c.get("/api/health", headers=authfix.reader()).json()["hosts"]
        self.assertFalse([x for x in hl if x.get("host_refused")])
        self.assertEqual(self.post(rec("grade-02")).status_code, 403)     # the next write drops the stale entry
        self.assertEqual(set(json.loads(f.read_text())), {"grade-01"})

    def test_resolved_by_server_receive_time_not_agent_clock(self):
        import agent_health
        now = datetime.datetime(2026, 10, 28, 12, 0, tzinfo=datetime.timezone.utc)
        refused = {"grade-01": {"sent": "g", "route": "ingest", "count": 1, "first_at": "2026-10-28T11:00:00+00:00",
                                "last_at": "2026-10-28T11:30:00+00:00"}}

        def r(ts, rx):
            return {"host": "grade-01", "ts": ts, "_rx": {"at": rx}, "agent": {"version": "v"}}
        # slow agent clock: ts before the refusal, received after it -> resolved
        rows = agent_health.health([r("2026-10-28T10:00:00+00:00", "2026-10-28T11:45:00+00:00")], now=now, refused=refused)
        self.assertNotIn("host_refused", rows["grade-01"])
        # fast agent clock: ts after the refusal, but received before it -> still an alert
        rows = agent_health.health([r("2026-10-28T11:50:00+00:00", "2026-10-28T11:10:00+00:00")], now=now, refused=refused)
        self.assertIn("host_refused", rows["grade-01"])
        self.assertNotIn("_last_rx", rows["grade-01"])


def _server():
    import uvicorn
    srv = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=0, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    return srv, th, srv.servers[0].sockets[0].getsockname()[1]


def _agent_modules():
    import importlib.util
    agent = HERE.parents[1] / "agent"

    def load(name):
        spec = importlib.util.spec_from_file_location("agent_" + name, agent / "tests" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return load("util").agent_env, load("mock_resolve")


class EndToEnd(unittest.TestCase):
    """real server under uvicorn (ephemeral port) + real agent turns."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.th, cls.port = _server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.should_exit = True
        cls.th.join(5)

    def setUp(self):
        clean()
        self._submit = pms_sync.progress_submit

    def tearDown(self):
        pms_sync.progress_submit = self._submit

    def env(self, **kw):
        agent_env, mr = _agent_modules()
        e = dict(EDITORTRACK_SERVER="http://127.0.0.1:%d/ingest" % self.port, EDITORTRACK_TOKEN=authfix.machine_token("e2e"),
                 EDITORTRACK_TOKEN_MAP=os.environ["EDITORTRACK_TOKEN_MAP"],
                 EDITORTRACK_READ_USERS=os.environ["EDITORTRACK_READ_USERS"], EDITORTRACK_STORE=TMP)
        e.update(kw)
        return agent_env(**e), mr

    def _prep(self, m, mr, hostname):
        m["runner"].socket.gethostname = lambda: hostname
        m["runner"].sample = lambda: {"idle_sec": 30}
        m["resolve_poller"]._load_resolve = mr.loader(mr.World())

    def test_agent_timeout_then_resend_recorded_once(self):
        # the old failure: the answer came after the agent's timeout (PMS in the request path). Simulate a slow
        # answer AFTER the store write; the agent times out, queues, and re-sends next turn
        def slow_submit(*a):
            time.sleep(2.5)
            return False
        pms_sync.progress_submit = slow_submit
        ctx, mr = self.env(EDITORTRACK_HTTP_TIMEOUT="1")
        with ctx as (m, sd):
            self._prep(m, mr, "e2e")
            self.assertIsNone(m["runner"].main())              # timed out -> queued
            [line] = (sd / "queue.jsonl").read_text().splitlines()
            rid = json.loads(line)["rid"]
            time.sleep(2)                                      # the slow request finishes on the server
            self.assertEqual([r["rid"] for r in stored()], [rid])
            pms_sync.progress_submit = self._submit
            (sd / "send_backoff.json").unlink()
            self.assertEqual(m["runner"].main(), 200)          # new record + the queued re-send
            self.assertFalse((sd / "queue.jsonl").exists())
        rids = [r["rid"] for r in stored()]
        self.assertEqual(len(rids), 2)
        self.assertEqual(rids.count(rid), 1)

    def test_hostname_variant_and_mismatch_keep_records(self):
        ctx, mr = self.env()
        with ctx as (m, sd):
            self._prep(m, mr, "E2E.corp.example")             # Windows-style DNS name, other case
            self.assertEqual(m["runner"].main(), 200)
            m["runner"].socket.gethostname = lambda: "editbay-7"   # token was issued for e2e
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(m["runner"].main())
                (sd / "send_backoff.json").unlink()
                self.assertIsNone(m["runner"].main())
            self.assertEqual(len((sd / "queue.jsonl").read_text().splitlines()), 2)   # kept, not dropped
            self.assertIn("refused host 'editbay-7'", err.getvalue())
            self.assertIn("EDITORTRACK_HOST=e2e", err.getvalue())
            self.assertEqual(err.getvalue().count("refused host"), 1)                  # rate-limited log
            self.assertEqual(json.loads((sd / "host_refused.json").read_text())["token_host"], "e2e")
            self.assertFalse((sd / "rejected.json").exists())                          # nothing dropped
            # the fix on the workstation: EDITORTRACK_HOST from the token config -> queued records go out too
            os.environ["EDITORTRACK_HOST"] = "e2e"
            (sd / "send_backoff.json").unlink()
            self.assertEqual(m["runner"].main(), 200)
            self.assertFalse((sd / "queue.jsonl").exists())
            self.assertFalse((sd / "host_refused.json").exists())
        recs = stored()
        self.assertEqual(len(recs), 4)
        self.assertEqual({r["host"] for r in recs}, {"e2e"})
        self.assertEqual(len({r["rid"] for r in recs}), 4)


if __name__ == "__main__":
    unittest.main()

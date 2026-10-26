"""PROGRESS v2: what the server sends to PMS (contract v2, PMS 9e67f60) through the real ingest path
(pms_sync.progress_on_ingest + progress_v2), with a fake PMS that records every POST body.

The exact 2026-10-14 records (fixture) must never produce 100 or done; field names and types must be
exactly PMS ProgressRecord's; nothing is sent while ERP_PROGRESS_PATH is unset; state survives a restart.
marker: worstcase
run: cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_progress_v2 -v
"""
import datetime, json, os, pathlib, shutil, sys, tempfile, unittest, urllib.error

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixtures" / "replay"
for k in [k for k in os.environ if k.startswith(("ERP_", "EDITORTRACK_SCOPE", "EDITORTRACK_PROGRESS", "EDITORTRACK_STABLE",
                                                 "EDITORTRACK_JOB_GUARD", "EDITORTRACK_MASS"))]:
    del os.environ[k]
sys.path.insert(0, str(HERE.parent))

import pms_sync          # noqa: E402
import progress_v2 as pv  # noqa: E402

MARKERS = {"worstcase"}
# kpp-pms-api/backend/routers/integrations/editortrack.py ProgressRecord at 9e67f60: name -> allowed types
CONTRACT = {"project_no": (str,), "stage": (str,), "progress_pct": (int, float), "observed_at": (str,),
            "host": (str,), "provisional": (bool,), "total_confirmed": (bool,), "done": (bool,),
            "done_count": (int,), "total": (int,), "work_done": (int,), "reasons": (list,)}
STUDY_CODES = {"scope_change", "scope_shrink", "scope_shrink_ignored", "scope_mismatch", "scope_hint",
               "scope_unconfirmed", "rework", "regression", "replaced", "reopened", "job_mismatch",
               "reference_timeline", "late_record", "awaiting_stability", "debounced_drop", "detector_pending",
               "mass_grade", "unexplained_drop", "no_data", "no_identity"}
OWN_CODES = {"scope_grew_after_lock"}         # ours, outside the study list (PMS shows unknown codes raw)


class FakePMS:
    ERP_BASE = "http://pms.invalid"
    ERP_PROGRESS_PATH = "/api/integrations/editortrack/progress"
    ERP_PROGRESS_METHOD = "POST"

    def __init__(self, fail_422=False):
        self.bodies, self.fail_422 = [], fail_422

    def _post_auth(self, url, body, method="POST"):
        assert url == self.ERP_BASE + self.ERP_PROGRESS_PATH and method == "POST"
        json.dumps(body)                       # must be JSON-serialisable as sent
        self.bodies.append(body)
        if self.fail_422:
            raise urllib.error.HTTPError(url, 422, "unprocessable", {}, None)
        return {"data": {"stored": True}}


def erp_eff(job="TEST", stage="color", title=None):
    return lambda rec: {"job_id": job, "title": title or job, "stage": stage, "source": "erp"}


def fixture_records():
    out = []
    for f in sorted(FIX.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            if r.get("host") and r.get("ts"):  # ingest refuses the rest
                out.append(r)
    return out


def reset():
    pms_sync._pg.update({"engine": None, "pushed": {}, "store": None})
    for k, v in {"sent": 0, "rejected": 0, "refused_locally": 0, "errors": 0}.items():
        pms_sync.progress_status[k] = v
    pms_sync.progress_status["withheld"], pms_sync.progress_status["last"] = {}, {}


def rec(ts, v, g, project="KPP-2026-0101 BrandX", timeline="Master", pending=0, host="grade-01"):
    return {"schema": 3, "ts": ts, "host": host, "stage": "color",
            "source": {"ok": True, "project": project, "timeline_count": 1,
                       "timeline": {"name": timeline, "duration_sec": 100.0, "video_clips": v, "graded_clips": g,
                                    "grade": {"pending": pending}}},
            "progress": {"stage": "color", "pct": round(100.0 * g / v, 1) if v else None,
                         "detail": {"graded_clips": g, "video_clips": v}}}


def ts(minute):
    return (datetime.datetime(2026, 11, 2, 2, 0, tzinfo=datetime.timezone.utc)
            + datetime.timedelta(minutes=minute)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.store = tempfile.mkdtemp(prefix="et-prog-")
        self.addCleanup(shutil.rmtree, self.store, True)
        reset()


class Replay14Oct(Base):
    def test_exact_records_never_send_100_or_done(self):
        pms = FakePMS()
        for r in fixture_records():
            pms_sync.progress_on_ingest(r, erp_eff(), pms, self.store)
        self.assertEqual(pms_sync.progress_status["errors"], 0, pms_sync.progress_status["last_error"])
        pcts = [b["progress_pct"] for b in pms.bodies]
        self.assertEqual(pcts[-4:], [16.67, 99.0, 83.33, 75.0])
        self.assertFalse(any(b["done"] or b["progress_pct"] >= 100 for b in pms.bodies))
        self.assertTrue(all(b["provisional"] and not b["total_confirmed"] for b in pms.bodies))
        last = pms.bodies[-1]
        self.assertEqual((last["done_count"], last["total"]), (6, 8))
        self.assertIn("scope_change", last["reasons"])
        self.assertIn("regression", pms.bodies[-2]["reasons"])
        works = [b["work_done"] for b in pms.bodies]
        self.assertEqual(works, sorted(works))

    def test_every_body_is_exactly_the_pms_contract(self):
        pms = FakePMS()
        for r in fixture_records():
            pms_sync.progress_on_ingest(r, erp_eff(), pms, self.store)
        self.assertTrue(pms.bodies)
        for b in pms.bodies:
            self.assertLessEqual(set(b), set(CONTRACT))
            self.assertTrue({"project_no", "stage", "progress_pct", "observed_at", "provisional", "total_confirmed",
                             "done", "reasons"} <= set(b))
            for k, v in b.items():
                self.assertIsInstance(v, CONTRACT[k], k)
                if k in ("done_count", "total", "work_done"):
                    self.assertNotIsInstance(v, bool)
            self.assertLessEqual(set(b["reasons"]), STUDY_CODES | OWN_CODES)
            self.assertIsNone(pv.pms_check(b))
            self.assertEqual(round(b["progress_pct"], 2), b["progress_pct"])


class Gates(Base):
    def test_off_until_erp_progress_path_is_set(self):
        class Off(FakePMS):
            ERP_PROGRESS_PATH = ""
        pms = Off()
        for r in fixture_records()[-30:]:
            self.assertFalse(pms_sync.progress_on_ingest(r, erp_eff(), pms, self.store))
        self.assertEqual(pms.bodies, [])
        self.assertFalse(os.path.exists(os.path.join(self.store, pms_sync.PROGRESS_STATE_FILE)))

    def test_unbound_records_are_never_sent(self):
        pms = FakePMS()
        fallback = lambda r: {"job_id": "TEST", "title": "TEST", "stage": "color", "source": "fallback"}
        for r in fixture_records():
            pms_sync.progress_on_ingest(r, fallback, pms, self.store)
        self.assertEqual(pms.bodies, [])

    def test_project_not_matching_the_booking_sends_nothing(self):
        pms = FakePMS()
        for m in range(5):
            pms_sync.progress_on_ingest(rec(ts(m), 10, m, project="Other Client TVC"),
                                        erp_eff("KPP-2026-0101", title="BrandX"), pms, self.store)
        self.assertEqual(pms.bodies, [])
        self.assertEqual(pms_sync.progress_status["withheld"].get("job_mismatch"), 5)

    def test_detector_pending_and_reference_timeline_hold(self):
        pms = FakePMS()
        eff = erp_eff("KPP-2026-0101", title="BrandX")
        pms_sync.progress_on_ingest(rec(ts(0), 10, 2), eff, pms, self.store)
        pms_sync.progress_on_ingest(rec(ts(1), 10, 9, pending=4), eff, pms, self.store)
        pms_sync.progress_on_ingest(rec(ts(2), 10, 9, timeline="BrandX offline ref"), eff, pms, self.store)
        self.assertEqual([b["progress_pct"] for b in pms.bodies], [20.0])

    def test_late_backlog_record_does_not_move_the_value(self):
        pms = FakePMS()
        eff = erp_eff("KPP-2026-0101", title="BrandX")
        pms_sync.progress_on_ingest(rec(ts(10), 10, 8), eff, pms, self.store)
        pms_sync.progress_on_ingest(rec(ts(3), 10, 1), eff, pms, self.store)
        self.assertEqual([b["progress_pct"] for b in pms.bodies], [80.0])

    def test_a_host_clock_ahead_of_the_server_is_not_trusted(self):
        pms = FakePMS()
        eff = erp_eff("KPP-2026-0101", title="BrandX")
        future = rec(ts(600), 10, 9)
        future["_rx"] = {"at": ts(1)}           # stamped 10 h after the server got it
        pms_sync.progress_on_ingest(future, eff, pms, self.store)
        now = rec(ts(2), 10, 3)
        now["_rx"] = {"at": ts(2)}
        pms_sync.progress_on_ingest(now, eff, pms, self.store)
        self.assertEqual([b["progress_pct"] for b in pms.bodies], [30.0])

    def test_pms_422_is_counted_and_not_resent(self):
        pms = FakePMS(fail_422=True)
        eff = erp_eff("KPP-2026-0101", title="BrandX")
        for m in range(3):
            pms_sync.progress_on_ingest(rec(ts(m), 10, 2), eff, pms, self.store)
        self.assertEqual(len(pms.bodies), 1)
        self.assertEqual(pms_sync.progress_status["rejected"], 1)

    def test_a_crashing_pms_never_breaks_ingest(self):
        class Boom(FakePMS):
            def _post_auth(self, *a, **k):
                raise OSError("network down")
        self.assertFalse(pms_sync.progress_on_ingest(rec(ts(0), 10, 2), erp_eff("KPP-2026-0101", title="BrandX"),
                                                     Boom(), self.store))
        self.assertEqual(pms_sync.progress_status["errors"], 1)


class Persistence(Base):
    def test_restart_keeps_work_done_and_does_not_resend(self):
        pms = FakePMS()
        eff = erp_eff("KPP-2026-0101", title="BrandX")
        for m in range(5):
            pms_sync.progress_on_ingest(rec(ts(m), 10, m), eff, pms, self.store)
        before = pms.bodies[-1]
        mode = os.stat(os.path.join(self.store, pms_sync.PROGRESS_STATE_FILE)).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        reset()                                 # process restart: only the state file survives
        pms_sync.progress_on_ingest(rec(ts(5), 10, 4), eff, pms, self.store)
        self.assertEqual(pms.bodies[-1], before)          # same value: not sent again
        pms_sync.progress_on_ingest(rec(ts(6), 10, 5), eff, pms, self.store)
        self.assertEqual(pms.bodies[-1]["work_done"], before["work_done"] + 1)
        late = rec(ts(1), 10, 0)
        pms_sync.progress_on_ingest(late, eff, pms, self.store)   # host clock memory survived too
        self.assertEqual(pms.bodies[-1]["progress_pct"], 50.0)


class ScopeLock(Base):
    def test_lock_done_after_stability_then_scope_grew_after_lock(self):
        os.environ["EDITORTRACK_SCOPE_SOURCE"] = "lock"
        self.addCleanup(os.environ.pop, "EDITORTRACK_SCOPE_SOURCE", None)
        with open(os.path.join(self.store, "scope_locks.json"), "w") as f:
            json.dump({"KPP-2026-0101|color": {"total": 6, "locked_at": ts(0), "by": "conform lead"}}, f)
        pms = FakePMS()
        eff = erp_eff("KPP-2026-0101", title="BrandX")
        for m in range(0, 50):
            pms_sync.progress_on_ingest(rec(ts(m), 6, min(6, m // 2)), eff, pms, self.store)
        for m in range(50, 53):
            pms_sync.progress_on_ingest(rec(ts(m), 8, 6), eff, pms, self.store)
        done = [b for b in pms.bodies if b["done"]]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["observed_at"], ts(42))          # 6/6 from minute 12, held 30 min
        self.assertEqual((done[0]["progress_pct"], done[0]["done_count"], done[0]["total"]), (100.0, 6, 6))
        self.assertTrue(done[0]["total_confirmed"] and not done[0]["provisional"])
        before = [b for b in pms.bodies if b["observed_at"] < ts(42)]
        self.assertTrue(before and all(b["progress_pct"] <= 99 and not b["done"] for b in before))
        grew = pms.bodies[-1]
        self.assertEqual((grew["progress_pct"], grew["done"], grew["total"]), (75.0, False, 8))
        self.assertIn("scope_grew_after_lock", grew["reasons"])
        self.assertIn("scope_change", grew["reasons"])
        for b in pms.bodies:
            self.assertIsNone(pv.pms_check(b))

    def test_malformed_lock_file_means_no_lock(self):
        os.environ["EDITORTRACK_SCOPE_SOURCE"] = "lock"
        self.addCleanup(os.environ.pop, "EDITORTRACK_SCOPE_SOURCE", None)
        with open(os.path.join(self.store, "scope_locks.json"), "w") as f:
            f.write("{not json")
        pms = FakePMS()
        for m in range(0, 60):
            pms_sync.progress_on_ingest(rec(ts(m), 6, 6), erp_eff("KPP-2026-0101", title="BrandX"), pms, self.store)
        self.assertFalse(any(b["done"] for b in pms.bodies))
        self.assertEqual(max(b["progress_pct"] for b in pms.bodies), 99.0)


class PmsCheck(unittest.TestCase):
    """the local gate mirrors PMS 9e67f60 _check_v2 (and refuses 100 without done, our own rule)"""
    BASE = {"project_no": "P", "stage": "color", "observed_at": "2026-10-14T03:00:00Z"}

    def bad(self, **kw):
        return pv.pms_check({**self.BASE, **kw})

    def test_rules(self):
        self.assertIsNone(self.bad(progress_pct=99, provisional=True, total_confirmed=False, done=False))
        self.assertTrue(self.bad(progress_pct=100, provisional=True, total_confirmed=False, done=False))
        self.assertTrue(self.bad(progress_pct=100, provisional=False, total_confirmed=True, done=False))
        self.assertTrue(self.bad(progress_pct=99, provisional=False, total_confirmed=True, done=True))
        self.assertTrue(self.bad(progress_pct=100, provisional=False, total_confirmed=True, done=True,
                                 done_count=7, total=8))
        self.assertIsNone(self.bad(progress_pct=100, provisional=False, total_confirmed=True, done=True,
                                   done_count=8, total=8, work_done=9, reasons=[]))
        self.assertTrue(self.bad(progress_pct=99.999, provisional=True))
        self.assertTrue(self.bad(progress_pct=50, reasons=["Scope-Change"]))
        self.assertTrue(self.bad(progress_pct=50, reasons=["a"] * 17))
        self.assertTrue(self.bad(progress_pct=50, total=-1))
        self.assertTrue(self.bad(progress_pct=50, done=1))

    def test_seal_only_weakens(self):
        o = pv.out("P|color", 100.0, done=True, total=8, done_count=7, confirmed=True, provisional=False)
        s = pv.seal(o)
        self.assertFalse(s["done"])
        self.assertEqual(s["pct"], 99.0)
        self.assertTrue(s["provisional"])


if __name__ == "__main__":
    unittest.main()

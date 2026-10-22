"""กลุ่ม 4 — ERP ล่ม/ช้า: negative cache, /ingest ไม่รอ ERP, push % ใน background, booking ที่จับคู่ตอน ingest ไม่ถูกเขียนทับ
ERP ปลอม = HTTP server บน 127.0.0.1 (ไม่ต่อ ERP/PMS จริง)"""
import datetime
import http.server
import json
import os
import pathlib
import tempfile
import threading
import time
import unittest

try:
    from . import _env
except ImportError:
    import _env

import auth                      # noqa: E402
import dashboard_api as api      # noqa: E402
import erp                       # noqa: E402

TS = "2026-10-14T03:00:00+00:00"


def booking(job, bid="b-1"):
    return {"id": bid, "project_no": job, "project_name": f"name {job}", "activity": "Grading", "status": "CONFIRMED",
            "start_dt": "2026-10-14T00:00:00Z", "end_dt": "2026-10-14T12:00:00Z",
            "resources": [{"resource_code": "RM-03", "resource_type": "ROOM", "is_primary": True}]}


class FakeERP(http.server.BaseHTTPRequestHandler):
    schedule_delay = 0.0
    progress_delay = 0.0
    progress_codes = []                           # status ที่ตอบ POST ทีละครั้ง (หมด = 200)
    bookings = [booking("KPP-1")]
    hits = []

    def log_message(self, *a):
        pass

    def _reply(self, obj, code=200):
        b = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except OSError:
            pass                                  # client timeout ไปแล้ว

    def do_GET(self):
        FakeERP.hits.append(("GET", self.path.split("?")[0]))
        time.sleep(FakeERP.schedule_delay)
        self._reply({"data": {"bookings": FakeERP.bookings}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        FakeERP.hits.append(("POST", self.path, body))
        time.sleep(FakeERP.progress_delay)
        code = FakeERP.progress_codes.pop(0) if FakeERP.progress_codes else 200
        self._reply({"data": {"stored": code == 200}}, code)


class _ErpCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeERP)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self._saved = {k: getattr(erp, k) for k in ("ERP_BASE", "ERP_TOKEN_STATIC", "TIMEOUT", "FAIL_TTL",
                                                    "CACHE_TTL", "ERP_PROGRESS_PATH")}
        self._env = dict(os.environ)
        erp.ERP_BASE, erp.ERP_TOKEN_STATIC, erp.ERP_PROGRESS_PATH = self.base, "kpp_int_fake", "/progress"
        erp.TIMEOUT, erp.FAIL_TTL, erp.CACHE_TTL = 0.4, 60, 300
        os.environ["ERP_ROOM_MAP"] = json.dumps({"grade-03": "RM-03"})
        if erp._fetch_lock.acquire(timeout=5):          # รอ refresh ค้างจากเทสก่อน (background thread)
            erp._fetch_lock.release()
        erp._cache.update({"at": None, "rows": [], "fail_at": None, "error": None})
        FakeERP.schedule_delay = FakeERP.progress_delay = 0.0
        FakeERP.bookings = [booking("KPP-1")]
        FakeERP.hits = []
        FakeERP.progress_codes = []
        api._last_pushed.clear()
        api._rejected.clear()
        self._store = api.STORE
        api.STORE = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(erp, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        erp._cache.update({"at": None, "rows": [], "fail_at": None, "error": None})
        api.STORE = self._store
        time.sleep(0.05)

    def gets(self):
        return [h for h in FakeERP.hits if h[0] == "GET"]


class NegativeCache(_ErpCase):
    def test_timeout_is_cached_not_retried_per_lookup(self):
        FakeERP.schedule_delay = 1.0                     # ERP ค้าง > TIMEOUT
        t = time.monotonic()
        self.assertIsNone(erp.lookup("grade-03", TS))
        self.assertGreaterEqual(time.monotonic() - t, 0.35)
        self.assertIsNotNone(erp.cache_status()["failed_at"])
        t = time.monotonic()
        for _ in range(200):                             # เดิม: ทุก lookup รอ timeout ใหม่ (200 × 10 วิ)
            erp.lookup("grade-03", TS)
        self.assertLess(time.monotonic() - t, 0.2)
        self.assertEqual(len(self.gets()), 1)

    def test_old_rows_used_while_erp_down(self):
        self.assertEqual(erp.lookup("grade-03", TS)["job_id"], "KPP-1")
        erp._cache["at"] -= datetime.timedelta(seconds=erp.CACHE_TTL + 1)   # หมดอายุ
        FakeERP.schedule_delay = 1.0
        self.assertEqual(erp.lookup("grade-03", TS)["job_id"], "KPP-1")     # ล้ม → ใช้ cache เดิม
        self.assertEqual(erp.lookup("grade-03", TS)["job_id"], "KPP-1")
        self.assertEqual(len(self.gets()), 2)

    def test_retry_after_fail_ttl(self):
        FakeERP.schedule_delay = 1.0
        erp.lookup("grade-03", TS)
        FakeERP.schedule_delay = 0.0
        erp._cache["fail_at"] -= datetime.timedelta(seconds=erp.FAIL_TTL + 1)
        self.assertEqual(erp.lookup("grade-03", TS)["job_id"], "KPP-1")

    def test_non_blocking_lookup_never_waits(self):
        FakeERP.schedule_delay = 1.0
        t = time.monotonic()
        self.assertIsNone(erp.lookup("grade-03", TS, block=False))           # cache ว่าง → None ทันที
        self.assertLess(time.monotonic() - t, 0.1)


class IngestPath(_ErpCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        self.c = TestClient(api.app)
        self.h = {"Authorization": f"Bearer {auth.issue_token('grade-03', force=True)}"}

    def rec(self, pct=10.0, ts=TS):
        return {"schema": 3, "ts": ts, "host": "grade-03", "stage": "color",
                "activity": {"idle_sec": 1, "active": True, "resolve_frontmost": True},
                "source": {"ok": True, "project": "whatever"}, "progress": {"stage": "color", "pct": pct}}

    def stored(self):
        return [json.loads(ln) for p in sorted(api.STORE.glob("*.jsonl"))
                for ln in p.read_text(encoding="utf-8").splitlines()]

    def test_ingest_fast_while_erp_hangs_cold_cache(self):
        FakeERP.schedule_delay = 2.0
        t = time.monotonic()
        r = self.c.post("/ingest", json=self.rec(), headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertLess(time.monotonic() - t, 0.3)        # agent timeout = 5 วิ — เดิมรอ ERP 10 วิ
        self.assertNotIn("_job", self.stored()[0])        # ยังไม่มี booking ใน cache → จับคู่ทีหลังตอนอ่าน

    def test_progress_push_is_background_and_coalesced(self):
        erp.lookup("grade-03", TS)                        # cache อุ่นแล้ว
        FakeERP.progress_delay = 0.3                      # PMS ช้า (แต่ไม่เกิน TIMEOUT)
        t = time.monotonic()
        for pct in (10.0, 20.0, 30.0):
            r = self.c.post("/ingest", json=self.rec(pct), headers=self.h)
            self.assertEqual(r.json()["queued"], True)
        self.assertLess(time.monotonic() - t, 0.5)
        deadline = time.monotonic() + 5
        while api._last_pushed.get(("KPP-1", "color")) != 30.0 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(api._last_pushed.get(("KPP-1", "color")), 30.0)
        posts = [h for h in FakeERP.hits if h[0] == "POST"]
        self.assertLessEqual(len(posts), 2)               # 10 ส่งไปแล้ว ระหว่างนั้น 20 ถูก 30 ทับ
        self.assertEqual(posts[-1][2]["progress_pct"], 30.0)
        self.assertEqual(posts[-1][2]["observed_at"], TS)

    def wait_for(self, cond, sec=5):
        deadline = time.monotonic() + sec
        while not cond() and time.monotonic() < deadline:
            time.sleep(0.05)
        return cond()

    def posts(self):
        return [h for h in FakeERP.hits if h[0] == "POST"]

    def test_permanent_4xx_dropped_after_logging_not_retried_forever(self):
        erp.lookup("grade-03", TS)
        old = api.PUSH_RETRY_SEC
        api.PUSH_RETRY_SEC = 0.05                          # ถ้าวนซ้ำจะเห็นภายในไม่กี่รอบ
        try:
            FakeERP.progress_codes = [422]
            with self.assertLogs("editortrack", level="WARNING") as cm:
                self.assertTrue(self.c.post("/ingest", json=self.rec(10.0), headers=self.h).json()["queued"])
                self.assertTrue(self.wait_for(lambda: ("KPP-1", "color") in api._rejected))
            self.assertTrue(any("422" in m and "ทิ้ง" in m for m in cm.output))
            time.sleep(0.4)
            self.assertEqual(len(self.posts()), 1)                          # เดิม: ส่ง 422 ซ้ำทุก 60 วิ ตลอดไป
            self.assertEqual(len(api._pending), 0)
            self.assertEqual(self.c.post("/ingest", json=self.rec(10.0), headers=self.h).json()["queued"], False)
            _env.ensure_read_user()
            pms = self.c.get("/api/pms", headers=_env.basic()).json()
            self.assertEqual(pms["progress_rejected"], [{"job": "KPP-1", "stage": "color", "pct": 10.0, "status": 422}])
            self.assertTrue(self.c.post("/ingest", json=self.rec(20.0), headers=self.h).json()["queued"])   # ค่าใหม่ลองได้
            self.assertTrue(self.wait_for(lambda: api._last_pushed.get(("KPP-1", "color")) == 20.0))
            self.assertNotIn(("KPP-1", "color"), api._rejected)
        finally:
            api.PUSH_RETRY_SEC = old

    def test_transient_errors_still_retried(self):
        erp.lookup("grade-03", TS)
        old = api.PUSH_RETRY_SEC
        api.PUSH_RETRY_SEC = 0.05
        try:
            FakeERP.progress_codes = [503, 429]
            self.c.post("/ingest", json=self.rec(40.0), headers=self.h)
            self.assertTrue(self.wait_for(lambda: api._last_pushed.get(("KPP-1", "color")) == 40.0))
            self.assertEqual(len(self.posts()), 3)
            self.assertEqual(api._rejected, {})
        finally:
            api.PUSH_RETRY_SEC = old

    def test_booking_stored_at_ingest_survives_booking_edit(self):
        erp.lookup("grade-03", TS)
        self.c.post("/ingest", json=self.rec(), headers=self.h)
        st = self.stored()[0]
        self.assertEqual((st["_job"]["job_id"], st["_job"]["booking_id"]), ("KPP-1", "b-1"))
        FakeERP.bookings = [booking("KPP-2", "b-2")]      # มีคนแก้ booking ใน ERP
        erp._cache["at"] -= datetime.timedelta(seconds=erp.CACHE_TTL + 1)
        self.assertEqual(api._eff_job(st)["job_id"], "KPP-1")                  # ประวัติไม่เปลี่ยน
        self.assertEqual(api._eff_job(self.rec())["job_id"], "KPP-2")          # record ใหม่ใช้ booking ใหม่

    def test_agent_cannot_inject_job(self):
        rec = {**self.rec(), "_job": {"job_id": "FORGED", "booking_id": "x"}}
        FakeERP.schedule_delay = 2.0
        self.c.post("/ingest", json=rec, headers=self.h)
        self.assertNotIn("_job", self.stored()[0])


class RoomMap(unittest.TestCase):
    def test_file_cached_until_changed_and_home_not_read(self):
        env = dict(os.environ)
        try:
            os.environ.pop("ERP_ROOM_MAP", None)
            f = pathlib.Path(tempfile.mkdtemp()) / "room_map.json"
            os.environ["ERP_ROOM_MAP_FILE"] = str(f)
            self.assertEqual(erp._room_map(), {})
            f.write_text(json.dumps({"grade-01": "RM-01"}))
            self.assertEqual(erp._room_map(), {"grade-01": "RM-01"})
            os.utime(f, ns=(1, 1))
            f.write_text(json.dumps({"grade-01": "RM-09"}))
            self.assertEqual(erp._room_map(), {"grade-01": "RM-09"})
            os.utime(f, ns=(2, 2))
            f.write_text(json.dumps({" Grade-02 ": "RM-02", "Grade-01": "RM-old", "grade-01": "RM-01"}))
            self.assertEqual(erp._room_map(), {"grade-02": "RM-02", "grade-01": "RM-01"})   # key = รูปที่ ingest เก็บ
            os.environ["ERP_ROOM_MAP"] = json.dumps({"Conform-01": "RM-C1"})
            self.assertEqual(erp._room_map(), {"conform-01": "RM-C1"})
            os.environ.pop("ERP_ROOM_MAP")
            os.environ.pop("ERP_ROOM_MAP_FILE")
            self.assertEqual(erp.room_map_file(), os.path.join(_env.FAKE_HOME, ".editortrack", "room_map.json"))
        finally:
            os.environ.clear()
            os.environ.update(env)


if __name__ == "__main__":
    unittest.main()

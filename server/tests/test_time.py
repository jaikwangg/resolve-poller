"""กลุ่ม 3 — เวลาต่อ record ตามช่องว่างจริง, 1 record ต่อ (host, นาที), ตัดวันตามเวลาท้องถิ่น, PMS ถือ window/ส่งแก้"""
import datetime
import json
import os
import pathlib
import tempfile
import time
import unittest

try:
    from . import _env
except ImportError:
    import _env

import dashboard_api as api      # noqa: E402
import explain as ex             # noqa: E402
import pms_sync                  # noqa: E402
import projects as pj            # noqa: E402
import report as rp              # noqa: E402

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 10, 14, 3, 0, 0, tzinfo=UTC)          # 10:00 เวลาไทย


def rec(sec, host="grade-03", active=True, rx_delay=0.0, **kw):
    t = T0 + datetime.timedelta(seconds=sec)
    r = {"schema": 3, "ts": t.isoformat(), "host": host, "stage": "color", "page": "color",
         "activity": {"idle_sec": 1.0, "active": active, "resolve_frontmost": True},
         "_rx": {"at": (t + datetime.timedelta(seconds=rx_delay)).isoformat()}}
    r.update(kw)
    return r


def eff(r):
    return {"job_id": "J1", "title": "J1", "stage": "color", "source": "fallback"}


class Credit(unittest.TestCase):
    def test_gap_capped_at_1_5_interval_and_last_is_nominal(self):
        rs = ex.assign_credit([rec(0), rec(60), rec(180), rec(240)], 1)
        self.assertEqual([r["_credit_sec"] for r in rs], [60, 90, 60, 60])   # 120 วิ → cap 90

    def test_drifting_loop_counts_real_time(self):
        # macOS loop: sleep 60 + เวลารัน 3 วิ → ห่าง 63 วิ · เดิมนับ 60 วิ/record (ขาด ~5%)
        rs = ex.assign_credit([rec(63 * i) for i in range(20)], 1)
        self.assertAlmostEqual(sum(r["_credit_sec"] for r in rs), 63 * 19 + 60)

    def test_credit_per_host(self):
        rs = ex.assign_credit([rec(0), rec(30, host="grade-04"), rec(60), rec(90, host="grade-04")], 1)
        self.assertEqual([r["_credit_sec"] for r in rs], [60, 60, 60, 60])

    def test_interval_is_configurable(self):
        rs = ex.assign_credit([rec(0), rec(600), rec(1500)], 10)
        self.assertEqual([r["_credit_sec"] for r in rs], [600, 900, 600])


class Dedupe(unittest.TestCase):
    def test_one_record_per_host_minute(self):
        rs = ex.dedupe([rec(0), rec(0.3), rec(29), rec(59), rec(60), rec(30, host="grade-04")])
        self.assertEqual([(r["host"], r["ts"][11:19]) for r in rs],
                         [("grade-03", "03:00:00"), ("grade-03", "03:01:00"), ("grade-04", "03:00:30")])

    def test_two_agents_offset_do_not_double_count(self):
        # 2 agent บนเครื่องเดียว ห่าง 31 วิ (เดิม DUP_SEC=30 → เก็บทั้งคู่ = เวลา × 2)
        raw = [rec(60 * i) for i in range(10)] + [rec(60 * i + 31) for i in range(10)]
        rs = ex.assign_credit(ex.dedupe(raw), 1)
        total = sum(r["_credit_sec"] for r in rs)
        self.assertLessEqual(total, 60 * 10 + 31)          # เวลาจริง ~10 นาที ไม่ใช่ 20
        self.assertGreaterEqual(total, 60 * 9)


class LocalDay(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"                          # process เป็น UTC — โค้ดต้องยังตัดวันตามเวลาไทย
        time.tzset()

    def tearDown(self):
        os.environ["TZ"] = self._tz or "Asia/Bangkok"
        time.tzset()

    def test_0300_bangkok_is_that_day_everywhere(self):
        r = {**rec(0), "ts": "2026-10-14T20:00:00+00:00"}  # 03:00 15 ต.ค. เวลาไทย
        self.assertEqual(ex.local_day(r["ts"]), "2026-10-15")
        self.assertEqual(api._day(r["ts"]), "2026-10-15")
        rep = rp.build_report([r], "2026-10-15", 1, eff)
        self.assertEqual(rep["hosts"][0]["summary"]["first"], "03:00")
        self.assertEqual(rp.build_report([r], "2026-10-14", 1, eff)["hosts"], [])
        self.assertTrue(pj.build_projects([dict(r)], 1, eff)["projects"][0]["first_at"].startswith("15/10 03:00"))

    def test_dashboard_today_uses_local_day(self):
        d = pathlib.Path(tempfile.mkdtemp())
        rs = [{**rec(0), "ts": f"2026-10-14T20:{m:02d}:00+00:00"} for m in range(30)]   # 03:00–03:29 15 ต.ค.
        (d / "2026-10-15.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rs), encoding="utf-8")
        old_store, old_today = api.STORE, ex.today
        api.STORE, ex.today = d, (lambda: datetime.date(2026, 10, 15))
        try:
            db = api.build_dashboard(2)
        finally:
            api.STORE, ex.today = old_store, old_today
        self.assertEqual(db["summary"]["active_hours_today"], 0.5)
        self.assertEqual(db["active_idle"]["workers"]["__team__"]["active"], [0, 0.5])

    def test_tz_configurable(self):
        old = os.environ.get("EDITORTRACK_TZ")
        os.environ["EDITORTRACK_TZ"] = "UTC"
        try:
            self.assertEqual(ex.local_day("2026-10-14T20:00:00+00:00"), "2026-10-14")
        finally:
            os.environ["EDITORTRACK_TZ"] = old or "Asia/Bangkok"


class FakeErp:
    ERP_ACTIVITY_PATH, ERP_BASE = "/activity", "http://fake"

    def __init__(self, upsert=False):
        self.calls, self.held, self.upsert = [], {}, upsert

    def _room_map(self):
        return {"grade-03": "RM-03"}

    def lookup_detail(self, host, ts, project=None):
        return {"booking_id": "b-1"}, None

    def push_activity(self, records):                     # เลียน PMS: ON CONFLICT (host, window_start) DO NOTHING
        self.calls.append([dict(r) for r in records])
        acc = dup = 0
        for r in records:
            k = (r["host"], r["window_start"])
            if k in self.held and not self.upsert:
                dup += 1
            else:
                self.held[k] = r["active_seconds"]
                acc += 1
        return {"accepted": acc, "duplicates": dup, "rejected": []}


class PmsLate(unittest.TestCase):
    def setUp(self):
        pms_sync._sent.clear()
        pms_sync.status["corrections"] = {"sent": 0, "applied": 0, "ignored_by_pms": 0, "ignored_seconds": 0}

    def run_sync(self, recs, now, erp):
        return pms_sync.sync_once(lambda days: ex.assign_credit(ex.dedupe([dict(r) for r in recs]), 1), 1, eff, erp,
                                  now=now)

    def test_window_held_until_host_flushed(self):
        # window 10:00–10:10 · เครื่องหลุด 10:05 → ส่งย้อนหลังตอน 10:40 (rx ช้า)
        live = [rec(60 * i) for i in range(5)]
        late = [rec(60 * i, rx_delay=2100) for i in range(5, 10)]
        erp = FakeErp()
        self.run_sync(live, T0 + datetime.timedelta(minutes=20), erp)      # host เงียบหลัง window → ยังไม่ส่ง
        self.assertEqual(erp.calls, [])
        self.assertEqual(pms_sync.status["held"], 1)
        back = live + late + [rec(2400, rx_delay=0)]                        # 10:40 กลับมา + flush queue
        self.run_sync(back, T0 + datetime.timedelta(minutes=41), erp)      # เพิ่งรับ < SETTLE_SEC → ยังรอ
        self.assertEqual(erp.calls, [])
        self.run_sync(back, T0 + datetime.timedelta(minutes=43), erp)
        self.assertEqual([(w["window_start"][11:16], w["active_seconds"]) for w in erp.calls[0]],
                         [("03:00", 600)])

    def test_max_hold_sends_when_host_never_returns(self):
        erp = FakeErp()
        self.run_sync([rec(60 * i) for i in range(5)], T0 + datetime.timedelta(seconds=600 + pms_sync.MAX_HOLD_SEC),
                      erp)
        self.assertEqual(erp.calls[0][0]["active_seconds"], 300)

    def test_late_record_resent_with_same_key_and_gap_counted(self):
        erp = FakeErp()
        base = [rec(60 * i) for i in range(5)] + [rec(700)]
        self.run_sync(base, T0 + datetime.timedelta(minutes=15), erp)
        self.assertEqual(erp.calls[0][0]["active_seconds"], 330)          # record 04:00 นับ 90 วิ (cap) ก่อนช่องว่าง
        more = base + [rec(60 * i, rx_delay=900) for i in range(5, 10)]    # มาช้ากว่า settle (เกิดได้ถ้าหลุดนาน)
        self.run_sync(more, T0 + datetime.timedelta(minutes=40), erp)
        fix = erp.calls[1][0]
        self.assertEqual((fix["host"], fix["window_start"], fix["active_seconds"]), ("grade-03", "2026-10-14T03:00:00Z", 600))
        c = pms_sync.status["corrections"]
        self.assertEqual((c["sent"], c["applied"], c["ignored_by_pms"], c["ignored_seconds"]), (1, 0, 1, 270))
        self.assertEqual(erp.held[("grade-03", "2026-10-14T03:00:00Z")], 330)   # PMS วันนี้ไม่อัปเดต = gap ที่รู้
        self.run_sync(more, T0 + datetime.timedelta(minutes=45), erp)           # ไม่ส่งซ้ำอีกถ้าค่าไม่เปลี่ยน
        self.assertEqual(len(erp.calls), 2)

    def test_correction_applies_if_pms_upserts(self):
        erp = FakeErp(upsert=True)
        base = [rec(60 * i) for i in range(5)] + [rec(700)]
        self.run_sync(base, T0 + datetime.timedelta(minutes=15), erp)
        self.run_sync(base + [rec(60 * i, rx_delay=900) for i in range(5, 10)], T0 + datetime.timedelta(minutes=40), erp)
        self.assertEqual(pms_sync.status["corrections"]["applied"], 1)
        self.assertEqual(erp.held[("grade-03", "2026-10-14T03:00:00Z")], 600)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""replay ข้อมูลเทสจริง 7–14 ต.ค. 2026 (conform-01, Mac, ลบชื่อ user/IP/ชื่อ project จริงแล้ว) ผ่าน server
ล็อกผลที่ตรวจด้วยมือแล้ว — แก้ report/explain/pms_sync/ingest แล้วตัวเลขเปลี่ยน = เทสนี้ล้ม

รัน:  cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_replay -v
fixture: tests/fixtures/replay/*.jsonl (ชื่อไฟล์ = วันที่ server รับ เหมือน data/)
"""
import json, os, pathlib, sys, tempfile, time, unittest

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixtures" / "replay"
os.environ["TZ"] = "Asia/Bangkok"                 # report ตัดวัน/แสดงเวลาตามเวลาเครื่อง server (ไทย)
time.tzset()
os.environ["EDITORTRACK_STORE"] = str(FIX)
os.environ["EDITORTRACK_TOKEN"] = "t"
for k in [k for k in os.environ if k.startswith("ERP_")]:
    del os.environ[k]                             # ไม่ยิง PMS จริงระหว่างเทส
sys.path.insert(0, str(HERE.parent))

import dashboard_api as api      # noqa: E402
import explain as ex             # noqa: E402
import pms_sync                  # noqa: E402
import report as rp              # noqa: E402


def raw():
    return [json.loads(l) for f in sorted(FIX.glob("*.jsonl")) for l in f.read_text(encoding="utf-8").splitlines()]


def all_recs():
    return ex.dedupe(raw())


def day(d):
    return rp.build_report(all_recs(), d, 1, api._eff_job)["hosts"][0]


class Dedupe(unittest.TestCase):
    def test_drops_selftest_without_ts_and_overlapping_run(self):
        r = raw()
        self.assertEqual(len(r), 264)
        self.assertTrue(any(x.get("host") == "selftest" and "ts" not in x for x in r))   # หลุดเข้ามา 8 ต.ค.
        d = all_recs()
        self.assertEqual(len(d), 262)            # −1 selftest −1 record ซ้อน (ห่าง 0.29 วิ, 8 ต.ค. 09:44)
        self.assertFalse(any(x["host"] == "selftest" for x in d))


class Report(unittest.TestCase):
    def test_colorist_session_14oct(self):
        h = day("2026-10-14")
        s = h["summary"]
        self.assertEqual((s["records"], s["first"], s["last"]), (64, "01:53", "17:28"))
        self.assertEqual((s["active_min"], s["first_active"], s["last_active"]), (24, "16:56", "17:20"))
        self.assertEqual((s["gap_count"], s["gap_min"]), (15, 877))
        self.assertEqual({p["page"]: p["minutes"] for p in h["by_page"]}, {"color": 18, "media": 3, "deliver": 2, "edit": 1})
        self.assertEqual([(p["at"], p["to"]) for p in h["progress"] if p["job"] == "TEST"],
                         [("01:53", 0.0), ("17:00", 16.7), ("17:13", 100.0), ("17:16", 83.3), ("17:17", 75.0)])
        jump = [p for p in h["progress"] if p.get("jump")]
        self.assertEqual([(p["at"], p["detail"]) for p in jump], [("17:13", "6/6 clip")])
        w = " ".join(h["warnings"])
        self.assertIn("เพิ่ม 5 clip ในรอบเดียว", w)
        self.assertIn("ลดลง (6→5)", w)
        self.assertIn("EDITORTRACK_STAGE=conform", w)

    def test_resolve_open_but_untouched_is_idle_12oct(self):
        # 12 ต.ค.: Resolve อยู่หน้าสุดแต่ไม่มี input ~20 นาที (idle 139→1225 วิ) — ต้องไม่นับเป็นเวลาทำงาน
        h = day("2026-10-12")
        front = sum(1 for r in all_recs() if r["ts"].startswith("2026-10-12")
                    and r["activity"].get("resolve_frontmost"))
        self.assertGreater(front, h["summary"]["active_min"] + 15)

    def test_locked_mac_wakes_hourly_9oct(self):
        # Mac ล็อก/หลับ: agent ตื่นราวชั่วโมงละครั้ง (idle เพิ่มแค่ ~60 วิต่อครั้ง = หลับระหว่างนั้น)
        h = day("2026-10-09")
        self.assertEqual(h["summary"]["active_min"], 0)
        self.assertGreaterEqual(h["summary"]["gap_count"], 15)
        self.assertTrue(all("ล็อกจอ" in g["why"] for g in h["gaps"]))


class Explain(unittest.TestCase):
    def test_every_record_explains(self):
        for r in all_recs():
            e = ex.explain(r, api._eff_job(r))
            self.assertTrue(e["lines"])

    def test_active_needs_resolve_frontmost(self):
        r = all_recs()
        raw_active = sum(1 for x in r if x["activity"].get("active"))
        counted = sum(1 for x in r if ex.is_active(x))
        self.assertLess(counted, raw_active)    # Terminal/TextEdit ที่มี input ไม่นับ


class PmsWindows(unittest.TestCase):
    def test_windows_from_14oct_session(self):
        import datetime
        now = datetime.datetime(2026, 10, 14, 12, 0, tzinfo=datetime.timezone.utc)
        booking = {"booking_id": "b-1"}
        old = pms_sync.LOOKBACK_HOURS
        pms_sync.LOOKBACK_HOURS = 48
        try:
            w = pms_sync.build_windows(all_recs(), 1, api._eff_job, {"conform-01": "RM-CS-01"},
                                       now=now, lookup=lambda h, ts, p: (booking, None))
        finally:
            pms_sync.LOOKBACK_HOURS = old
        mine = [x for x in w if x["window_start"].startswith("2026-10-14")]
        self.assertEqual([(x["window_start"][11:16], x["active_seconds"]) for x in mine],
                         [("09:50", 240), ("10:00", 600), ("10:10", 540), ("10:20", 60)])
        self.assertTrue(all(x["resource_code"] == "RM-CS-01" and x["booking_id"] == "b-1" for x in mine))
        self.assertFalse(any("user" in x for x in w))     # PMS ไม่รับชื่อคน


    def test_window_without_booking_is_not_sent(self):
        # กติกา PMS 15 ต.ค.: ทุกช่วงต้องมี booking_id หรือ project_no — รหัสห้องอย่างเดียวถูกปฏิเสธ
        import datetime
        now = datetime.datetime(2026, 10, 14, 12, 0, tzinfo=datetime.timezone.utc)
        unbound = {}
        w = pms_sync.build_windows(all_recs(), 1, api._eff_job, {"conform-01": "RM-CS-01"}, now=now,
                                   lookup=lambda h, ts, p: (None, "ambiguous_booking"), unbound=unbound)
        self.assertEqual(w, [])
        self.assertEqual(unbound, {"ambiguous_booking": 4})

    def test_window_passes_resolve_project_to_lookup(self):
        import datetime
        now = datetime.datetime(2026, 10, 14, 12, 0, tzinfo=datetime.timezone.utc)
        seen = []
        pms_sync.build_windows(all_recs(), 1, api._eff_job, {}, now=now,
                               lookup=lambda h, ts, p: (seen.append(p), ({"booking_id": "x"}, None))[1])
        self.assertEqual(seen, ["Untitled Project", "TEST", "TEST", "TEST"])   # project ที่เปิดมากสุดในช่วง


class PickBooking(unittest.TestCase):
    A = {"id": "a", "project_no": "KPP-2026-0005", "project_name": "Films Reel"}
    B = {"id": "b", "project_no": "KPP-2026-0002", "project_name": "DEV-BURNUP"}

    def pick(self, cands, project):
        import erp
        b, why = erp.pick_booking(cands, project)
        return (b or {}).get("id"), why

    def test_single_booking_needs_no_match(self):
        self.assertEqual(self.pick([self.A], "anything"), ("a", None))

    def test_many_bookings_match_project_no_in_resolve_name(self):
        self.assertEqual(self.pick([self.A, self.B], "KPP-2026-0002_conform_v3"), ("b", None))

    def test_many_bookings_match_project_name(self):
        self.assertEqual(self.pick([self.A, self.B], "20260922 Films reel"), ("a", None))

    def test_many_bookings_no_match_is_ambiguous(self):
        self.assertEqual(self.pick([self.A, self.B], "TEST"), (None, "ambiguous_booking"))
        self.assertEqual(self.pick([self.A, self.B], None), (None, "ambiguous_booking"))

    def test_no_booking(self):
        self.assertEqual(self.pick([], "TEST"), (None, "no_booking"))


class Projects(unittest.TestCase):
    def setUp(self):
        import projects as pj
        self.ps = {p["job_id"]: p for p in pj.build_projects(all_recs(), 1, api._eff_job)["projects"]}

    def test_test_project_counts_and_history_14oct(self):
        p = self.ps["TEST"]
        self.assertEqual((p["counts"]["video_clips"], p["counts"]["graded_clips"]), (8, 6))
        # เปลี่ยนเฉพาะตอนตัวเลขเปลี่ยน: 0/6 → 1/6 → 6/6 (กระโดด) → 5/6 (ลด) → 6/8
        h = [(x["graded_clips"], x["video_clips"], x["jump"], x["drop"]) for x in reversed(p["history"])]
        self.assertEqual(h, [(0, 6, False, False), (1, 6, False, False), (6, 6, True, False),
                             (5, 6, False, True), (6, 8, False, False)])
        self.assertEqual(p["records"], 234)

    def test_project_without_timeline_has_no_counts(self):
        self.assertIsNone(self.ps["Untitled Project"]["counts"])
        self.assertEqual(self.ps["FILMS-REEL"]["counts"]["video_clips"], 55)


class Ingest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.tmp = tempfile.mkdtemp()
        self._store = api.STORE
        api.STORE = pathlib.Path(self.tmp)
        self.c = TestClient(api.app)

    def tearDown(self):
        api.STORE = self._store

    def test_rejects_record_without_ts_or_host(self):
        h = {"Authorization": "Bearer t"}
        self.assertEqual(self.c.post("/ingest", json={"schema": 3, "host": "selftest", "stage": "color"}, headers=h).status_code, 400)
        self.assertEqual(self.c.post("/ingest", json={"ts": "2026-10-14T10:00:00+00:00"}, headers=h).status_code, 400)
        ok = json.loads((FIX / "2026-10-14.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(self.c.post("/ingest", json=ok, headers=h).status_code, 200)
        self.assertEqual(self.c.post("/ingest", json=ok, headers={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()

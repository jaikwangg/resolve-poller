"""runner: กันรันซ้อน (กลุ่ม 3) · offline queue (กลุ่ม 5)"""
import datetime
import http.server
import json
import threading
import time
import unittest

try:
    from . import _env  # noqa: F401
except ImportError:
    import _env  # noqa: F401

import runner  # noqa: E402


class Lock(unittest.TestCase):
    def test_second_run_is_skipped_while_first_holds_lock(self):
        a = runner.run_lock()
        self.assertTrue(a)
        try:
            self.assertIsNone(runner.run_lock())
        finally:
            a.close()
        b = runner.run_lock()
        self.assertTrue(b)
        b.close()


class FakeServer(http.server.BaseHTTPRequestHandler):
    codes = []            # status ที่จะตอบทีละ request (หมด = 200)
    got = []
    delay = 0.0           # วิ ต่อ request — จำลอง server/เครื่องช้า (CPUQuota)

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        FakeServer.got.append(json.loads(self.rfile.read(n)))
        if FakeServer.delay:
            time.sleep(FakeServer.delay)
        code = FakeServer.codes.pop(0) if FakeServer.codes else 200
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


def rec(i, age_h=0.0):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=age_h, minutes=100 - i)
    return {"schema": 3, "ts": t.isoformat(), "host": "grade-03", "n": i}


class Queue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/ingest"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self._servers = runner.SERVERS
        runner.SERVERS = [self.url]
        FakeServer.codes, FakeServer.got, FakeServer.delay = [], [], 0.0
        self._budget = runner.FLUSH_BUDGET_S, runner.FLUSH_MAX_LINES, runner.FLUSH_PERSIST_EVERY
        runner._write_queue([])

    def tearDown(self):
        runner.SERVERS = self._servers
        runner.FLUSH_BUDGET_S, runner.FLUSH_MAX_LINES, runner.FLUSH_PERSIST_EVERY = self._budget
        FakeServer.delay = 0.0
        runner._write_queue([])

    def queued(self):
        return [json.loads(x)["n"] for x in runner._read_queue()]

    def test_offline_goes_to_queue_then_flushes_in_order(self):
        runner.SERVERS = ["http://127.0.0.1:9/ingest"]          # ไม่มีใครฟัง
        for i in range(3):
            self.assertIsNone(runner.post(rec(i)))
        self.assertEqual(self.queued(), [0, 1, 2])
        runner.SERVERS = [self.url]
        self.assertEqual(runner.post(rec(3)), 200)
        self.assertEqual([g["n"] for g in FakeServer.got], [0, 1, 2, 3])   # backlog ก่อน ตัวใหม่ทีหลัง (ตามเวลา)
        self.assertFalse(runner.QUEUE.exists())

    def test_permanent_4xx_is_final_not_queued(self):
        for code in (400, 413, 422):
            FakeServer.codes = [code]
            self.assertEqual(runner.post(rec(0)), code)
            self.assertEqual(self.queued(), [], code)
        FakeServer.codes = [429]                                # 429/408 = ลองใหม่
        runner.post(rec(1))
        self.assertEqual(self.queued(), [1])

    def test_401_403_are_queued_not_lost(self):
        """ช่วง rollout token: server ออก token ใหม่แล้วแต่ env.sh ยังเป็นตัวเก่า (หรือกลับกัน) → ต้องไม่ทิ้งข้อมูล"""
        FakeServer.codes = [401]
        self.assertEqual(runner.post(rec(0)), 401)
        FakeServer.codes = [403]
        self.assertEqual(runner.post(rec(1)), 403)
        self.assertEqual(self.queued(), [0, 1])
        FakeServer.codes = [401]                                # ยังผิด: backlog ติดที่ตัวแรก ตัวใหม่ต่อท้าย ไม่ส่งแซง
        runner.post(rec(2))
        self.assertEqual(self.queued(), [0, 1, 2])
        FakeServer.got = []
        self.assertEqual(runner.post(rec(3)), 200)              # token แก้แล้ว → ส่งครบตามลำดับ
        self.assertEqual([g["n"] for g in FakeServer.got], [0, 1, 2, 3])
        self.assertFalse(runner.QUEUE.exists())

    def test_401_backlog_stays_capped(self):
        old = runner.QUEUE_MAX_LINES, runner.QUEUE_MAX_BYTES
        runner.QUEUE_MAX_LINES, runner.QUEUE_MAX_BYTES = 3, 10
        try:
            for i in range(6):
                FakeServer.codes = [401]
                runner.post(rec(i))
            self.assertEqual(self.queued(), [3, 4, 5])
        finally:
            runner.QUEUE_MAX_LINES, runner.QUEUE_MAX_BYTES = old

    def test_5xx_is_retried(self):
        FakeServer.codes = [503]
        runner.post(rec(0))
        self.assertEqual(self.queued(), [0])

    def test_rejected_line_does_not_block_flush(self):
        runner._write_queue([json.dumps(rec(i)) for i in range(3)] + ["not json"])
        FakeServer.codes = [400]                               # queue ตัวแรก 400 → ทิ้ง ไม่ค้าง · ที่เหลือ + ตัวใหม่ผ่าน
        runner.post(rec(9))
        self.assertEqual([g["n"] for g in FakeServer.got], [0, 1, 2, 9])
        self.assertFalse(runner.QUEUE.exists())

    def test_failure_mid_flush_keeps_unsent_lines(self):
        runner._write_queue([json.dumps(rec(i)) for i in range(4)])
        FakeServer.codes = [200, 503]                          # q0 ok, q1 ล้ม → ตัวใหม่ไม่ส่งแซง ต่อท้าย queue
        runner.post(rec(9))
        self.assertEqual(self.queued(), [1, 2, 3, 9])
        self.assertEqual([g["n"] for g in FakeServer.got], [0, 1])

    def test_crash_mid_flush_loses_nothing(self):
        runner._write_queue([json.dumps(rec(i)) for i in range(3)])
        old = runner._send
        calls = []

        def boom(r):
            calls.append(r["n"])
            if len(calls) == 2:
                raise KeyboardInterrupt                          # process ตายกลางทาง
            return "ok", 200
        runner._send = boom
        try:
            with self.assertRaises(KeyboardInterrupt):
                runner._flush_queue()
        finally:
            runner._send = old
        self.assertEqual(self.queued(), [0, 1, 2])             # เดิม: unlink ก่อนส่ง → หายหมด

    def test_big_backlog_slow_server_stops_within_budget_and_continues(self):
        """S1: backlog ใหญ่ + server ช้า → หยุดในงบเวลา, ความคืบหน้าถูกเขียนลงไฟล์, record ใหม่ต่อท้าย queue
        ไม่หาย ไม่ซ้ำ ไม่สลับลำดับ · รอบถัดไปส่งต่อจากที่ค้าง (เดิม: ถูก kill ที่ 120 วิ → ส่งชุดเดิมซ้ำตลอด)"""
        n = 60
        runner._write_queue([json.dumps(rec(i)) for i in range(n)])
        FakeServer.delay = 0.02
        runner.FLUSH_BUDGET_S, runner.FLUSH_MAX_LINES, runner.FLUSH_PERSIST_EVERY = 0.3, 10_000, 5
        fresh = 100
        t0 = time.monotonic()
        self.assertIsNone(runner.post(rec(fresh)))             # ยังไม่หมด → ไม่ส่งตัวใหม่แซง
        took = time.monotonic() - t0
        self.assertLess(took, 0.3 + 0.5)                       # งบ + 1 request ที่ค้างอยู่ตอนหมดงบ + เผื่อ
        sent = [g["n"] for g in FakeServer.got]
        self.assertTrue(0 < len(sent) < n, sent)
        self.assertNotIn(fresh, sent)
        self.assertEqual(sent + self.queued(), list(range(n)) + [fresh])   # ไม่หาย ไม่ซ้ำ ไม่สลับ
        for _ in range(50):                                    # รอบต่อ ๆ ไป: ส่งต่อจากที่ค้างจนหมด
            if not runner.QUEUE.exists():
                break
            fresh += 1
            runner.post(rec(fresh))
        self.assertFalse(runner.QUEUE.exists())
        got = [g["n"] for g in FakeServer.got]
        self.assertEqual(got, list(range(n)) + list(range(100, fresh + 1)))

    def test_line_budget_per_run(self):
        runner._write_queue([json.dumps(rec(i)) for i in range(10)])
        runner.FLUSH_MAX_LINES = 4
        runner.post(rec(50))
        self.assertEqual([g["n"] for g in FakeServer.got], [0, 1, 2, 3])
        self.assertEqual(self.queued(), [4, 5, 6, 7, 8, 9, 50])
        runner.post(rec(51))
        self.assertEqual(self.queued(), [8, 9, 50, 51])

    def test_kill_mid_flush_keeps_persisted_progress(self):
        """ถูก kill (TimeoutStartSec) กลางทาง → สิ่งที่ส่งไปแล้วถึงจุด persist ล่าสุดไม่ต้องส่งซ้ำ ที่เหลือครบตามลำดับ"""
        runner._write_queue([json.dumps(rec(i)) for i in range(20)])
        runner.FLUSH_PERSIST_EVERY = 3
        old = runner._send
        calls = []

        def killed(r):
            calls.append(r["n"])
            if len(calls) == 8:                                # ส่ง 0..6 สำเร็จ แล้วตายตอนตัวที่ 7
                raise SystemExit("SIGTERM")
            return "ok", 200
        runner._send = killed
        try:
            with self.assertRaises(SystemExit):
                runner._flush_queue()
        finally:
            runner._send = old
        self.assertEqual(self.queued(), list(range(6, 20)))   # persist หลัง 3 และ 6 · 6 ส่งซ้ำได้ (server dedupe ต่อนาที)

    def test_queue_capped_by_lines_and_age(self):
        old = runner.QUEUE_MAX_LINES
        runner.QUEUE_MAX_LINES = 5
        try:
            runner._write_queue([json.dumps(rec(i)) for i in range(8)])
            self.assertEqual([json.loads(x)["n"] for x in runner._trim(runner._read_queue())], [3, 4, 5, 6, 7])
        finally:
            runner.QUEUE_MAX_LINES = old
        lines = [json.dumps(rec(0, age_h=runner.QUEUE_MAX_AGE_H + 1)), json.dumps(rec(1))]
        self.assertEqual([json.loads(x)["n"] for x in runner._trim(lines)], [1])

    def test_enqueue_trims_when_over_size(self):
        old_b, old_l = runner.QUEUE_MAX_BYTES, runner.QUEUE_MAX_LINES
        runner.QUEUE_MAX_BYTES, runner.QUEUE_MAX_LINES = 10, 2
        try:
            for i in range(4):
                runner._enqueue([json.dumps(rec(i))])
            self.assertEqual(self.queued(), [2, 3])
        finally:
            runner.QUEUE_MAX_BYTES, runner.QUEUE_MAX_LINES = old_b, old_l

    def test_write_is_atomic_tmp_rename(self):
        runner._write_queue(["{}"])
        self.assertFalse(runner.QUEUE.with_name(runner.QUEUE.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()

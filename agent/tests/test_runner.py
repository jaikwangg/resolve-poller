"""runner: กันรันซ้อน (กลุ่ม 3) · offline queue (กลุ่ม 5)"""
import datetime
import http.server
import json
import threading
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

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        FakeServer.got.append(json.loads(self.rfile.read(n)))
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
        FakeServer.codes, FakeServer.got = [], []
        runner._write_queue([])

    def tearDown(self):
        runner.SERVERS = self._servers
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
        self.assertEqual([g["n"] for g in FakeServer.got], [3, 0, 1, 2])
        self.assertFalse(runner.QUEUE.exists())

    def test_4xx_is_final_not_queued(self):
        FakeServer.codes = [401]
        self.assertEqual(runner.post(rec(0)), 401)
        self.assertEqual(self.queued(), [])                     # เดิม: token ผิด → queue โต ~1,440 บรรทัด/วัน
        FakeServer.codes = [429]                                # 429/408 = ลองใหม่
        runner.post(rec(1))
        self.assertEqual(self.queued(), [1])

    def test_5xx_is_retried(self):
        FakeServer.codes = [503]
        runner.post(rec(0))
        self.assertEqual(self.queued(), [0])

    def test_rejected_line_does_not_block_flush(self):
        runner._write_queue([json.dumps(rec(i)) for i in range(3)] + ["not json"])
        FakeServer.codes = [200, 400]                          # ตัวปัจจุบันผ่าน · queue ตัวแรก 400 → ทิ้ง ไม่ค้าง
        runner.post(rec(9))
        self.assertEqual([g["n"] for g in FakeServer.got], [9, 0, 1, 2])
        self.assertFalse(runner.QUEUE.exists())

    def test_failure_mid_flush_keeps_unsent_lines(self):
        runner._write_queue([json.dumps(rec(i)) for i in range(4)])
        FakeServer.codes = [200, 200, 503]                     # current ok, q0 ok, q1 ล้ม
        runner.post(rec(9))
        self.assertEqual(self.queued(), [1, 2, 3])

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

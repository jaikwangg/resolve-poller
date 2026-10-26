"""ET-5: the sender/queue policy (agent/runner.py, docs/research/AGENT_SEND_POLICY.md).

A record the server refuses as unusable (400/413/422) is final: dropped from the queue, counted, logged
locally (bounded) and reported in the next heartbeat, and the records behind it are still delivered.
Every other answer (401/403/404/405/408/409/425/429, 3xx, 5xx) and every network error or timeout keeps
the record and starts an exponential send backoff (no network at all while it lasts, Retry-After honoured,
capped). A run of final refusals with no delivery = "reject hold": stop dropping. The agent never sends or
queues a record whose ts lacks a UTC offset. No Resolve call is involved anywhere in this file.

marker: safety  (run: ./run_tests.sh safety)
"""
import json, os, pathlib, sys, time, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tests.util import agent_env                       # noqa: E402
from tests.fake_server import FakeServer               # noqa: E402

MARKERS = {"safety"}

TS = "2026-10-26T10:00:00+00:00"


def rec(i, **kw):
    r = {"schema": 3, "host": "h1", "ts": TS, "i": i}
    r.update(kw)
    return r


def bad_is_400(body):
    return 400 if body.get("bad") else 200


def ids(srv):
    return [b.get("i") for b in srv.posts()]


class Base(unittest.TestCase):
    def queue(self, sd):
        q = sd / "queue.jsonl"
        return [json.loads(l) for l in q.read_text().splitlines()] if q.exists() else []

    def rejected(self, sd):
        p = sd / "rejected.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def log(self, sd):
        p = sd / "rejected.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def expire_backoff(self, sd):
        p = sd / "send_backoff.json"
        if p.exists():
            b = json.loads(p.read_text())
            b["next_at"] = time.time() - 1
            p.write_text(json.dumps(b))


class FinalRejection(Base):
    def test_rejected_queued_record_is_dropped_and_later_records_delivered(self):
        with FakeServer(ingest_status=bad_is_400) as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            r = m["runner"]
            r._enqueue([json.dumps(rec(0)), json.dumps(rec(1, bad=True)), json.dumps(rec(2)), json.dumps(rec(3))])
            self.assertEqual(r.post(rec(9)), 200)
            self.assertEqual(ids(srv), [9, 0, 1, 2, 3])                 # live first, then the backlog in order
            self.assertEqual(self.queue(sd), [])                         # nothing left behind the bad record
            st = self.rejected(sd)
            self.assertEqual((st["total"], st["streak"]), (1, 0))       # a later delivery resets the streak
            self.assertEqual(st["last"]["status"], 400)
            lg = self.log(sd)
            self.assertEqual(len(lg), 1)
            self.assertEqual(json.loads(lg[0]["record"])["i"], 1)
            self.assertIn("fake 400", lg[0]["reason"])
            hb = r._heartbeat(None, {}, time.time())                     # next heartbeat reports it
            self.assertEqual((hb["rejected"], hb["reject_hold"], hb["send_fails"]), (1, False, 0))
            self.assertFalse((sd / "send_backoff.json").exists())

    def test_rejected_live_record_is_not_queued_and_backlog_still_flushes(self):
        with FakeServer(ingest_status=bad_is_400) as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            r = m["runner"]
            r._enqueue([json.dumps(rec(0)), json.dumps(rec(1))])
            self.assertEqual(r.post(rec(9, bad=True)), 400)
            self.assertEqual(ids(srv), [9, 0, 1])
            self.assertEqual(self.queue(sd), [])
            self.assertEqual(self.rejected(sd)["total"], 1)

    def test_413_and_422_are_final_too(self):
        for code in (413, 422):
            with self.subTest(code=code), \
                    FakeServer(ingest_status=lambda b, c=code: c if b.get("bad") else 200) as srv, \
                    agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
                r = m["runner"]
                r._enqueue([json.dumps(rec(1, bad=True)), json.dumps(rec(2))])
                r.post(rec(9))
                self.assertEqual(ids(srv), [9, 1, 2])
                self.assertEqual(self.queue(sd), [])
                self.assertEqual(self.rejected(sd)["last"]["status"], code)

    def test_two_rejections_counted_cumulatively_across_turns(self):
        with FakeServer(ingest_status=bad_is_400) as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            r = m["runner"]
            r.post(rec(1, bad=True))
            r.post(rec(2))
            r.post(rec(3, bad=True))
            self.assertEqual(r._heartbeat(None, {}, time.time())["rejected"], 2)


class RetryableAnswers(Base):
    def test_non_final_statuses_keep_the_record_and_back_off(self):
        for code in (401, 403, 404, 405, 408, 409, 410, 415, 425, 429, 500, 502, 503, 504):
            with self.subTest(code=code), FakeServer(ingest_status=code) as srv, \
                    agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
                r = m["runner"]
                r._enqueue([json.dumps(rec(0))])
                self.assertIsNone(r.post(rec(9)))
                self.assertEqual(ids(srv), [9])                          # no flush after a failure
                self.assertEqual([x["i"] for x in self.queue(sd)], [0, 9])
                self.assertEqual(self.rejected(sd), {})                  # nothing dropped
                b = json.loads((sd / "send_backoff.json").read_text())
                self.assertEqual((b["fails"], b["status"]), (1, code))

    def test_redirect_is_not_followed_and_record_kept(self):
        def redirect(body):
            return (302, {"Location": "http://127.0.0.1:9/elsewhere"})
        with FakeServer(ingest_status=redirect) as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            r = m["runner"]
            self.assertIsNone(r.post(rec(9)))
            self.assertEqual([(mm, p) for mm, p, _ in srv.requests], [("POST", "/ingest")])
            self.assertEqual([x["i"] for x in self.queue(sd)], [9])

    def test_5xx_backoff_means_no_network_then_drains_in_order(self):
        state = {"code": 500}
        with FakeServer(ingest_status=lambda b: state["code"]) as srv, \
                agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            r = m["runner"]
            self.assertIsNone(r.post(rec(1)))
            self.assertEqual(len(srv.posts()), 1)
            for i in (2, 3, 4):                                         # inside the backoff: queued, no request
                self.assertIsNone(r.post(rec(i)))
            self.assertEqual(len(srv.posts()), 1)
            self.assertEqual([x["i"] for x in self.queue(sd)], [1, 2, 3, 4])
            self.expire_backoff(sd)
            self.assertIsNone(r.post(rec(5)))                           # still 500: fails 2, wait doubles
            b = json.loads((sd / "send_backoff.json").read_text())
            self.assertEqual(b["fails"], 2)
            self.assertAlmostEqual(b["next_at"] - time.time(), 120, delta=5)
            self.assertEqual(len(srv.posts()), 2)
            state["code"] = 200
            self.expire_backoff(sd)
            self.assertEqual(r.post(rec(6)), 200)
            self.assertEqual(ids(srv)[2:], [6, 1, 2, 3, 4, 5])
            self.assertEqual(self.queue(sd), [])
            self.assertFalse((sd / "send_backoff.json").exists())

    def test_failure_mid_flush_keeps_that_record_and_the_rest(self):
        state = {"n": 0}
        def seq(body):
            state["n"] += 1
            return 500 if state["n"] == 4 else 200                      # live ok, 2 backlog ok, then 500
        with FakeServer(ingest_status=seq) as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            r = m["runner"]
            r._enqueue([json.dumps(rec(i)) for i in range(6)])
            self.assertEqual(r.post(rec(9)), 200)
            self.assertEqual(ids(srv), [9, 0, 1, 2])
            self.assertEqual([x["i"] for x in self.queue(sd)], [2, 3, 4, 5])
            self.assertTrue((sd / "send_backoff.json").exists())

    def test_timeout_is_retry_with_one_attempt_per_turn(self):
        with FakeServer(ingest_status=lambda b: "hang", hang_sec=1.6) as srv, \
                agent_env(EDITORTRACK_SERVER=srv.url + "/ingest", EDITORTRACK_HTTP_TIMEOUT="1") as (m, sd):
            r = m["runner"]
            r._enqueue([json.dumps(rec(i)) for i in range(50)])
            t0 = time.time()
            self.assertIsNone(r.post(rec(9)))
            self.assertLess(time.time() - t0, 3)
            self.assertEqual(len(srv.posts()), 1)
            self.assertEqual(len(self.queue(sd)), 51)
            self.assertEqual(self.rejected(sd), {})
            self.assertIsNone(json.loads((sd / "send_backoff.json").read_text())["status"])

    def test_server_down_is_retry(self):
        with agent_env() as (m, sd):                                    # BASE_ENV points at port 9 (nothing)
            r = m["runner"]
            self.assertIsNone(r.post(rec(1)))
            self.assertEqual(len(self.queue(sd)), 1)
            self.assertTrue((sd / "send_backoff.json").exists())

    def test_backoff_exponential_capped_and_retry_after(self):
        with agent_env() as (m, sd):
            r = m["runner"]
            now = 1_000_000.0
            waits = [r._send_backoff_fail(500, now=now) for _ in range(7)]
            self.assertEqual(waits, [60, 120, 240, 480, 900, 900, 900])
            r._send_backoff_ok()
            self.assertEqual(r._send_backoff_fail(429, 300, now=now), 300)          # Retry-After > base
            self.assertEqual(r._send_backoff_fail(429, 99999, now=now), 900)        # capped
            self.assertEqual(r._send_backoff_fail(429, 5, now=now), 240)            # never shorter than backoff

    def test_retry_after_header_is_honoured(self):
        with FakeServer(ingest_status=lambda b: (429, {"Retry-After": "300"})) as srv, \
                agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            m["runner"].post(rec(1))
            b = json.loads((sd / "send_backoff.json").read_text())
            self.assertAlmostEqual(b["next_at"] - time.time(), 300, delta=5)

    def test_corrupt_or_future_backoff_file_never_blocks_forever(self):
        with agent_env() as (m, sd):
            r = m["runner"]
            sd.mkdir(parents=True)
            (sd / "send_backoff.json").write_text("{not json")
            self.assertFalse(r._send_backoff_active())
            self.assertEqual(r._send_backoff_fail(500), 60)                          # restarts at 1
            (sd / "send_backoff.json").write_text(json.dumps({"fails": 3, "next_at": time.time() + 10 * 86400}))
            self.assertFalse(r._send_backoff_active())                               # clock went backwards


class RejectHold(Base):
    def test_server_refusing_everything_stops_the_drops(self):
        state = {"code": 400}
        with FakeServer(ingest_status=lambda b: state["code"]) as srv, \
                agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            r = m["runner"]
            r._enqueue([json.dumps(rec(i)) for i in range(30)])
            self.assertEqual(r.post(rec(99)), 400)                      # live dropped: streak 1
            self.assertEqual(self.rejected(sd)["total"], 10)            # 9 more, then the hold
            self.assertEqual(len(srv.posts()), 11)                      # 11th refused -> kept, flush stopped
            self.assertEqual([x["i"] for x in self.queue(sd)], list(range(9, 30)))
            hb = r._heartbeat(None, {}, time.time())
            self.assertTrue(hb["reject_hold"])
            self.expire_backoff(sd)
            self.assertIsNone(r.post(rec(100)))                         # in hold: a refused live record is kept
            self.assertEqual(self.rejected(sd)["total"], 10)
            self.assertEqual(self.queue(sd)[-1]["i"], 100)
            state["code"] = 200
            self.expire_backoff(sd)
            self.assertEqual(r.post(rec(101)), 200)
            self.assertEqual(self.queue(sd), [])
            self.assertEqual(self.rejected(sd)["streak"], 0)
            self.assertFalse(r._heartbeat(None, {}, time.time())["reject_hold"])

    def test_flush_requests_bounded_even_when_every_record_is_refused(self):
        with FakeServer(ingest_status=400) as srv, \
                agent_env(EDITORTRACK_SERVER=srv.url + "/ingest", EDITORTRACK_REJECT_HOLD_STREAK="100000",
                          EDITORTRACK_FLUSH_MAX="300") as (m, sd):
            r = m["runner"]
            r._enqueue([json.dumps(rec(i)) for i in range(1000)])
            r.post(rec(-1))
            self.assertEqual(len(srv.posts()), 301)
            self.assertEqual(len(self.queue(sd)), 700)


class BoundedLog(Base):
    def test_reject_log_keeps_newest_lines_and_bytes(self):
        with agent_env(EDITORTRACK_REJECT_LOG_MAX_LINES="5", EDITORTRACK_REJECT_LOG_MAX_BYTES="100000") as (m, sd):
            r = m["runner"]
            for i in range(20):
                r._reject(json.dumps(rec(i)), 400, "x")
            lg = self.log(sd)
            self.assertEqual([json.loads(e["record"])["i"] for e in lg], [15, 16, 17, 18, 19])
            self.assertEqual(self.rejected(sd)["total"], 20)
        with agent_env(EDITORTRACK_REJECT_LOG_MAX_BYTES="4000") as (m, sd):
            r = m["runner"]
            for i in range(50):
                r._reject(json.dumps(rec(i, pad="y" * 300)), 400, "x")
            self.assertLessEqual((sd / "rejected.log").stat().st_size, 4000)
            self.assertEqual(json.loads(self.log(sd)[-1]["record"])["i"], 49)

    def test_big_record_logged_by_hash_only(self):
        with agent_env() as (m, sd):
            r = m["runner"]
            r._reject(json.dumps(rec(1, pad="z" * 20000)), 413, "too big")
            e = self.log(sd)[0]
            self.assertNotIn("record", e)
            self.assertEqual(len(e["record_sha256"]), 64)
            self.assertGreater(e["record_bytes"], 20000)

    def test_unwritable_state_never_raises(self):
        with agent_env() as (m, sd):
            sd.mkdir(parents=True)
            (sd / "rejected.log").mkdir()
            m["runner"]._reject("{}", 400, "x")                         # must not raise


class NoNaiveTimestamp(Base):
    def test_live_record_without_utc_offset_is_never_sent_or_queued(self):
        for ts, why in (("2026-10-26T10:00:00", "ts_without_offset"), ("2026-10-26T17:00:00+07:00", "ts_not_utc"),
                        ("yesterday", "bad_ts"), (None, "no_ts")):
            with self.subTest(ts=ts), FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
                r = m["runner"]
                self.assertIsNone(r.post(rec(1, ts=ts)))
                self.assertEqual(srv.posts(), [])
                self.assertEqual(self.queue(sd), [])
                st = self.rejected(sd)
                self.assertEqual((st["total"], st["streak"], st["last"]["reason"]), (1, 0, why))

    def test_naive_or_broken_queued_line_dropped_locally_rest_delivered(self):
        with FakeServer() as srv, agent_env(EDITORTRACK_SERVER=srv.url + "/ingest") as (m, sd):
            r = m["runner"]
            r._enqueue([json.dumps(rec(0)), json.dumps(rec(1, ts="2026-10-26T10:00:00")), "{broken", json.dumps(rec(2))])
            self.assertEqual(r.post(rec(9)), 200)
            self.assertEqual(ids(srv), [9, 0, 2])
            self.assertEqual(self.queue(sd), [])
            self.assertEqual([e["reason"] for e in self.log(sd)], ["ts_without_offset", "bad_json"])

    def test_agent_timestamps_are_utc(self):
        with agent_env() as (m, sd):
            r = m["runner"]
            import datetime
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat()   # what build_record writes
            self.assertIsNone(r._ts_problem(rec(1, ts=ts)))
            src = (pathlib.Path(r.__file__)).read_text()
            self.assertIn('"ts": datetime.datetime.now(datetime.timezone.utc).isoformat()', src)


class MultiUrl(Base):
    def test_final_answer_from_backup_url_drops_and_server_error_tries_next(self):
        with FakeServer(ingest_status=400) as bad, FakeServer(ingest_status=500) as err, FakeServer() as ok:
            with agent_env(EDITORTRACK_SERVER="http://127.0.0.1:9/ingest,%s/ingest" % bad.url) as (m, sd):
                self.assertEqual(m["runner"].post(rec(1)), 400)
                self.assertEqual(self.queue(sd), [])
            with agent_env(EDITORTRACK_SERVER="%s/ingest,%s/ingest" % (err.url, ok.url)) as (m, sd):
                self.assertEqual(m["runner"].post(rec(2)), 200)
                self.assertEqual(ok.posts()[0]["i"], 2)


if __name__ == "__main__":
    unittest.main()

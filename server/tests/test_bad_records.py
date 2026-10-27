#!/usr/bin/env python3
"""A bad record must never take the server down (F3, TEST_GAPS ET-2).

One record with a naive ts ("2026-10-14T10:05:00", no offset) made explain.dedupe raise TypeError, so every
/api/* route answered 500 and pms_sync.build_windows raised (PMS sync stopped for the whole lookback).
  - /ingest rejects it (400) with the same check the readers use (explain.record_problem).
  - A bad line already on disk is skipped and counted (/api/health and /api/inbox "skipped"), never a 500.
  - Every field a reader touches, set to every wrong JSON type, in one store: every route still answers.

run:  cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_bad_records -v
"""
import copy, datetime, json, os, pathlib, sys, tempfile, time, unittest

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixtures" / "replay"
os.environ["TZ"] = "Asia/Bangkok"
time.tzset()
os.environ.setdefault("EDITORTRACK_STORE", tempfile.mkdtemp())
for k in [k for k in os.environ if k.startswith("ERP_")]:
    del os.environ[k]                             # never call a real PMS from a test
sys.path.insert(0, str(HERE.parent))
from tests import authfix   # noqa: E402
authfix.install(hosts=("conform-01",))

import dashboard_api as api      # noqa: E402
import explain as ex             # noqa: E402
import pms_sync                  # noqa: E402

H = authfix.machine("conform-01")              # every record posted here is the conform-01 fixture
NAIVE = "2026-10-14T10:05:00"
ROUTES = ["/api/health", "/api/jobs", "/api/shots", "/api/dashboard", "/api/inbox", "/api/inbox?host=bad-01",
          "/api/pms", "/api/report", "/api/report?host=bad-01", "/api/projects", "/health"]
WRONG = [[], {}, "x", 1.5, -1, True, None, NAIVE, [1], {"a": 1}, [{"a": 1}], "", float("nan")]


def last_fixture():
    return json.loads((FIX / "2026-10-14.jsonl").read_text(encoding="utf-8").splitlines()[-1])


def paths(o, pre=()):
    yield pre
    if isinstance(o, dict):
        for k, v in o.items():
            yield from paths(v, pre + (k,))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from paths(v, pre + (i,))


def with_value(o, path, v):
    if not path:
        return v
    o = copy.deepcopy(o)
    cur = o
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = v
    return o


class Base(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._store = api.STORE
        api.STORE = self.tmp
        self.c = TestClient(api.app, raise_server_exceptions=False, headers=authfix.reader())   # read routes need auth
        self.now = datetime.datetime.now(datetime.timezone.utc)

    def tearDown(self):
        api.STORE = self._store

    def good(self, host, minutes_ago):
        r = last_fixture()
        r["host"] = host
        r["activity"].update(active=True, resolve_frontmost=True)        # counts as work (pms windows)
        r["ts"] = (self.now - datetime.timedelta(minutes=minutes_ago)).isoformat()
        return r

    def write_today(self, recs, extra_lines=()):
        with open(self.tmp / f"{datetime.date.today().isoformat()}.jsonl", "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            for ln in extra_lines:
                f.write(ln + "\n")

    def assert_all_routes_answer(self):
        for u in ROUTES:
            r = self.c.get(u)
            self.assertEqual(r.status_code, 200, f"{u} -> {r.status_code}")


class NaiveTimestamp(Base):
    def test_ingest_rejects_ts_without_offset_and_a_bad_line_cannot_stop_reads(self):
        bad = last_fixture()
        bad["ts"] = NAIVE
        r = self.c.post("/ingest", json=bad, headers=H)
        self.assertEqual(r.status_code, 400)
        self.assertIn("ts_without_offset", r.json()["detail"])
        self.assertEqual(list(self.tmp.glob("*.jsonl")), [])          # nothing stored
        for ok_ts in ("2026-10-14T10:05:00Z", "2026-10-14T10:05:00+00:00", "2026-10-14T17:05:00+07:00"):
            self.assertEqual(self.c.post("/ingest", json={**bad, "ts": ok_ts}, headers=H).status_code, 200, ok_ts)

        # a line like that already on disk (written before this fix, or by hand): skipped + counted, never a 500
        recs = [self.good("grade-01", m) for m in (40, 38, 36)] + [self.good("conform-02", m) for m in (40, 38)]
        naive = self.good("bad-01", 39)
        naive["ts"] = (self.now - datetime.timedelta(minutes=39)).replace(tzinfo=None).isoformat()
        self.write_today(recs + [naive], extra_lines=["{not json", ""])
        loaded = api._load_recent(2)
        self.assertEqual(sorted({r["host"] for r in loaded}), ["conform-02", "grade-01"])
        self.assertEqual(api.load_skipped[2]["bad_record"], 1)
        self.assertEqual(api.load_skipped[2]["reasons"], {"ts_without_offset": 1})
        self.assertEqual(api.load_skipped[2]["bad_json"], 1)
        self.assert_all_routes_answer()
        self.assertEqual(self.c.get("/api/health").json()["skipped"]["bad_record"], 1)
        self.assertEqual(self.c.get("/api/inbox").json()["skipped"]["reasons"], {"ts_without_offset": 1})

        # pms_sync: build_windows over the same load still returns the other hosts' windows
        wins = pms_sync.build_windows(loaded, 1, api._eff_job, {}, now=self.now + datetime.timedelta(hours=1),
                                      lookup=lambda h, ts, p: ({"booking_id": "b-" + h}, None))
        self.assertEqual(sorted({w["host"] for w in wins}), ["conform-02", "grade-01"])

    def test_dedupe_alone_survives_naive_and_aware_mixed(self):
        a = self.good("h", 5)
        n = dict(a, ts=NAIVE)
        skipped = {}
        self.assertEqual(ex.dedupe([a, n, dict(a, ts=None), "x", dict(a, host=["h"])], skipped), [a])
        self.assertEqual(skipped, {"ts_without_offset": 1, "no_ts": 1, "not_object": 1, "bad_host": 1})
        self.assertIsNone(ex._dt(NAIVE))
        self.assertIsNotNone(ex._dt("2026-10-14T10:05:00Z"))


class AnyWrongType(Base):
    def test_every_field_with_every_wrong_type_in_one_store_never_500s(self):
        base = self.good("bad-01", 0)
        base["source"]["shots"] = [{"code": "A1", "graded": True}]
        base["source"]["items"] = [{"sid": "A1.mov", "id": "u1", "graded": True}]
        base["agent"] = {"version": "1", "rss_mb": 10, "turn_ms": 5, "guard": {"ms": 3}, "queue_lines": 0}
        base["page"] = "color"
        base["source"].update(reason="resolve_not_running", detail="x", page="color")
        base["source"]["timeline"]["grade"] = {"pending": 0}
        recs, rejected, i = [self.good("grade-01", m) for m in (50, 48)], 0, 0
        for p in list(paths(base)):
            for v in WRONG:
                rec = with_value(base, p, v)
                if isinstance(rec, dict) and p != ("ts",):
                    i += 1
                    rec["ts"] = (self.now - datetime.timedelta(seconds=40 * i)).isoformat()   # no dedupe
                    if p != ("progress", "pct") and isinstance(rec.get("progress"), dict):
                        rec["progress"]["pct"] = float(i % 97)       # a new value each record: report events/jumps
                    if p[:2] != ("source", "ok") and isinstance(rec.get("source"), dict) and i % 2:
                        rec["source"]["ok"] = False                  # half take the "could not read" branches
                rejected += ex.record_problem(ex.finite(rec)) is not None     # NaN -> null first, as on load
                recs.append(rec)
        self.assertGreater(rejected, 50)
        self.assertGreater(len(recs) - rejected, 300)             # most variants are accepted and rendered
        self.write_today(recs)
        self.assert_all_routes_answer()
        self.assertEqual(api.load_skipped[2]["bad_record"], rejected)
        pms_sync.build_windows(api._load_recent(2), 1, api._eff_job, {}, now=self.now + datetime.timedelta(hours=1),
                               lookup=lambda h, ts, p: ({"booking_id": "b"}, None))

    def test_health_alert_skips_a_bad_stored_record(self):
        import agent_health
        import health_alert
        bad = self.good("bad-01", 3)
        bad["agent"] = "x"
        naive = dict(self.good("bad-02", 3), ts=NAIVE)
        self.write_today([self.good("grade-01", 5), bad, naive])
        rows = agent_health.health(health_alert.load_records(self.tmp))
        self.assertEqual(sorted(rows), ["grade-01"])

    def test_ingest_and_readers_use_the_same_rule(self):
        base = last_fixture()
        for bad, why in ((dict(base, progress={"pct": "75"}), "bad_type:progress.pct"),
                         (dict(base, source="x"), "bad_type:source"),
                         (dict(base, host=""), "bad_host"),
                         (dict(base, ts="yesterday"), "bad_ts"),
                         ([base], "not_object")):
            self.assertEqual(ex.record_problem(bad), why)
            r = self.c.post("/ingest", json=bad, headers=H)
            self.assertEqual(r.status_code, 400)
            self.assertIn(why, r.json()["detail"])

    def test_every_replay_fixture_record_is_still_accepted(self):
        for f in sorted(FIX.glob("*.jsonl")):
            for n, ln in enumerate(f.read_text(encoding="utf-8").splitlines()):
                r = json.loads(ln)
                if r.get("host") and r.get("ts"):            # the 8 Oct {"host":"selftest"} line has no ts
                    self.assertIsNone(ex.record_problem(r), f"{f.name}:{n + 1}")


if __name__ == "__main__":
    unittest.main()

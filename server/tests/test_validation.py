"""ตรวจ record ตอน /ingest (400) + หน้าอ่านทนต่อ record ผิดรูปที่อยู่บนดิสก์แล้ว (ข้าม + log ไม่ 500)

เดิม: POST {"host": ..., "ts": ..., "job": "x"} → 200 เก็บลงดิสก์ → /api/jobs, /api/dashboard ฯลฯ ตอบ 500 ทุกครั้ง
"""
import copy
import datetime
import json
import pathlib
import shutil
import tempfile
import unittest

try:
    from . import _env
except ImportError:
    import _env

import auth                      # noqa: E402
import dashboard_api as api      # noqa: E402
import pms_sync                  # noqa: E402
import validate                  # noqa: E402

READS = ["/api/jobs", "/api/shots", "/api/dashboard", "/api/inbox", "/api/report", "/api/projects", "/api/pms"]
WEIRD = ["x", "", 1, -1, 1.5, 1e9, True, None, [], [1], {}, {"a": 1}, float("inf"), float("nan")]


def now_iso(sec=0):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=sec)).isoformat()


def full_rec(sec=0, host="grade-03"):
    """record เต็มรูปแบบแบบที่ agent color ส่งจริง (ทุก field ที่ server อ่าน)"""
    return {
        "schema": 3, "ts": now_iso(sec), "host": host, "stage": "color", "page": "color",
        "activity": {"idle_sec": 3.0, "active": True, "resolve_frontmost": True, "locked": False, "platform": "Linux"},
        "source": {"ok": True, "project": "JOB-1_color", "page": "color", "timeline_count": 1, "rendering": False,
                   "timeline": {"name": "TL1", "duration_sec": 60.0, "fps": 24.0, "video_clips": 10, "graded_clips": 4,
                                "markers": 2},
                   "shots": [{"code": "SH010", "graded": True}, {"code": "SH020", "graded": False}],
                   "render": {"polled": False}},
        "job": {"job_id": "JOB-1", "title": "Job 1", "role": "color", "resolved_by": "naming", "linked": True},
        "progress": {"stage": "color", "pct": 40.0, "done": None, "total": None,
                     "detail": {"graded_clips": 4, "video_clips": 10}},
    }


def paths(o, p=()):
    """ทุก path ใน dict (รวม dict ซ้อน และ element แรกของ list)"""
    if isinstance(o, dict):
        for k, v in o.items():
            yield p + (k,)
            yield from paths(v, p + (k,))
    elif isinstance(o, list) and o:
        yield p + (0,)
        yield from paths(o[0], p + (0,))


def mutate(rec, path, val):
    r = copy.deepcopy(rec)
    cur = r
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = val
    return r


class _Api(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self._store = api.STORE
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="editortrack-val-"))
        api.STORE = self.tmp
        _env.ensure_read_user()
        self.c = TestClient(api.app)
        self.h = _env.basic()
        self.tok = {"Authorization": "Bearer " + auth.issue_token("grade-03", force=True)}

    def tearDown(self):
        api.STORE = self._store
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_disk(self, recs, raw_lines=()):
        """เขียนลงดิสก์ตรง ๆ (ข้าม /ingest) — จำลอง record เสียที่เก็บไว้ก่อนมีการตรวจ"""
        fp = self.tmp / f"{api._ex.today().isoformat()}.jsonl"
        with open(fp, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            for ln in raw_lines:
                f.write(ln + "\n")

    def reads_ok(self, why=""):
        for u in READS:
            r = self.c.get(u, headers=self.h)
            self.assertEqual(r.status_code, 200, f"{u} {why}: {r.text[:200]}")


class Ingest(_Api):
    def post(self, rec):
        return self.c.post("/ingest", json=rec, headers=self.tok)

    def test_full_record_accepted(self):
        self.assertEqual(self.post(full_rec()).status_code, 200)

    def test_reviewer_case_job_string_rejected_and_reads_stay_up(self):
        r = self.post({"host": "grade-03", "ts": now_iso(), "job": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("job", r.json()["detail"])
        self.assertEqual(list(self.tmp.glob("*.jsonl")), [])          # ไม่ได้เก็บ
        self.reads_ok()

    def test_rejects_non_dict_sections(self):
        for k in ("job", "source", "progress", "activity"):
            for v in ("x", 1, [1], True):
                self.assertEqual(self.post({**full_rec(), k: v}).status_code, 400, (k, v))
        for k in ("job", "source", "progress", "activity"):            # ไม่มี / null = ได้
            r = full_rec()
            r.pop(k)
            self.assertEqual(self.post(r).status_code, 200, k)

    def test_stage_must_be_known(self):
        for s in ("grading", "Color", "", 3, ["color"]):
            self.assertEqual(self.post({**full_rec(), "stage": s}).status_code, 400, s)
        for s in validate.STAGES:
            self.assertEqual(self.post({**full_rec(), "stage": s}).status_code, 200, s)

    def test_pct_finite_and_in_range(self):
        for v in (-0.1, 100.1, 1e9, "50", True, [50]):
            self.assertEqual(self.post(mutate(full_rec(), ("progress", "pct"), v)).status_code, 400, v)
        for v in (0, 0.0, 55.5, 100, None):
            self.assertEqual(self.post(mutate(full_rec(), ("progress", "pct"), v)).status_code, 200, v)
        # NaN / Infinity (Python json ส่งได้) — client ส่งเป็นข้อความดิบ
        for lit in ("NaN", "Infinity", "-Infinity"):
            body = json.dumps(full_rec()).replace('"pct": 40.0', f'"pct": {lit}')
            r = self.c.post("/ingest", content=body, headers={**self.tok, "Content-Type": "application/json"})
            self.assertEqual(r.status_code, 400, lit)

    def test_host_pattern_and_normalised(self):
        for h in ("grade 03", "grade-03;rm", "<b>x</b>", "-grade", "a" * 300, 3, ["grade-03"], ""):
            self.assertEqual(self.post({**full_rec(), "host": h}).status_code, 400, h)
        # station ตั้ง hostname ตัวเล็กทุกเครื่อง (fixture + room_map ตัวเล็กหมด) → server เก็บตัวเล็ก
        self.assertEqual(self.post({**full_rec(), "host": "  Grade-03 "}).status_code, 200)
        saved = [json.loads(ln) for fp in self.tmp.glob("*.jsonl") for ln in fp.read_text().splitlines()]
        self.assertEqual([r["host"] for r in saved], ["grade-03"])

    def test_ts_must_be_parseable_with_timezone(self):
        for ts in ("yesterday", "2026-10-14T10:00:00", 1729000000, "", None):
            self.assertEqual(self.post({**full_rec(), "ts": ts}).status_code, 400, ts)
        self.assertEqual(self.post({**full_rec(), "ts": "2026-10-14T10:00:00Z"}).status_code, 200)

    def test_server_fields_from_agent_are_dropped_not_rejected(self):
        r = self.post({**full_rec(), "_job": "x", "_rx": [1]})
        self.assertEqual(r.status_code, 200)
        saved = json.loads(next(self.tmp.glob("*.jsonl")).read_text().splitlines()[0])
        self.assertNotIn("_job", saved)
        self.assertIsInstance(saved["_rx"], dict)

    def test_fixture_records_still_accepted(self):
        n = 0
        for fp in sorted(_env.FIX.glob("*.jsonl")):
            for ln in fp.read_text(encoding="utf-8").splitlines():
                r = json.loads(ln)
                if r.get("host") != "conform-01" or n >= 40:
                    continue
                n += 1
                self.assertIsNone(validate.problem(r, strict=True), ln[:200])
        self.assertEqual(n, 40)

    def test_fuzz_ingest_never_500_and_accepted_never_breaks_reads(self):
        base = full_rec()
        accepted = 0
        for i, p in enumerate(paths(base)):
            for v in WEIRD:
                body = json.dumps(mutate(base, p, v))
                r = self.c.post("/ingest", content=body, headers={**self.tok, "Content-Type": "application/json"})
                ok = (200, 400, 401) if p == ("host",) else (200, 400)        # host อื่น = ไม่มี token → 401
                self.assertIn(r.status_code, ok, (p, v, r.text[:200]))
                accepted += r.status_code == 200
        self.assertGreater(accepted, 0)
        self.reads_ok("หลัง fuzz ingest")


class TolerantReads(_Api):
    def test_bad_records_on_disk_are_skipped_and_logged(self):
        good = full_rec(120)
        self.write_disk([{"host": "grade-03", "ts": now_iso(), "job": "x"},      # เคส reviewer
                         {"host": "grade-03", "ts": now_iso(), "source": [1]},
                         {"host": "grade-03", "ts": 5},
                         {"host": ["grade-03"], "ts": now_iso()},
                         "just a string", [1, 2], good],
                        raw_lines=["{not json", ""])
        with open(next(self.tmp.glob("*.jsonl")), "ab") as f:
            f.write(b'{"host": "\xff\xfe"}\n')                                  # ไม่ใช่ UTF-8
        with self.assertLogs("editortrack", level="WARNING") as cm:
            recs = api._load_recent(2)
        self.assertEqual([r["ts"] for r in recs], [good["ts"]])
        self.assertTrue(any("ข้าม record ผิดรูป" in m for m in cm.output))
        self.reads_ok()
        j = self.c.get("/api/jobs", headers=self.h).json()
        self.assertEqual([x["job_id"] for x in j["jobs"]], ["JOB-1"])

    def test_skip_log_not_repeated_every_request(self):
        self.write_disk([{"host": "grade-03", "ts": now_iso(), "job": "x"}])
        api._load_recent(2)
        with self.assertNoLogs("editortrack", level="WARNING"):
            api._load_recent(2)

    def test_fuzz_disk_records_never_500(self):
        """record ทุกแบบที่ /ingest เดิมรับได้ (host+ts ถูก) แต่ field ข้างในผิดชนิด → อยู่บนดิสก์แล้วหน้าอ่านต้องไม่ 500"""
        base = full_rec(300)
        bad = []
        for p in paths(base):
            if p in (("host",), ("ts",)):
                continue
            for v in WEIRD:
                bad.append(mutate(base, p, v))
        for i in range(0, len(bad), 40):                                   # ทีละชุด — ชุดไหนพังจะรู้
            chunk = bad[i:i + 40] + [full_rec(60)]
            self.write_disk(chunk)
            self.reads_ok(f"chunk {i}")
            recs = api._load_recent(2)
            self.assertTrue(recs)
            pms_sync.build_windows(recs, 1, api._eff_job, {},                # thread PMS ใช้ชุดเดียวกัน
                                   now=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1))
            for r in recs:
                api._ex.explain(r, api._eff_job(r))


class HostTokensNormalised(unittest.TestCase):
    def test_mixed_case_token_file_and_issue(self):
        tok = auth.issue_token("  Conform-09 ", force=True)
        self.assertIn("conform-09", auth.host_tokens())
        self.assertEqual(auth.check_agent("conform-09", f"Bearer {tok}"), (True, "ok"))
        self.assertTrue(auth.revoke_token("CONFORM-09"))

    def _legacy(self, hosts):
        auth.save_json(auth.tokens_file(), {"hosts": hosts})

    def _keys(self):
        return list(json.loads(auth.tokens_file().read_text(encoding="utf-8"))["hosts"])

    def test_legacy_mixed_case_entry_can_be_revoked(self):
        """S2: ไฟล์เก่าออกด้วย 'Grade-07' → revoke grade-07 ต้องได้ (เดิม pop('grade-07') ไม่เจอ → token ยังใช้ได้)"""
        self._legacy({"Grade-07": {"sha256": auth.sha256("et_old")}, "grade-01": {"sha256": "x"}})
        self.assertEqual(auth.check_agent("grade-07", "Bearer et_old"), (True, "ok"))
        self.assertTrue(auth.revoke_token("grade-07"))
        self.assertEqual(auth.check_agent("grade-07", "Bearer et_old")[0], False)
        self.assertEqual(self._keys(), ["grade-01"])
        self.assertFalse(auth.revoke_token("GRADE-07"))

    def test_legacy_mixed_case_entry_is_replaced_not_duplicated(self):
        self._legacy({"Grade-08": {"sha256": auth.sha256("et_old")}})
        with self.assertRaises(ValueError):                    # มีอยู่แล้ว (ต่างแค่ตัวพิมพ์) → ต้อง --force
            auth.issue_token("grade-08")
        tok = auth.issue_token("grade-08", force=True)
        self.assertEqual(self._keys(), ["grade-08"])           # ไม่มี key ซ้ำ 'Grade-08' + 'grade-08'
        self.assertEqual(auth.check_agent("grade-08", f"Bearer {tok}"), (True, "ok"))
        self.assertEqual(auth.check_agent("grade-08", "Bearer et_old")[0], False)

    def test_duplicate_legacy_keys_collapse_on_write(self):
        self._legacy({"Grade-09": {"sha256": auth.sha256("et_old")}, "grade-09": {"sha256": auth.sha256("et_new")},
                      "Conform-03": {"sha256": "y"}})
        self.assertEqual(auth.check_agent("grade-09", "Bearer et_new"), (True, "ok"))   # รูปตัวเล็ก (ใหม่กว่า) ชนะ
        auth.issue_token("grade-02", force=True)               # เขียนไฟล์ครั้งใดก็ normalise ทั้งไฟล์
        self.assertEqual(sorted(self._keys()), ["conform-03", "grade-02", "grade-09"])
        self.assertTrue(auth.revoke_token("grade-09"))
        self.assertNotIn("grade-09", auth.host_tokens())

    def test_public_reason_hides_unknown_host(self):
        self.assertEqual(auth.public_reason("unknown_host"), auth.public_reason("bad_token"))
        self.assertEqual(auth.public_reason("no_token"), "no_token")


if __name__ == "__main__":
    unittest.main()

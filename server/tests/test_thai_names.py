#!/usr/bin/env python3
"""Thai project / clip names (F3, TEST_GAPS ET-1).

The old matcher kept only [0-9a-z]: "โฆษณา ธนาคาร 2026" became "2026" and picked booking "BrandX 2026 TVC"
(work went to the wrong PMS booking), a Thai-only project became "" and job_matches returned True (the
wrong-job guard was skipped), and clips ฉาก1_เทค2 / ซีน1_เทค2 were one shot "12".

run:  cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_thai_names -v
"""
import datetime, os, pathlib, shutil, sys, tempfile, unittest

HERE = pathlib.Path(__file__).parent
for k in [k for k in os.environ if k.startswith(("ERP_", "EDITORTRACK_SCOPE", "EDITORTRACK_PROGRESS", "EDITORTRACK_STABLE",
                                                 "EDITORTRACK_JOB_GUARD", "EDITORTRACK_MASS"))]:
    del os.environ[k]
sys.path.insert(0, str(HERE.parent))

import erp               # noqa: E402
import pms_sync          # noqa: E402
import progress_v2 as pv  # noqa: E402

THAI_AD = {"id": "a", "project_no": "KPP-2026-0007", "project_name": "โฆษณา ธนาคาร"}
BRANDX = {"id": "b", "project_no": "KPP-2026-0002", "project_name": "BrandX 2026 TVC"}
KPP1 = {"id": "k1", "project_no": "KPP-1", "project_name": "Short A"}
KPP10 = {"id": "k10", "project_no": "KPP-10", "project_name": "Short B"}
SARA_AM = "ำ"                       # ำ
SARA_AM_SPLIT = "ํา"           # ํ + า (how some keyboards / PDFs write it)


def pick(cands, project):
    b, why = erp.pick_booking(cands, project)
    return (b or {}).get("id"), why


class PickBooking(unittest.TestCase):
    def test_thai_project_name_never_matches_by_digits_alone(self):
        self.assertEqual(pick([THAI_AD, BRANDX], "โฆษณา ธนาคาร 2026"), ("a", None))
        self.assertEqual(pick([BRANDX, THAI_AD], "โฆษณา ธนาคาร 2026"), ("a", None))      # order does not matter

    def test_thai_name_with_a_number_only_in_common_is_unmatched(self):
        # only "2026" in common with BrandX, nothing with the Thai booking -> nothing is sent (D5 default)
        self.assertEqual(pick([THAI_AD, BRANDX], "งานแต่ง 2026"), (None, "ambiguous_booking"))
        self.assertEqual(pick([THAI_AD, BRANDX], "2026"), (None, "ambiguous_booking"))
        self.assertEqual(pick([THAI_AD, BRANDX], "๒๐๒๖"), (None, "ambiguous_booking"))   # Thai digits

    def test_thai_name_without_spaces_or_with_zero_width_space_matches(self):
        self.assertEqual(pick([THAI_AD, BRANDX], "โฆษณาธนาคาร_grade_v3"), ("a", None))
        self.assertEqual(pick([THAI_AD, BRANDX], "โฆษณา​ธนาคาร"), ("a", None))

    def test_project_code_wins_for_mixed_names(self):
        self.assertEqual(pick([THAI_AD, BRANDX], "KPP-2026-0002_โฆษณา ธนาคาร"), ("b", None))
        self.assertEqual(pick([THAI_AD, BRANDX], "kpp20260007 ตัดต่อ"), ("a", None))

    def test_kpp_1_is_not_kpp_10(self):
        self.assertEqual(pick([KPP1, KPP10], "KPP-10_grade"), ("k10", None))
        self.assertEqual(pick([KPP1, KPP10], "KPP-1_grade"), ("k1", None))
        self.assertEqual(pick([KPP1, KPP10], "KPP-100"), (None, "ambiguous_booking"))

    def test_digit_only_project_code_is_never_used(self):
        a = {"id": "a", "project_no": "2026", "project_name": None}
        b = {"id": "b", "project_no": "2027", "project_name": None}
        self.assertEqual(pick([a, b], "โฆษณา 2026"), (None, "ambiguous_booking"))

    def test_sara_am_in_either_form_is_one_name(self):
        x = {"id": "x", "project_no": None, "project_name": "งานทำสี ธนาคาร"}
        y = {"id": "y", "project_no": None, "project_name": "BrandX TVC"}
        self.assertEqual(pick([x, y], "งานท" + SARA_AM_SPLIT + "สี ธนาคาร"), ("x", None))
        self.assertEqual(pick([x, y], "งานท" + SARA_AM + "สี ธนาคาร"), ("x", None))


    def test_thai_match_never_ends_inside_a_syllable(self):
        x = {"id": "x", "project_no": None, "project_name": "โฆษณา ธนาคาร"}
        y = {"id": "y", "project_no": None, "project_name": "BrandX TVC"}
        self.assertEqual(pick([x, y], "โฆษณา ธนาคาร่"), (None, "ambiguous_booking"))   # + tone mark
        self.assertEqual(pick([x, y], "โฆษณา ธนาคาร_v2"), ("x", None))

    def test_tone_mark_typing_order_is_one_name(self):
        x = {"id": "x", "project_no": None, "project_name": "กิ่งไม้ ทองคำ"}
        y = {"id": "y", "project_no": None, "project_name": "BrandX TVC"}
        self.assertEqual(pick([x, y], "ก่ิงไม้ ทองคำ"), ("x", None))


class JobGuard(unittest.TestCase):
    CASES = [
        # project in Resolve, booking project_no, booking name, matches
        ("โฆษณา ธนาคาร", "KPP-2026-0002", "BrandX TVC", False),          # TEST_GAPS ET-1
        ("โฆษณา ธนาคาร", None, "BrandX TVC", False),
        ("โฆษณา ธนาคาร 2026", "KPP-2026-0002", "BrandX 2026 TVC", False),  # only the year in common
        ("โฆษณา ธนาคาร", "KPP-2026-0007", "โฆษณา ธนาคาร", True),
        ("โฆษณาธนาคาร_color", None, "โฆษณา ธนาคาร", True),
        ("KPP-2026-0007 โฆษณา", "KPP-2026-0007", "BrandX TVC", True),       # code in a mixed name
        ("ทีวีซี KPP-10", "KPP-1", "BrandX TVC", False),                   # KPP-1 vs KPP-10 kept
        ("2026", "KPP-2026-0002", "BrandX 2026 TVC", False),               # digits only
        ("๒๐๒๖", "2026", None, False),                                      # Thai digits, digit-only code
        ("___", "KPP-1", "BrandX TVC", False),                             # nothing to match on
        ("", "KPP-1", "BrandX TVC", True),                                 # no project open: no counts
        (None, "KPP-1", "BrandX TVC", True),
    ]

    def test_cases(self):
        for project, pno, name, ok in self.CASES:
            with self.subTest(project=project):
                self.assertIs(pv.job_matches(project, pno, name), ok)

    def test_thai_only_project_is_withheld_end_to_end(self):
        store = tempfile.mkdtemp(prefix="et-thai-")
        self.addCleanup(shutil.rmtree, store, True)
        pms_sync._pg.update({"engine": None, "pushed": {}, "store": None})
        pms_sync.progress_status["withheld"], pms_sync.progress_status["sent"] = {}, 0

        class PMS:
            ERP_BASE, ERP_PROGRESS_PATH, ERP_PROGRESS_METHOD = "http://pms.invalid", "/p", "POST"
            bodies = []

            def _post_auth(self, url, body, method="POST"):
                self.bodies.append(body)
                return {"data": {"stored": True}}

        def rec(m, project):
            t = (datetime.datetime(2026, 11, 2, 2, 0, tzinfo=datetime.timezone.utc)
                 + datetime.timedelta(minutes=m)).isoformat()
            return {"schema": 3, "ts": t, "host": "grade-01", "stage": "color",
                    "source": {"ok": True, "project": project,
                               "timeline": {"name": "Master", "duration_sec": 100.0, "video_clips": 10,
                                            "graded_clips": m}},
                    "progress": {"stage": "color", "pct": 10.0 * m, "detail": {"graded_clips": m, "video_clips": 10}}}

        eff = lambda r: {"job_id": "KPP-2026-0002", "title": "BrandX TVC", "stage": "color", "source": "erp"}
        pms = PMS()
        for m in range(5):
            pms_sync.progress_on_ingest(rec(m, "โฆษณา ธนาคาร"), eff, pms, store)
        self.assertEqual(pms.bodies, [])
        self.assertEqual(pms_sync.progress_status["withheld"].get("job_mismatch"), 5)

        eff_th = lambda r: {"job_id": "KPP-2026-0007", "title": "โฆษณา ธนาคาร", "stage": "color", "source": "erp"}
        pms_sync.progress_on_ingest(rec(6, "โฆษณา ธนาคาร"), eff_th, pms, store)
        self.assertEqual([b["project_no"] for b in pms.bodies], ["KPP-2026-0007"])


class ClipIdentity(unittest.TestCase):
    def test_thai_clip_names_keep_distinct_identities(self):
        self.assertNotEqual(pv._norm_code("ฉาก1_เทค2.mov"), pv._norm_code("ซีน1_เทค2.mov"))
        self.assertEqual(pv._norm_code("ฉาก1_เทค2_v003.mov"), pv._norm_code("ฉาก1 เทค2.mov"))
        self.assertEqual(pv._norm_code("ฉากท" + SARA_AM + "สี.mov"), pv._norm_code("ฉากท" + SARA_AM_SPLIT + "สี.mov"))
        self.assertEqual(pv._norm_code("ฉาก๑.mov"), pv._norm_code("ฉาก1.mov"))
        ids = pv.identities([{"sid": "ฉาก1_เทค2.mov", "id": "u1", "track": 1, "start": 0, "end": 10},
                             {"sid": "ซีน1_เทค2.mov", "id": "u2", "track": 1, "start": 20, "end": 30}])
        self.assertEqual(len(ids), 2)

    def test_latin_clip_codes_unchanged(self):
        for name, code in (("A001_C003_v002.mov", "A001C003"), ("sh010-comp.v12.exr", "SH010COMP"),
                           ("Shot 12.mxf", "SHOT12"), ("", ""), (None, "")):
            self.assertEqual(pv._norm_code(name), code)


if __name__ == "__main__":
    unittest.main()

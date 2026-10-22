"""กลุ่ม 5 — esc ใน HTML (XSS), เพดาน ?days=, JSON เสียที่ /ingest, เกณฑ์ "กระโดด" เดียวกันทุกหน้า"""
import datetime
import pathlib
import re
import shutil
import subprocess
import unittest

try:
    from . import _env
except ImportError:
    import _env

import auth                      # noqa: E402
import dashboard_api as api      # noqa: E402
import projects as pj            # noqa: E402
import report as rp              # noqa: E402

SERVER = pathlib.Path(__file__).parents[1]
PAGES = ["dashboard.html", "inbox.html", "report.html", "projects.html"]
EVIL = """<img src=x onerror="alert('x')">&"""
WANT = "&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;"


def esc_src(page):
    line = next(ln for ln in (SERVER / page).read_text(encoding="utf-8").splitlines() if ln.startswith("const esc = "))
    return line[:line.rindex(";") + 1] if "//" in line else line


class Escape(unittest.TestCase):
    def test_esc_escapes_quotes_static(self):
        for p in PAGES:
            src = esc_src(p)
            self.assertIn('"', re.search(r"\[(.*?)\]/g", src).group(1), p)
            self.assertIn("'", re.search(r"\[(.*?)\]/g", src).group(1), p)

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node")
    def test_esc_runtime(self):
        import json
        for p in PAGES:
            code = esc_src(p) + f"\nprocess.stdout.write(esc({json.dumps(EVIL)}) + '|' + esc(null) + '|' + esc(0));"
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=20)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout, WANT + "||0", p)

    def test_dashboard_tooltip_labels_escaped(self):
        src = (SERVER / "dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn("${PHASE_TH[ph]||ph}", src)          # page จาก agent ต้องผ่าน esc
        self.assertNotIn('<div class="th">${r.dataset.th}', src)
        self.assertIn('data-rows="${esc(rows)}"', src)


class DaysLimit(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        _env.ensure_read_user()
        self.c = TestClient(api.app)
        self.h = _env.basic()

    def test_days_out_of_range_400(self):
        for path in ("/api/jobs", "/api/dashboard", "/api/shots", "/api/inbox"):
            for d in (0, -1, 367, 1000000):
                self.assertEqual(self.c.get(f"{path}?days={d}", headers=self.h).status_code, 400, (path, d))
            self.assertEqual(self.c.get(f"{path}?days=366", headers=self.h).status_code, 200, path)

    def test_projects_clamped_and_report_day_limited(self):
        self.assertEqual(self.c.get("/api/projects?days=3650", headers=self.h).status_code, 200)
        old = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
        self.assertEqual(self.c.get(f"/api/report?day={old}", headers=self.h).status_code, 400)
        self.assertEqual(self.c.get("/api/report?day=bad", headers=self.h).status_code, 400)

    def test_non_json_ingest_400(self):
        h = {"Authorization": f"Bearer {auth.issue_token('grade-01', force=True)}", "Content-Type": "application/json"}
        self.assertEqual(self.c.post("/ingest", content=b"{oops", headers=h).status_code, 400)
        self.assertEqual(self.c.post("/ingest", content=b'"string"', headers=h).status_code, 400)


def series(graded):
    t0 = datetime.datetime(2026, 10, 14, 3, 0, tzinfo=datetime.timezone.utc)
    out = []
    for i, g in enumerate(graded):
        out.append({"schema": 3, "host": "grade-03", "stage": "color", "ts": (t0 + datetime.timedelta(minutes=i)).isoformat(),
                    "activity": {"idle_sec": 1, "active": True, "resolve_frontmost": True},
                    "source": {"ok": True, "project": "P", "timeline": {"name": "T", "video_clips": 10, "graded_clips": g}},
                    "job": {"job_id": "P", "title": "P"},
                    "progress": {"stage": "color", "pct": g * 10.0, "detail": {"graded_clips": g, "video_clips": 10}}})
    return out


class JumpThreshold(unittest.TestCase):
    def eff(self, r):
        return {"job_id": "P", "title": "P", "stage": "color", "source": "fallback"}

    def check(self, graded):
        recs = series(graded)
        r = rp.build_report(recs, "2026-10-14", 1, self.eff)["hosts"][0]
        rj = [p["to"] for p in r["progress"] if p.get("jump")]
        pjj = [h["graded_clips"] for h in pj.build_projects(series(graded), 1, self.eff)["projects"][0]["history"] if h["jump"]]
        return rj, pjj

    def test_exactly_threshold_is_not_a_jump_on_both_pages(self):
        self.assertEqual(self.check([0, rp.JUMP_CLIPS]), ([], []))           # เดิม /report ว่ากระโดด /projects ไม่
        self.assertEqual(self.check([0, rp.JUMP_CLIPS + 1]), ([40.0], [4]))


if __name__ == "__main__":
    unittest.main()

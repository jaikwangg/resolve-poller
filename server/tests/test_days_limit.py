"""?days= is bounded (ported from audit-fixes 66176b8, test_bugs.DaysLimit).

run:  cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_days_limit -v
"""
import datetime, os, pathlib, sys, tempfile, unittest

HERE = pathlib.Path(__file__).parent
TMP = tempfile.mkdtemp(prefix="et-days-")
import atexit, shutil                                  # noqa: E402
atexit.register(shutil.rmtree, TMP, True)
os.environ["EDITORTRACK_STORE"] = TMP
for k in [k for k in os.environ if k.startswith("ERP_")]:
    del os.environ[k]                                  # never call a real PMS from a test
sys.path.insert(0, str(HERE.parent))
from tests import authfix          # noqa: E402
authfix.install()

import dashboard_api as api        # noqa: E402


class DaysLimit(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.c = TestClient(api.app, raise_server_exceptions=False, headers=authfix.reader())

    def test_days_out_of_range_400(self):
        for u in ("/api/jobs", "/api/shots", "/api/dashboard", "/api/inbox", "/api/health"):
            for d in (0, -1, api.MAX_DAYS + 1, 10 ** 9):
                self.assertEqual(self.c.get(f"{u}?days={d}").status_code, 400, (u, d))
            self.assertEqual(self.c.get(f"{u}?days=1").status_code, 200, u)
            self.assertEqual(self.c.get(f"{u}?days={api.MAX_DAYS}").status_code, 200, u)

    def test_projects_clamped_and_report_day_limited(self):
        self.assertEqual(self.c.get("/api/projects?days=1000000000").status_code, 200)
        old = (datetime.date.today() - datetime.timedelta(days=api.MAX_DAYS + 5)).isoformat()
        self.assertEqual(self.c.get(f"/api/report?day={old}").status_code, 400)
        self.assertEqual(self.c.get("/api/report?day=0001-01-01").status_code, 400)
        self.assertEqual(self.c.get("/api/report").status_code, 200)


if __name__ == "__main__":
    unittest.main()

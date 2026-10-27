"""A PMS outage must not slow the server down (ported idea from audit-fixes 10a0e1f).

- erp._bookings: a failed fetch is remembered for ERP_FAIL_TTL; lookups in that window use the old rows
  without calling PMS again.
- /ingest runs the PMS step in the threadpool, so a slow PMS does not block the event loop.

run:  cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_erp_outage -v
"""
import datetime, os, pathlib, sys, tempfile, unittest

HERE = pathlib.Path(__file__).parent
for k in [k for k in os.environ if k.startswith("ERP_")]:
    del os.environ[k]                                  # never call a real PMS from a test
os.environ.setdefault("EDITORTRACK_STORE", tempfile.mkdtemp(prefix="et-erp-"))
sys.path.insert(0, str(HERE.parent))

import erp                         # noqa: E402


class NegativeCache(unittest.TestCase):
    def setUp(self):
        self._fetch, self._cache = erp._fetch, dict(erp._cache)
        self.calls = 0

    def tearDown(self):
        erp._fetch = self._fetch
        erp._cache.clear()
        erp._cache.update(self._cache)

    def test_failed_fetch_not_retried_within_fail_ttl(self):
        def down():
            self.calls += 1
            raise OSError("PMS down")
        erp._fetch = down
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=erp.CACHE_TTL + 5)
        erp._cache.update(at=old, rows=[{"id": "kept"}], failed_at=None)
        for _ in range(5):
            self.assertEqual(erp._bookings(), [{"id": "kept"}])     # stale rows kept
        self.assertEqual(self.calls, 1)
        # after FAIL_TTL the next lookup tries again, and a success clears the failure
        erp._cache["failed_at"] -= datetime.timedelta(seconds=erp.FAIL_TTL + 1)
        erp._fetch = lambda: [{"id": "new"}]
        self.assertEqual(erp._bookings(), [{"id": "new"}])
        self.assertIsNone(erp._cache["failed_at"])


class IngestOffLoop(unittest.TestCase):
    def test_ingest_uses_threadpool_for_pms(self):
        src = (HERE.parent / "dashboard_api.py").read_text(encoding="utf-8")
        self.assertIn("await run_in_threadpool(pms_sync.progress_on_ingest", src)


if __name__ == "__main__":
    unittest.main()

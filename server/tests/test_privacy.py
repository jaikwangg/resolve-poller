"""กลุ่ม 2 (server) — ไม่เก็บ user/ชื่อแอป, ที่เก็บนอก repo สิทธิ์ 700, retention"""
import datetime
import json
import os
import pathlib
import tempfile
import unittest

try:
    from . import _env
except ImportError:
    import _env

import auth                      # noqa: E402
import dashboard_api as api      # noqa: E402
import explain as ex             # noqa: E402
import store                     # noqa: E402

LEGACY = {"schema": 3, "ts": "2026-10-14T10:00:00+00:00", "host": "conform-01", "user": "somchai", "stage": "conform",
          "activity": {"idle_sec": 2.0, "active": True, "resolve_frontmost": False,
                       "frontmost_name": "Inbox — client-secret-project.eml", "frontmost_bundle": "com.apple.mail"}}


class Minimise(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self._store = api.STORE
        api.STORE = pathlib.Path(tempfile.mkdtemp())
        self.c = TestClient(api.app)

    def tearDown(self):
        api.STORE = self._store

    def test_ingest_drops_user_and_window_names_from_old_agents(self):
        tok = auth.issue_token("conform-01", force=True)
        r = self.c.post("/ingest", json=LEGACY, headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.status_code, 200)
        raw = "".join(p.read_text(encoding="utf-8") for p in api.STORE.glob("*.jsonl"))
        self.assertNotIn("somchai", raw)
        self.assertNotIn("client-secret-project", raw)
        self.assertNotIn("com.apple.mail", raw)
        rec = json.loads(raw.splitlines()[0])
        self.assertNotIn("user", rec)
        self.assertEqual(rec["activity"]["locked"], False)

    def test_locked_kept_as_boolean(self):
        r = store.minimise({"activity": {"frontmost_name": "loginwindow"}})
        self.assertEqual(r["activity"], {"locked": True})
        self.assertTrue(ex.is_locked({"locked": True}))
        self.assertEqual(ex.front_label({"locked": False, "resolve_frontmost": True}), "Resolve")
        self.assertEqual(ex.front_label({"locked": False, "resolve_frontmost": False}), "แอปอื่น")

    def test_explain_and_api_never_show_user_or_app_name(self):
        e = ex.explain(store.minimise(json.loads(json.dumps(LEGACY))))
        blob = json.dumps(e, ensure_ascii=False)
        self.assertNotIn("somchai", blob)
        self.assertNotIn("client-secret-project", blob)

    def test_scrub_rewrites_old_files(self):
        d = pathlib.Path(tempfile.mkdtemp())
        fp = d / "2026-10-14.jsonl"
        fp.write_text(json.dumps(LEGACY) + "\n" + "not json\n", encoding="utf-8")
        self.assertEqual(store.scrub_file(fp), 1)
        txt = fp.read_text(encoding="utf-8")
        self.assertNotIn("somchai", txt)
        self.assertIn("not json", txt)                 # บรรทัดเสียไม่หาย
        self.assertEqual(store.scrub_file(fp), 0)      # รอบสองไม่มีอะไรเปลี่ยน


class StoreDir(unittest.TestCase):
    def test_default_outside_repo(self):
        env = dict(os.environ)
        try:
            os.environ.pop("EDITORTRACK_STORE", None)
            os.environ.pop("EDITORTRACK_HOME", None)
            self.assertEqual(store.default_store(), pathlib.Path("/srv/editortrack/data"))
            os.environ["EDITORTRACK_STORE"] = "data"            # ของเดิมยังใช้ได้
            self.assertEqual(store.default_store(), pathlib.Path("data"))
        finally:
            os.environ.clear()
            os.environ.update(env)

    def test_ensure_dir_mode_700(self):
        d = pathlib.Path(tempfile.mkdtemp()) / "a" / "data"
        store.ensure_dir(d)
        self.assertEqual(d.stat().st_mode & 0o777, 0o700)
        os.chmod(d, 0o775)                                       # dir เดิมที่ group อ่านได้ → ลดเป็น 700
        store.ensure_dir(d)
        self.assertEqual(d.stat().st_mode & 0o777, 0o700)


class Retention(unittest.TestCase):
    def test_deletes_only_files_older_than_n_days(self):
        d = pathlib.Path(tempfile.mkdtemp())
        today = datetime.date(2026, 10, 22)
        for name in ("2026-04-24.jsonl", "2026-04-25.jsonl", "2026-10-21.jsonl", "notes.jsonl", "2026-01-01.txt"):
            (d / name).write_text("{}\n")
        gone = store.apply_retention(d, 180, today)              # cutoff = 2026-04-25
        self.assertEqual(gone, ["2026-04-24.jsonl"])
        self.assertEqual(sorted(p.name for p in d.iterdir()),
                         ["2026-01-01.txt", "2026-04-25.jsonl", "2026-10-21.jsonl", "notes.jsonl"])

    def test_zero_disables_and_default_is_180(self):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "2000-01-01.jsonl").write_text("{}\n")
        self.assertEqual(store.apply_retention(d, 0, datetime.date(2026, 10, 22)), [])
        env = dict(os.environ)
        try:
            os.environ.pop("EDITORTRACK_RETENTION_DAYS", None)
            self.assertEqual(store.retention_days(), 180)
        finally:
            os.environ.clear()
            os.environ.update(env)


if __name__ == "__main__":
    unittest.main()

"""กลุ่ม 1 — token ต่อเครื่อง, login หน้าอ่าน, จำกัดขนาด body, bind 127.0.0.1"""
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
import run                       # noqa: E402
import manage_tokens             # noqa: E402

REC = {"schema": 3, "ts": "2026-10-14T10:00:00+00:00", "host": "grade-03", "stage": "color",
       "activity": {"idle_sec": 3.0, "active": True, "resolve_frontmost": True}}


class _Client(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self._store = api.STORE
        api.STORE = pathlib.Path(tempfile.mkdtemp())
        self.c = TestClient(api.app)
        self._env = dict(os.environ)

    def tearDown(self):
        api.STORE = self._store
        os.environ.clear()
        os.environ.update(self._env)


class AgentTokens(_Client):
    def test_per_host_token_accepts_own_host_only(self):
        tok = auth.issue_token("grade-03", force=True)
        h = {"Authorization": f"Bearer {tok}"}
        self.assertEqual(self.c.post("/ingest", json=REC, headers=h).status_code, 200)
        # token ของ grade-03 ส่งแทน grade-04 ไม่ได้ (ปลอม host ไม่ได้)
        r = self.c.post("/ingest", json={**REC, "host": "grade-04"}, headers=h)
        self.assertEqual(r.status_code, 401)
        self.assertIn("bad_token", r.text)            # unknown_host ตอบเหมือน bad_token (ไม่บอกว่า host ไหนมี token)
        self.assertNotIn("unknown_host", r.text)
        auth.issue_token("grade-04", force=True)
        self.assertIn("bad_token", self.c.post("/ingest", json={**REC, "host": "grade-04"}, headers=h).text)

    def test_store_keeps_only_hash(self):
        tok = auth.issue_token("grade-05", force=True)
        raw = auth.tokens_file().read_text(encoding="utf-8")
        self.assertNotIn(tok, raw)
        self.assertIn(auth.sha256(tok), raw)
        self.assertEqual(oct(auth.tokens_file().stat().st_mode & 0o777), "0o600")

    def test_revoke_takes_effect_without_restart(self):
        tok = auth.issue_token("grade-06", force=True)
        h = {"Authorization": f"Bearer {tok}"}
        rec = {**REC, "host": "grade-06"}
        self.assertEqual(self.c.post("/ingest", json=rec, headers=h).status_code, 200)
        self.assertTrue(auth.revoke_token("grade-06"))
        self.assertEqual(self.c.post("/ingest", json=rec, headers=h).status_code, 401)

    def test_issue_refuses_overwrite_without_force(self):
        auth.issue_token("conform-02", force=True)
        with self.assertRaises(ValueError):
            auth.issue_token("conform-02")

    def test_leaked_default_token_always_rejected(self):
        os.environ["EDITORTRACK_TOKEN"] = "kpp-et-test"
        os.environ["EDITORTRACK_ALLOW_SHARED_TOKEN"] = "1"
        r = self.c.post("/ingest", json={**REC, "host": "new-host"}, headers={"Authorization": "Bearer kpp-et-test"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("leaked_token", r.text)

    def test_shared_token_only_with_explicit_opt_in_and_not_for_hosts_with_own_token(self):
        os.environ["EDITORTRACK_TOKEN"] = "legacy-shared-123"
        h = {"Authorization": "Bearer legacy-shared-123"}
        rec = {**REC, "host": "legacy-host"}
        self.assertEqual(self.c.post("/ingest", json=rec, headers=h).status_code, 401)   # ไม่ opt-in
        os.environ["EDITORTRACK_ALLOW_SHARED_TOKEN"] = "1"
        self.assertEqual(self.c.post("/ingest", json=rec, headers=h).status_code, 200)
        auth.issue_token("legacy-host", force=True)                                     # มี token ของตัวเองแล้ว
        self.assertEqual(self.c.post("/ingest", json=rec, headers=h).status_code, 401)

    def test_manage_tokens_cli(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(manage_tokens.main(["issue", "cli-host", "--force"]), 0)
            manage_tokens.main(["list"])
        self.assertIn("cli-host", out.getvalue())
        self.assertIn("cli-host", auth.host_tokens())


class BodyLimit(_Client):
    def test_large_body_413_and_non_json_400(self):
        tok = auth.issue_token("grade-03", force=True)
        h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
        old = api.MAX_BODY
        api.MAX_BODY = 2000
        try:
            big = {**REC, "pad": "x" * 5000}
            self.assertEqual(self.c.post("/ingest", content=json.dumps(big), headers=h).status_code, 413)
        finally:
            api.MAX_BODY = old
        self.assertEqual(self.c.post("/ingest", content=b"not json{", headers=h).status_code, 400)
        self.assertEqual(self.c.post("/ingest", content=b"[1,2]", headers=h).status_code, 400)


class ReadAuth(_Client):
    PATHS = ["/", "/inbox", "/report", "/projects", "/deck", "/api/jobs", "/api/dashboard", "/api/shots",
             "/api/inbox", "/api/report", "/api/projects", "/api/pms"]

    def test_every_page_and_api_needs_login(self):
        for p in self.PATHS:
            r = self.c.get(p)
            self.assertEqual(r.status_code, 401, p)
            self.assertIn("Basic", r.headers.get("www-authenticate", ""), p)

    def test_no_user_configured_means_closed(self):
        os.environ["EDITORTRACK_READ_USERS_FILE"] = str(_env.TMP / "nobody.json")
        self.assertEqual(self.c.get("/api/jobs", headers=_env.basic()).status_code, 401)

    def test_login_works_and_wrong_password_fails(self):
        _env.ensure_read_user()
        self.assertEqual(self.c.get("/api/jobs", headers=_env.basic()).status_code, 200)
        self.assertEqual(self.c.get("/", headers=_env.basic()).status_code, 200)
        self.assertEqual(self.c.get("/api/jobs", headers=_env.basic(pw="wrong-password")).status_code, 401)

    def test_removed_user_loses_access_immediately(self):
        auth.set_read_user("temp-user", "temp-password-1")
        h = _env.basic("temp-user", "temp-password-1")
        self.assertEqual(self.c.get("/api/jobs", headers=h).status_code, 200)
        auth.remove_read_user("temp-user")
        self.assertEqual(self.c.get("/api/jobs", headers=h).status_code, 401)

    def test_mode_none_opens_reads_but_not_ingest(self):
        from fastapi.testclient import TestClient
        os.environ["EDITORTRACK_READ_AUTH"] = "none"
        lo = TestClient(api.app, base_url="http://127.0.0.1:8000")     # connection เข้าทาง loopback (proxy เครื่องเดียวกัน)
        self.assertEqual(lo.get("/api/jobs").status_code, 200)
        self.assertEqual(lo.post("/ingest", json=REC).status_code, 401)

    def test_mode_none_refused_on_lan_interface(self):
        """uvicorn ตรง --host 0.0.0.0 ข้าม guard ของ run.py → connection จาก LAN ต้องยังถูกปฏิเสธ"""
        from fastapi.testclient import TestClient
        os.environ["EDITORTRACK_READ_AUTH"] = "none"
        for base in ("http://192.168.140.126:8000", "http://10.0.0.5", "http://editortrack.lan"):
            self.assertEqual(TestClient(api.app, base_url=base).get("/api/jobs").status_code, 401, base)
        self.assertEqual(TestClient(api.app, base_url="http://[::1]:8000").get("/api/jobs").status_code, 200)

    def test_local_is_loopback(self):
        for a, want in [(("127.0.0.1", 8000), True), (("::1", 8000), True), (("/run/et.sock", None), True),
                        (("0.0.0.0", 8000), False), (("192.168.1.5", 8000), False), (None, False), (("testserver", 80), False)]:
            self.assertEqual(auth.local_is_loopback(a), want, a)

    def test_healthz_open(self):
        self.assertEqual(self.c.get("/healthz").json(), {"ok": True})

    def test_password_hash_roundtrip(self):
        h = auth.hash_password("abc", iterations=1000)
        self.assertTrue(auth.verify_password("abc", h))
        self.assertFalse(auth.verify_password("abd", h))


class StartupChecks(unittest.TestCase):
    """guard ไม่ได้อยู่แค่ใน run.py: `uvicorn dashboard_api:app` ตรงก็ต้องเตือน และยังปฏิเสธการอ่าน"""
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_no_read_users_is_loud_and_reads_still_refused(self):
        from fastapi.testclient import TestClient
        os.environ["EDITORTRACK_READ_USERS_FILE"] = str(pathlib.Path(tempfile.mkdtemp()) / "none.json")
        os.environ.pop(api.VIA_RUN_ENV, None)
        with self.assertLogs("editortrack", level="ERROR") as cm:
            msgs = api.startup_checks()
        self.assertTrue(any("read user" in m for m in msgs))
        self.assertTrue(any("run.py" in m for m in msgs))                # unit ยังเรียก uvicorn ตรง
        self.assertTrue(all("!!!" in m for m in cm.output))
        with TestClient(api.app) as c:                                   # startup event รันจริง (ไม่ล้ม)
            self.assertEqual(c.get("/api/jobs").status_code, 401)
            self.assertEqual(c.get("/healthz").status_code, 200)

    def test_quiet_when_configured_via_run_py(self):
        _env.ensure_read_user()
        os.environ[api.VIA_RUN_ENV] = "1"
        self.assertEqual(api.startup_checks(), [])

    def test_run_py_marks_launch(self):
        import uvicorn
        called = {}
        old = uvicorn.run
        uvicorn.run = lambda *a, **k: called.update(k)
        try:
            os.environ.pop(api.VIA_RUN_ENV, None)
            os.environ.pop("EDITORTRACK_BIND", None)
            self.assertEqual(run.main([]), 0)
        finally:
            uvicorn.run = old
        self.assertEqual(os.environ.get(api.VIA_RUN_ENV), "1")
        self.assertEqual(called["host"], "127.0.0.1")


class Bind(unittest.TestCase):
    def test_default_is_loopback_and_lan_is_explicit(self):
        self.assertEqual(run.bind_host([], {}), "127.0.0.1")
        self.assertEqual(run.bind_host(["--lan"], {}), "0.0.0.0")
        self.assertEqual(run.bind_host([], {"EDITORTRACK_BIND": "192.168.1.5"}), "192.168.1.5")

    def test_refuses_lan_without_read_auth(self):
        env = dict(os.environ)
        try:
            os.environ["EDITORTRACK_READ_AUTH"] = "none"
            os.environ["EDITORTRACK_BIND"] = "0.0.0.0"
            self.assertEqual(run.main([]), 2)
        finally:
            os.environ.clear()
            os.environ.update(env)


class MakeEnv(unittest.TestCase):
    def _run(self, token):
        import shutil
        import subprocess
        d = pathlib.Path(tempfile.mkdtemp())
        shutil.copy(pathlib.Path(__file__).parents[2] / "agent" / "deploy" / "make-env.sh", d / "make-env.sh")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(_env.FAKE_HOME),
               "STAGE": "color", "DETECT_GRADE": "1", "SERVER": "http://127.0.0.1:8000/ingest"}
        if token is not None:
            env["TOKEN"] = token
        p = subprocess.run(["sh", str(d / "make-env.sh")], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=20)
        return p.returncode, d / "env.sh"

    def test_make_env_has_no_default_token(self):
        rc, out = self._run(None)                 # ไม่มี tty + ไม่ส่ง TOKEN → ต้องล้ม ไม่ใช้ค่า default
        self.assertNotEqual(rc, 0)
        self.assertFalse(out.exists())
        self.assertNotEqual(self._run("kpp-et-test")[0], 0)
        rc, out = self._run("et_abc123")
        self.assertEqual(rc, 0)
        self.assertIn("EDITORTRACK_TOKEN=et_abc123", out.read_text(encoding="utf-8"))
        self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()

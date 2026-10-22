"""deploy/make-env.sh — env.sh มี token ต้องเป็น 600 เสมอ แม้ไฟล์มีอยู่แล้ว (เดิม umask มีผลแค่ไฟล์ใหม่)"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

try:
    from . import _env  # noqa: F401
except ImportError:
    import _env  # noqa: F401

DEPLOY = pathlib.Path(__file__).parents[1] / "deploy"


class MakeEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="editortrack-makeenv-"))
        shutil.copy(DEPLOY / "make-env.sh", self.tmp / "make-env.sh")
        self.out = self.tmp / "env.sh"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_it(self, token="et_abc"):
        env = {**os.environ, "STAGE": "color", "DETECT_GRADE": "1",
               "SERVER": "http://127.0.0.1:9/ingest", "TOKEN": token}
        return subprocess.run(["sh", str(self.tmp / "make-env.sh")], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)

    def mode(self):
        return oct(self.out.stat().st_mode & 0o777)

    def test_new_file_is_600(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.mode(), "0o600")
        self.assertIn("EDITORTRACK_TOKEN=et_abc", self.out.read_text())

    def test_existing_world_readable_file_is_tightened(self):
        self.out.write_text("# old\n")
        os.chmod(self.out, 0o644)
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.mode(), "0o600")

    def test_token_from_env_is_masked_in_output(self):
        r = self.run_it(token="et_secret_value_123")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("et_secret_value_123", r.stdout + r.stderr)
        self.assertIn("TOKEN = ***(19 ตัว)", r.stdout)
        self.assertIn("SERVER = http://127.0.0.1:9/ingest", r.stdout)       # ค่าอื่นยังแสดงตามเดิม
        self.assertIn("EDITORTRACK_TOKEN=et_secret_value_123", self.out.read_text())

    def test_leaked_token_refused_and_file_untouched(self):
        r = self.run_it(token="kpp-et-test")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()

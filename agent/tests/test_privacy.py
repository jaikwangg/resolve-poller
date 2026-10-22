"""กลุ่ม 2 (agent) — ไม่ส่ง user / ชื่อแอป-หน้าต่าง, Resolve frontmost ตัดสินจาก identity ตรงตัว, shots ปิดเป็น default"""
import os
import unittest

try:
    from . import _env
except ImportError:
    import _env

import activity_sampler as samp   # noqa: E402
import runner                     # noqa: E402


class ResolveIdentity(unittest.TestCase):
    def test_bundle_exact(self):
        self.assertTrue(samp.is_resolve_bundle("com.blackmagic-design.DaVinciResolve"))
        self.assertFalse(samp.is_resolve_bundle("com.google.Chrome"))
        self.assertFalse(samp.is_resolve_bundle("com.blackmagic-design.DaVinciResolve.helper"))

    def test_process_exact_not_substring(self):
        self.assertTrue(samp.is_resolve_proc("/opt/resolve/bin/resolve"))
        self.assertTrue(samp.is_resolve_proc(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"))
        self.assertTrue(samp.is_resolve_proc("resolve"))                     # /proc/pid/comm
        for x in ("firefox", "/usr/lib/firefox/firefox", "how to resolve merge conflicts - Google Chrome",
                  "resolver", "chrome.exe", ""):
            self.assertFalse(samp.is_resolve_proc(x), x)

    def test_xprop_parsers(self):
        self.assertEqual(samp.parse_xprop_pid("_NET_WM_PID(CARDINAL) = 4242"), 4242)
        self.assertEqual(samp.parse_xprop_pid("4242"), 4242)
        self.assertIsNone(samp.parse_xprop_pid("_NET_WM_PID:  not found."))
        self.assertEqual(samp.parse_wm_class('WM_CLASS(STRING) = "resolve", "Resolve"'), ["resolve", "resolve"])

    def test_proc_exe_reads_comm_when_exe_unreadable(self):
        d = _env.TMP / "proc" / "77"
        d.mkdir(parents=True, exist_ok=True)
        (d / "comm").write_text("resolve\n")
        self.assertEqual(samp.proc_exe(77, proc=str(_env.TMP / "proc")), "resolve")

    def test_sample_has_no_names(self):
        s = samp.sample()                       # เครื่องเทสไม่มี X11/Quartz ก็ได้ → unavailable แต่รูปเดียวกัน
        for k in ("frontmost_name", "frontmost_bundle", "window", "title"):
            self.assertNotIn(k, s)
        self.assertIsInstance(s["resolve_frontmost"], bool)


class Record(unittest.TestCase):
    def test_no_user_and_no_window_title(self):
        old_c, old_s = runner.collect, runner.sample
        runner.collect = lambda stage: {"ok": False, "reason": "resolve_not_running"}
        runner.sample = lambda: {"idle_sec": 3.0, "resolve_frontmost": True, "locked": False, "platform": "linux"}
        try:
            rec = runner.build_record()
        finally:
            runner.collect, runner.sample = old_c, old_s
        self.assertNotIn("user", rec)
        self.assertNotIn("frontmost_name", rec["activity"])
        self.assertTrue(rec["activity"]["active"])
        self.assertEqual(rec["schema"], 3)

    def test_shots_off_by_default(self):
        import resolve_poller
        self.assertFalse(resolve_poller.EXTRACT_SHOTS)


if __name__ == "__main__":
    unittest.main()

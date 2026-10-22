"""runner: กันรันซ้อน (กลุ่ม 3) · offline queue (กลุ่ม 5)"""
import unittest

try:
    from . import _env  # noqa: F401
except ImportError:
    import _env  # noqa: F401

import runner  # noqa: E402


class Lock(unittest.TestCase):
    def test_second_run_is_skipped_while_first_holds_lock(self):
        a = runner.run_lock()
        self.assertTrue(a)
        try:
            self.assertIsNone(runner.run_lock())
        finally:
            a.close()
        b = runner.run_lock()
        self.assertTrue(b)
        b.close()


if __name__ == "__main__":
    unittest.main()

"""เทส agent แบบ offline: HOME ปลอม (queue/cache/registry ไม่แตะ ~/.editortrack จริง) · ไม่ต่อ Resolve/server"""
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="editortrack-agent-test-"))
if not os.environ.get("_EDITORTRACK_AGENT_TEST_ENV"):
    os.environ["_EDITORTRACK_AGENT_TEST_ENV"] = "1"
    os.environ["HOME"] = str(TMP)
    for k in [k for k in os.environ if k.startswith("EDITORTRACK_")]:
        del os.environ[k]
    os.environ["EDITORTRACK_SERVER"] = "http://127.0.0.1:9/ingest"    # port 9 (discard) — ไม่มีอะไรรับ
    os.environ["EDITORTRACK_TOKEN"] = "et_test"
    sys.path.insert(0, str(HERE.parent))

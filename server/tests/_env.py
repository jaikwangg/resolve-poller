"""ตั้ง env ให้เทสรันแบบ offline ก่อน import โมดูล server — import ไฟล์นี้ก่อนเสมอในทุกเทส

- HOME ปลอม (ไม่อ่าน ~/.editortrack/room_map.json หรือไฟล์จริงของเครื่อง)
- EDITORTRACK_HOME / STORE / TOKENS / READ_USERS ชี้ไป temp dir (ไม่แตะ /srv/editortrack หรือ server/data)
- ลบ ERP_* ทั้งหมด (ไม่ยิง PMS/ERP จริง)
"""
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixtures" / "replay"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="editortrack-test-"))
FAKE_HOME = TMP / "home"
FAKE_HOME.mkdir(exist_ok=True)

if not os.environ.get("_EDITORTRACK_TEST_ENV"):
    os.environ["_EDITORTRACK_TEST_ENV"] = "1"
    os.environ["HOME"] = str(FAKE_HOME)
    os.environ["TZ"] = "Asia/Bangkok"             # process TZ — โค้ดใช้ EDITORTRACK_TZ อยู่แล้ว แต่กันพลาด
    time.tzset()
    os.environ["EDITORTRACK_TZ"] = "Asia/Bangkok"
    os.environ["EDITORTRACK_HOME"] = str(TMP / "srv")
    import shutil
    shutil.copytree(FIX, TMP / "store")           # สำเนา fixture (server chmod 700 ที่เก็บ — ไม่แตะ dir ใน repo)
    os.environ["EDITORTRACK_STORE"] = str(TMP / "store")
    os.environ["EDITORTRACK_TOKENS_FILE"] = str(TMP / "srv" / "agent_tokens.json")
    os.environ["EDITORTRACK_READ_USERS_FILE"] = str(TMP / "srv" / "read_users.json")
    os.environ["EDITORTRACK_RETENTION_DAYS"] = "0"   # ห้ามลบ fixture (0 = ปิด retention)
    for k in ("EDITORTRACK_TOKEN", "EDITORTRACK_ALLOW_SHARED_TOKEN", "EDITORTRACK_READ_AUTH",
              "EDITORTRACK_INTERVAL_MIN", "ERP_ROOM_MAP_FILE"):
        os.environ.pop(k, None)
    for k in [k for k in os.environ if k.startswith("ERP_") or k.startswith("PMS_")]:
        del os.environ[k]
    os.environ["ERP_ROOM_MAP_FILE"] = str(FAKE_HOME / "no-room-map.json")
    sys.path.insert(0, str(HERE.parent))

READ_USER, READ_PASS = "tester", "test-password-123"


def basic(user=READ_USER, pw=READ_PASS):
    import base64
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def ensure_read_user():
    import auth
    if READ_USER not in auth.read_users():
        auth.set_read_user(READ_USER, READ_PASS)

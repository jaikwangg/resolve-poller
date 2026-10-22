#!/usr/bin/env python3
"""auth — ใครส่ง record ได้ (/ingest) และใครอ่านหน้า/ API ได้

1) agent → POST /ingest: token ต่อเครื่อง (per-host)
   - server เก็บแค่ SHA-256 ของ token ต่อ host ในไฟล์ EDITORTRACK_TOKENS_FILE
     (default /srv/editortrack/agent_tokens.json, สิทธิ์ 600) — ออก/ถอน token ด้วย `python manage_tokens.py`
   - token ผูกกับ host: record ที่ host ไม่ตรงกับเจ้าของ token ถูกปฏิเสธ · host ที่ไม่มีในไฟล์ถูกปฏิเสธ
   - token รวมแบบเดิม (EDITORTRACK_TOKEN) ใช้ได้เฉพาะเมื่อตั้ง EDITORTRACK_ALLOW_SHARED_TOKEN=1
     และเฉพาะ host ที่ยังไม่มี token ของตัวเอง (ช่วงย้ายเครื่องทีละห้อง) · `kpp-et-test` ถูกปฏิเสธเสมอ (เคยอยู่ใน repo)
2) คนอ่าน → ทุกหน้า + /api/*: HTTP Basic auth (EDITORTRACK_READ_AUTH=basic, default)
   - user/รหัสผ่าน (PBKDF2) ในไฟล์ EDITORTRACK_READ_USERS_FILE (default /srv/editortrack/read_users.json)
   - EDITORTRACK_READ_AUTH=none = ปิด (ใช้เมื่อ reverse proxy เช่น Caddy ทำ auth ให้แล้ว และ bind 127.0.0.1 เท่านั้น)
   - ยังไม่ได้สร้าง user = อ่านไม่ได้เลย (ปิดไว้ก่อน ไม่เปิดโล่ง)
"""
import base64
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import tempfile
import time

LEAKED_TOKENS = {"kpp-et-test"}       # เคยเป็น default ใน agent/deploy/make-env.sh (repo public) — ห้ามใช้อีก
PBKDF2_ITER = 200_000
_VERIFIED_TTL = 300                    # วินาทีที่จำ header ที่ตรวจผ่านแล้ว (PBKDF2 ช้าโดยตั้งใจ — ไม่ทำซ้ำทุก request)
_verified = {}                         # sha256(header) -> หมดอายุ (epoch)
_file_cache = {}                       # path -> (mtime, data)


def home():
    return pathlib.Path(os.environ.get("EDITORTRACK_HOME", "/srv/editortrack"))


def tokens_file():
    return pathlib.Path(os.environ.get("EDITORTRACK_TOKENS_FILE") or home() / "agent_tokens.json")


def read_users_file():
    return pathlib.Path(os.environ.get("EDITORTRACK_READ_USERS_FILE") or home() / "read_users.json")


def read_auth_mode():
    return (os.environ.get("EDITORTRACK_READ_AUTH") or "basic").strip().lower()


def sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _load(path):
    """อ่าน JSON แบบ cache ตาม mtime — แก้ไฟล์ (ออก/ถอน token) แล้วมีผลทันทีไม่ต้อง restart"""
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _file_cache.get(str(path))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    _file_cache[str(path)] = (mt, data)
    return data


def save_json(path, data):
    """เขียนแบบ atomic (tmp + rename) สิทธิ์ 600 · โฟลเดอร์ 700"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _file_cache.pop(str(path), None)


def norm_host(h):
    """รูปเดียวกับที่ /ingest เก็บ (validate.norm_host) — ตัดช่องว่าง + ตัวพิมพ์เล็ก"""
    return h.strip().lower() if isinstance(h, str) else h


def host_tokens():
    """host -> sha256 ของ token (host เป็นตัวเล็ก — ไฟล์เก่าที่ออกด้วยตัวใหญ่ก็ยังใช้ได้)"""
    hosts = _load(tokens_file()).get("hosts") or {}
    return {norm_host(h): (v.get("sha256") if isinstance(v, dict) else v) for h, v in hosts.items() if v}


# เหตุผลที่ตอบกลับ agent — unknown_host กับ bad_token รวมเป็นอันเดียว (ไม่บอกว่า host ไหนมี token แล้ว)
_PUBLIC_REASON = {"unknown_host": "bad_token"}


def public_reason(why):
    return _PUBLIC_REASON.get(why, why)


def _bearer(authorization):
    a = authorization or ""
    return a[7:].strip() if a[:7].lower() == "bearer " else ""


def check_agent(host, authorization):
    """→ (ok, เหตุผล) · เหตุผล: ok / shared / no_token / leaked_token / unknown_host / bad_token"""
    tok = _bearer(authorization)
    if not tok:
        return False, "no_token"
    if tok in LEAKED_TOKENS:
        return False, "leaked_token"
    known = host_tokens()
    host = norm_host(host)
    if host in known:
        return (True, "ok") if hmac.compare_digest(sha256(tok), str(known[host])) else (False, "bad_token")
    shared = os.environ.get("EDITORTRACK_TOKEN", "")
    if (os.environ.get("EDITORTRACK_ALLOW_SHARED_TOKEN", "").strip().lower() in ("1", "true", "yes", "on")
            and shared and shared not in LEAKED_TOKENS and hmac.compare_digest(tok, shared)):
        return True, "shared"
    return False, "unknown_host"


def issue_token(host, force=False):
    """ออก token ใหม่ให้ host → คืน token (แสดงครั้งเดียว — server เก็บแค่ hash)"""
    host = norm_host(host)
    path = tokens_file()
    data = _load(path) or {}
    hosts = dict(data.get("hosts") or {})
    if host in hosts and not force:
        raise ValueError(f"{host} มี token อยู่แล้ว — ใช้ --force เพื่อออกใหม่ (token เดิมใช้ไม่ได้ทันที)")
    tok = "et_" + secrets.token_urlsafe(32)
    hosts[host] = {"sha256": sha256(tok), "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    save_json(path, {**data, "hosts": hosts})
    return tok


def revoke_token(host):
    host = norm_host(host)
    path = tokens_file()
    data = _load(path) or {}
    hosts = dict(data.get("hosts") or {})
    if hosts.pop(host, None) is None:
        return False
    save_json(path, {**data, "hosts": hosts})
    return True


# ---------------- read auth (Basic) ----------------
def hash_password(pw, salt=None, iterations=PBKDF2_ITER):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(pw, stored):
    try:
        algo, it, salt, want = str(stored).split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def read_users():
    return _load(read_users_file()).get("users") or {}


def set_read_user(name, pw):
    if not name or ":" in name:
        raise ValueError("ชื่อ user ห้ามว่างและห้ามมี ':'")
    path = read_users_file()
    data = _load(path) or {}
    users = dict(data.get("users") or {})
    users[name] = hash_password(pw)
    save_json(path, {**data, "users": users})


def remove_read_user(name):
    path = read_users_file()
    data = _load(path) or {}
    users = dict(data.get("users") or {})
    if users.pop(name, None) is None:
        return False
    save_json(path, {**data, "users": users})
    return True


def check_read(authorization):
    """→ True ถ้าอ่านได้ · mode none = ทุกคนอ่านได้ (ต้องมี proxy ทำ auth ให้)"""
    if read_auth_mode() == "none":
        return True
    a = authorization or ""
    if a[:6].lower() != "basic ":
        return False
    try:                                 # ผูก cache กับ mtime ของไฟล์ user → ลบ/เปลี่ยนรหัสแล้วมีผลทันที
        mt = read_users_file().stat().st_mtime_ns
    except OSError:
        return False
    key = sha256(f"{mt}|{a}")
    now = time.time()
    if _verified.get(key, 0) > now:
        return True
    try:
        name, _, pw = base64.b64decode(a[6:].strip()).decode("utf-8").partition(":")
    except Exception:
        return False
    stored = read_users().get(name)
    if stored and verify_password(pw, stored):
        if len(_verified) > 1000:
            _verified.clear()
        _verified[key] = now + _VERIFIED_TTL
        return True
    return False

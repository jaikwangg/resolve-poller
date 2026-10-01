#!/usr/bin/env python3
"""
erp.py — ERP connector (server-side) สำหรับ KPP ERP: login เอา token เอง → GET /api/schedule
→ join room + time → job (zero-touch; worker/PM ไม่ต้องทำอะไร)

- ทำงานที่ SERVER เท่านั้น (ERP อยู่ใน LAN + creds อยู่ที่นี่)
- token: POST /api/auth/login → data.access_token (อายุ ~8 ชม, data.expires_at) → cache + refresh อัตโนมัติ + retry เมื่อ 401
- ดึง booking ช่วง lookback (default 8 วัน) ครั้งเดียว → cache (TTL) → match ในหน่วยความจำ
- role = "ผสม": จาก activity ใน booking ก่อน → fallback role ของเครื่อง (caller ทำ)
- mock mode: ENV `ERP_MOCK` = path json (รูปตาม response จริง) สำหรับเทส/offline

ENV (ตั้งค่าจริงที่ server — อย่า commit ลง repo):
  ERP_BASE_URL=http://<erp-host>:8010          # ตั้งค่าจริงใน env เท่านั้น — อย่า commit
  ERP_LOGIN_PATH=/api/auth/login   ERP_USERNAME=<user>   ERP_PASSWORD=<pass>
  ERP_BOOKINGS_PATH=/api/schedule?start_dt={start}&end_dt={end}
  ERP_RECORDS_PATH=data.bookings
  ERP_ROOM_MAP={"grade-03":"RM-03","conform-01":"RM-01",...}   (host↔resource_code — IT กรอก)
  ERP_TOKEN=<access_token>   # ทางเลือก: ใส่ token ตรง ๆ สำหรับเทสเร็ว (หมดใน 8 ชม)
"""
import os, json, datetime, urllib.request, urllib.error

ERP_BASE = os.environ.get("ERP_BASE_URL", "")
ERP_LOGIN_PATH = os.environ.get("ERP_LOGIN_PATH", "/api/auth/login")
ERP_USER = os.environ.get("ERP_USERNAME", "")
ERP_PASS = os.environ.get("ERP_PASSWORD", "")
ERP_BOOKINGS_PATH = os.environ.get("ERP_BOOKINGS_PATH", "/api/schedule?start_dt={start}&end_dt={end}")
ERP_RECORDS_PATH = os.environ.get("ERP_RECORDS_PATH", "data.bookings")
ERP_TOKEN_STATIC = os.environ.get("ERP_TOKEN", "")
ERP_MOCK = os.environ.get("ERP_MOCK")
CACHE_TTL = int(os.environ.get("ERP_CACHE_TTL", "300"))
LOOKBACK_DAYS = int(os.environ.get("ERP_LOOKBACK_DAYS", "8"))
STATUS_DENY = set(s.strip().upper() for s in os.environ.get("ERP_STATUS_DENY", "CANCELLED,CANCELED,DELETED,TENTATIVE").split(",") if s.strip())

F = {"start": os.environ.get("ERP_F_START", "start_dt"),
     "end":   os.environ.get("ERP_F_END", "end_dt"),
     "job":   os.environ.get("ERP_F_JOB", "project_no"),
     "title": os.environ.get("ERP_F_TITLE", "project_name"),
     "task":  os.environ.get("ERP_F_TASK", "activity")}

TASK_ROLE = {"grading": "colorist", "grade": "colorist", "color": "colorist", "colour": "colorist", "cc": "colorist",
             "conform": "conform", "conforming": "conform", "online": "conform", "onlining": "conform",
             "edit": "conform", "editorial": "conform"}

# activity (ERP) → stage (5 stage ของ pipeline)
STAGE_MAP = {"grading": "color", "grade": "color", "color": "color", "colour": "color", "cc": "color",
             "conform": "conform", "conforming": "conform", "online": "conform", "edit": "conform", "editorial": "conform",
             "data": "data", "data management": "data", "dit": "data", "ingest": "data", "data wrangle": "data",
             "subtitle": "subtitle", "subtitles": "subtitle", "caption": "subtitle", "captions": "subtitle",
             "master": "master", "mastering": "master", "deliverable": "master"}

_tok = {"token": None, "exp": None}
_cache = {"at": None, "rows": []}


def _dig(obj, dotted):
    for k in dotted.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
        if obj is None:
            return None
    return obj


def _parse_dt(s):
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def _post_json(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _login():
    if ERP_TOKEN_STATIC:
        return ERP_TOKEN_STATIC
    now = datetime.datetime.now(datetime.timezone.utc)
    if _tok["token"] and _tok["exp"] and now < _tok["exp"] - datetime.timedelta(minutes=5):
        return _tok["token"]
    resp = _post_json(f"{ERP_BASE}{ERP_LOGIN_PATH}", {"username": ERP_USER, "password": ERP_PASS})
    _tok["token"] = _dig(resp, "data.access_token")
    _tok["exp"] = _parse_dt(_dig(resp, "data.expires_at")) or (now + datetime.timedelta(hours=8))
    return _tok["token"]


def _extract_room(b):
    """room อยู่ใน resources[] → เลือก is_primary ก่อน, ไม่งั้น resource_type==ROOM"""
    res = b.get("resources") or []
    for r in res:
        if r.get("is_primary"):
            return r.get("resource_code")
    for r in res:
        if str(r.get("resource_type", "")).upper() == "ROOM":
            return r.get("resource_code")
    return None


def _fetch():
    if ERP_MOCK:
        with open(ERP_MOCK, encoding="utf-8") as f:
            data = json.load(f)
    elif ERP_BASE:
        now = datetime.datetime.now(datetime.timezone.utc)
        start = (now - datetime.timedelta(days=LOOKBACK_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        path = ERP_BOOKINGS_PATH.format(start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        end=end.strftime("%Y-%m-%dT%H:%M:%SZ"))
        url = f"{ERP_BASE}{path}"
        try:
            data = _get_json(url, _login())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):            # token หมด/ไม่ผ่าน → login ใหม่แล้วลองอีกครั้ง
                _tok["token"] = None
                data = _get_json(url, _login())
            else:
                raise
    else:
        return []
    rows = _dig(data, ERP_RECORDS_PATH) if ERP_RECORDS_PATH else None
    if rows is None:
        rows = data if isinstance(data, list) else []
    return rows


def _bookings():
    now = datetime.datetime.now(datetime.timezone.utc)
    age = (now - _cache["at"]).total_seconds() if _cache["at"] else 1e9
    if age > CACHE_TTL:
        try:
            _cache["rows"] = _fetch()
            _cache["at"] = now
        except Exception:
            pass          # ดึงใหม่ไม่ได้ → ใช้ cache เดิม (degrade gracefully)
    return _cache["rows"] or []


def _room_map():
    raw = os.environ.get("ERP_ROOM_MAP")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    try:
        with open(os.path.expanduser("~/.editortrack/room_map.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def lookup(host, ts):
    """หา booking ที่ห้อง (map จาก host) และเวลา ts อยู่ในช่วง → dict หรือ None."""
    t = _parse_dt(ts)
    if not t:
        return None
    room = _room_map().get(host, host)              # host → resource_code (default = host เอง)
    for b in _bookings():
        if str(b.get("status", "")).upper() in STATUS_DENY:
            continue
        if str(_extract_room(b)) != str(room):
            continue
        s, e = _parse_dt(b.get(F["start"])), _parse_dt(b.get(F["end"]))
        if s and e and s <= t <= e:
            task = str(b.get(F["task"], "")).lower()
            return {"job_id": b.get(F["job"]),
                    "title": b.get(F["title"]) or b.get(F["job"]),
                    "role": TASK_ROLE.get(task),          # compat เดิม
                    "stage": STAGE_MAP.get(task),         # 5-stage · None → caller fallback stage เครื่อง
                    "target_duration_sec": b.get("target_duration_sec"),
                    "status": b.get("status"), "source": "erp"}
    return None

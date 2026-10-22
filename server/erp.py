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
  ERP_TOKEN=<access_token>   # ทางเลือก: ใส่ token ตรง ๆ สำหรับเทสเร็ว (หมดใน 8 ชม) · PMS integration key (kpp_int_…) ใส่ที่นี่
  ERP_ACTIVITY_PATH=/api/integrations/editortrack/activity   # เปิดการส่งช่วงเวลาทำงาน (pms_sync.py)
  ERP_PROGRESS_PATH=/api/integrations/editortrack/progress   # เปิดการส่ง % (ส่งเมื่อค่าเปลี่ยน + observed_at)
  สัญญาฝั่ง PMS: ~/github/pms docs/integrations/editortrack.md
"""
import os, re, json, datetime, logging, threading, urllib.request, urllib.error

log = logging.getLogger("editortrack")

ERP_BASE = os.environ.get("ERP_BASE_URL", "")
ERP_LOGIN_PATH = os.environ.get("ERP_LOGIN_PATH", "/api/auth/login")
ERP_USER = os.environ.get("ERP_USERNAME", "")
ERP_PASS = os.environ.get("ERP_PASSWORD", "")
ERP_BOOKINGS_PATH = os.environ.get("ERP_BOOKINGS_PATH", "/api/schedule?start_dt={start}&end_dt={end}")
ERP_RECORDS_PATH = os.environ.get("ERP_RECORDS_PATH", "data.bookings")
ERP_TOKEN_STATIC = os.environ.get("ERP_TOKEN", "")
ERP_MOCK = os.environ.get("ERP_MOCK")
CACHE_TTL = int(os.environ.get("ERP_CACHE_TTL", "300"))
FAIL_TTL = int(os.environ.get("ERP_FAIL_TTL", "60"))         # ดึงไม่ได้ → ไม่ลองใหม่ภายในเวลานี้ (negative cache) ใช้ cache เดิม
TIMEOUT = float(os.environ.get("ERP_TIMEOUT", "10"))          # วินาทีต่อ request ไป ERP/PMS
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

# ---- push progress กลับ ERP (ตั้ง ERP_PROGRESS_PATH เพื่อเปิด; ว่าง=ไม่ push) — รอ spec จริงจาก ERP ----
ERP_PROGRESS_PATH = os.environ.get("ERP_PROGRESS_PATH", "")   # เช่น /api/progress
ERP_PROGRESS_METHOD = os.environ.get("ERP_PROGRESS_METHOD", "POST")
# ---- push เวลาทำงานเป็นช่วง (PMS: /api/integrations/editortrack/activity) — ว่าง=ไม่ push ----
ERP_ACTIVITY_PATH = os.environ.get("ERP_ACTIVITY_PATH", "")
ACTIVITY_BATCH = 2000          # PMS รับสูงสุดต่อ POST
PF = {"job":   os.environ.get("ERP_PF_JOB", "project_no"),
      "stage": os.environ.get("ERP_PF_STAGE", "activity"),
      "pct":   os.environ.get("ERP_PF_PCT", "progress_pct")}

_tok = {"token": None, "exp": None}
_cache = {"at": None, "rows": [], "fail_at": None, "error": None}
_fetch_lock = threading.Lock()          # ดึงทีละ 1 — request อื่นใช้ cache เดิมไม่ต้องรอ
_rm_cache = {"key": None, "map": {}}


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
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
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


def _refresh():
    """ดึง booking ใหม่ 1 ครั้ง (ถือ lock อยู่แล้ว) · ล้ม → จำเวลาที่ล้ม (negative cache) แล้วใช้ cache เดิม"""
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        _cache["rows"] = _fetch()
        _cache["at"], _cache["fail_at"], _cache["error"] = now, None, None
    except Exception as e:
        _cache["fail_at"], _cache["error"] = datetime.datetime.now(datetime.timezone.utc), f"{type(e).__name__}: {e}"
        log.warning("ERP fetch failed (ไม่ลองใหม่ %ss, ใช้ cache เดิม %d แถว): %s", FAIL_TTL, len(_cache["rows"] or []),
                    _cache["error"])


def _stale():
    now = datetime.datetime.now(datetime.timezone.utc)
    if _cache["fail_at"] and (now - _cache["fail_at"]).total_seconds() < FAIL_TTL:
        return False                     # เพิ่งล้ม → ยังไม่ลองใหม่
    return not _cache["at"] or (now - _cache["at"]).total_seconds() > CACHE_TTL


def _bookings(block=True):
    """booking จาก cache · หมดอายุ → ดึงใหม่ (ทีละ 1 thread; ตัวอื่นใช้ cache เดิมทันที)
    block=False → ไม่รอ ERP เลย: สั่งดึงใน background แล้วคืน cache ที่มี (ใช้ใน /ingest)"""
    if _stale():
        if block:
            if _fetch_lock.acquire(blocking=False):
                try:
                    if _stale():
                        _refresh()
                finally:
                    _fetch_lock.release()
        elif _fetch_lock.acquire(blocking=False):
            def bg():
                try:
                    if _stale():
                        _refresh()
                finally:
                    _fetch_lock.release()
            threading.Thread(target=bg, name="erp-refresh", daemon=True).start()
    return _cache["rows"] or []


def cache_status():
    iso = lambda d: d.isoformat() if d else None       # noqa: E731
    return {"fetched_at": iso(_cache["at"]), "rows": len(_cache["rows"] or []),
            "failed_at": iso(_cache["fail_at"]), "error": _cache["error"]}


def room_map_file():
    return os.environ.get("ERP_ROOM_MAP_FILE") or os.path.expanduser("~/.editortrack/room_map.json")


def _room_map():
    """host → resource_code · ERP_ROOM_MAP (JSON ใน env) ก่อน → ไฟล์ ERP_ROOM_MAP_FILE (default ~/.editortrack/room_map.json)
    cache ตามค่า env/mtime ของไฟล์ — ไม่อ่านไฟล์ซ้ำทุก lookup"""
    raw = os.environ.get("ERP_ROOM_MAP")
    path = room_map_file()
    try:
        mt = os.stat(path).st_mtime_ns
    except OSError:
        mt = None
    key = (raw, path, mt)
    if _rm_cache["key"] == key:
        return _rm_cache["map"]
    m = {}
    if raw:
        try:
            m = json.loads(raw)
        except Exception:
            m = {}
    if not m and mt is not None:
        try:
            with open(path, encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            m = {}
    _rm_cache["key"], _rm_cache["map"] = key, m if isinstance(m, dict) else {}
    return _rm_cache["map"]


def _norm(x):
    return re.sub(r"[^0-9a-z]+", "", str(x or "").lower())


def pick_booking(cands, project):
    """เลือก booking เดียวจากหลายตัวที่ห้อง+เวลาตรง (PMS 15 ต.ค.: ห้องรับหลายงานพร้อมกันได้)
    1 ตัว → ตัวนั้น · หลายตัว → ตัวที่ project_no หรือชื่อ project ตรงกับ project ที่เปิดใน Resolve
    → (booking, None) หรือ (None, เหตุผล)"""
    if not cands:
        return None, "no_booking"
    if len(cands) == 1:
        return cands[0], None
    p = _norm(project)
    if p:
        hit = [b for b in cands if _norm(b.get(F["job"])) and _norm(b.get(F["job"])) in p]
        if len(hit) != 1:
            hit = [b for b in cands if _norm(b.get(F["title"])) and
                   (_norm(b.get(F["title"])) in p or p in _norm(b.get(F["title"])))]
        if len(hit) == 1:
            return hit[0], None
    return None, "ambiguous_booking"


def lookup_detail(host, ts, project=None, block=True):
    """→ (dict ของ booking ที่ใช้ หรือ None, เหตุผลเมื่อไม่ได้: no_booking / ambiguous_booking / bad_ts)
    block=False = ใช้แค่ cache (ไม่รอ ERP)"""
    t = _parse_dt(ts)
    if not t:
        return None, "bad_ts"
    room = _room_map().get(host, host)              # host → resource_code (default = host เอง)
    cands = []
    for b in _bookings(block):
        if str(b.get("status", "")).upper() in STATUS_DENY:
            continue
        if str(_extract_room(b)) != str(room):
            continue
        s, e = _parse_dt(b.get(F["start"])), _parse_dt(b.get(F["end"]))
        if s and e and s <= t <= e:
            cands.append(b)
    b, why = pick_booking(cands, project)
    if b is None:
        return None, why
    task = str(b.get(F["task"], "")).lower()
    return {"job_id": b.get(F["job"]),
            "booking_id": b.get("id"), "resource_code": room,
            "title": b.get(F["title"]) or b.get(F["job"]),
            "role": TASK_ROLE.get(task),          # compat เดิม
            "stage": STAGE_MAP.get(task),         # 5-stage · None → caller fallback stage เครื่อง
            "target_duration_sec": b.get("target_duration_sec"),
            "status": b.get("status"), "source": "erp"}, None


def lookup(host, ts, project=None, block=True):
    """หา booking ที่ห้อง (map จาก host) และเวลา ts อยู่ในช่วง → dict หรือ None.
    project = ชื่อ project ที่เปิดใน Resolve ใช้เลือกเมื่อห้องมีหลาย booking พร้อมกัน"""
    return lookup_detail(host, ts, project, block)[0]


def _post_auth(url, payload, method="POST"):
    data = json.dumps(payload).encode()

    def _do(tok):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": f"Bearer {tok}",
                                              "Content-Type": "application/json", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    try:
        return _do(_login())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            _tok["token"] = None
            return _do(_login())
        raise


def push_progress(job_id, stage, pct, extra=None):
    """ส่ง progress กลับ ERP (best-effort). no-op ถ้ายังไม่ตั้ง ERP_PROGRESS_PATH.
    payload field ปรับผ่าน ERP_PF_* (รอ spec จริงจาก ERP)."""
    if not ERP_PROGRESS_PATH or not ERP_BASE or not job_id or pct is None:
        return False
    payload = {PF["job"]: job_id, PF["stage"]: stage, PF["pct"]: pct}
    if extra:
        payload.update(extra)
    try:
        _post_auth(f"{ERP_BASE}{ERP_PROGRESS_PATH}", payload, ERP_PROGRESS_METHOD)
        return True
    except Exception as e:
        log.warning("push_progress %s/%s failed: %s", job_id, stage, e)
        return False


def push_activity(records):
    """ส่งช่วงเวลาทำงาน (window) ให้ PMS เป็นชุด ≤2000 → {"accepted","duplicates","rejected":[{index,reason}]}
    index ใน rejected ปรับเป็นตำแหน่งใน records ทั้งก้อนแล้ว · None = ยังไม่ตั้งค่า (ไม่ได้ส่ง)
    ส่งซ้ำปลอดภัย: PMS นับ (key, host, window_start) เดิมเป็น duplicates"""
    if not ERP_ACTIVITY_PATH or not ERP_BASE or not records:
        return None
    out = {"accepted": 0, "duplicates": 0, "rejected": []}
    for i in range(0, len(records), ACTIVITY_BATCH):
        resp = _post_auth(f"{ERP_BASE}{ERP_ACTIVITY_PATH}", {"records": records[i:i + ACTIVITY_BATCH]})
        d = _dig(resp, "data") or {}
        out["accepted"] += d.get("accepted") or 0
        out["duplicates"] += d.get("duplicates") or 0
        out["rejected"] += [{**r, "index": r.get("index", 0) + i} for r in d.get("rejected") or []]
    return out

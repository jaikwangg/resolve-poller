#!/usr/bin/env python3
"""ส่งเวลาทำงานให้ PMS เป็นช่วงละ WINDOW_MIN นาที (POST /api/integrations/editortrack/activity)

record ที่ ingest มาทุกนาที → รวมต่อ host ต่อ window → active_seconds (นับเฉพาะ sample ที่ explain.is_active)
- ส่งเฉพาะ window ที่ปิดแล้ว (จบไปเกิน SETTLE_SEC) และมี active_seconds > 0
- ไม่ส่ง user/ชื่อคน (PMS ไม่รับ — PDPA)
- ทุกช่วงต้องมี booking_id (กติกา PMS 15 ต.ค.: ห้องรับหลายงานพร้อมกันได้ รหัสห้องอย่างเดียวถูกปฏิเสธ)
  ช่วงที่หา booking ไม่ได้ หรือห้องมีหลาย booking แล้วเลือกจาก project ใน Resolve ไม่ได้ → ไม่ส่ง นับไว้ใน status["unbound"]
  (เราไม่มี project_no ของ PMS สำหรับเวลานอก booking — ชื่อ project ใน Resolve ไม่ใช่ project_no)
- ส่งซ้ำปลอดภัย (PMS dedupe ด้วย host+window_start) → restart แล้วส่ง LOOKBACK ใหม่ได้ นับเป็น duplicates
ENV: PMS_WINDOW_MIN=10 · PMS_SYNC_EVERY_SEC=300 · PMS_LOOKBACK_HOURS=24 · (เปิดด้วย ERP_ACTIVITY_PATH ใน erp.py)
"""
import collections
import datetime
import os
import threading

import explain as _ex

WINDOW_MIN = int(os.environ.get("PMS_WINDOW_MIN", "10"))
EVERY_SEC = int(os.environ.get("PMS_SYNC_EVERY_SEC", "300"))
LOOKBACK_HOURS = int(os.environ.get("PMS_LOOKBACK_HOURS", "24"))
SETTLE_SEC = 120               # รอ record ที่มาช้า (offline queue) ก่อนปิด window

status = {"enabled": False, "last_run": None, "last_result": None, "last_error": None, "sent": 0, "unbound": {}}
_done = set()                  # (host, window_start) ที่ PMS ตอบแล้ว (accepted/duplicate/rejected) — ไม่ส่งซ้ำใน process นี้


def _floor(t):
    return t.replace(minute=t.minute - t.minute % WINDOW_MIN, second=0, microsecond=0)


def _iso(t):
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_windows(recs, interval_min, eff_job, room_map, now=None, lookup=None, unbound=None):
    """record → รายการ window ตามสัญญา PMS (ยังไม่กรองตัวที่ส่งแล้ว)
    lookup(host, ts, project) -> (booking dict | None, เหตุผล) · ช่วงที่ไม่ได้ booking ไม่อยู่ในผล นับลง unbound"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=LOOKBACK_HOURS)
    win = datetime.timedelta(minutes=WINDOW_MIN)
    groups = collections.defaultdict(list)
    for r in recs:
        t = _ex._dt(r.get("ts"))
        host = r.get("host")
        if not t or not host or t < cutoff:
            continue
        groups[(host, _floor(t.astimezone(datetime.timezone.utc)))].append((t, r))

    out = []
    for (host, ws), items in sorted(groups.items()):
        we = ws + win
        if (now - we).total_seconds() < SETTLE_SEC:
            continue
        active = [r for _, r in items if _ex.is_active(r)]
        secs = min(int(win.total_seconds()), len(active) * interval_min * 60)
        if secs <= 0:
            continue
        first = min(active, key=lambda r: r.get("ts", ""))
        stages = collections.Counter(eff_job(r).get("stage") for r in active)
        stage = stages.most_common(1)[0][0]
        rec = {"host": host, "window_start": _iso(ws), "window_end": _iso(we), "active_seconds": secs}
        if stage in ("data", "conform", "color", "subtitle", "master"):
            rec["stage"] = stage
        if room_map.get(host):
            rec["resource_code"] = room_map[host]
        projects = collections.Counter((r.get("source") or {}).get("project") for r in active)
        b, why = lookup(host, first.get("ts"), projects.most_common(1)[0][0]) if lookup else (None, "no_lookup")
        if not (b and b.get("booking_id")):
            if unbound is not None:
                unbound[why or "no_booking_id"] = unbound.get(why or "no_booking_id", 0) + 1
            continue
        rec["booking_id"] = str(b["booking_id"])
        out.append(rec)
    return out


def sync_once(load_recent, interval_min, eff_job, erp):
    """1 รอบ: สร้าง window → ส่งตัวที่ยังไม่เคยส่ง → จำผล"""
    status["enabled"] = bool(erp and erp.ERP_ACTIVITY_PATH and erp.ERP_BASE)
    if not status["enabled"]:
        return None
    status["last_run"] = datetime.datetime.now().astimezone().isoformat()
    try:
        days = LOOKBACK_HOURS // 24 + 2
        unbound = {}
        wins = build_windows(load_recent(days), interval_min, eff_job, erp._room_map(),
                             lookup=erp.lookup_detail, unbound=unbound)
        status["unbound"] = unbound            # ช่วงที่ไม่ส่ง (ใน LOOKBACK) แยกตามเหตุผล
        todo = [w for w in wins if (w["host"], w["window_start"]) not in _done]
        if not todo:
            status["last_result"] = {"accepted": 0, "duplicates": 0, "rejected": [], "pending": 0}
            return status["last_result"]
        res = erp.push_activity(todo)
        for w in todo:
            _done.add((w["host"], w["window_start"]))
        res["rejected"] = [{**r, "window": todo[r["index"]]} for r in res["rejected"] if r["index"] < len(todo)]
        status["last_result"], status["last_error"] = res, None
        status["sent"] += res["accepted"]
        return res
    except Exception as e:                     # PMS ล่ม/เน็ตหลุด → รอบหน้าลองใหม่ (ไม่ได้จำว่าส่งแล้ว)
        status["last_error"] = f"{type(e).__name__}: {e}"
        return None


def start(load_recent, interval_min, eff_job, erp):
    def loop():
        ev = threading.Event()
        while True:
            sync_once(load_recent, interval_min, eff_job, erp)
            ev.wait(EVERY_SEC)
    threading.Thread(target=loop, name="pms-sync", daemon=True).start()


# ---------------- progress % (PMS contract v2, POST /api/integrations/editortrack/progress) ----------------
# ค่า % ที่ส่ง PMS คำนวณที่ server ด้วย progress_v2.Engine (ขั้น 1–2 ของ C3 ในการศึกษา worst-case):
# ไม่ส่ง 100/done จนกว่าขอบเขตจะยืนยันและนิ่ง · ก่อนนั้น provisional ไม่เกิน cap (99) · พักค่าเดิมตอน
# detector ยังไม่เสร็จ / เปิด timeline offline / project ใน Resolve ไม่ตรง booking · record ค้างจาก queue ไม่ขยับค่า
# ฟิลด์ตามสัญญา v2 ของ PMS (9e67f60, docs/integrations/editortrack.md §3.1) ชื่อ/ชนิดตรงตัว — ไม่ผ่าน ERP_PF_*
# ยังปิดอยู่จนกว่าจะตั้ง ERP_PROGRESS_PATH (+ ERP_BASE_URL) · best-effort: พังแล้ว ingest ยังรับ record ตามปกติ
import json as _json
import os as _os

import progress_v2 as _pv

PROGRESS_STATE_FILE = "progress_v2_state.json"     # ใน STORE: state ของ engine + ค่าที่ส่งสำเร็จล่าสุด (กัน restart แล้ว work_done ถอย)
SCOPE_LOCKS_FILE = "scope_locks.json"               # ใน STORE: ขอบเขตที่คนยืนยัน (D1 = lock) — python3 progress_v2.py lock ...
progress_status = {"enabled": False, "config": None, "sent": 0, "rejected": 0, "refused_locally": 0, "errors": 0,
                   "withheld": {}, "last_error": None, "last": {}}
_pg = {"engine": None, "pushed": {}, "store": None}
_pg_lock = threading.Lock()


def _progress_enabled(erp):
    return bool(erp and getattr(erp, "ERP_PROGRESS_PATH", "") and getattr(erp, "ERP_BASE", ""))


def _load_progress_state(store):
    """the persisted engine state; a corrupt or wrongly shaped file is moved aside (kept for a look) and the
    engine starts fresh: values stay provisional, only work_done may restart lower (said in last_error)"""
    path = _os.path.join(str(store), PROGRESS_STATE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            st = _json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        st = None
    if _pv.valid_state(st) and isinstance(st.get("pushed", {}), dict):
        return st
    try:
        _os.replace(path, path + ".corrupt-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    except OSError:
        pass
    progress_status["last_error"] = "progress state file unreadable: moved aside, engine restarted"
    return {}


def _save_progress_state(store, engine, pushed):
    path = _os.path.join(str(store), PROGRESS_STATE_FILE)
    tmp = path + ".tmp"
    fd = _os.open(tmp, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
    with _os.fdopen(fd, "w", encoding="utf-8") as f:
        _json.dump({**engine.state(), "pushed": pushed}, f, ensure_ascii=False)
        f.flush()
        _os.fsync(f.fileno())                    # a power cut must not leave an empty file behind os.replace
    _os.replace(tmp, path)


def _engine(store):
    if _pg["engine"] is None or _pg["store"] != str(store):
        st = _load_progress_state(store)
        cfg = _pv.Config.from_env()
        _pg["engine"], _pg["pushed"], _pg["store"] = _pv.Engine(cfg, st), dict(st.get("pushed") or {}), str(store)
        progress_status["config"] = cfg.describe()
    return _pg["engine"]


def progress_on_ingest(rec, eff_job, erp, store):
    """เรียกจาก /ingest หลังเขียน record ลง disk แล้ว → True เมื่อส่ง PMS สำเร็จ. ไม่ raise (best-effort)."""
    progress_status["enabled"] = _progress_enabled(erp)
    if not progress_status["enabled"]:
        return False
    try:
        with _pg_lock:
            return _progress_step(rec, eff_job, erp, store)
    except Exception as e:                       # ไม่ให้ปัญหาฝั่ง PMS/คำนวณ ทำให้ ingest ล้ม
        progress_status["errors"] += 1
        progress_status["last_error"] = f"{type(e).__name__}: {e}"
        return False


def _progress_step(rec, eff_job, erp, store):
    eng = _engine(store)
    eff = eff_job(rec)
    stage = eff.get("stage")
    key = f"{eff.get('job_id')}|{stage}"
    scope = None
    if "lock" in eng.cfg.scope_source:
        scope = _pv.lock_for(_pv.load_scope_locks(_os.path.join(str(store), SCOPE_LOCKS_FILE)), key, rec.get("ts"))
    o = eng.step(_pv.obs_from_record(rec, eff, scope))
    pushed = False
    if not o["send"]:
        why = next((r for r in _pv.HOLDS if r in o["reasons"]),
                   "unbound" if eff.get("source") != "erp" else "unsendable")
        progress_status["withheld"][why] = progress_status["withheld"].get(why, 0) + 1
    else:
        body = _pv.payload(o, eff["job_id"], stage, rec.get("ts"), rec.get("host"))
        sig = _sig(body)
        problem = _pv.pms_check(body)
        if problem:                              # ไม่ส่งสิ่งที่ PMS จะตอบ 422 หรือผิดกติกา zero-incident ของเราเอง
            progress_status["refused_locally"] += 1
            progress_status["last_error"] = f"refused locally: {problem}"
        elif _pg["pushed"].get(key) != sig:
            pushed = _post_progress(erp, body)
            if pushed:
                _pg["pushed"][key] = sig
                progress_status["sent"] += 1
                progress_status["last"][key] = body
    _save_progress_state(store, eng, _pg["pushed"])
    return pushed


def _sig(body):
    """what makes a new PMS row worth sending: the value and its counts. Reasons alone do not: a drop's reason
    rides on the row that drops, and a resend of the same % with fewer reasons would hide it (PMS explains the
    latest row against the one before it)."""
    return _json.dumps({k: v for k, v in body.items() if k not in ("observed_at", "host", "reasons")}, sort_keys=True)


def _post_progress(erp, body):
    """POST ตรงตามสัญญา (ไม่ผ่าน erp.push_progress ที่แมปชื่อฟิลด์ด้วย ERP_PF_*) — 422 = PMS ไม่รับ ไม่ส่งซ้ำค่าเดิม"""
    import urllib.error
    try:
        erp._post_auth(f"{erp.ERP_BASE}{erp.ERP_PROGRESS_PATH}", body, getattr(erp, "ERP_PROGRESS_METHOD", "POST"))
        return True
    except urllib.error.HTTPError as e:
        if e.code == 422:
            progress_status["rejected"] += 1
            progress_status["last_error"] = f"PMS 422 for {body.get('project_no')}|{body.get('stage')}"
            key = f"{body.get('project_no')}|{body.get('stage')}"
            _pg["pushed"][key] = _sig(body)
            return False
        raise

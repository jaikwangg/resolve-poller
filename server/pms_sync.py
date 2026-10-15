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

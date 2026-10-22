#!/usr/bin/env python3
"""ส่งเวลาทำงานให้ PMS เป็นช่วงละ WINDOW_MIN นาที (POST /api/integrations/editortrack/activity)

record ที่ ingest มาทุกนาที → รวมต่อ host ต่อ window → active_seconds = ผลรวม credit ของ record ที่ explain.is_active
  (credit = min(ช่องว่างถึง record ถัดไปของ host, interval × 1.5) — explain.assign_credit) ไม่เกินความยาว window
- ไม่ส่ง user/ชื่อคน (PMS ไม่รับ — PDPA)
- ทุกช่วงต้องมี booking_id (กติกา PMS 15 ต.ค.: ห้องรับหลายงานพร้อมกันได้ รหัสห้องอย่างเดียวถูกปฏิเสธ)
  booking ที่จับคู่ไว้ตอน ingest (record["_job"]) ใช้ก่อน → ไม่มีค่อย lookup สด
  ช่วงที่หา booking ไม่ได้ หรือห้องมีหลาย booking แล้วเลือกจาก project ใน Resolve ไม่ได้ → ไม่ส่ง นับไว้ใน status["unbound"]

**idempotency key = (host, window_start)** — PMS เก็บ unique (integration key, host, window_start) แบบ ON CONFLICT DO NOTHING:
ส่งซ้ำ = "duplicates" ไม่บันทึกซ้ำ และ **ไม่อัปเดตค่าเดิม** (PMS ไม่มีวิธีส่งแก้ — ดู ~/github/pms docs/integrations/editortrack.md)
→ ห้ามเปลี่ยน PMS_WINDOW_MIN หลังเริ่มส่งจริง (ขอบ window เปลี่ยน = key ใหม่ = PMS นับซ้ำช่วงที่ทับกัน)
ดังนั้นกัน record มาช้า (offline queue) ด้วยการ **ถือ window ไว้จนนิ่ง**:
  ส่งเมื่อ (ก) ปิดไปแล้ว ≥ SETTLE_SEC และ server ได้รับ record ของ host นั้นที่ ts ≥ ปลาย window มาแล้ว ≥ SETTLE_SEC
  (agent ส่ง queue ทั้งหมดในรอบเดียวกับที่ส่งสำเร็จ → record ช้าของ window นั้นมาครบแล้ว) หรือ (ข) ปิดไปแล้ว ≥ MAX_HOLD_SEC (เครื่องปิด/หาย)
ถ้ายังมี record มาช้ากว่านั้นจน active_seconds เพิ่ม → ส่ง window เดิมซ้ำด้วย key เดิม (ถ้า PMS รองรับ upsert วันหน้าจะมีผลเอง)
  วันนี้ PMS ตอบ duplicate → นับไว้ใน status["corrections"]["ignored_seconds"] = เวลาที่ PMS ขาดจริง (gap ที่รู้แล้ว)
ENV: PMS_WINDOW_MIN=10 · PMS_SYNC_EVERY_SEC=300 · PMS_LOOKBACK_HOURS=24 · PMS_SETTLE_SEC=120 · PMS_MAX_HOLD_SEC=21600
     (เปิดด้วย ERP_ACTIVITY_PATH ใน erp.py)
"""
import collections
import datetime
import os
import threading

import explain as _ex

WINDOW_MIN = int(os.environ.get("PMS_WINDOW_MIN", "10"))
EVERY_SEC = int(os.environ.get("PMS_SYNC_EVERY_SEC", "300"))
LOOKBACK_HOURS = int(os.environ.get("PMS_LOOKBACK_HOURS", "24"))
SETTLE_SEC = int(os.environ.get("PMS_SETTLE_SEC", "120"))          # รอ queue ของ agent flush หลังกลับมาออนไลน์
MAX_HOLD_SEC = int(os.environ.get("PMS_MAX_HOLD_SEC", "21600"))    # 6 ชม. — host เงียบหลังปิด window นานเท่านี้ = ส่งเท่าที่มี

status = {"enabled": False, "last_run": None, "last_result": None, "last_error": None, "sent": 0, "unbound": {},
          "held": 0, "corrections": {"sent": 0, "applied": 0, "ignored_by_pms": 0, "ignored_seconds": 0}}
_sent = {}                     # (host, window_start) -> active_seconds ที่ส่งไปแล้ว (PMS ตอบแล้ว) ใน process นี้


def _floor(t):
    return t.replace(minute=t.minute - t.minute % WINDOW_MIN, second=0, microsecond=0)


def _iso(t):
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rx(r):
    return _ex._dt((r.get("_rx") or {}).get("at")) or _ex._dt(r.get("ts"))


def build_windows(recs, interval_min, eff_job, room_map, now=None, lookup=None, unbound=None, held=None):
    """record → รายการ window ตามสัญญา PMS (ยังไม่กรองตัวที่ส่งแล้ว)
    lookup(host, ts, project) -> (booking dict | None, เหตุผล) · ช่วงที่ไม่ได้ booking ไม่อยู่ในผล นับลง unbound
    held = list ที่เติม (host, window_start) ที่ยังไม่นิ่ง (รอ record ช้า)"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=LOOKBACK_HOURS)
    win = datetime.timedelta(minutes=WINDOW_MIN)
    _ex.ensure_credit(recs, interval_min)
    groups = collections.defaultdict(list)
    seen_after = collections.defaultdict(list)       # host -> [(ts, rx)] ใช้ตัดสินว่า window นิ่งหรือยัง
    for r in recs:
        t = _ex._dt(r.get("ts"))
        host = r.get("host")
        if not t or not host:
            continue
        t = _ex._utc(t)
        seen_after[host].append((t, _rx(r)))
        if t < cutoff:
            continue
        groups[(host, _floor(t))].append((t, r))

    def settled(host, we):
        age = (now - we).total_seconds()
        if age < SETTLE_SEC:
            return False
        if age >= MAX_HOLD_SEC:
            return True
        return any(t >= we and rx is not None and (now - _ex._utc(rx)).total_seconds() >= SETTLE_SEC
                   for t, rx in seen_after[host])

    out = []
    for (host, ws), items in sorted(groups.items()):
        we = ws + win
        if not settled(host, we):
            if held is not None and (now - we).total_seconds() >= 0:
                held.append((host, _iso(ws)))
            continue
        active = [r for _, r in items if _ex.is_active(r)]
        secs = min(int(win.total_seconds()), int(round(sum(_ex.credit_sec(r, interval_min) for r in active))))
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
        stored = next((r["_job"] for r in sorted(active, key=lambda r: r.get("ts", ""))
                       if isinstance(r.get("_job"), dict) and r["_job"].get("booking_id")), None)
        if stored:                                   # จับคู่ไว้ตอน ingest — booking แก้ทีหลังไม่เปลี่ยนประวัติ
            b, why = stored, None
        else:
            projects = collections.Counter((r.get("source") or {}).get("project") for r in active)
            b, why = lookup(host, first.get("ts"), projects.most_common(1)[0][0]) if lookup else (None, "no_lookup")
        if not (b and b.get("booking_id")):
            if unbound is not None:
                unbound[why or "no_booking_id"] = unbound.get(why or "no_booking_id", 0) + 1
            continue
        rec["booking_id"] = str(b["booking_id"])
        out.append(rec)
    return out


def _key(w):
    return (w["host"], w["window_start"])


def sync_once(load_recent, interval_min, eff_job, erp, now=None):
    """1 รอบ: สร้าง window (เฉพาะที่นิ่งแล้ว) → ส่งตัวใหม่ + ตัวที่ active_seconds เพิ่มหลังส่ง (key เดิม) → จำผล"""
    status["enabled"] = bool(erp and erp.ERP_ACTIVITY_PATH and erp.ERP_BASE)
    if not status["enabled"]:
        return None
    status["last_run"] = _ex.now_local().isoformat()
    try:
        days = LOOKBACK_HOURS // 24 + 2
        unbound, held = {}, []
        wins = build_windows(load_recent(days), interval_min, eff_job, erp._room_map(), now=now,
                             lookup=erp.lookup_detail, unbound=unbound, held=held)
        status["unbound"] = unbound            # ช่วงที่ไม่ส่ง (ใน LOOKBACK) แยกตามเหตุผล
        status["held"] = len(held)             # ช่วงที่ปิดแล้วแต่ยังรอ record ช้า
        new = [w for w in wins if _key(w) not in _sent]
        fix = [w for w in wins if _key(w) in _sent and w["active_seconds"] > _sent[_key(w)]]
        res = {"accepted": 0, "duplicates": 0, "rejected": [], "pending": 0}
        if new:
            r = erp.push_activity(new)
            for w in new:
                _sent[_key(w)] = w["active_seconds"]
            res["accepted"] += r["accepted"]
            res["duplicates"] += r["duplicates"]
            res["rejected"] += [{**x, "window": new[x["index"]]} for x in r["rejected"] if x["index"] < len(new)]
        if fix:                                # แก้ด้วย key เดิม — PMS วันนี้ตอบ duplicate (ไม่อัปเดต) → นับเป็น gap
            r = erp.push_activity(fix)
            c = status["corrections"]
            c["sent"] += len(fix)
            c["applied"] += r["accepted"]
            c["ignored_by_pms"] += r["duplicates"]
            if r["duplicates"]:
                c["ignored_seconds"] += sum(w["active_seconds"] - _sent[_key(w)] for w in fix)
            for w in fix:
                _sent[_key(w)] = w["active_seconds"]
            res["corrections"] = {"sent": len(fix), "accepted": r["accepted"], "duplicates": r["duplicates"]}
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

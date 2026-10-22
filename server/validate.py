#!/usr/bin/env python3
"""ตรวจรูปร่าง record — ใช้ 2 ที่
1) /ingest (strict=True): record ผิดรูป → 400 ไม่เก็บลงดิสก์
2) อ่านไฟล์ (strict=False): record ผิดรูปที่เก็บไว้ก่อนมีการตรวจ → ข้าม + log (ไม่ทำให้ /api/* ตอบ 500)

เดิม {"host": "x", "ts": "...", "job": "x"} ผ่าน /ingest ได้ แล้วทุกหน้าอ่านตอบ 500 (rec["job"].get → AttributeError)
"""
import datetime
import math
import re

STAGES = ("data", "conform", "color", "subtitle", "master")
# hostname: ตัวพิมพ์เล็ก (server แปลงให้ตอนรับ) ตัวเลข - _ . · ขึ้นต้น/ลงท้ายด้วยตัวอักษร/ตัวเลข · ยาวไม่เกิน 253
HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,251}[a-z0-9])?$")
_DICTS = ("job", "source", "progress", "activity", "_job", "_rx")
_STRS = ("stage", "page")


def norm_host(h):
    """host จาก agent → รูปที่เก็บ (ตัดช่องว่าง + ตัวพิมพ์เล็ก — station ทุกเครื่องตั้ง hostname เป็นตัวเล็กอยู่แล้ว)"""
    return h.strip().lower() if isinstance(h, str) else h


def parse_ts(s):
    """ISO 8601 ที่มี timezone → datetime · อื่น ๆ → None"""
    if not isinstance(s, str) or not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _opt(d, key, kind):
    """d[key] ไม่มี / None / เป็นชนิด kind → True"""
    v = d.get(key)
    return v is None or isinstance(v, kind)


def problem(rec, strict=True):
    """→ None ถ้า record ใช้ได้ · ไม่งั้นข้อความสั้น ๆ บอกว่าผิดตรงไหน"""
    if not isinstance(rec, dict):
        return "record ต้องเป็น JSON object"
    host = rec.get("host")
    if not isinstance(host, str) or not host:
        return "host ต้องเป็น string"
    if strict and not HOST_RE.match(host):
        return "host ต้องเป็น hostname (a-z 0-9 - _ .)"
    if parse_ts(rec.get("ts")) is None:
        return "ts ต้องเป็น ISO 8601 มี timezone"
    for k in _DICTS:
        if not _opt(rec, k, dict):
            return f"{k} ต้องเป็น object"
    for k in _STRS:
        if not _opt(rec, k, str):
            return f"{k} ต้องเป็น string"
    if strict and rec.get("stage") is not None and rec["stage"] not in STAGES:
        return f"stage ต้องเป็นหนึ่งใน {', '.join(STAGES)}"

    job = rec.get("job") or {}
    for k in ("job_id", "title", "role", "resolved_by"):
        if not _opt(job, k, str):
            return f"job.{k} ต้องเป็น string"

    prog = rec.get("progress") or {}
    pct = prog.get("pct")
    if pct is not None and (not _num(pct) or not 0 <= pct <= 100):
        return "progress.pct ต้องเป็นตัวเลข 0–100 หรือ null"
    if not _opt(prog, "stage", str) or not _opt(prog, "detail", dict):
        return "progress.stage ต้องเป็น string และ progress.detail ต้องเป็น object"
    det = prog.get("detail") or {}
    for k in ("graded_clips", "video_clips"):
        if det.get(k) is not None and not _num(det[k]):
            return f"progress.detail.{k} ต้องเป็นตัวเลข"

    act = rec.get("activity") or {}
    if act.get("idle_sec") is not None and not _num(act["idle_sec"]):
        return "activity.idle_sec ต้องเป็นตัวเลข"

    src = rec.get("source") or {}
    for k in ("timeline", "render"):
        if not _opt(src, k, dict):
            return f"source.{k} ต้องเป็น object"
    if not _opt(src, "shots", list) or not all(isinstance(s, dict) for s in src.get("shots") or []):
        return "source.shots ต้องเป็น list ของ object"
    if any(s.get("code") is not None and not isinstance(s["code"], str) for s in src.get("shots") or []):
        return "source.shots[].code ต้องเป็น string"
    tl = src.get("timeline") or {}
    for k in ("duration_sec", "video_clips", "graded_clips", "markers", "fps"):
        if tl.get(k) is not None and not _num(tl[k]):
            return f"source.timeline.{k} ต้องเป็นตัวเลข"

    st = rec.get("_job") or {}
    for k in ("job_id", "title", "stage"):
        if not _opt(st, k, str):
            return f"_job.{k} ต้องเป็น string"
    rx = rec.get("_rx") or {}
    if rx.get("at") is not None and parse_ts(rx["at"]) is None:
        return "_rx.at ต้องเป็น ISO 8601 มี timezone"
    return None

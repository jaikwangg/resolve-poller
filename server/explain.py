#!/usr/bin/env python3
"""อธิบาย record ที่ agent ส่งเข้า /ingest เป็นภาษาคน — ใช้กับ /api/inbox และหน้า /inbox
explain(rec, eff) -> {summary, lines[], warnings[], fields[]}
"""
import datetime
import math

LATE_SEC = 120          # ts ของ agent ช้ากว่าเวลาที่ server รับเกินนี้ = มาจาก offline queue
STALE_SEC = 300         # host เงียบเกินนี้ = ขาดการติดต่อ

# path -> ความหมาย (ใช้ทำตาราง field)
GLOSSARY = {
    "schema": "เวอร์ชันรูปแบบ record (ปัจจุบัน = 3)",
    "ts": "เวลาที่ agent เก็บข้อมูล (UTC) — ไม่ใช่เวลาที่ server ได้รับ",
    "host": "ชื่อเครื่อง workstation — server ใช้แยกห้อง (ต้องตรงกับ room_map)",
    "user": "ชื่อ user ที่ login บนเครื่อง",
    "stage": "stage ที่เครื่องนี้ถูกตั้งให้วัด (EDITORTRACK_STAGE): data/conform/color/subtitle/master",
    "page": "หน้า Resolve ที่ worker เปิดอยู่ตอนเก็บ (edit/color/fusion/deliver ...)",
    "activity.idle_sec": "จำนวนวินาทีนับจาก input ล่าสุด (เมาส์/คีย์บอร์ด)",
    "activity.active": "ค่าดิบจาก agent: มี input ภายใน threshold (default 90 วิ) — server นับเป็นเวลาทำงานเฉพาะเมื่อ Resolve อยู่หน้าด้วย (stage conform/color)",
    "activity.frontmost_name": "ชื่อแอปที่อยู่หน้าสุดตอนเก็บ",
    "activity.frontmost_bundle": "bundle id ของแอปหน้าสุด (macOS)",
    "activity.resolve_frontmost": "DaVinci Resolve อยู่หน้าสุดหรือไม่",
    "activity.platform": "ระบบปฏิบัติการของเครื่อง agent",
    "source.ok": "collector อ่านข้อมูลต้นทางสำเร็จหรือไม่ (false = ดู reason)",
    "source.reason": "สาเหตุที่ collector อ่านไม่ได้",
    "source.detail": "รายละเอียดของสาเหตุ",
    "source.ts": "เวลาที่ poll Resolve",
    "source.page": "หน้า Resolve ปัจจุบัน",
    "source.project": "ชื่อ project ที่เปิดอยู่ใน Resolve",
    "source.timeline_count": "จำนวน timeline ใน project",
    "source.rendering": "Resolve กำลัง render อยู่หรือไม่",
    "source.timeline.name": "ชื่อ timeline ที่เปิดอยู่",
    "source.timeline.duration_sec": "ความยาว timeline (วินาที)",
    "source.timeline.fps": "frame rate ของ timeline",
    "source.timeline.video_tracks": "จำนวน video track",
    "source.timeline.audio_tracks": "จำนวน audio track",
    "source.timeline.video_clips": "จำนวน clip วิดีโอทั้งหมด = ตัวหารของ color %",
    "source.timeline.graded_clips": "จำนวน clip ที่เกรดแล้ว = ตัวเศษของ color % (วิธีนับดู timeline.grade.method)",
    "source.timeline.grade.method": "วิธีนับ clip ที่เกรด: lut = export LUT แล้วสีเปลี่ยนจริง · nodes = มี node เกิน 1 (แบบเดิม หยาบ)",
    "source.timeline.grade.exported": "จำนวน clip ที่ export LUT ตรวจใหม่ในรอบนี้ (ที่เหลือใช้ผลจาก cache)",
    "source.timeline.grade.pending": "clip ที่ยังไม่ได้ตรวจเพราะหมดเวลาต่อรอบ — ใช้ผลเก่า รอบหน้าตรวจต่อ",
    "source.timeline.grade.failed": "clip ที่ export LUT ไม่ได้ (นับด้วยเกณฑ์ node แทน)",
    "source.timeline.grade.ms": "เวลาที่ใช้ตรวจ grade รอบนี้ (มิลลิวินาที)",
    "source.timeline.markers": "จำนวน marker บน timeline",
    "source.timeline.scan_ms": "เวลาที่ใช้สแกน timeline (มิลลิวินาที)",
    "source.scope_hint": "เป้าหมายจาก marker @scope (ถ้ามี)",
    "source.job_hint": "job id จาก marker @job (ถ้ามี)",
    "source.offline_target_sec": "ความยาว offline edit ที่ใช้เป็นเป้าของ conform %",
    "source.shots": "รายการ shot code จาก marker พร้อมสถานะเกรด",
    "source.render.polled": "ได้ถามคิว render หรือไม่ (ปิดไว้ เพราะทำให้ Resolve เด้งไปหน้า Deliver)",
    "source.render.jobs": "จำนวน job ในคิว render",
    "source.render.max_completion": "% render สูงสุดในคิว",
    "job.job_id": "job ที่ agent เดาได้เอง (server จะใช้ค่าจาก ERP ก่อนถ้ามี)",
    "job.title": "ชื่อ job ฝั่ง agent",
    "job.role": "role ของเครื่องฝั่ง agent",
    "job.resolved_by": "agent ได้ job_id มาจากไหน: marker > registry > naming > fallback",
    "job.linked": "ผูกกับ job จริงแล้วหรือยัง (false = ใช้ชื่อ project แทน)",
    "job.target_duration_sec": "ความยาวเป้าหมายจาก registry (ถ้ามี)",
    "progress.stage": "stage ที่ % นี้เป็นของ",
    "progress.pct": "% ความคืบหน้าของ stage นี้ (null = ไม่มีตัวหาร วัดไม่ได้)",
    "progress.done": "ค่าดิบที่ทำแล้ว (ใช้กับ stage ที่นับไฟล์/cue)",
    "progress.total": "ค่าดิบเป้าหมาย",
    "progress.detail.graded_clips": "ตัวเศษของ color %",
    "progress.detail.video_clips": "ตัวหารของ color %",
    "_rx.at": "เวลาที่ server ได้รับ record นี้ (UTC)",
    "_rx.ip": "IP ของเครื่องที่ส่งเข้ามา",
}

RESOLVED_BY = {
    "marker": "marker @job บน timeline",
    "registry": "registry (jobs.json)",
    "naming": "รูปแบบชื่อ project",
    "fallback": "ไม่พบการผูก job — ใช้ชื่อ project แทน",
    "erp": "ERP booking (ห้อง + เวลา)",
}
REASONS = {
    "resolve_not_running": "ต่อ Resolve ไม่ได้ (โปรแกรมปิดอยู่ หรือไม่ใช่ Studio / ยังไม่เปิด external scripting)",
    "no_project_open": "Resolve เปิดอยู่แต่ไม่มี project",
    "unknown_stage": "ค่า EDITORTRACK_STAGE ไม่ถูกต้อง",
    "no_watch_dir": "ยังไม่ได้ตั้งโฟลเดอร์ที่จะนับไฟล์ (stage data/master)",
    "no_subtitle_path": "ยังไม่ได้ตั้ง path ของไฟล์ subtitle",
    "no_subtitle_files": "ไม่พบไฟล์ .srt/.vtt ใน path ที่ตั้งไว้",
}


RESOLVE_STAGES = {"conform", "color"}        # stage ที่ทำใน Resolve — active ต้องมี Resolve อยู่หน้าสุดด้วย


DUP_SEC = 30            # record จาก host เดียวกันห่างกันน้อยกว่านี้ = agent รันซ้อน (เทส 8 ต.ค.: 2 ตัวห่าง 0.29 วิ) นับครั้งเดียว


# Record shape the readers rely on (/api/*, report, projects, health, pms_sync). One wrong type here made a
# route raise (TypeError/AttributeError -> 500), so the same check runs at /ingest (400) and on every load
# (skip + count). Only types are checked, never values: a field may always be missing or null.
_N = type(None)
_STR = (str, _N)
_NUM = (int, float, _N)
_DICT = (dict, _N)
_LIST = (list, _N)
_SCALAR = (str, int, float, bool, _N)
SHAPE = {
    "user": _SCALAR, "stage": _STR, "page": _SCALAR,
    "activity": _DICT, "source": _DICT, "job": _DICT, "progress": _DICT, "_rx": _DICT, "agent": _DICT,
    "activity.idle_sec": _NUM, "activity.frontmost_name": _SCALAR,
    "source.reason": _SCALAR, "source.detail": _SCALAR + (dict, list), "source.page": _SCALAR,
    "source.timeline.grade": _DICT,
    "progress.detail.graded_clips": _NUM, "progress.detail.video_clips": _NUM,
    "source.project": _SCALAR, "source.timeline": _DICT, "source.shots": _LIST, "source.items": _LIST,
    "source.timeline.name": _SCALAR, "source.timeline.duration_sec": _NUM, "source.timeline.video_clips": _NUM,
    "source.timeline.graded_clips": _NUM, "source.timeline.markers": _NUM,
    "job.job_id": _SCALAR, "job.title": _SCALAR, "job.role": _SCALAR, "job.resolved_by": _SCALAR,
    "progress.stage": _SCALAR, "progress.pct": _NUM, "progress.done": _NUM, "progress.total": _NUM,
    "progress.detail": _DICT,
    "_rx.at": _STR, "_rx.ip": _SCALAR,
    "agent.guard": _DICT,
}


def record_problem(rec):
    """None when the record is safe for every reader, else a short reason (used by /ingest for the 400
    and by dedupe to skip + count). A record must have a non-empty string host and an ISO ts WITH a
    timezone offset (the agent sends UTC "+00:00"; a naive ts is rejected, never guessed)."""
    if not isinstance(rec, dict):
        return "not_object"
    if not isinstance(rec.get("host"), str) or not rec["host"].strip():
        return "bad_host"
    if not isinstance(rec.get("ts"), str):
        return "no_ts"
    if _dt(rec["ts"]) is None:
        return "ts_without_offset" if _naive(rec["ts"]) else "bad_ts"
    for path, types in SHAPE.items():
        cur = rec
        for k in path.split("."):
            cur = cur.get(k) if isinstance(cur, dict) else None
        if not isinstance(cur, types):
            return f"bad_type:{path}"
    for s in (rec.get("source") or {}).get("shots") or []:
        if not isinstance(s, dict) or not isinstance(s.get("code"), _STR):
            return "bad_type:source.shots[]"
    return None


def finite(o):
    """NaN / ±Infinity -> None, recursively (Python's JSON reads and writes them, but a JSON response refuses
    them: one NaN in a stored record made /api/inbox 500). Explicit rule, applied at /ingest and on load."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: finite(v) for k, v in o.items()}
    if isinstance(o, list):
        return [finite(v) for v in o]
    return o


def _naive(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).tzinfo is None
    except (TypeError, ValueError):
        return False


def dedupe(recs, skipped=None):
    """ตัด record ที่ไม่มี host/ts และ record ซ้อนของ host เดียวกัน (< DUP_SEC) — คืนเรียงตาม host, ts
    record ที่ใช้ไม่ได้ (record_problem) ถูกข้าม · skipped (dict) นับเหตุผลไว้ให้ผู้เรียก"""
    good = []
    for r in recs:
        why = record_problem(r)
        if why is None:
            good.append(r)
        elif skipped is not None:
            skipped[why] = skipped.get(why, 0) + 1
    out, last = [], {}
    for r in sorted(good, key=lambda r: (r["host"], _dt(r["ts"]))):
        t = _dt(r["ts"])
        p = last.get(r["host"])
        if p is not None and (t - p).total_seconds() < DUP_SEC:
            continue
        last[r["host"]] = t
        out.append(r)
    return out


def is_active(rec):
    """เวลาทำงานที่ server นับ = มี input (activity.active) และ Resolve อยู่หน้าสุด
    stage ที่ไม่ได้ทำใน Resolve (data/subtitle/master) ดูแค่ input"""
    act = rec.get("activity") or {}
    if not act.get("active"):
        return False
    if rec.get("stage") in RESOLVE_STAGES:
        return bool(act.get("resolve_frontmost"))
    return True


def _dt(s):
    """ISO time WITH a timezone offset -> aware datetime; anything else (missing, not a string, unparseable,
    or naive like "2026-10-14T10:05:00") -> None. A naive time cannot be ordered against aware ones
    (TypeError in sorted/min) and its zone is unknown, so it is treated as no time at all."""
    if not isinstance(s, str):
        return None
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo is not None and d.utcoffset() is not None else None


def _local(d):
    return d.astimezone().strftime("%d/%m %H:%M:%S") if d else "?"


def _dur(sec):
    sec = int(abs(sec))
    if sec < 90:
        return f"{sec} วิ"
    if sec < 5400:
        return f"{sec // 60} นาที"
    return f"{sec / 3600:.1f} ชม."


def _flatten(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _flatten(v, f"{p}.{k}" if p else k)
    elif isinstance(o, list):
        yield p, f"[{len(o)} รายการ]"
    else:
        yield p, o


def delay_sec(rec):
    """วินาทีระหว่างเวลาที่ agent เก็บ กับเวลาที่ server รับ (None = record เก่าไม่มี _rx)"""
    a, b = _dt(rec.get("ts")), _dt((rec.get("_rx") or {}).get("at"))
    return (b - a).total_seconds() if a and b else None


def explain(rec, eff=None, room_map=None):
    eff = eff or {}
    lines, warns = [], []
    host, stage = rec.get("host") or "?", rec.get("stage") or "?"
    ts = _dt(rec.get("ts"))
    rx = rec.get("_rx") or {}

    # --- ใคร / เมื่อไหร่ / มาทางไหน ---
    lines.append(f"เครื่อง {host} (user {rec.get('user') or '?'}) รายงาน stage {stage} · เก็บเมื่อ {_local(ts)}")
    d = delay_sec(rec)
    if d is None:
        lines.append("ไม่มีเวลารับของ server (record ก่อนเปิดใช้การบันทึก _rx)")
    elif d > LATE_SEC:
        lines.append(f"server รับเมื่อ {_local(_dt(rx.get('at')))} จาก IP {rx.get('ip') or '?'} — ช้า {_dur(d)}")
        warns.append(f"ส่งย้อนหลัง {_dur(d)}: เครื่องนี้ต่อ server ไม่ได้ช่วงหนึ่ง แล้วส่งจาก offline queue")
    elif d < -LATE_SEC:
        lines.append(f"server รับเมื่อ {_local(_dt(rx.get('at')))} จาก IP {rx.get('ip') or '?'}")
        warns.append(f"นาฬิกาเครื่อง agent เดินเร็วกว่า server {_dur(d)} — เวลาใน dashboard จะเพี้ยน")
    else:
        lines.append(f"server รับทันที ({d:.1f} วิ) จาก IP {rx.get('ip') or '?'}")

    # --- activity ---
    act = rec.get("activity") or {}
    idle = act.get("idle_sec")
    front = act.get("frontmost_name") or "ไม่ทราบ"
    if idle is None:
        lines.append(f"อ่านค่า idle ไม่ได้ · แอปหน้าสุด: {front}")
        warns.append("อ่าน idle ไม่ได้ — นับเป็น idle เสมอ (Linux ต้องมี libxss1 + X11, macOS ต้องรันเป็น LaunchAgent ของ user)")
    else:
        state = "ACTIVE" if is_active(rec) else "IDLE"
        lines.append(f"{state}: ไม่มี input มา {_dur(idle)} · แอปหน้าสุด: {front}"
                     + (" (Resolve)" if act.get("resolve_frontmost") else " (ไม่ใช่ Resolve)"))
        if act.get("active") and not is_active(rec):
            lines.append(f"มี input แต่ Resolve ไม่ได้อยู่หน้าสุด ({front}) — ไม่นับเป็นเวลาทำงาน")
        if front == "loginwindow":
            lines.append("หน้าจอล็อกหรืออยู่หน้า login")

    # --- source ---
    src = rec.get("source") or {}
    if src.get("ok") is False:
        why = REASONS.get(src.get("reason"), src.get("reason") or "ไม่ทราบสาเหตุ")
        lines.append(f"อ่านข้อมูลงานไม่ได้: {why}" + (f" ({src.get('detail')})" if src.get("detail") else ""))
        warns.append("record นี้ไม่มี % — ใช้ได้แค่ข้อมูล active/idle")
    elif src:
        tl = src.get("timeline") or {}
        if tl:
            lines.append(f"Resolve: project “{src.get('project')}” · timeline “{tl.get('name')}” "
                         f"ยาว {_dur(tl.get('duration_sec') or 0)} @ {tl.get('fps')} fps · "
                         f"{tl.get('video_clips')} clip · marker {tl.get('markers')} · หน้า {src.get('page')}"
                         + (" · กำลัง render" if src.get("rendering") else ""))
        else:
            lines.append(f"ต้นทาง: project “{src.get('project')}”")

    # --- job ---
    job = rec.get("job") or {}
    if eff.get("job_id"):
        by = eff.get("source")
        lines.append(f"นับเป็น job “{eff.get('title') or eff['job_id']}” ({eff['job_id']}) stage {eff.get('stage')} "
                     f"— ที่มา: {RESOLVED_BY.get(by, by or '?')}")
        if by == "fallback" or (by != "erp" and job.get("linked") is False):
            warns.append("ยังไม่ผูกกับ job จริง — ใช้ชื่อ project ของ Resolve เป็น job (ERP ไม่ได้ตั้ง หรือไม่มี booking ของห้องนี้ในเวลานี้)")
    elif src.get("ok") is not False:
        warns.append("ไม่มี job_id — record นี้จะไม่ขึ้นใน /api/jobs")

    # --- progress ---
    prog = rec.get("progress") or {}
    if prog:
        pct, det = prog.get("pct"), prog.get("detail") or {}
        if pct is None:
            lines.append(f"% ของ {prog.get('stage')}: วัดไม่ได้ (ไม่มีตัวหาร)")
            if prog.get("stage") == "conform":
                warns.append("conform ไม่มีเป้า: หา offline timeline ในโปรเจคไม่เจอ และไม่มี scope ใน registry")
        elif prog.get("stage") == "color" and det.get("video_clips") is not None:
            lines.append(f"color = {pct}% (เกรดแล้ว {det.get('graded_clips')} จาก {det.get('video_clips')} clip)")
        elif prog.get("done") is not None:
            lines.append(f"{prog.get('stage')} = {pct}% ({prog.get('done')} จาก {prog.get('total')})")
        else:
            lines.append(f"{prog.get('stage')} = {pct}%")

    # --- ตรวจ config ---
    if rec.get("schema") != 3:
        warns.append(f"schema = {rec.get('schema')} (server คาดหวัง 3) — agent อาจเป็นเวอร์ชันเก่า")
    pre = host.split("-")[0].lower()
    if (pre == "grade" and stage != "color") or (pre == "conform" and stage != "conform"):
        warns.append(f"ชื่อเครื่อง {host} ไม่ตรงกับ stage {stage} — เช็ค EDITORTRACK_STAGE หรือชื่อเครื่อง")
    if room_map is not None and host not in room_map:
        warns.append(f"{host} ไม่อยู่ใน room_map — server จับคู่กับห้องใน ERP ไม่ได้")

    fields = [{"path": p, "value": v, "meaning": GLOSSARY.get(p, "")} for p, v in _flatten(rec)]
    return {"summary": lines[0], "lines": lines, "warnings": warns, "fields": fields}

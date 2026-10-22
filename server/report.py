#!/usr/bin/env python3
"""report รายวันต่อเครื่อง — สรุป record ดิบเป็นภาษาคน ใช้กับ /api/report และหน้า /report
build_report(recs, day, interval_min, eff_job, host=None) -> {day, hosts:[{summary, by_job, by_page, segments, progress, gaps, warnings}]}
"""
import collections

import explain as _ex

GAP_FACTOR = 3          # ห่างกันเกิน interval × นี้ = ข้อมูลขาดช่วง
JUMP_CLIPS = 3          # graded เพิ่ม "เกิน" นี้ (> 3) ใน 1 รอบ = กระโดด — ใช้ทั้ง /report และ /projects (is_jump)


def is_jump(delta):
    """จำนวน clip ที่เกรดเพิ่มในรอบเดียวมากผิดปกติ (apply grade ทีละหลาย clip หรือเกณฑ์นับเกินจริง)"""
    return delta is not None and delta > JUMP_CLIPS


def _local(ts):
    return _ex.to_local(_ex._dt(ts))          # EDITORTRACK_TZ (default Asia/Bangkok) — ไม่ขึ้นกับ TZ ของ process


def _hm(d):
    return d.strftime("%H:%M") if d else "?"


def _mins(n):
    n = int(round(n))
    if n < 60:
        return f"{n} นาที"
    return f"{n // 60} ชม. {n % 60} นาที" if n % 60 else f"{n // 60} ชม."


def _state(r):
    """สถานะของ record 1 ตัว — record ติดกันที่ state เท่ากันจะรวมเป็นช่วงเดียว"""
    src = r.get("source") or {}
    act = r.get("activity") or {}
    if src.get("ok") is False:
        what = _ex.REASONS.get(src.get("reason"), src.get("reason") or "อ่านข้อมูลงานไม่ได้")
    else:
        tl = (src.get("timeline") or {}).get("name")
        what = f"{src.get('project') or '?'}" + (f" / {tl}" if tl else "") + (f" · หน้า {src.get('page')}" if src.get("page") else "")
    return (what, _ex.front_label(act), _ex.is_active(r))


def _host_report(recs, interval_min, eff_job):
    recs = sorted(recs, key=lambda r: r.get("ts", ""))
    gap_sec = interval_min * 60 * GAP_FACTOR
    by_job, by_page = collections.Counter(), collections.Counter()      # วินาที (credit ต่อ record)
    segments, gaps, progress, warnings = [], [], [], []
    last_pct = {}                  # (job, stage) -> (pct, detail)
    first_active = last_active = None
    prev = None

    for r in recs:
        t = _local(r.get("ts"))
        if prev is not None and t and prev[0] and (t - prev[0]).total_seconds() > gap_sec:
            locked = _ex.is_locked(prev[1].get("activity") or {})
            gaps.append({"from": _hm(prev[0]), "to": _hm(t), "minutes": int((t - prev[0]).total_seconds() // 60),
                         "why": "เครื่องล็อกจอ/หลับ (record ก่อนหน้าอยู่หน้า login)" if locked
                                else "ไม่มี record — agent หยุด ต่อ server ไม่ได้ หรือเครื่องหลับ"})
        st = _state(r)
        seg = segments[-1] if segments else None
        if seg and seg["_k"] == st and not (gaps and gaps[-1]["to"] == _hm(t) and seg["end"] != _hm(t)):
            seg["end"], seg["n"] = _hm(t), seg["n"] + 1
        else:
            segments.append({"_k": st, "start": _hm(t), "end": _hm(t), "n": 1,
                             "what": st[0], "front": st[1], "active": st[2]})

        eff = eff_job(r)
        if _ex.is_active(r):
            first_active = first_active or t
            last_active = t
            c = _ex.credit_sec(r, interval_min)
            by_job[eff.get("title") or eff.get("job_id") or "(ไม่มี job)"] += c
            by_page[r.get("page") or r.get("stage") or "?"] += c

        prog = r.get("progress") or {}
        pct = prog.get("pct")
        if pct is not None and eff.get("job_id"):
            k = (eff["job_id"], prog.get("stage") or eff.get("stage"))
            det = prog.get("detail") or {}
            old = last_pct.get(k)
            if old is None or old[0] != pct:
                ev = {"at": _hm(t), "job": eff.get("title") or eff["job_id"], "stage": k[1],
                      "from": old[0] if old else None, "to": pct,
                      "detail": (f"{det.get('graded_clips')}/{det.get('video_clips')} clip"
                                 if "graded_clips" in det else None)}
                if old and "graded_clips" in det and "graded_clips" in (old[1] or {}):
                    jump = (det.get("graded_clips") or 0) - (old[1].get("graded_clips") or 0)
                    if is_jump(jump):
                        ev["jump"] = True
                        warnings.append(f"{_hm(t)} {ev['job']}: clip ที่เกรดเพิ่ม {jump} clip ในรอบเดียว "
                                        f"({old[1].get('graded_clips')}→{det.get('graded_clips')}) — อาจ apply grade ทีละหลาย clip "
                                        f"หรือเกณฑ์นับ (lut/nodes) นับเกินจริง ควรเช็คกับคนทำ")
                    elif jump < 0:
                        warnings.append(f"{_hm(t)} {ev['job']}: clip ที่เกรดลดลง ({old[1].get('graded_clips')}→{det.get('graded_clips')}) — ลบ node/ reset grade หรือเปลี่ยน timeline")
                progress.append(ev)
            last_pct[k] = (pct, det)
        prev = (t, r)

    for s in segments:
        s.pop("_k")
    last = recs[-1]
    if last.get("stage") == "color" and str(last.get("host", "")).startswith("conform"):
        warnings.append(f"เครื่อง {last.get('host')} ตั้ง stage เป็น color — ถ้าเป็นห้อง conform ให้ตั้ง EDITORTRACK_STAGE=conform")
    if any((eff_job(r).get("source") == "fallback") for r in recs):
        warnings.append("บาง record ยังไม่ผูกกับ job จริง — ใช้ชื่อ project ของ Resolve เป็น job แทน")

    active_min = int(round(sum(by_job.values()) / 60))
    m = lambda sec: int(round(sec / 60))       # noqa: E731
    return {
        "host": last.get("host"), "stage": last.get("stage"),
        "summary": {"records": len(recs), "first": _hm(_local(recs[0].get("ts"))), "last": _hm(_local(last.get("ts"))),
                    "active_min": active_min, "active_text": _mins(active_min),
                    "first_active": _hm(first_active) if first_active else None,
                    "last_active": _hm(last_active) if last_active else None,
                    "gap_count": len(gaps), "gap_min": sum(g["minutes"] for g in gaps)},
        "by_job": [{"job": k, "minutes": m(v), "text": _mins(m(v))} for k, v in by_job.most_common()],
        "by_page": [{"page": k, "minutes": m(v), "text": _mins(m(v))} for k, v in by_page.most_common()],
        "segments": segments, "progress": progress, "gaps": gaps, "warnings": warnings,
    }


def build_report(recs, day, interval_min, eff_job, host=None):
    """day = วันที่ตามเวลาท้องถิ่น EDITORTRACK_TZ (YYYY-MM-DD) · credit คิดจาก record ทั้งชุดก่อนตัดวัน"""
    _ex.ensure_credit(recs, interval_min)
    by_host = collections.defaultdict(list)
    hosts_all = set()
    for r in recs:
        t = _local(r.get("ts"))
        if not t or t.date().isoformat() != day:
            continue
        hosts_all.add(r.get("host") or "?")
        if host and r.get("host") != host:
            continue
        by_host[r.get("host") or "?"].append(r)
    return {"day": day, "interval_min": interval_min, "hosts_available": sorted(hosts_all),
            "generated_at": _ex.now_local().isoformat(),
            "hosts": [_host_report(v, interval_min, eff_job) for _, v in sorted(by_host.items())]}

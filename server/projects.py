#!/usr/bin/env python3
"""ตัวเลขนับรายโปรเจค (คลิป / คลิปที่เกรด / shot) — ใช้กับ /api/projects และหน้า /projects
build_projects(recs, interval_min, eff_job) -> {projects:[{counts, rooms, shots, history, ...}]}
ใช้ข้อมูลจริงที่ server รับเท่านั้น · ตัวเลขมาจาก timeline ที่เปิดอยู่ในห้องตอน agent อ่าน
"""

import explain as _ex
from report import JUMP_CLIPS

HISTORY_MAX = 30        # จำนวนครั้งที่ตัวเลขเปลี่ยน ที่ส่งไปแสดง (ล่าสุดก่อน)


def _local(ts):
    d = _ex.to_local(_ex._dt(ts))
    return d.strftime("%d/%m %H:%M") if d else "?"


def _counts(r):
    """ตัวเลขนับจาก record 1 ตัว · None = record นี้นับไม่ได้ (Resolve ไม่ตอบ / ไม่ได้เปิด timeline)"""
    src = r.get("source") or {}
    tl = src.get("timeline") or {}
    if src.get("ok") is False or tl.get("video_clips") is None:
        return None
    shots = [s for s in (src.get("shots") or []) if s.get("code")]
    return {"timeline": tl.get("name"), "video_clips": tl.get("video_clips"),
            "graded_clips": tl.get("graded_clips"), "markers": tl.get("markers"),
            "duration_sec": tl.get("duration_sec"), "shots": len(shots),
            "shots_graded": sum(1 for s in shots if s.get("graded"))}


def build_projects(recs, interval_min, eff_job):
    _ex.ensure_credit(recs, interval_min)
    projects = {}
    for r in sorted(recs, key=lambda x: x.get("ts", "")):
        eff = eff_job(r)
        jid = eff.get("job_id")
        if not jid:
            continue
        stage = eff.get("stage") or "?"
        host = r.get("host") or "?"
        p = projects.setdefault(jid, {"job_id": jid, "title": eff.get("title") or jid, "source": eff.get("source"),
                                      "resolve_projects": [], "records": 0, "active_min": 0,
                                      "first_ts": r.get("ts"), "last_ts": None,
                                      "rooms": {}, "history": [], "_prev": {}, "_shots": {}})
        p["records"] += 1
        p["last_ts"] = r.get("ts")
        p["active_min"] += _ex.credit_sec(r, interval_min) / 60 if _ex.is_active(r) else 0
        rp = (r.get("source") or {}).get("project")
        if rp and rp not in p["resolve_projects"]:
            p["resolve_projects"].append(rp)

        c = _counts(r)
        if c is None:
            continue
        p["rooms"][(stage, host)] = {"stage": stage, "host": host, "ts": r.get("ts"),
                                     "at": _local(r.get("ts")), "pct": (r.get("progress") or {}).get("pct"), **c}
        for s in (r.get("source") or {}).get("shots") or []:
            if s.get("code"):
                d = p["_shots"].setdefault(s["code"], {"conformed": False, "graded": False})
                d["conformed"] |= stage == "conform"
                d["graded"] = bool(s.get("graded")) if stage == "color" else d["graded"]

        # ประวัติ: เก็บเฉพาะตอนตัวเลขเปลี่ยน (ต่อห้อง) — record ที่ตัวเลขเท่าเดิมไม่บอกอะไรเพิ่ม
        key = (c["timeline"], c["video_clips"], c["graded_clips"], c["shots"], c["shots_graded"])
        prev = p["_prev"].get(host)
        if prev != key:
            dg = None if prev is None or prev[2] is None or c["graded_clips"] is None else c["graded_clips"] - prev[2]
            p["history"].append({"at": _local(r.get("ts")), "ts": r.get("ts"), "host": host, "stage": stage,
                                 "timeline": c["timeline"], "video_clips": c["video_clips"],
                                 "graded_clips": c["graded_clips"], "shots": c["shots"],
                                 "delta_graded": dg,
                                 "jump": dg is not None and dg > JUMP_CLIPS,
                                 "drop": dg is not None and dg < 0,
                                 "timeline_changed": prev is not None and prev[0] != c["timeline"]})
            p["_prev"][host] = key

    out = []
    for p in projects.values():
        rooms = sorted(p.pop("rooms").values(), key=lambda x: x["ts"] or "", reverse=True)
        shots = p.pop("_shots")
        p.pop("_prev")
        latest = rooms[0] if rooms else None
        notes = []
        if not rooms:
            notes.append("ไม่มีตัวเลขนับ: Resolve ไม่ได้เปิด timeline ของโปรเจคนี้ตอนที่ agent อ่าน")
        if len({r["host"] for r in rooms}) > 1:
            notes.append("ทำหลายห้อง: ตัวเลขด้านบนมาจากห้องที่อ่านล่าสุด ไม่ได้รวมข้ามห้อง (แต่ละห้องดูในตาราง)")
        if any(h["jump"] or h["drop"] for h in p["history"]):
            notes.append(f"มีครั้งที่จำนวนคลิปที่เกรดเพิ่มเกิน {JUMP_CLIPS} ในรอบเดียวหรือลดลง: ตัวเลขการเกรดยังเป็นค่าประมาณ")
        p["history"] = p["history"][::-1][:HISTORY_MAX]
        p["active_min"] = int(round(p["active_min"]))
        out.append({**p, "first_at": _local(p["first_ts"]), "last_at": _local(p["last_ts"]),
                    "stages": sorted({r["stage"] for r in rooms}), "hosts": sorted({r["host"] for r in rooms}),
                    "counts": None if latest is None else {
                        k: latest[k] for k in ("timeline", "video_clips", "graded_clips", "markers",
                                               "duration_sec", "shots", "shots_graded", "host", "at", "pct")},
                    "rooms": rooms,
                    "shots": {"total": len(shots), "conformed": sum(d["conformed"] for d in shots.values()),
                              "graded": sum(d["graded"] for d in shots.values())},
                    "notes": notes})
    out.sort(key=lambda x: x["last_ts"] or "", reverse=True)
    return {"generated_at": _ex.now_local().isoformat(), "interval_min": interval_min,
            "projects": out}

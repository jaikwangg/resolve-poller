#!/usr/bin/env python3
"""A5 — โหลด target scope + คำนวณ % แบบ phase-weighted (renormalize เฉพาะเฟสที่วัดได้).
งานหลัก = ตัดต่อ (edit) + ปรับสี (color) → weights edit .45 / color .40 / deliver .15 (audio ตัดออก)."""
import json, os, re

SCOPE_DIR = os.environ.get("EDITORTRACK_SCOPES", os.path.expanduser("~/.editortrack/scopes"))
DEFAULT_WEIGHTS = {"edit": 0.5, "color": 0.5}   # เหลือ edit + color (deliver/audio ไม่ใช้)


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "unknown")


def load_scope(project, marker_hint=None):
    """priority: registry file (authority) → marker hint overlay → None."""
    scope = None
    path = os.path.join(SCOPE_DIR, f"{_safe(project)}.json")
    if project and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            scope = json.load(f)
    if marker_hint:
        scope = scope or {"project": project, "phase_weights": DEFAULT_WEIGHTS, "source": "marker"}
        if "duration" in marker_hint:
            scope["target_duration_sec"] = float(str(marker_hint["duration"]).rstrip("s"))
        if "shots" in marker_hint:
            scope["expected_shots"] = int(marker_hint["shots"])
    if scope and "phase_weights" not in scope:
        scope["phase_weights"] = DEFAULT_WEIGHTS
    return scope


def compute_progress(snap, scope):
    tl = (snap or {}).get("timeline", {}) or {}
    scope = scope or {}
    tgt = scope.get("target_duration_sec")       # ตัวหารของ duration
    exp = scope.get("expected_shots")            # ตัวหารของ shots
    n_shots = len(snap.get("shots") or []) if snap else 0   # shots ที่ conform แล้ว (marker ในไฟล์)

    phases, detail = {}, {}

    # --- edit/conform = ผสม duration + shots (เฉลี่ยเฉพาะส่วนที่มีตัวหาร) ---
    edit_parts = {}
    if tl.get("duration_sec") is not None and tgt:
        edit_parts["duration"] = _clamp(tl["duration_sec"] / tgt)
    if n_shots and exp:
        edit_parts["shots"] = _clamp(n_shots / exp)
    if edit_parts:
        phases["edit"] = sum(edit_parts.values()) / len(edit_parts)
        detail = {k: round(v * 100, 1) for k, v in edit_parts.items()}

    # --- color = graded clips / total (node > 1) ---
    if tl.get("graded_clips") is not None and tl.get("video_clips"):
        phases["color"] = _clamp(tl["graded_clips"] / tl["video_clips"])

    # --- deliver (ปกติปิด POLL_RENDER → unmeasured) ---
    mc = (snap.get("render", {}) or {}).get("max_completion")
    if mc is not None:
        phases["deliver"] = _clamp(mc / 100.0)

    if not phases:
        return {"bounded": False,
                "note": "ยังไม่ได้ตั้ง target_duration_sec/expected_shots — รายงานค่าดิบแทน %",
                "absolute": {"duration_sec": tl.get("duration_sec"),
                             "video_clips": tl.get("video_clips"), "shots_seen": n_shots}}

    w = scope.get("phase_weights", DEFAULT_WEIGHTS)
    measured = {k: v for k, v in phases.items() if v is not None}
    wsum = sum(w.get(k, 0) for k in measured) or 1.0
    overall = sum(w.get(k, 0) * v for k, v in measured.items()) / wsum
    unmeasured = sorted((set(w) | set(phases)) - set(measured))
    flags = []
    if tgt and tl.get("duration_sec", 0) > tgt:
        flags.append("over_target_duration")
    return {"bounded": True, "overall_pct": round(overall * 100, 1),
            "phases": {k: round(v * 100, 1) for k, v in measured.items()},
            "edit_detail": detail or None,       # {duration:%, shots:%} ของเฟส edit
            "shots_seen": n_shots,
            "measured_phases": sorted(measured), "unmeasured_phases": unmeasured, "flags": flags}

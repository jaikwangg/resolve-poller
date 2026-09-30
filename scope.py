#!/usr/bin/env python3
"""A5 — โหลด target scope + คำนวณ % แบบ phase-weighted (renormalize เฉพาะเฟสที่วัดได้).
งานหลัก = ตัดต่อ (edit) + ปรับสี (color) → weights edit .45 / color .40 / deliver .15 (audio ตัดออก)."""
import json, os, re

SCOPE_DIR = os.environ.get("EDITORTRACK_SCOPES", os.path.expanduser("~/.editortrack/scopes"))
DEFAULT_WEIGHTS = {"edit": 0.45, "color": 0.40, "deliver": 0.15}


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
    if not scope or not scope.get("target_duration_sec"):
        return {"bounded": False,
                "note": "ยังไม่ได้ตั้ง target scope — รายงานค่าดิบแทน %",
                "absolute": {"duration_sec": tl.get("duration_sec"), "video_clips": tl.get("video_clips")}}

    phases = {}
    if tl.get("duration_sec") is not None:
        phases["edit"] = _clamp(tl["duration_sec"] / scope["target_duration_sec"])
    mc = (snap.get("render", {}) or {}).get("max_completion")
    if mc is not None:                          # deliver วัดได้เฉพาะเมื่อ POLL_RENDER เปิด (ไม่งั้น unmeasured)
        phases["deliver"] = _clamp(mc / 100.0)
    if tl.get("graded_clips") is not None and tl.get("video_clips"):
        phases["color"] = _clamp(tl["graded_clips"] / tl["video_clips"])

    w = scope.get("phase_weights", DEFAULT_WEIGHTS)
    measured = {k: v for k, v in phases.items() if v is not None}
    wsum = sum(w.get(k, 0) for k in measured) or 1.0
    overall = sum(w.get(k, 0) * v for k, v in measured.items()) / wsum

    unmeasured = sorted((set(w) | set(phases)) - set(measured))
    flags = []
    if tl.get("duration_sec", 0) > scope["target_duration_sec"]:
        flags.append("over_target_duration")
    return {"bounded": True, "overall_pct": round(overall * 100, 1),
            "phases": {k: round(v * 100, 1) for k, v in measured.items()},
            "measured_phases": sorted(measured), "unmeasured_phases": unmeasured, "flags": flags}

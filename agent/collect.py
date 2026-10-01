#!/usr/bin/env python3
"""dispatcher: เลือก collector ตาม stage → คืน progress รูปเดียวกัน (normalized)
normalized = {ok, stage, project, job_hint, scope_hint, pct, done, total, detail, raw, [page]}

stage (pipeline order): data → conform → color → subtitle → master
  conform/color  → Resolve (resolve_poller + scope)
  data/master    → fs_collector (นับไฟล์ที่ผลิต)
  subtitle       → subtitle_collector (นับ cue)
"""
STAGES = ["data", "conform", "color", "subtitle", "master"]

_ALIAS = {"colorist": "color", "grade": "color", "grading": "color",
          "edit": "conform", "online": "conform", "editorial": "conform",
          "mastering": "master",
          "dit": "data", "data management": "data", "datamgmt": "data", "ingest": "data",
          "subtitles": "subtitle", "caption": "subtitle", "captions": "subtitle"}


def canon(s):
    return _ALIAS.get((s or "").strip().lower(), (s or "").strip().lower())


def collect(stage):
    s = canon(stage)
    if s in ("conform", "color"):
        return _resolve(s)
    if s in ("data", "master"):
        import fs_collector
        return fs_collector.collect(s)
    if s == "subtitle":
        import subtitle_collector
        return subtitle_collector.collect(s)
    return {"ok": False, "stage": s, "reason": "unknown_stage", "project": None, "job_hint": None}


def _resolve(stage):
    import resolve_poller, scope
    snap = resolve_poller.snapshot()
    if not snap.get("ok"):
        return {"ok": False, "stage": stage, "reason": snap.get("reason"),
                "project": None, "job_hint": None, "raw": snap}
    tl = snap.get("timeline", {}) or {}
    if stage == "conform":
        # goal = ความยาว offline edit (auto) · fallback scope registry · ไม่มี → ค่าดิบ (pct None)
        target = snap.get("offline_target_sec")
        sc = {"target_duration_sec": target} if target else (scope.load_scope(snap.get("project"), snap.get("scope_hint")) or {})
        prog = scope.compute_progress(snap, sc)
        pct = (prog.get("phases") or {}).get("edit")
        detail = {"duration_sec": tl.get("duration_sec"), "offline_target_sec": target,
                  "shots_seen": prog.get("shots_seen"), "parts": prog.get("edit_detail")}
    else:  # color — graded/total clips (self-defined, ไม่ต้อง target)
        prog = scope.compute_progress(snap, {})
        pct = (prog.get("phases") or {}).get("color")
        detail = {"graded_clips": tl.get("graded_clips"), "video_clips": tl.get("video_clips")}
    return {"ok": True, "stage": stage, "project": snap.get("project"),
            "job_hint": snap.get("job_hint"), "scope_hint": snap.get("scope_hint"),
            "pct": pct, "done": None, "total": None, "detail": detail,
            "raw": snap, "page": snap.get("page")}


if __name__ == "__main__":
    import json, os
    print(json.dumps(collect(os.environ.get("EDITORTRACK_STAGE", "conform")), ensure_ascii=False, indent=2, default=str))

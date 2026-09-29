#!/usr/bin/env python3
"""A1 — snapshot metric จาก DaVinci Resolve Studio (read-only, ไม่ใช้ LLM).
ดึง @scope / @job / @role markers ออกมาเป็น hint ให้ scope.py และ joblink.py ด้วย."""
import os, sys, datetime, re

DETECT_GRADE = False  # เปิดหลัง probe.py ยืนยัน GetNodeGraph บนเวอร์ชันคุณ (color เป็นเฟสหลัก!)


def _load_resolve():
    api = os.environ.get(
        "RESOLVE_SCRIPT_API",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
    )
    mod = os.path.join(api, "Modules")
    if mod not in sys.path:
        sys.path.append(mod)
    try:
        import DaVinciResolveScript as bmd
    except ImportError:
        return None
    return bmd.scriptapp("Resolve")


def snapshot():
    resolve = _load_resolve()
    if resolve is None:
        return {"ok": False, "reason": "resolve_not_running"}
    pm = resolve.GetProjectManager()
    proj = pm.GetCurrentProject() if pm else None
    if proj is None:
        return {"ok": False, "reason": "no_project_open", "page": resolve.GetCurrentPage()}

    out = {
        "ok": True,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "page": resolve.GetCurrentPage(),
        "project": proj.GetName(),
        "timeline_count": proj.GetTimelineCount(),
        "rendering": bool(proj.IsRenderingInProgress()),
    }
    tl = proj.GetCurrentTimeline()
    if tl:
        try:
            fps = float(proj.GetSetting("timelineFrameRate")) or 24.0
        except Exception:
            fps = 24.0
        frames = (tl.GetEndFrame() or 0) - (tl.GetStartFrame() or 0)
        vtracks = tl.GetTrackCount("video") or 0
        atracks = tl.GetTrackCount("audio") or 0
        vclips, graded = 0, 0
        for i in range(1, vtracks + 1):
            items = tl.GetItemListInTrack("video", i) or []   # NOTE: transition ไม่รวม
            vclips += len(items)
            if DETECT_GRADE:
                for it in items:
                    if _is_graded(it):
                        graded += 1
        out["timeline"] = {
            "name": tl.GetName(),
            "duration_sec": round(frames / fps, 2) if fps else None,
            "fps": fps, "video_tracks": vtracks, "audio_tracks": atracks,
            "video_clips": vclips,
            "graded_clips": graded if DETECT_GRADE else None,
            "markers": len(tl.GetMarkers() or {}),
        }
        out["scope_hint"] = _marker_kv(tl, "scope")
        out["job_hint"] = _job_hint(tl)

    jobs = proj.GetRenderJobList() or []
    comps = []
    for j in jobs:
        jid = j.get("JobId")                       # PROBE: ยืนยันชื่อ key
        st = proj.GetRenderJobStatus(jid) if jid else {}
        comps.append(st.get("CompletionPercentage", 0))
    out["render"] = {"jobs": len(jobs), "max_completion": max(comps) if comps else 0,
                     "any_complete": any(c >= 100 for c in comps)}
    return out


def _is_graded(item):
    """HEURISTIC + version-dependent — เปิดหลัง probe. เกณฑ์: color node > 1."""
    try:
        g = item.GetNodeGraph()
        n = g.GetNumNodes() if g else 0
        return bool(n and n > 1)
    except Exception:
        return False


def _iter_marker_text(tl):
    for _, m in (tl.GetMarkers() or {}).items():
        yield f"{m.get('name', '')} {m.get('note', '')}"


def _marker_kv(tl, tag):
    """'@scope duration=30 shots=12' → {'duration':'30','shots':'12'}"""
    for text in _iter_marker_text(tl):
        if f"@{tag}" in text:
            kv = {}
            for tok in text.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    kv[k.strip()] = v.strip()
            return kv or None
    return None


def _job_hint(tl):
    """'@job PROMO_Q4' / '@role colorist' → {'job':..,'role':..}"""
    hint = {}
    for text in _iter_marker_text(tl):
        mj = re.search(r"@job\s+(\S+)", text)
        mr = re.search(r"@role\s+(\S+)", text)
        if mj:
            hint["job"] = mj.group(1)
        if mr:
            hint["role"] = mr.group(1)
    return hint or None


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))

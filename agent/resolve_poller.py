#!/usr/bin/env python3
"""A1 — snapshot metric จาก DaVinci Resolve Studio (read-only, ไม่ใช้ LLM).
ดึง @scope / @job / @role markers ออกมาเป็น hint ให้ scope.py และ joblink.py ด้วย."""
import os, sys, time, datetime, re

def _envflag(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")

# ตั้งค่าต่อเครื่องผ่าน env ได้ — ห้อง color กับ ห้อง edit ใช้ค่าต่างกัน
DETECT_GRADE = _envflag("EDITORTRACK_DETECT_GRADE", True)    # color room=on · **edit room ตั้ง =0** (ไม่ต้องสแกนสี → poll เบา)
POLL_RENDER = _envflag("EDITORTRACK_POLL_RENDER", False)     # ⚠️ on = GetRenderJobList เด้งหน้า Deliver → รบกวน worker (deliver ไม่ใช้แล้ว)
EXTRACT_SHOTS = _envflag("EDITORTRACK_EXTRACT_SHOTS", True)  # ดึง shots จาก marker (code + graded/conformed)
RESTORE_PAGE = _envflag("EDITORTRACK_RESTORE_PAGE", True)    # กันเหนียว: ถ้าหน้าเปลี่ยนระหว่าง poll → คืนหน้าเดิม
EXTRACT_CLIP_META = _envflag("EDITORTRACK_EXTRACT_CLIP_META", False)  # ⚠️ หนัก: ดึง meta ราย clip (source/metadata/node label/version/fusion) — เปิดเฉพาะตอนต้องการ detail ลึก + ยืนยันด้วย probe ก่อน
try:
    CLIP_META_MAX = int(os.environ.get("EDITORTRACK_CLIP_META_MAX", "0") or 0)  # จำกัดจำนวน clip ที่ดึง detail (0 = ทั้งหมด) — กัน payload บวมบน timeline ใหญ่
except ValueError:
    CLIP_META_MAX = 0


def _safe(obj, method, *args):
    """เรียก obj.method(*args) แบบปลอดภัย (รวม getattr) → ค่า หรือ None ถ้าไม่มี/เรียกไม่ผ่าน."""
    try:
        return getattr(obj, method)(*args)
    except Exception:
        return None


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
    start_page = out["page"]                      # จำหน้าเดิมไว้ (กันเหนียว)
    tl = proj.GetCurrentTimeline()
    if tl:
        try:
            fps = float(proj.GetSetting("timelineFrameRate")) or 24.0
        except Exception:
            fps = 24.0
        frames = (tl.GetEndFrame() or 0) - (tl.GetStartFrame() or 0)
        vtracks = tl.GetTrackCount("video") or 0
        atracks = tl.GetTrackCount("audio") or 0
        t0 = time.time()
        vclips, graded = 0, 0
        ranges = []          # (start, end, graded) ต่อ clip — ใช้ match marker→shot (สแกนรอบเดียว)
        clips_detail = []    # เปิดด้วย EXTRACT_CLIP_META เท่านั้น (ดึง meta ลึกราย clip)
        for i in range(1, vtracks + 1):
            items = tl.GetItemListInTrack("video", i) or []   # NOTE: transition ไม่รวม
            vclips += len(items)
            for it in items:
                g = _is_graded(it) if DETECT_GRADE else None
                if g:
                    graded += 1
                if EXTRACT_SHOTS:
                    try:
                        ranges.append((it.GetStart(), it.GetEnd(), g))
                    except Exception:
                        pass
                if EXTRACT_CLIP_META and (CLIP_META_MAX == 0 or len(clips_detail) < CLIP_META_MAX):
                    clips_detail.append(_clip_detail(it, "V%d" % i))
        scan_ms = int((time.time() - t0) * 1000)
        out["timeline"] = {
            "name": tl.GetName(),
            "duration_sec": round(frames / fps, 2) if fps else None,
            "fps": fps, "video_tracks": vtracks, "audio_tracks": atracks,
            "video_clips": vclips,
            "graded_clips": graded if DETECT_GRADE else None,
            "markers": len(tl.GetMarkers() or {}),
            "scan_ms": scan_ms,          # ⏱️ เวลาสแกน clip ทั้งหมด (perf)
        }
        out["scope_hint"] = _marker_kv(tl, "scope")
        out["job_hint"] = _job_hint(tl)
        out["offline_target_sec"] = _offline_target(proj, fps, tl.GetName())   # goal ของ conform
        if EXTRACT_SHOTS:
            out["shots"] = _shots_from_markers(tl, ranges)
        if EXTRACT_CLIP_META:
            out["clips_detail"] = clips_detail
            out["clips_detail_count"] = len(clips_detail)

    # render status: IsRenderingInProgress (บน out["rendering"] แล้ว) ปลอดภัย ไม่เปลี่ยนหน้า
    # แต่ GetRenderJobList/GetRenderJobStatus เปลี่ยนหน้าเป็น Deliver → ปิดไว้ (POLL_RENDER)
    if POLL_RENDER:
        jobs = proj.GetRenderJobList() or []       # ⚠️ เด้งไปหน้า Deliver — ใช้เฉพาะเครื่องที่ปลอดภัย
        comps = []
        for j in jobs:
            jid = j.get("JobId")                   # PROBE: ยืนยันชื่อ key
            st = proj.GetRenderJobStatus(jid) if jid else {}
            comps.append(st.get("CompletionPercentage", 0))
        out["render"] = {"jobs": len(jobs), "max_completion": max(comps) if comps else 0,
                         "any_complete": any(c >= 100 for c in comps), "polled": True}
    else:
        out["render"] = {"jobs": None, "max_completion": None, "polled": False}

    # กันเหนียว: ถ้ามี call ไหนแอบเปลี่ยนหน้าระหว่าง poll → คืนหน้าเดิมให้ worker
    if RESTORE_PAGE and start_page:
        try:
            if resolve.GetCurrentPage() != start_page:
                resolve.OpenPage(start_page)
                out["page_restored"] = start_page
        except Exception:
            pass
    return out


def _is_graded(item):
    """HEURISTIC + version-dependent — เปิดหลัง probe. เกณฑ์: color node > 1."""
    try:
        g = item.GetNodeGraph()
        n = g.GetNumNodes() if g else 0
        return bool(n and n > 1)
    except Exception:
        return False


# คีย์ ClipProperty ที่สนใจ (GetClipProperty() no-arg คืน dict ทั้งหมด → เลือกเฉพาะนี้ กัน payload บวม)
_SRC_KEYS = ("Resolution", "Video Codec", "FPS", "File Path", "Usage", "Date Created", "Type")


def _color_detail(item):
    """ชั้นสีราย clip: node count + label + LUT + versions + group (ทุกตัว 🟠 version-dependent → safe)."""
    c = {}
    g = _safe(item, "GetNodeGraph")
    if g is not None:
        n = _safe(g, "GetNumNodes")
        if isinstance(n, int):
            c["nodes"] = n
            c["graded"] = n > 1
            labels, luts = [], {}
            for i in range(1, n + 1):
                lb = _safe(g, "GetNodeLabel", i)
                if lb:
                    labels.append(lb)
                lut = _safe(g, "GetLUT", i)
                if lut and lut not in ("", "None"):
                    luts[str(i)] = lut
            if labels:
                c["node_labels"] = labels
            if luts:
                c["luts"] = luts
    vers = _safe(item, "GetVersionNameList", 0)   # 0 = local versions
    if vers:
        c["versions"] = vers
    grp = _safe(item, "GetColorGroup")
    if grp is not None:
        c["color_group"] = _safe(grp, "GetName") or str(grp)
    return c or None


def _clip_detail(item, track):
    """ดึง meta ราย clip แบบ safe ทุก method (เปิดด้วย EXTRACT_CLIP_META).
    ฟิลด์ที่หายไป = เวอร์ชันนี้ไม่คืนค่า/ทีมไม่ได้ใส่ (ดู probe §5d ว่าตัวไหนใช้ได้จริง)."""
    d = {"name": _safe(item, "GetName"), "track": track,
         "start": _safe(item, "GetStart"), "end": _safe(item, "GetEnd"),
         "duration": _safe(item, "GetDuration"),
         "left_offset": _safe(item, "GetLeftOffset"), "right_offset": _safe(item, "GetRightOffset"),
         "clip_color": _safe(item, "GetClipColor"), "flags": _safe(item, "GetFlagList")}
    cm = _safe(item, "GetMarkers")
    if isinstance(cm, dict) and cm:
        d["markers"] = [{"frame": k, "name": v.get("name"), "color": v.get("color"), "note": v.get("note")}
                        for k, v in cm.items()]
    mp = _safe(item, "GetMediaPoolItem")
    if mp is not None:
        props = _safe(mp, "GetClipProperty")      # no-arg → dict ทั้งหมด
        if isinstance(props, dict):
            src = {k: props.get(k) for k in _SRC_KEYS if props.get(k)}
            if src:
                d["source"] = src
        meta = _safe(mp, "GetMetadata")           # no-arg → dict (🟠 บางเวอร์ชันต้องใส่ key ทีละตัว)
        if isinstance(meta, dict):
            meta = {k: v for k, v in meta.items() if v}
            if meta:
                d["metadata"] = meta
    color = _color_detail(item)
    if color:
        d["color"] = color
    fc = _safe(item, "GetFusionCompCount")
    if fc:
        d["fusion_comps"] = fc
    return {k: v for k, v in d.items() if v not in (None, [], {})}


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


OFFLINE_MATCH = os.environ.get("EDITORTRACK_OFFLINE_MATCH", r"offline|editorial|_ref\b|reference|picture\s*lock|ผ่านตัด")


def _offline_target(proj, fps, current_name):
    """goal ของ conform = ความยาว 'offline/editorial timeline' ในโปรเจค (วินาที).
    หา timeline ที่ชื่อ match OFFLINE_MATCH (ข้ามไทม์ไลน์ที่กำลังทำ) → duration.
    ⚠️ ต้อง validate โครงสร้างจริง (ชื่อ offline timeline) ด้วย probe — คืน None ถ้าไม่เจอ → conform เป็นค่าดิบ."""
    try:
        n = proj.GetTimelineCount() or 0
    except Exception:
        return None
    for i in range(1, n + 1):
        try:
            tl = proj.GetTimelineByIndex(i)       # ไม่เปลี่ยน current timeline (อ่านอย่างเดียว)
            name = tl.GetName() or ""
            if name == current_name:
                continue
            if re.search(OFFLINE_MATCH, name, re.I):
                frames = (tl.GetEndFrame() or 0) - (tl.GetStartFrame() or 0)
                return round(frames / (fps or 24.0), 2) if frames > 0 else None
        except Exception:
            continue
    return None


def _shots_from_markers(tl, ranges):
    """marker name = shot code → จับคู่ clip ที่ frame นั้น → graded ต่อ shot (match ในหน่วยความจำ)."""
    tl_start = tl.GetStartFrame() or 0
    shots = []
    for off, m in (tl.GetMarkers() or {}).items():
        code = (m.get("name") or "").strip()
        if not code or re.match(r"^Marker\s+\d+$", code, re.I):   # ข้าม marker default (ไม่ใช่ shot code)
            continue
        f = tl_start + off                       # marker key = offset จาก timeline start
        graded = None
        for s, e, g in ranges:
            if s <= f < e:
                graded = g
                break
        shots.append({"code": code, "frame": f, "graded": graded, "color": m.get("color")})
    return shots


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

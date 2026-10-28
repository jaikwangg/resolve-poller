#!/usr/bin/env python3
"""A1 — snapshot metric จาก DaVinci Resolve Studio (read-only, ไม่ใช้ LLM).
ดึง @scope / @job / @role markers ออกมาเป็น hint ให้ scope.py และ joblink.py ด้วย."""
import os, sys, time, datetime, re
from grade_detect import Detector
from resolve_guard import guard, Ctx, ForbiddenCall, ScanAborted

def _envflag(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _envint(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# ตั้งค่าต่อเครื่องผ่าน env ได้ — ห้อง color กับ ห้อง edit ใช้ค่าต่างกัน
DETECT_GRADE = _envflag("EDITORTRACK_DETECT_GRADE", True)    # color room=on · **edit room ตั้ง =0** (ไม่ต้องสแกนสี → poll เบา)
# shot codes = client data: off by default (docs/DATA_SHEET.md); turning it on needs the client/owner consent first
EXTRACT_SHOTS = _envflag("EDITORTRACK_EXTRACT_SHOTS", False)  # ดึง shots จาก marker (code + graded/conformed)
EXTRACT_CLIP_META = _envflag("EDITORTRACK_EXTRACT_CLIP_META", False)  # ⚠️ หนัก: ดึง meta ราย clip (source/metadata/node label/version/fusion) — เปิดเฉพาะตอนต้องการ detail ลึก + ยืนยันด้วย probe ก่อน
CLIP_META_MAX = _envint("EDITORTRACK_CLIP_META_MAX", 0)       # จำกัดจำนวน clip ที่ดึง detail (0 = ทั้งหมด)
# REMOVED (zero-incident, 2026-10-26): EDITORTRACK_POLL_RENDER (GetRenderJobList jumped to the Deliver
# page on a real workstation) and EDITORTRACK_RESTORE_PAGE (called OpenPage = itself a UI change, and
# could yank an editor back if they switched page during a scan). Neither can be re-enabled by env.

# ---- safety budget (resolve_guard enforces per call) ----
SCAN_BUDGET_MS = _envint("EDITORTRACK_SCAN_BUDGET_MS", 1500)   # wall time for all Resolve calls in one poll
MAX_CALLS = _envint("EDITORTRACK_MAX_CALLS", 20000)            # API calls per poll
MAX_CLIPS = _envint("EDITORTRACK_MAX_CLIPS", 5000)             # above this: counts only, no per-clip calls
MIN_IDLE_SEC = _envint("EDITORTRACK_SCAN_MIN_IDLE_SEC", 5)     # scan only when no input for this long
PLAYBACK_PROBE_MS = _envint("EDITORTRACK_PLAYBACK_PROBE_MS", 250)
CHUNK_CLIPS = _envint("EDITORTRACK_CHUNK_CLIPS", 200)          # every N clips: yield to Resolve + re-check user input
YIELD_MS = _envint("EDITORTRACK_YIELD_MS", 10)
SKIP_PAGES = {p.strip() for p in os.environ.get("EDITORTRACK_SKIP_PAGES", "deliver").split(",") if p.strip()}
# Resolve versions verified on a dummy project (phase 3). Unknown version -> no scan (fail closed).
VERIFIED_VERSIONS = [v.strip() for v in os.environ.get("EDITORTRACK_RESOLVE_VERIFIED", "").split(",") if v.strip()]


def _safe(obj, method, *args):
    """เรียก obj.method(*args) แบบปลอดภัย (รวม getattr) → ค่า หรือ None ถ้าไม่มี/เรียกไม่ผ่าน.
    ForbiddenCall / ScanAborted ไม่ถูกกลืน — ต้องหยุดทั้งรอบ"""
    try:
        return getattr(obj, method)(*args)
    except (ForbiddenCall, ScanAborted):
        raise
    except Exception:
        return None


def _load_resolve(ctx=None):
    """คืน Resolve ที่ห่อด้วย resolve_guard เสมอ (ไม่มีทางได้ object ดิบ)."""
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
    raw = bmd.scriptapp("Resolve")
    return guard(raw, ctx if ctx is not None else Ctx())


def version_verified(version):
    """'20.2.1.5' ok ถ้า EDITORTRACK_RESOLVE_VERIFIED มี '20.2' หรือ '20.2.1' (prefix ทีละส่วน)."""
    if not version:
        return False
    parts = str(version).strip().split(".")
    for v in VERIFIED_VERSIONS:
        vp = v.split(".")
        if parts[:len(vp)] == vp:
            return True
    return False


def _skip(reason, ctx, **kw):
    return {"ok": False, "reason": reason, "skipped": True, "guard": _guard_info(ctx), **kw}


def _guard_info(ctx):
    return {"calls": ctx.calls, "ms": int(ctx.elapsed_ms()), "aborted": ctx.aborted,
            "violations": list(ctx.violations)}


def snapshot(idle_fn=None, sleep=time.sleep, load=None):
    """1 poll. Fail closed: any doubt -> {"ok": False, "skipped": True, reason} with no further calls.
    idle_fn() -> วินาทีที่ไม่มี input (None = ไม่รู้ → ไม่สแกน)."""
    ctx = Ctx(deadline_ms=SCAN_BUDGET_MS, max_calls=MAX_CALLS)
    try:
        return _snapshot(ctx, idle_fn, sleep, load)
    except ForbiddenCall as e:
        return {"ok": False, "reason": "forbidden_call", "forbidden": str(e), "guard": _guard_info(ctx)}
    except ScanAborted as e:
        if str(e) == "user_returned":                 # ผู้ใช้กลับมาแตะเครื่องระหว่างสแกน → หยุดทันที (ไม่ใช่ error)
            return _skip("user_returned", ctx)
        return _skip("scan_budget", ctx, budget=str(e))


def _snapshot(ctx, idle_fn, sleep, load):
    resolve = (load or _load_resolve)(ctx)
    if resolve is None:
        return {"ok": False, "reason": "resolve_not_running", "guard": _guard_info(ctx)}
    product = _safe(resolve, "GetProductName")
    version = _safe(resolve, "GetVersionString")
    if not product or "Studio" not in str(product):
        return _skip("not_studio", ctx, product=product)
    if not version_verified(version):
        return _skip("resolve_version_unverified", ctx, version=version)
    pm = resolve.GetProjectManager()
    proj = pm.GetCurrentProject() if pm else None
    if proj is None:
        return {"ok": False, "reason": "no_project_open", "page": _safe(resolve, "GetCurrentPage"),
                "guard": _guard_info(ctx)}
    start_page = resolve.GetCurrentPage()
    if not start_page:
        return _skip("resolve_busy", ctx)                     # loading / modal / quitting
    if start_page in SKIP_PAGES:
        return _skip("page_" + start_page, ctx, page=start_page)
    if proj.IsRenderingInProgress():
        return _skip("rendering", ctx, page=start_page)
    idle0 = idle_fn() if idle_fn else None
    if idle0 is None:
        return _skip("idle_unknown", ctx, page=start_page)
    if idle0 < MIN_IDLE_SEC:
        return _skip("user_active", ctx, page=start_page)
    tl = proj.GetCurrentTimeline()
    if tl:   # playback: playhead moves between two reads → ไม่สแกนระหว่างเล่น (เฟรมตกไม่ได้)
        tc1 = _safe(tl, "GetCurrentTimecode")
        sleep(PLAYBACK_PROBE_MS / 1000.0)
        tc2 = _safe(tl, "GetCurrentTimecode")
        if tc1 is None or tc1 != tc2:
            return _skip("playback" if tc1 is not None else "timecode_unknown", ctx, page=start_page)
    pname = proj.GetName()

    out = {
        "ok": True,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "page": start_page,
        "project": pname,
        "resolve_version": version,
        "timeline_count": proj.GetTimelineCount(),
        "rendering": False,
    }
    tl_id = None
    if tl:
        tl_id = _safe(tl, "GetUniqueId") or tl.GetName()
        out["timeline"], extra = _scan_timeline(resolve, proj, tl, pname, ctx, idle_fn, idle0, sleep)
        out.update(extra)

    # post-check: ถ้าระหว่างสแกน project/timeline/หน้าเปลี่ยน → ทิ้งผลรอบนี้ (ไม่คืนหน้าให้ — ห้ามแตะ UI)
    end_page = resolve.GetCurrentPage()
    pm2 = resolve.GetProjectManager()
    proj2 = pm2.GetCurrentProject() if pm2 else None
    same_proj = proj2 is not None and proj2.GetName() == pname
    tl2 = proj2.GetCurrentTimeline() if same_proj else None
    same_tl = (tl2 is None and tl_id is None) or (tl2 is not None and (_safe(tl2, "GetUniqueId") or tl2.GetName()) == tl_id)
    if end_page != start_page or not same_proj or not same_tl:
        idle1 = idle_fn() if idle_fn else None
        scan_s = ctx.elapsed_ms() / 1000.0
        # user ไม่ได้แตะเครื่องตลอดช่วงสแกน แต่หน้าเปลี่ยน → สงสัยว่าเป็นเพราะ agent → trip
        suspect = idle1 is not None and idle1 >= idle0 + scan_s - 1
        return {"ok": False, "reason": "state_changed_during_scan", "suspect_agent": bool(suspect),
                "page_before": start_page, "page_after": end_page, "guard": _guard_info(ctx)}
    out["render"] = {"jobs": None, "max_completion": None, "polled": False}
    out["guard"] = _guard_info(ctx)
    return out


def _scan_timeline(resolve, proj, tl, pname, ctx, idle_fn=None, idle0=None, sleep=time.sleep):
    try:
        fps = float(proj.GetSetting("timelineFrameRate")) or 24.0
    except (ForbiddenCall, ScanAborted):
        raise
    except Exception:
        fps = 24.0
    t_start = tl.GetStartFrame() or 0
    frames = (tl.GetEndFrame() or 0) - t_start
    vtracks = tl.GetTrackCount("video") or 0
    atracks = tl.GetTrackCount("audio") or 0
    t0 = time.time()
    tracks = [(i, tl.GetItemListInTrack("video", i) or []) for i in range(1, vtracks + 1)]  # NOTE: transition ไม่รวม
    vclips = sum(len(items) for _, items in tracks)
    tl_name = tl.GetName()
    markers = tl.GetMarkers() or {}
    per_clip = vclips <= MAX_CLIPS
    det = Detector(resolve, tl, key=f"{pname}/{_safe(tl, 'GetUniqueId') or tl_name}") if (DETECT_GRADE and per_clip) else None
    graded = 0
    ranges = []          # (start, end, graded) ต่อ clip — ใช้ match marker→shot (สแกนรอบเดียว)
    clips_detail = []    # เปิดด้วย EXTRACT_CLIP_META เท่านั้น (ดึง meta ลึกราย clip)
    try:
        if per_clip:
            n = 0
            for i, items in tracks:
                for it in items:
                    n += 1
                    if CHUNK_CLIPS and n % CHUNK_CLIPS == 0:
                        sleep(YIELD_MS / 1000.0)          # ปล่อย main thread ของ Resolve เป็นช่วง ๆ
                        now_idle = idle_fn() if idle_fn else None
                        if now_idle is None or (idle0 is not None and now_idle < idle0):
                            raise ScanAborted("user_returned")
                    g = det.graded(it) if det else None
                    if g:
                        graded += 1
                    if EXTRACT_SHOTS:
                        s, e = _safe(it, "GetStart"), _safe(it, "GetEnd")
                        if s is not None and e is not None:
                            ranges.append((s, e, g))
                    if EXTRACT_CLIP_META and (CLIP_META_MAX == 0 or len(clips_detail) < CLIP_META_MAX):
                        clips_detail.append(_clip_detail(it, "V%d" % i))
        grade_info = det.finish() if det else None
    finally:
        if det:
            det.close()                       # สแกนหยุดกลางทาง (ScanAborted ฯลฯ) → temp ไม่ค้าง
    scan_ms = int((time.time() - t0) * 1000)
    timeline = {
        "name": tl_name,
        "duration_sec": round(frames / fps, 2) if fps else None,
        "fps": fps, "video_tracks": vtracks, "audio_tracks": atracks,
        "video_clips": vclips,
        "graded_clips": graded if (DETECT_GRADE and per_clip) else None,
        "markers": len(markers),
        "scan_ms": scan_ms,          # ⏱️ เวลาสแกน clip ทั้งหมด (perf)
        "grade": grade_info,         # method ที่ใช้จริง (lut/nodes) + exported/pending/failed/ms
        "per_clip": per_clip,        # False = timeline ใหญ่เกิน MAX_CLIPS → นับอย่างเดียว
    }
    extra = {"scope_hint": _marker_kv(markers, "scope"), "job_hint": _job_hint(markers),
             "offline_target_sec": _offline_target(proj, fps, tl_name)}   # goal ของ conform
    if EXTRACT_SHOTS:
        extra["shots"] = _shots_from_markers(t_start, markers, ranges)
    if EXTRACT_CLIP_META:
        extra["clips_detail"] = clips_detail
        extra["clips_detail_count"] = len(clips_detail)
    return timeline, extra


# คีย์ ClipProperty ที่สนใจ (GetClipProperty() no-arg คืน dict ทั้งหมด → เลือกเฉพาะนี้ กัน payload บวม)
_SRC_KEYS = ("Resolution", "Video Codec", "FPS", "File Path", "Usage", "Date Created", "Type")


def _color_detail(item):
    """ชั้นสีราย clip: node count + label + versions + group (ไม่เก็บชื่อ LUT) (ทุกตัว 🟠 version-dependent → safe)."""
    c = {}
    g = _safe(item, "GetNodeGraph")
    if g is not None:
        n = _safe(g, "GetNumNodes")
        if isinstance(n, int):
            c["nodes"] = n
            c["graded"] = n > 1
            labels = []
            for i in range(1, n + 1):
                lb = _safe(g, "GetNodeLabel", i)
                if lb:
                    labels.append(lb)
            if labels:
                c["node_labels"] = labels
            # LUT names are never collected (data minimisation, docs/DATA_SHEET.md)
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


def _iter_marker_text(markers):
    for _, m in (markers or {}).items():
        yield f"{m.get('name', '')} {m.get('note', '')}"


def _marker_kv(markers, tag):
    """'@scope duration=30 shots=12' → {'duration':'30','shots':'12'}"""
    for text in _iter_marker_text(markers):
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
    except (ForbiddenCall, ScanAborted):
        raise
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
        except (ForbiddenCall, ScanAborted):
            raise
        except Exception:
            continue
    return None


def _shots_from_markers(tl_start, markers, ranges):
    """marker name = shot code → จับคู่ clip ที่ frame นั้น → graded ต่อ shot (match ในหน่วยความจำ)."""
    shots = []
    for off, m in (markers or {}).items():
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


def _job_hint(markers):
    """'@job PROMO_Q4' / '@role colorist' → {'job':..,'role':..}"""
    hint = {}
    for text in _iter_marker_text(markers):
        mj = re.search(r"@job\s+(\S+)", text)
        mr = re.search(r"@role\s+(\S+)", text)
        if mj:
            hint["job"] = mj.group(1)
        if mr:
            hint["role"] = mr.group(1)
    return hint or None


def idle_seconds():
    """idle จาก activity_sampler (OS เท่านั้น ไม่แตะ Resolve) — None ถ้าอ่านไม่ได้"""
    try:
        from activity_sampler import sample
        return sample().get("idle_sec")
    except Exception:
        return None


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(idle_fn=idle_seconds), ensure_ascii=False, indent=2))

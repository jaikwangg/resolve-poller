#!/usr/bin/env python3
"""
probe.py — ตรวจว่า DaVinci Resolve Scripting API บนเครื่อง+เวอร์ชันของคุณ
"เผยอะไรได้จริงบ้าง" ก่อนจะ trust Track A — โดยเฉพาะ COLOR และ AUDIO ที่ยังไม่แน่ใจ

รันขณะ Resolve Studio เปิด + มีโปรเจค+timeline (มี video clip, audio clip, และ
render job 1 ตัวในคิว จะยืนยันได้ครบสุด):
    python3 probe.py

ผลลัพธ์: พิมพ์เป็น section + capability map + สรุป + config ที่ต้องแปะ
และเซฟ ~/.editortrack/probe_report.json

ทุกการเรียกหุ้ม try/except — สคริปต์นี้จะไม่พังไม่ว่าเวอร์ชันไหน
"""
import os, sys, json, datetime, pathlib

REPORT = {"ts": datetime.datetime.now().isoformat(), "checks": {}, "methods": {}, "color": {}, "audio": {}}


# ---------- helpers ----------
def h(title):
    print("\n" + "=" * 68 + f"\n  {title}\n" + "=" * 68)


def sub(title):
    print(f"\n  --- {title} ---")


def call(obj, method, *args, show=True):
    """เรียก obj.method(*args) แบบปลอดภัย → (ok, value|None)."""
    try:
        fn = getattr(obj, method)
    except Exception as e:
        if show: print(f"    ✗ {method:<26} — ไม่มี method นี้")
        return False, None
    try:
        val = fn(*args)
    except Exception as e:
        if show: print(f"    ✗ {method:<26} — เรียกไม่ผ่าน ({str(e)[:40]})")
        return False, None
    if show:
        s = repr(val)
        print(f"    ✓ {method:<26} = {s[:82]}{'…' if len(s) > 82 else ''}")
    return True, val


def probe_list(obj, specs, label, store_key=None):
    """specs: list ของ (method,) หรือ (method, (args...)). คืน dict ผลลัพธ์."""
    sub(label)
    res = {}
    for spec in specs:
        m = spec[0]; args = spec[1] if len(spec) > 1 else ()
        ok, val = call(obj, m, *args)
        res[m] = {"ok": ok, "type": type(val).__name__ if ok else None}
    if store_key:
        REPORT["methods"][store_key] = res
    return res


def dump_dir(obj, label):
    """พยายาม dir() — remote proxy อาจไม่ list method จริง แต่ลองดู."""
    try:
        ms = sorted(m for m in dir(obj) if not m.startswith("_"))
        print(f"    dir() เห็น {len(ms)} ชื่อ: {', '.join(ms[:40])}{' …' if len(ms) > 40 else ''}")
        if len(ms) < 5:
            print("    (น้อย = เป็น remote proxy ตามคาด → เชื่อผล 'ลองเรียก' ด้านบนแทน)")
    except Exception as e:
        print(f"    dir() ไม่ได้: {e}")


def load_resolve():
    api = os.environ.get(
        "RESOLVE_SCRIPT_API",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
    )
    mod = os.path.join(api, "Modules")
    if mod not in sys.path:
        sys.path.append(mod)
    import DaVinciResolveScript as bmd
    return bmd.scriptapp("Resolve")


def save_and_exit(code=0):
    # คืนหน้าที่เปิดไว้ตอนเริ่ม (probe/§4 render อาจเด้งไป Deliver ชั่วคราว)
    try:
        r = globals().get("resolve")
        sp = globals().get("_START_PAGE")
        if r is not None and sp:
            r.OpenPage(sp)
            print(f"  ↩ คืนหน้าเดิม: {sp}")
    except Exception:
        pass
    out = os.path.expanduser("~/.editortrack/probe_report.json")
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(REPORT, open(out, "w"), ensure_ascii=False, indent=2, default=str)
    print(f"\n  เซฟรายงานที่ {out}")
    sys.exit(code)


# ---------- 1. connection ----------
h("1. ENVIRONMENT & CONNECTION")
resolve = None
try:
    resolve = load_resolve()
except ImportError as e:
    print(f"  ✗ import DaVinciResolveScript ไม่ได้: {e}")
    print("    → เช็ค RESOLVE_SCRIPT_API / PYTHONPATH (ดู Prerequisites ใน TRACK_A.md)")
if resolve is None:
    REPORT["checks"]["connect"] = False
    print("  ✗ เชื่อมต่อ Resolve ไม่ได้ — เปิด Resolve + external scripting=Local + เป็น Studio")
    save_and_exit(1)

REPORT["checks"]["connect"] = True
print("  ✓ เชื่อมต่อ Resolve สำเร็จ")
_START_PAGE = resolve.GetCurrentPage()   # จำหน้าเดิมไว้คืนตอนจบ (§4 อาจเด้งไป Deliver)
_, product = call(resolve, "GetProductName")
_, version = call(resolve, "GetVersionString")
REPORT["product"], REPORT["version"] = product, version
is_studio = bool(product and "Studio" in str(product))
REPORT["checks"]["studio"] = is_studio
print(f"  → Studio? {'✅' if is_studio else '❌ (external scripting ใช้ไม่ได้เต็ม)'}")


# ---------- 2. bundle id ----------
h("2. RESOLVE BUNDLE ID (สำหรับ activity_sampler)")
try:
    from AppKit import NSWorkspace
    found = []
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        nm = a.localizedName() or ""
        if "resolve" in nm.lower() or "davinci" in nm.lower():
            found.append(a.bundleIdentifier() or "")
            print(f"  ✓ {nm}  →  {a.bundleIdentifier()}")
    REPORT["checks"]["bundle_id"] = found
    if found:
        print(f"  → RESOLVE_BUNDLE_IDS = {{{', '.join(repr(b) for b in found)}}}")
    else:
        print("  ⚠ ไม่เจอ Resolve — เปิดโปรแกรมก่อน")
except ImportError:
    print("  ⚠ ไม่มี pyobjc (AppKit) — ข้ามได้ ถ้ายังไม่ทดสอบ A2")
    REPORT["checks"]["bundle_id"] = None


# ---------- 3. capability map: Resolve / Project / Timeline ----------
h("3. METHOD MAP — Resolve / Project / Timeline")
probe_list(resolve, [("GetCurrentPage",), ("GetProductName",), ("GetVersionString",),
                     ("GetProjectManager",)], "Resolve", "resolve")

pm = resolve.GetProjectManager()
proj = pm.GetCurrentProject() if pm else None
tl = None
if not proj:
    print("\n  ✗ ไม่มีโปรเจคเปิด — เปิดโปรเจคก่อนเพื่อ probe ส่วนที่เหลือ")
    save_and_exit(1)

probe_list(proj, [("GetName",), ("GetTimelineCount",), ("IsRenderingInProgress",),
                  ("GetCurrentRenderMode",), ("GetSetting", ("timelineFrameRate",)),
                  ("GetRenderJobList",), ("GetCurrentTimeline",)], "Project", "project")

tl = proj.GetCurrentTimeline()
if not tl:
    print("\n  ✗ ไม่มี timeline เปิด — เปิด timeline ก่อน")
    save_and_exit(1)

probe_list(tl, [("GetName",), ("GetStartFrame",), ("GetEndFrame",),
                ("GetTrackCount", ("video",)), ("GetTrackCount", ("audio",)),
                ("GetTrackCount", ("subtitle",)), ("GetMarkers",),
                ("GetCurrentTimecode",), ("GetCurrentVideoItem",),
                ("GetSetting", ("timelineFrameRate",)),
                ("GetTrackName", ("video", 1))], "Timeline", "timeline")

_, fps_s = call(tl, "GetSetting", "timelineFrameRate", show=False)
try:
    FPS = float(fps_s) or 24.0
except Exception:
    FPS = 24.0
print(f"\n  → fps = {FPS}")


# ---------- 4. render queue — ชื่อ key จริง ----------
h("4. RENDER QUEUE — ชื่อ key จริง")
print("  (ℹ️ ขั้นนี้จะเด้งไปหน้า Deliver ชั่วคราว — probe จะคืนหน้าเดิมให้ตอนจบ)")
_, jobs = call(proj, "GetRenderJobList", show=False)
jobs = jobs or []
print(f"  jobs ในคิว = {len(jobs)}")
if not jobs:
    print("  ⚠ คิวว่าง — ไป Deliver > Add to Render Queue 1 job แล้วรันใหม่ เพื่อยืนยัน key")
    REPORT["checks"]["render_keys"] = "empty_queue"
else:
    j0 = jobs[0]
    print(f"  ✓ job[0] full = {json.dumps(j0, ensure_ascii=False, default=str)[:300]}")
    jid = None
    for cand in ("JobId", "jobId", "id", "JobID", "RenderJobId"):
        if isinstance(j0, dict) and cand in j0:
            jid = j0[cand]; print(f"  ✓ job-id key = '{cand}'"); break
    if jid is not None:
        _, st = call(proj, "GetRenderJobStatus", jid, show=False)
        if isinstance(st, dict):
            print(f"  ✓ GetRenderJobStatus keys = {list(st.keys())}")
            print(f"    full = {json.dumps(st, ensure_ascii=False, default=str)}")
            REPORT["checks"]["render_keys"] = {"id_key": [c for c in ('JobId','jobId','id','JobID') if c in j0],
                                               "status_keys": list(st.keys())}
    else:
        print(f"  ⚠ หา job-id key ไม่เจอ — keys ที่มี: {list(j0.keys()) if isinstance(j0,dict) else type(j0)}")


# ---------- 5. COLOR — ลองทุกทาง ----------
h("5. COLOR / GRADE DETECTION — ลองทุก method")
# เก็บ video clip หลายตัว (สูงสุด 25) มาดูรวม
clips = []
for i in range(1, (tl.GetTrackCount("video") or 0) + 1):
    clips += (tl.GetItemListInTrack("video", i) or [])
print(f"  video clips = {len(clips)}")

if not clips:
    print("  ⚠ ไม่มี clip — วาง video clip ก่อน")
    REPORT["color"] = {"status": "no_clip"}
else:
    c0 = clips[0]
    sub(f"method บน TimelineItem (clip: {c0.GetName()})")
    tci = probe_list(c0, [
        ("GetName",), ("GetDuration",), ("GetStart",), ("GetEnd",),
        ("GetSourceStartFrame",), ("GetSourceEndFrame",),
        ("GetLeftOffset",), ("GetRightOffset",),
        ("GetClipColor",), ("GetFlagList",), ("GetMarkers",),
        ("GetFusionCompCount",), ("GetFusionCompNameList",),
        ("GetNodeGraph",), ("GetNumNodes",),
        ("GetVersionNameList", (0,)), ("GetVersionNameList", (1,)),
        ("GetCurrentVersionName",), ("GetMediaPoolItem",), ("GetProperty",),
        ("GetColorGroup",), ("GetNodeLabel", (1,)),
    ], "TimelineItem", "timelineitem")

    # 5a. ทาง node graph
    sub("ทาง A: node graph")
    graph_ok = False
    _, g = call(c0, "GetNodeGraph", show=False)
    if g is not None:
        graph_ok = True
        print("    ✓ GetNodeGraph() คืน object")
        probe_list(g, [("GetNumNodes",), ("GetNodeLabel", (1,)),
                       ("GetToolsInNode", (1,)), ("GetLUT", (1,)),
                       ("GetNodeCacheMode", (1,))], "Graph object", "graph")
    else:
        print("    ✗ GetNodeGraph() ใช้ไม่ได้บนเวอร์ชันนี้")

    # 5b. ทาง color versions
    sub("ทาง B: color versions")
    _, v0 = call(c0, "GetVersionNameList", 0, show=False)
    _, v1 = call(c0, "GetVersionNameList", 1, show=False)
    print(f"    local versions  = {v0}")
    print(f"    remote versions = {v1}")

    # 5c. distribution ของ node count ทั้ง timeline (ให้ user เห็นเกณฑ์ graded จริง)
    node_counts, ok_nodes = [], 0
    if graph_ok:
        sub("ทาง C: node-count distribution (ทั้ง timeline)")
        for c in clips[:25]:
            try:
                n = c.GetNodeGraph().GetNumNodes()
                if isinstance(n, int):
                    node_counts.append(n); ok_nodes += 1
            except Exception:
                pass
        if node_counts:
            gt1 = sum(1 for n in node_counts if n > 1)
            print(f"    อ่านได้ {ok_nodes}/{len(clips[:25])} clip · nodes: min={min(node_counts)} "
                  f"max={max(node_counts)} · clip ที่ nodes>1 = {gt1}/{len(node_counts)}")
            print(f"    → ถ้าใช้เกณฑ์ 'graded = nodes>1' จะได้ graded_clips = {gt1}")
    REPORT["color"] = {
        "node_graph": graph_ok,
        "num_nodes_readable": ok_nodes,
        "node_counts_sample": node_counts,
        "has_version_api": bool(v0 is not None),
        "clip_getnumnodes": tci.get("GetNumNodes", {}).get("ok"),
    }

    # verdict
    if graph_ok and ok_nodes:
        print("\n  → COLOR: ✅ ใช้ node graph ได้ → เปิด DETECT_GRADE=True (เกณฑ์ nodes>1, ดู distribution ประกอบ)")
    elif v0 is not None:
        print("\n  → COLOR: ⚠️ node graph ไม่ได้ แต่มี version API → detect 'graded' แบบหยาบจากจำนวน version ได้")
    else:
        print("\n  → COLOR: ❌ ยังดึงสถานะเกรดไม่ได้ — ปล่อย DETECT_GRADE=False (เฟส color ถูกข้ามใน %)")


# ---------- 5d. DEEP CLIP META — ยืนยัน EXTRACT_CLIP_META ----------
h("5d. DEEP CLIP META — ยืนยัน EXTRACT_CLIP_META (ราย clip + perf)")
if not clips:
    print("  ⚠ ไม่มี clip — ข้าม")
    REPORT["clip_meta"] = {"status": "no_clip"}
else:
    REPORT["clip_meta"] = {}
    c0 = clips[0]
    # 1) คีย์ทั้งหมดที่ MediaPoolItem เผย (เอาไว้จูน _SRC_KEYS / เลือก metadata)
    _, mp = call(c0, "GetMediaPoolItem", show=False)
    if mp is not None:
        _, allprops = call(mp, "GetClipProperty", show=False)   # no-arg → dict ทั้งหมด
        if isinstance(allprops, dict):
            print(f"  ✓ ClipProperty keys ({len(allprops)}): {list(allprops.keys())}")
            REPORT["clip_meta"]["clip_property_keys"] = list(allprops.keys())
        else:
            print(f"  ⚠ GetClipProperty() ไม่คืน dict (ได้ {type(allprops).__name__}) — เวอร์ชันนี้ต้องเรียกทีละ key")
        _, allmeta = call(mp, "GetMetadata", show=False)        # no-arg → dict (🟠)
        if isinstance(allmeta, dict):
            nonempty = [k for k, v in allmeta.items() if v]
            print(f"  ✓ Metadata keys ที่มีค่า ({len(nonempty)}): {nonempty}")
            REPORT["clip_meta"]["metadata_keys"] = nonempty
        else:
            print(f"  ⚠ GetMetadata() ไม่คืน dict (ได้ {type(allmeta).__name__}) — อาจต้องใส่ key ทีละตัว")
    else:
        print("  ⚠ GetMediaPoolItem() = None (clip นี้ไม่มี media pool item?)")

    # 2) รันโค้ดจริง resolve_poller._clip_detail กับ clip[0] → เห็นว่าได้ field ไหนจริง
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import resolve_poller as rp
        detail = rp._clip_detail(c0, "V1")
        print(f"\n  ✓ _clip_detail(clip[0]) → fields: {list(detail.keys())}")
        if "color" in detail:
            print(f"    color: {json.dumps(detail['color'], ensure_ascii=False, default=str)}")
        print("  full JSON (clip[0]):")
        print("    " + json.dumps(detail, ensure_ascii=False, default=str, indent=2).replace("\n", "\n    "))
        REPORT["clip_meta"]["sample_fields"] = list(detail.keys())
        REPORT["clip_meta"]["sample"] = detail

        # 3) perf: ดึง detail ครบทั้ง timeline — ตัวเลขนี้คือภาระจริงถ้าเปิด EXTRACT_CLIP_META
        import time as _t
        t0 = _t.time()
        for c in clips:
            rp._clip_detail(c, "V?")
        ms = int((_t.time() - t0) * 1000)
        per = round(ms / max(len(clips), 1), 1)
        print(f"\n  ⏱️ ดึง detail ครบ {len(clips)} clip ใช้ {ms} ms  (~{per} ms/clip)")
        print(f"    → เทียบ scan เบา (นับ node) ~0.2s · ถ้าหนักเกินให้ตั้ง EDITORTRACK_CLIP_META_MAX=<n>")
        REPORT["clip_meta"]["scan_ms_all"] = ms
        REPORT["clip_meta"]["scan_clips"] = len(clips)
        REPORT["clip_meta"]["ms_per_clip"] = per
    except Exception as e:
        print(f"  ✗ เรียก resolve_poller._clip_detail ไม่ผ่าน: {e}")
        REPORT["clip_meta"]["error"] = str(e)


# ---------- 6. AUDIO — ลองทุกทาง ----------
h("6. AUDIO COVERAGE — ลองทุก method")
a_tracks = tl.GetTrackCount("audio") or 0
print(f"  audio tracks = {a_tracks}")
a_items = []
for i in range(1, a_tracks + 1):
    items = tl.GetItemListInTrack("audio", i) or []
    a_items.append((i, items))

flat = [it for _, items in a_items for it in items]
print(f"  audio clips รวม = {len(flat)}")

if not flat:
    print("  ⚠ ไม่มี audio clip — วาง audio แล้วลองใหม่")
    REPORT["audio"] = {"status": "no_clip"}
else:
    sub(f"method บน audio item (clip แรก)")
    ai = probe_list(flat[0], [("GetName",), ("GetStart",), ("GetEnd",),
                              ("GetDuration",), ("GetSourceStartFrame",),
                              ("GetProperty",)], "AudioItem", "audioitem")
    can_span = ai.get("GetStart", {}).get("ok") and ai.get("GetEnd", {}).get("ok")

    # coverage แบบ merge overlap ต่อ track + รวม
    def merge_len(items):
        spans = []
        for it in items:
            try:
                spans.append((it.GetStart(), it.GetEnd()))
            except Exception:
                pass
        if not spans:
            return 0
        spans.sort()
        total, cs, ce = 0, spans[0][0], spans[0][1]
        for s, e in spans[1:]:
            if s <= ce:
                ce = max(ce, e)
            else:
                total += ce - cs; cs, ce = s, e
        total += ce - cs
        return total

    if can_span:
        sub("coverage (merge overlap)")
        tl_frames = (tl.GetEndFrame() or 0) - (tl.GetStartFrame() or 0)
        per_track = {}
        for ti, items in a_items:
            if items:
                fr = merge_len(items)
                per_track[f"A{ti}"] = round(fr / FPS, 1)
        union = merge_len(flat)
        cov_sec = round(union / FPS, 1)
        ratio = round(union / tl_frames, 2) if tl_frames else None
        print(f"    ต่อ track (วิ): {per_track}")
        print(f"    union coverage = {cov_sec} วิ · ratio ต่อความยาว timeline = {ratio}")
        REPORT["audio"] = {"readable": True, "coverage_sec": cov_sec, "ratio": ratio, "per_track": per_track}
        print("\n  → AUDIO: ✅ เปิดเฟส audio ได้ (ใช้ union coverage / timeline duration)")
    else:
        REPORT["audio"] = {"readable": False}
        print("\n  → AUDIO: ❌ ดึง span ไม่ได้ — ปล่อยเฟส audio ปิดไว้")


# ---------- 6b. raw dir() ----------
h("6b. RAW dir() (best-effort — remote proxy อาจไม่โชว์)")
dump_dir(tl, "Timeline")
if clips:
    dump_dir(clips[0], "TimelineItem")


# ---------- 7. summary ----------
h("7. สรุป CHECKLIST + CONFIG ที่ต้องแปะ")
c = REPORT["checks"]
m = lambda v: "✅" if v is True else ("❌" if v is False else "⚠️ ")
print(f"  {m(c.get('connect'))} เชื่อมต่อ Resolve")
print(f"  {m(c.get('studio'))} Studio ({REPORT.get('product')} {REPORT.get('version')})")
bid = c.get("bundle_id")
print(f"  {m(bool(bid))} bundle id")
rk = c.get("render_keys")
print(f"  {m(rk not in (None, 'empty_queue'))} render keys {'(คิวว่าง)' if rk == 'empty_queue' else ''}")
col = REPORT.get("color", {})
print(f"  {m(bool(col.get('node_graph') and col.get('num_nodes_readable')))} COLOR detection (node graph)")
print(f"  {m(REPORT.get('audio', {}).get('readable') is True)} AUDIO coverage")
cm = REPORT.get("clip_meta", {})
if cm.get("sample_fields"):
    print(f"  {m(True)} DEEP clip-meta ({len(cm['sample_fields'])} fields · {cm.get('scan_ms_all','?')}ms/{cm.get('scan_clips','?')} clip)")
elif cm.get("status") == "no_clip":
    print(f"  {m(None)} DEEP clip-meta (ไม่มี clip)")
elif cm:
    print(f"  {m(False)} DEEP clip-meta (เรียกไม่ผ่าน — ดู §5d)")

print("\n  === แปะลงโค้ด ===")
if bid:
    print(f"  activity_sampler.py:  RESOLVE_BUNDLE_IDS = {{{', '.join(repr(b) for b in bid)}}}")
if col.get("node_graph") and col.get("num_nodes_readable"):
    print("  resolve_poller.py:    DETECT_GRADE = True   # node graph ใช้ได้")
else:
    print("  resolve_poller.py:    DETECT_GRADE = False  # ยังดึงเกรดไม่ได้")
if isinstance(rk, dict):
    print(f"  render id/status keys: {rk}")
if cm.get("sample_fields"):
    per = cm.get("ms_per_clip", "?")
    print(f"  resolve_poller.py:    EDITORTRACK_EXTRACT_CLIP_META=1  # ดึง detail ลึกได้ (~{per} ms/clip)")
    if isinstance(cm.get("scan_ms_all"), int) and cm["scan_ms_all"] > 1500:
        print(f"                        EDITORTRACK_CLIP_META_MAX=50   # ⚠ {cm['scan_ms_all']}ms/รอบ ถือว่าหนัก — จำกัดจำนวน clip")
    if cm.get("clip_property_keys"):
        print(f"  (ClipProperty keys จริงบนเครื่องนี้ → จูน _SRC_KEYS ได้: {cm['clip_property_keys']})")

save_and_exit(0)

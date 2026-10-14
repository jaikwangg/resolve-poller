#!/usr/bin/env python3
"""probe_grade.py — เทียบเกณฑ์ "เกรดแล้ว" แบบเดิม (node > 1) กับแบบใหม่ (ExportLUT ≠ identity) บน Resolve จริง

ใช้ยืนยันก่อนเปิด EDITORTRACK_GRADE_METHOD=lut บนห้องจริง:
  1. เปิด Resolve Studio + project ทดสอบ อยู่หน้า color
  2. เตรียม clip ให้ครบ 4 แบบ: ไม่แตะเลย · เพิ่ม node เปล่า · เกรดใน node เดียว · เกรดหลาย node
     (ถ้ามี: clip ที่มี window/blur อย่างเดียว · clip ที่อยู่ใน color group ที่เกรดระดับ group)
  3. python3 probe_grade.py   → ดูตารางว่าคอลัมน์ lut ตรงกับที่ทำจริงไหม

read-only: ExportLUT เขียนไฟล์ temp แล้วลบ ไม่แก้ project · เช็คหน้า Resolve ก่อน/หลังว่าไม่ถูกเปลี่ยน
ผลเซฟที่ ~/.editortrack/probe_grade.json
"""
import json, os, pathlib, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_poller import _load_resolve, _safe
from grade_detect import cube_deviation, EPS


def main():
    resolve = _load_resolve()
    if resolve is None:
        sys.exit("ต่อ Resolve ไม่ได้ — เปิด Resolve Studio และเปิด external scripting (Preferences → System → General) ก่อน")
    proj = resolve.GetProjectManager().GetCurrentProject()
    tl = proj.GetCurrentTimeline() if proj else None
    if not tl:
        sys.exit("ไม่มี project/timeline เปิดอยู่")
    page0 = resolve.GetCurrentPage()
    kind = getattr(resolve, "EXPORT_LUT_17PTCUBE", None)
    print(f"Resolve {_safe(resolve, 'GetVersionString')} · project {proj.GetName()} · timeline {tl.GetName()} · หน้า {page0}")
    print(f"EXPORT_LUT_17PTCUBE = {kind!r}" + ("  ✗ ไม่มี constant นี้ → ใช้วิธี lut ไม่ได้" if kind is None else ""))

    rows, tmp = [], tempfile.mkdtemp(prefix="et-probe-")
    cur = _safe(_safe(tl, "GetCurrentVideoItem"), "GetUniqueId")
    for t in range(1, (tl.GetTrackCount("video") or 0) + 1):
        for it in tl.GetItemListInTrack("video", t) or []:
            g = _safe(it, "GetNodeGraph")
            n = _safe(g, "GetNumNodes") if g is not None else None
            row = {"track": t, "name": _safe(it, "GetName"), "uid": _safe(it, "GetUniqueId"), "nodes": n,
                   "labels": [_safe(g, "GetNodeLabel", i) for i in range(1, (n or 0) + 1)] if g is not None else None,
                   "tools": [_safe(g, "GetToolsInNode", i) for i in range(1, (n or 0) + 1)] if g is not None else None,
                   "group": _safe(_safe(it, "GetColorGroup"), "GetName"),
                   "old_graded": bool(n and n > 1), "lut_dev": None, "lut_graded": None, "export_ms": None, "error": None}
            row["current"] = row["uid"] == cur
            if kind is not None:
                path = os.path.join(tmp, "g.cube")
                t0 = time.time()
                try:
                    ok = it.ExportLUT(kind, path)
                    row["export_ms"] = int((time.time() - t0) * 1000)
                    if ok and os.path.exists(path):
                        dev = cube_deviation(open(path, encoding="utf-8", errors="replace").read())
                        row["lut_dev"] = None if dev is None else round(dev, 4)
                        row["lut_graded"] = None if dev is None else dev > EPS
                        if dev is None:
                            row["error"] = "อ่าน .cube ไม่ได้"
                    else:
                        row["error"] = f"ExportLUT คืน {ok!r}"
                except Exception as e:
                    row["error"] = f"{type(e).__name__}: {e}"
                finally:
                    if os.path.exists(path):
                        os.remove(path)
            rows.append(row)
    os.rmdir(tmp)
    page1 = resolve.GetCurrentPage()

    print(f"\n{'':2}{'clip':28} {'nodes':>5} {'เดิม':>5} {'lut':>5} {'dev':>7} {'ms':>5}  group / หมายเหตุ")
    for r in rows:
        flag = "≠" if r["lut_graded"] is not None and r["lut_graded"] != r["old_graded"] else " "
        print(f"{'▶' if r['current'] else ' '}{flag}{str(r['name'])[:28]:28} {str(r['nodes']):>5} "
              f"{'Y' if r['old_graded'] else '-':>5} {('Y' if r['lut_graded'] else '-') if r['lut_graded'] is not None else '?':>5} "
              f"{str(r['lut_dev']):>7} {str(r['export_ms']):>5}  {r['group'] or ''} {r['error'] or ''}")
    ms = [r["export_ms"] for r in rows if r["export_ms"] is not None]
    old = sum(r["old_graded"] for r in rows)
    new = sum(bool(r["lut_graded"]) for r in rows)
    print(f"\nclip {len(rows)} · เกรดแล้ว เดิม {old} · lut {new} · ต่างกัน {sum(1 for r in rows if r['lut_graded'] is not None and r['lut_graded'] != r['old_graded'])} clip (≠)")
    if ms:
        print(f"export ต่อ clip เฉลี่ย {sum(ms) / len(ms):.0f} ms · สูงสุด {max(ms)} ms · ทั้ง timeline {sum(ms)} ms")
    print("หน้า Resolve: " + ("ไม่เปลี่ยน ✓" if page0 == page1 else f"⚠ เปลี่ยนจาก {page0} เป็น {page1} — ห้ามเปิดใช้บนห้องจริง"))
    print("\nเช็คด้วยตา: คอลัมน์ lut ต้องเป็น Y เฉพาะ clip ที่ปรับสีจริง · node เปล่า = - · เกรด node เดียว = Y"
          "\nclip ที่มีแค่ window/blur หรือเกรดระดับ group ถ้าได้ - แปลว่า LUT ไม่เห็น (บอกทีมพัฒนา)")
    out = pathlib.Path(os.path.expanduser("~/.editortrack/probe_grade.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"page_before": page0, "page_after": page1, "rows": rows}, ensure_ascii=False, indent=1, default=str))
    print(f"บันทึกผลที่ {out}")


if __name__ == "__main__":
    main()

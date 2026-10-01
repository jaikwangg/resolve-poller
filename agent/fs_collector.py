#!/usr/bin/env python3
"""collector สำหรับ stage ที่ไม่ใช่ Resolve และวัดจาก "ไฟล์ที่ผลิต" — data management, mastering.
นับไฟล์/ขนาดในโฟลเดอร์เทียบเป้า (ไม่พึ่ง API ของ tool ใด ๆ — ใช้ได้กับทุกโปรแกรม).

env:
  EDITORTRACK_WATCH_DIR      โฟลเดอร์ที่ดู (เช่น ingest/ หรือ masters/)
  EDITORTRACK_WATCH_GLOB     pattern ไฟล์ (default *) เช่น *.mxf, *.mov
  EDITORTRACK_WATCH_RECURSIVE  1/0 (default 1)
  EDITORTRACK_TARGET_COUNT   เป้าจำนวนไฟล์ (เช่น จำนวน reel/shot ที่ต้อง ingest/master)
  EDITORTRACK_TARGET_SIZE_GB เป้าขนาดรวม (ใช้ถ้าไม่มี target count)
"""
import os, glob, fnmatch, datetime


def collect(stage="data"):
    watch = os.environ.get("EDITORTRACK_WATCH_DIR", "")
    pattern = os.environ.get("EDITORTRACK_WATCH_GLOB", "*")
    recursive = os.environ.get("EDITORTRACK_WATCH_RECURSIVE", "1") not in ("0", "false", "no")
    target_count = int(os.environ.get("EDITORTRACK_TARGET_COUNT", "0") or 0)
    target_gb = float(os.environ.get("EDITORTRACK_TARGET_SIZE_GB", "0") or 0)

    if not watch or not os.path.isdir(watch):
        return {"ok": False, "stage": stage, "reason": "no_watch_dir", "detail": {"watch_dir": watch}}

    files = []
    if recursive:
        for root, _, fs in os.walk(watch):
            files += [os.path.join(root, f) for f in fs if fnmatch.fnmatch(f, pattern)]
    else:
        files = [p for p in glob.glob(os.path.join(watch, pattern)) if os.path.isfile(p)]

    count = len(files)
    size = sum(os.path.getsize(p) for p in files if os.path.exists(p))
    newest = max((os.path.getmtime(p) for p in files), default=0)

    pct, done, total = None, count, (target_count or None)
    if target_count:
        pct = min(100.0, count / target_count * 100)
    elif target_gb:
        pct = min(100.0, (size / 1e9) / target_gb * 100)
        done, total = round(size / 1e9, 1), target_gb

    return {
        "ok": True, "stage": stage,
        "project": os.path.basename(watch.rstrip("/\\")) or None,
        "job_hint": None, "scope_hint": None,
        "pct": round(pct, 1) if pct is not None else None, "done": done, "total": total,
        "detail": {"files": count, "size_gb": round(size / 1e9, 2),
                   "newest": _iso(newest), "watch_dir": watch, "glob": pattern},
        "raw": {"files": count, "size_bytes": size},
    }


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat() if ts else None


if __name__ == "__main__":
    import json
    print(json.dumps(collect(os.environ.get("EDITORTRACK_STAGE", "data")), ensure_ascii=False, indent=2))

#!/usr/bin/env python3
"""collector สำหรับ subtitle (ไม่ได้ทำใน Resolve) — parse ไฟล์ซับ .srt/.vtt นับ cue + เวลาล่าสุด.
(.stl EBU เป็น binary → นับ cue ไม่ได้ตรง ๆ, ใช้ mtime/มี-ไฟล์ แทน)

env:
  EDITORTRACK_SUBTITLE_PATH   ไฟล์ซับ หรือ โฟลเดอร์ที่มีไฟล์ซับ
  EDITORTRACK_TARGET_CUES     เป้าจำนวน cue (จากสคริปต์/สปอต) — ไม่มีก็รายงานค่าดิบ
"""
import os, re, glob, datetime

_SRT = re.compile(r"\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->")
_VTT = re.compile(r"\d{2}:\d{2}[:.]\d{2}[.]\d{3}\s*-->")
_TC_END = re.compile(r"-->\s*(\d{2}:\d{2}:\d{2})")


def _count_cues(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        data = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return None, None
    if ext == ".srt":
        cues = len(_SRT.findall(data))
    elif ext == ".vtt":
        cues = len(_VTT.findall(data))
    elif ext == ".stl":
        cues = None                      # binary — นับตรงไม่ได้
    else:
        cues = len(re.findall(r"-->", data)) or None
    ends = _TC_END.findall(data)
    return cues, (ends[-1] if ends else None)


def collect(stage="subtitle"):
    path = os.environ.get("EDITORTRACK_SUBTITLE_PATH", "")
    target = int(os.environ.get("EDITORTRACK_TARGET_CUES", "0") or 0)
    if not path or not os.path.exists(path):
        return {"ok": False, "stage": stage, "reason": "no_subtitle_path", "detail": {"path": path}}

    if os.path.isfile(path):
        files = [path]
    else:
        files = [p for p in glob.glob(os.path.join(path, "*"))
                 if os.path.splitext(p)[1].lower() in (".srt", ".vtt", ".stl")]
    if not files:
        return {"ok": False, "stage": stage, "reason": "no_subtitle_files", "detail": {"path": path}}

    total_cues, last_tc, newest = 0, None, 0
    for f in files:
        c, lt = _count_cues(f)
        if c:
            total_cues += c
        if lt:
            last_tc = lt
        newest = max(newest, os.path.getmtime(f))

    pct = min(100.0, total_cues / target * 100) if (target and total_cues) else None
    return {
        "ok": True, "stage": stage,
        "project": os.path.splitext(os.path.basename(files[0]))[0],
        "job_hint": None, "scope_hint": None,
        "pct": round(pct, 1) if pct is not None else None,
        "done": total_cues or None, "total": target or None,
        "detail": {"cues": total_cues, "files": len(files), "last_cue": last_tc,
                   "newest": datetime.datetime.fromtimestamp(newest, datetime.timezone.utc).isoformat() if newest else None},
        "raw": {"cues": total_cues},
    }


if __name__ == "__main__":
    import json
    print(json.dumps(collect(), ensure_ascii=False, indent=2))

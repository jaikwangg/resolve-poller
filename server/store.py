#!/usr/bin/env python3
"""ที่เก็บ record (JSONL รายวัน) — ที่อยู่, สิทธิ์, ตัด field ที่ไม่จำเป็น, retention

ที่อยู่: EDITORTRACK_STORE (ตั้งเอง — เข้ากันได้กับของเดิม เช่น EDITORTRACK_STORE=data)
         ไม่ตั้ง → $EDITORTRACK_HOME/data (default /srv/editortrack/data) — นอก git checkout, สิทธิ์ 700
retention: EDITORTRACK_RETENTION_DAYS (default 180 · 0 = ไม่ลบ) — ลบไฟล์ YYYY-MM-DD.jsonl ที่เก่ากว่า N วัน
           ทำตอน start และทุก 24 ชม. (ไฟล์ตั้งชื่อตามวันที่ server รับ = record ในไฟล์อายุพอ ๆ กัน)
"""
import datetime
import logging
import os
import pathlib
import threading

log = logging.getLogger("editortrack")

# field ที่ไม่เก็บแม้ agent เวอร์ชันเก่าจะส่งมา (PDPA: ไม่ระบุตัวคน / ไม่เก็บชื่อหน้าต่าง-แอป)
DROP_TOP = ("user",)
DROP_ACTIVITY = ("frontmost_name", "frontmost_bundle")


def default_store():
    s = os.environ.get("EDITORTRACK_STORE")
    if s:
        return pathlib.Path(s)
    return pathlib.Path(os.environ.get("EDITORTRACK_HOME", "/srv/editortrack")) / "data"


def ensure_dir(path):
    """สร้าง dir (700) · มีอยู่แล้วแต่ group/other อ่านได้ → ลดเป็น 700"""
    path = pathlib.Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except PermissionError as e:
        raise SystemExit(f"editortrack: สร้างที่เก็บข้อมูล {path} ไม่ได้ ({e}) — สร้างให้ user ที่รัน server: "
                         f"sudo install -d -m 700 -o $USER {path}  หรือตั้ง EDITORTRACK_STORE") from e
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o700)
    except OSError as e:
        log.warning("chmod 700 %s ไม่ได้: %s", path, e)
    return path


def minimise(rec):
    """ตัด user / ชื่อแอป-หน้าต่างออกก่อนเขียนลงดิสก์ (และตอนอ่านไฟล์เก่า) · เก็บ 'จอล็อก' เป็น boolean แทน"""
    if not isinstance(rec, dict):
        return rec
    for k in DROP_TOP:
        rec.pop(k, None)
    act = rec.get("activity")
    if isinstance(act, dict):
        if "locked" not in act and "frontmost_name" in act:
            act["locked"] = act.get("frontmost_name") == "loginwindow" or \
                act.get("frontmost_bundle") == "com.apple.loginwindow"
        for k in DROP_ACTIVITY:
            act.pop(k, None)
    return rec


def retention_days():
    try:
        return int(os.environ.get("EDITORTRACK_RETENTION_DAYS", "180"))
    except ValueError:
        return 180


def apply_retention(store, days, today):
    """ลบไฟล์วันที่ < today − days → รายชื่อไฟล์ที่ลบ · days <= 0 = ไม่ลบ"""
    if days is None or days <= 0:
        return []
    cutoff = today - datetime.timedelta(days=days)
    gone = []
    for fp in sorted(pathlib.Path(store).glob("*.jsonl")):
        try:
            d = datetime.date.fromisoformat(fp.stem)
        except ValueError:
            continue
        if d < cutoff:
            try:
                fp.unlink()
                gone.append(fp.name)
            except OSError as e:
                log.warning("retention: ลบ %s ไม่ได้: %s", fp, e)
    if gone:
        log.info("retention %d วัน: ลบ %s", days, ", ".join(gone))
    return gone


def scrub_file(fp):
    """เขียนไฟล์เก่าใหม่แบบ atomic โดยตัด field ที่ไม่เก็บแล้ว → จำนวนบรรทัดที่เปลี่ยน"""
    import json
    import tempfile
    fp = pathlib.Path(fp)
    out, changed = [], 0
    for ln in fp.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(ln)
        except Exception:
            out.append(ln)
            continue
        new = json.dumps(minimise(json.loads(ln)), ensure_ascii=False)
        changed += new != json.dumps(rec, ensure_ascii=False)
        out.append(new)
    if changed:
        fd, tmp = tempfile.mkstemp(dir=str(fp.parent), prefix=f".{fp.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, fp)
    return changed


def start_retention(get_store, today_fn, every_sec=86400):
    def loop():
        ev = threading.Event()
        while True:
            try:
                apply_retention(get_store(), retention_days(), today_fn())
            except Exception as e:                  # ไม่ให้ thread ตาย
                log.warning("retention error: %s", e)
            ev.wait(every_sec)
    threading.Thread(target=loop, name="retention", daemon=True).start()


if __name__ == "__main__":
    # python store.py scrub [dir]   — ตัด user/ชื่อแอปออกจากไฟล์ JSONL เดิม (ทำครั้งเดียวหลังอัปเดต)
    # python store.py retention [dir] — ลบไฟล์เก่ากว่า EDITORTRACK_RETENTION_DAYS ทันที
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    d = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else default_store()
    if cmd == "scrub":
        for fp in sorted(d.glob("*.jsonl")):
            print(fp.name, scrub_file(fp))
    elif cmd == "retention":
        print(apply_retention(d, retention_days(), datetime.date.today()))
    else:
        print("ใช้: python store.py scrub|retention [dir]")
        sys.exit(2)

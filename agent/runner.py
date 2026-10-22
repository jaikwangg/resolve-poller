#!/usr/bin/env python3
"""A3 — poll (collector ตาม stage) → resolve job → POST (หลาย URL สำรองได้ + offline queue). record schema 3.
stage จาก EDITORTRACK_STAGE (หรือ EDITORTRACK_ROLE เดิม): data/conform/color/subtitle/master
"""
import json, os, socket, datetime, urllib.request, pathlib
from activity_sampler import sample
from collect import collect, canon
from joblink import resolve_job

IDLE_THRESHOLD = int(os.environ.get("EDITORTRACK_IDLE", "90"))   # วิ; เกินนี้ = idle
STAGE = canon(os.environ.get("EDITORTRACK_STAGE") or os.environ.get("EDITORTRACK_ROLE") or "conform")
# URL ของ /ingest — ใส่หลายตัวคั่นด้วย , ได้ (ตัวแรก = หลัก ที่เหลือ = สำรอง ลองตามลำดับ)
# ทุก URL ต้องชี้ไป server เครื่องเดียวกัน (เช่น IP ใน LAN, ชื่อเครื่อง, IP ผ่าน VPN) ไม่งั้นข้อมูลจะแยกกันอยู่คนละที่
SERVERS = [u.strip() for u in os.environ.get("EDITORTRACK_SERVER", "http://localhost:8000/ingest").split(",") if u.strip()]
TOKEN = os.environ.get("EDITORTRACK_TOKEN", "")
HTTP_TIMEOUT = int(os.environ.get("EDITORTRACK_HTTP_TIMEOUT", "5"))   # วิ; server ค้าง → เข้า offline queue ไม่ค้าง agent
QUEUE = pathlib.Path(os.path.expanduser("~/.editortrack/queue.jsonl"))
LAST_OK = QUEUE.with_name("last_server")        # URL ที่ส่งสำเร็จล่าสุด — รอบหน้าลองตัวนี้ก่อน ไม่ต้องรอ timeout ของตัวที่ล่ม


def build_record():
    col = collect(STAGE)
    act = sample()
    idle = act.get("idle_sec")
    rec = {
        "schema": 3,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": socket.gethostname(),            # ไม่ส่ง user (ชื่อ login) — วัดรายเครื่อง/รายงาน ไม่ใช่รายคน (PDPA)
        "stage": STAGE,
        "activity": {**act, "active": bool(idle is not None and idle < IDLE_THRESHOLD)},
        "source": col.get("raw") if col.get("ok") else {"ok": False, "reason": col.get("reason"), "detail": col.get("detail")},
    }
    if col.get("ok"):
        rec["job"] = resolve_job(col.get("project") or rec["host"], col.get("job_hint"))
        rec["progress"] = {"stage": STAGE, "pct": col.get("pct"),
                           "done": col.get("done"), "total": col.get("total"), "detail": col.get("detail")}
        if col.get("page"):
            rec["page"] = col["page"]
    return rec


def _ordered():
    try:
        last = LAST_OK.read_text(encoding="utf-8").strip()
    except Exception:
        last = ""
    return ([last] if last in SERVERS else []) + [u for u in SERVERS if u != last]


def _send(rec):
    """ลองทีละ URL จนกว่าจะสำเร็จ → คืน HTTP status · ไม่สำเร็จสักตัว → None"""
    data = json.dumps(rec, ensure_ascii=False).encode()
    order = _ordered()
    for url in order:
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                status = r.status
        except Exception:
            continue
        if url != order[0] or not LAST_OK.exists():
            try:
                LAST_OK.parent.mkdir(parents=True, exist_ok=True)
                LAST_OK.write_text(url, encoding="utf-8")
            except Exception:
                pass
        return status
    return None


def _enqueue(lines):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE, "a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def post(rec):
    status = _send(rec)
    if status is None:
        _enqueue([json.dumps(rec, ensure_ascii=False)])
    else:
        _flush_queue()
    return status


def _flush_queue():
    if not QUEUE.exists():
        return
    lines = [ln for ln in QUEUE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    QUEUE.unlink()
    for i, ln in enumerate(lines):
        try:
            rec = json.loads(ln)
        except Exception:
            continue                                # บรรทัดเสีย — ทิ้ง
        if _send(rec) is None:                      # หลุดกลางทาง → เก็บที่เหลือไว้ รอบหน้าส่งต่อ
            _enqueue(lines[i:])
            return


if __name__ == "__main__":
    print(post(build_record()))

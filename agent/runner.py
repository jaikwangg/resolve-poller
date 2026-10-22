#!/usr/bin/env python3
"""A3 — poll (collector ตาม stage) → resolve job → POST (หลาย URL สำรองได้ + offline queue). record schema 3.
stage จาก EDITORTRACK_STAGE (หรือ EDITORTRACK_ROLE เดิม): data/conform/color/subtitle/master
"""
import json, os, socket, datetime, urllib.request, urllib.error, pathlib
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
LOCK = QUEUE.with_name("runner.lock")           # กันรันซ้อนบนเครื่องเดียวกัน
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


RETRY_STATUS = {408, 425, 429}                  # 4xx ที่ลองใหม่ได้ · 4xx อื่น = server ไม่มีวันรับ (token ผิด, record เสีย) → ทิ้ง+log
QUEUE_MAX_LINES = int(os.environ.get("EDITORTRACK_QUEUE_MAX", "10080"))        # ~7 วันที่ 1 record/นาที
QUEUE_MAX_AGE_H = float(os.environ.get("EDITORTRACK_QUEUE_MAX_AGE_H", "168"))  # record เก่ากว่านี้ทิ้ง (server/PMS ไม่ใช้แล้ว)
QUEUE_MAX_BYTES = int(os.environ.get("EDITORTRACK_QUEUE_MAX_MB", "16")) * 1024 * 1024


def _log(msg):
    """→ stdout (run-agent.sh ส่งเข้า ~/.editortrack/agent.log)"""
    print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def _send(rec):
    """ลองทีละ URL → ("ok", status) · ("final", status) = 4xx ที่ลองใหม่ไม่มีประโยชน์ · ("retry", status|None)"""
    data = json.dumps(rec, ensure_ascii=False).encode()
    order = _ordered()
    last = None
    for url in order:
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            e.close()
            if 400 <= e.code < 500 and e.code not in RETRY_STATUS:
                return "final", e.code                  # ทุก URL = server เดียวกัน → ตอบเหมือนกัน ไม่ต้องลองตัวอื่น
            last = e.code
            continue
        except Exception:
            continue
        if url != order[0] or not LAST_OK.exists():
            try:
                LAST_OK.parent.mkdir(parents=True, exist_ok=True)
                LAST_OK.write_text(url, encoding="utf-8")
            except Exception:
                pass
        return "ok", status
    return "retry", last


def _read_queue():
    try:
        return [ln for ln in QUEUE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except FileNotFoundError:
        return []


def _write_queue(lines):
    """เขียน queue ใหม่แบบ atomic (tmp + rename) · ว่าง = ลบไฟล์"""
    if not lines:
        try:
            QUEUE.unlink()
        except FileNotFoundError:
            pass
        return
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE.with_name(QUEUE.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, QUEUE)


def _too_old(ln, now):
    try:
        t = datetime.datetime.fromisoformat(json.loads(ln)["ts"])
        return (now - t).total_seconds() > QUEUE_MAX_AGE_H * 3600
    except Exception:
        return False


def _trim(lines):
    """ตัดตามอายุ + เพดานจำนวนบรรทัด (ทิ้งตัวเก่าสุดก่อน)"""
    now = datetime.datetime.now(datetime.timezone.utc)
    keep = [ln for ln in lines if not _too_old(ln, now)]
    if len(keep) > QUEUE_MAX_LINES:
        keep = keep[-QUEUE_MAX_LINES:]
    if len(keep) < len(lines):
        _log(f"queue: ทิ้ง {len(lines) - len(keep)} record (เก่ากว่า {QUEUE_MAX_AGE_H:g} ชม. หรือเกิน {QUEUE_MAX_LINES} บรรทัด)")
    return keep


def _enqueue(lines):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE, "a", encoding="utf-8") as f:     # append 1 บรรทัด (ปกติ) · เกินเพดานค่อยเขียนใหม่ทั้งไฟล์แบบ atomic
        for ln in lines:
            f.write(ln + "\n")
    try:
        size = QUEUE.stat().st_size
    except OSError:
        size = 0
    if size > QUEUE_MAX_BYTES or (size > 256 * 1024 and len(_read_queue()) > QUEUE_MAX_LINES):
        _write_queue(_trim(_read_queue()))


def post(rec):
    kind, status = _send(rec)
    if kind == "retry":
        _enqueue([json.dumps(rec, ensure_ascii=False)])
    elif kind == "final":
        _log(f"server ตอบ {status} — ทิ้ง record ts={rec.get('ts')} (ไม่เข้า queue: เช็ค token/เวอร์ชัน agent)")
    else:
        _flush_queue()
    return status


def _flush_queue():
    """ส่ง queue ทีละบรรทัด · ลบออกจากไฟล์เฉพาะตัวที่ส่งสำเร็จหรือ server ปฏิเสธถาวร (4xx) — เขียนไฟล์ใหม่หลังจบรอบ
    crash กลางทาง = ไฟล์เดิมยังอยู่ครบ (ส่งซ้ำได้: server เก็บ 1 record ต่อเครื่องต่อนาที)"""
    lines = _trim(_read_queue())
    if not lines:
        _write_queue([])
        return
    rest = []
    for i, ln in enumerate(lines):
        try:
            rec = json.loads(ln)
        except Exception:
            _log("queue: บรรทัดเสีย — ทิ้ง")
            continue
        kind, status = _send(rec)
        if kind == "final":
            _log(f"queue: server ตอบ {status} — ทิ้ง record ts={rec.get('ts')}")
            continue
        if kind == "retry":                          # หลุดกลางทาง → เก็บที่เหลือไว้ รอบหน้าส่งต่อ
            rest = lines[i:]
            break
    _write_queue(rest)


def run_lock():
    """กันรันซ้อน (install.sh --system + รันมือระหว่าง loop) → คืน file object ที่ถือ lock หรือ None ถ้ามีตัวอื่นรันอยู่
    Windows ไม่มี fcntl → ไม่ล็อก (server ตัดซ้อนด้วย 1 record ต่อ host ต่อนาทีอยู่แล้ว)"""
    try:
        import fcntl
    except ImportError:
        return True
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


if __name__ == "__main__":
    _lock = run_lock()
    if _lock is None:
        print("skip: agent อีกตัวกำลังรันอยู่ (lock)")
    else:
        print(post(build_record()))

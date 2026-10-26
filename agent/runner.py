#!/usr/bin/env python3
"""A3 — poll (collector ตาม stage) → resolve job → POST (หลาย URL สำรองได้ + offline queue). record schema 3.
stage จาก EDITORTRACK_STAGE (หรือ EDITORTRACK_ROLE เดิม): data/conform/color/subtitle/master
"""
import json, os, sys, time, socket, getpass, datetime, platform, urllib.request, pathlib
import safety
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
QUEUE_MAX_LINES = int(os.environ.get("EDITORTRACK_QUEUE_MAX_LINES", "5000"))      # ~3.5 วันที่ 1 record/นาที
QUEUE_MAX_BYTES = int(os.environ.get("EDITORTRACK_QUEUE_MAX_BYTES", str(10 * 1024 * 1024)))
FLUSH_MAX = int(os.environ.get("EDITORTRACK_FLUSH_MAX", "300"))                  # record ย้อนหลังต่อรอบ
FLUSH_BUDGET_SEC = float(os.environ.get("EDITORTRACK_FLUSH_BUDGET_SEC", "20"))


def _queue():
    return safety.state_dir() / "queue.jsonl"


def _last_ok():
    return safety.state_dir() / "last_server"     # URL ที่ส่งสำเร็จล่าสุด — รอบหน้าลองตัวนี้ก่อน


def _resolve_gate():
    """None = อนุญาตแตะ Resolve รอบนี้ · ไม่งั้นคืนเหตุผลที่ห้าม (ไม่มีการเรียก Resolve เลย)"""
    t = safety.tripped()
    if t:
        return "tripped:" + str(t)
    if not safety.lease_allows_resolve():
        return "lease_no_resolve"
    if safety.backoff_active():
        return "backoff"
    return None


def _collect_safely(act):
    """collect + circuit breaker/backoff. ไม่มี exception หลุดออกไป."""
    gate = _resolve_gate() if STAGE in ("conform", "color") else None
    if gate:
        return {"ok": False, "reason": gate, "skipped": True}
    idle = act.get("idle_sec")
    try:
        col = collect(STAGE, idle_fn=lambda: sample().get("idle_sec") if idle is not None else None)
    except Exception as e:                          # bug/ฟังก์ชันพัง → หยุดพักแบบ exponential ไม่ retry ถี่
        safety.backoff_fail("exception:" + type(e).__name__)
        return {"ok": False, "reason": "exception", "detail": type(e).__name__}
    raw = col.get("raw") or {}
    g = raw.get("guard") or {}
    if raw.get("reason") == "forbidden_call" or g.get("violations"):
        safety.trip("forbidden_call", raw.get("forbidden") or g.get("violations"))
    elif raw.get("reason") == "state_changed_during_scan" and raw.get("suspect_agent"):
        safety.trip("page_changed_while_idle", {"before": raw.get("page_before"), "after": raw.get("page_after")})
    elif raw.get("reason") == "scan_budget":
        safety.backoff_fail("scan_budget")
    elif col.get("ok"):
        safety.backoff_ok()
    return col


def build_record(prio=None, t0=None):
    t0 = t0 or time.time()
    act = sample()
    col = _collect_safely(act)
    idle = act.get("idle_sec")
    raw = col.get("raw") or {}
    rec = {
        "schema": 3,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "stage": STAGE,
        "activity": {**act, "active": bool(idle is not None and idle < IDLE_THRESHOLD)},
        "source": raw if col.get("ok") else {"ok": False, "reason": col.get("reason"), "detail": col.get("detail"),
                                              "skipped": bool(col.get("skipped") or raw.get("skipped"))},
    }
    if col.get("ok"):
        rec["job"] = resolve_job(col.get("project") or rec["host"], col.get("job_hint"))
        rec["progress"] = {"stage": STAGE, "pct": col.get("pct"),
                           "done": col.get("done"), "total": col.get("total"), "detail": col.get("detail")}
        if col.get("page"):
            rec["page"] = col["page"]
    rec["agent"] = _heartbeat(prio, raw, t0)       # health ของ agent เอง (server ใช้เฝ้า)
    return rec


def _heartbeat(prio, raw, t0):
    b = safety.backoff_state()
    return {"version": safety.AGENT_VERSION, "python": platform.python_version(),
            "os": "%s %s" % (platform.system(), platform.release()), "priority": prio,
            "rss_mb": safety.peak_rss_mb(), "turn_ms": int((time.time() - t0) * 1000),
            "guard": raw.get("guard"), "tripped": safety.tripped(),
            "backoff_fails": b.get("fails", 0), "queue_lines": _queue_len()}


def _queue_len():
    try:
        with open(_queue(), "rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _ordered():
    try:
        last = _last_ok().read_text(encoding="utf-8").strip()
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
        if url != order[0] or not _last_ok().exists():
            try:
                _last_ok().parent.mkdir(parents=True, exist_ok=True)
                _last_ok().write_text(url, encoding="utf-8")
            except Exception:
                pass
        return status
    return None


def _rewrite_queue(lines):
    """เขียน queue ใหม่แบบ atomic + ตัดให้อยู่ในเพดาน (เก็บของใหม่สุด)."""
    lines = lines[-QUEUE_MAX_LINES:] if QUEUE_MAX_LINES > 0 else []
    while lines and sum(len(l.encode("utf-8")) + 1 for l in lines) > QUEUE_MAX_BYTES:
        lines = lines[len(lines) // 10 or 1:]
    q = _queue()
    try:
        if not lines:
            if q.exists():
                q.unlink()
            return
        q.parent.mkdir(parents=True, exist_ok=True)
        tmp = q.with_name(q.name + ".tmp")
        tmp.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
        tmp.replace(q)
    except Exception:
        pass                                        # disk เต็ม/สิทธิ์ → ทิ้ง ไม่ crash


def _read_queue():
    try:
        return [ln for ln in _queue().read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return []


def _enqueue(lines):
    try:
        q = _queue()
        q.parent.mkdir(parents=True, exist_ok=True)
        with open(q, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
        if q.stat().st_size > QUEUE_MAX_BYTES or _queue_len() > QUEUE_MAX_LINES:
            _rewrite_queue(_read_queue())
    except Exception:
        pass


def post(rec):
    status = _send(rec)
    if status is None:
        _enqueue([json.dumps(rec, ensure_ascii=False)])
    else:
        _flush_queue()
    return status


def _flush_queue():
    """ส่ง record ย้อนหลังทีละไม่เกิน FLUSH_MAX / FLUSH_BUDGET_SEC ต่อรอบ (ไม่ยิงถล่ม server)."""
    lines = _read_queue()
    if not lines:
        return
    t0 = time.time()
    sent = 0
    for i, ln in enumerate(lines):
        if sent >= FLUSH_MAX or time.time() - t0 > FLUSH_BUDGET_SEC:
            _rewrite_queue(lines[i:])
            return
        try:
            rec = json.loads(ln)
        except Exception:
            continue                                # บรรทัดเสีย — ทิ้ง
        if _send(rec) is None:                      # หลุดกลางทาง → เก็บที่เหลือไว้ รอบหน้าส่งต่อ
            _rewrite_queue(lines[i:])
            return
        sent += 1
    _rewrite_queue([])


def main():
    """1 รอบ. คืน HTTP status, หรือ None ถ้าเงียบ (kill switch / lock / ไม่มี lease)."""
    t0 = time.time()
    safety.hard_timeout()
    prio = safety.lower_priority()
    if safety.kill_switch():
        return None                                 # เงียบสนิท: ไม่แตะ Resolve ไม่ใช้ network
    lock = safety.Lock()
    if not lock.acquire():
        return None                                 # มีรอบอื่นค้างอยู่
    safety.refresh_lease(SERVERS, TOKEN, socket.gethostname())
    if not safety.lease_ok():
        return None                                 # remote kill / ยังไม่ได้รับอนุญาต / lease หมดอายุ
    return post(build_record(prio, t0))


if __name__ == "__main__":
    try:
        print(main())
    except Exception as e:                          # ห้าม traceback วน — log บรรทัดเดียวแล้วจบ
        print("error", type(e).__name__)

#!/usr/bin/env python3
"""A3 — poll (collector ตาม stage) → resolve job → POST (offline queue). record schema 3.
stage จาก EDITORTRACK_STAGE (หรือ EDITORTRACK_ROLE เดิม): data/conform/color/subtitle/master
"""
import json, os, socket, getpass, datetime, urllib.request, pathlib
from activity_sampler import sample
from collect import collect, canon
from joblink import resolve_job

IDLE_THRESHOLD = int(os.environ.get("EDITORTRACK_IDLE", "90"))   # วิ; เกินนี้ = idle
STAGE = canon(os.environ.get("EDITORTRACK_STAGE") or os.environ.get("EDITORTRACK_ROLE") or "conform")
SERVER = os.environ.get("EDITORTRACK_SERVER", "http://localhost:8000/ingest")
TOKEN = os.environ.get("EDITORTRACK_TOKEN", "")
HTTP_TIMEOUT = int(os.environ.get("EDITORTRACK_HTTP_TIMEOUT", "5"))   # วิ; server ค้าง → เข้า offline queue ไม่ค้าง agent
QUEUE = pathlib.Path(os.path.expanduser("~/.editortrack/queue.jsonl"))


def build_record():
    col = collect(STAGE)
    act = sample()
    idle = act.get("idle_sec")
    rec = {
        "schema": 3,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
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


def post(rec):
    data = json.dumps(rec, ensure_ascii=False).encode()
    req = urllib.request.Request(
        SERVER, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            _flush_queue()
            return r.status
    except Exception:
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return None


def _flush_queue():
    if not QUEUE.exists():
        return
    lines = QUEUE.read_text(encoding="utf-8").splitlines()
    QUEUE.unlink()
    for ln in lines:
        try:
            post(json.loads(ln))
        except Exception:
            pass


if __name__ == "__main__":
    print(post(build_record()))

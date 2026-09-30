#!/usr/bin/env python3
"""A3 — poll → merge → resolve job → คำนวณ % → POST (มี offline queue). record schema 2.
wire: joblink.resolve_job() ผูก project เข้ากับ job (conform/color) + job_hint จาก poller."""
import json, os, socket, getpass, datetime, urllib.request, pathlib
from resolve_poller import snapshot
from activity_sampler import sample
from scope import load_scope, compute_progress, DEFAULT_WEIGHTS
from joblink import resolve_job

IDLE_THRESHOLD = int(os.environ.get("EDITORTRACK_IDLE", "90"))   # วิ; เกินนี้ = idle
SERVER = os.environ.get("EDITORTRACK_SERVER", "http://localhost:8000/ingest")
TOKEN = os.environ.get("EDITORTRACK_TOKEN", "")
QUEUE = pathlib.Path(os.path.expanduser("~/.editortrack/queue.jsonl"))


def build_record():
    snap = snapshot()
    act = sample()
    idle = act.get("idle_sec")
    rec = {
        "schema": 2,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "resolve": snap,
        "activity": {**act,
                     "active_in_resolve": bool(act.get("resolve_frontmost")
                                               and idle is not None and idle < IDLE_THRESHOLD)},
    }
    if snap.get("ok"):
        job = resolve_job(snap.get("project"), snap.get("job_hint"))   # ← wire job-link
        rec["job"] = job
        scope = load_scope(snap.get("project"), snap.get("scope_hint")) or {}
        if job.get("target_duration_sec"):          # target จาก job registry เป็นหลัก
            scope.setdefault("phase_weights", DEFAULT_WEIGHTS)
            scope["target_duration_sec"] = job["target_duration_sec"]
        rec["scope"] = scope or None
        rec["progress"] = compute_progress(snap, scope or None)
    return rec


def post(rec):
    data = json.dumps(rec, ensure_ascii=False).encode()
    req = urllib.request.Request(
        SERVER, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            _flush_queue()
            return r.status
    except Exception:
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE, "a", encoding="utf-8") as f:      # offline → เก็บไว้ส่งทีหลัง
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

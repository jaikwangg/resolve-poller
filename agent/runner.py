#!/usr/bin/env python3
"""A3 — poll (collector ตาม stage) → resolve job → POST (หลาย URL สำรองได้ + offline queue). record schema 3.
stage จาก EDITORTRACK_STAGE (หรือ EDITORTRACK_ROLE เดิม): data/conform/color/subtitle/master
"""
import json, os, sys, time, socket, getpass, datetime, platform, urllib.request, urllib.error, pathlib
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


def _envnum(name, default, cast=int):
    try:
        v = cast(os.environ.get(name, "") or default)
        return v if v == v and v >= 0 else cast(default)      # NaN / negative -> default
    except (TypeError, ValueError):
        return cast(default)


# ---- sender policy (ET-5, docs/research/AGENT_SEND_POLICY.md) ----
# The server answered that THIS record is unusable: retrying can never succeed, so the record is
# dropped from the queue, counted, logged locally (bounded) and reported in the next heartbeat.
# 400 = explain.record_problem (bad host/ts/type) or unreadable JSON · 413 = this record is too large ·
# 422 = request validation. Every other status (401/403 auth, 404/405/410 wrong URL or old server,
# 408/425/429, 409, any other 4xx, 3xx, 5xx) and every network error keeps the record and backs off:
# those describe the server, the proxy or the configuration, and treating them as final would drop
# every record behind them.
FINAL_STATUS = frozenset((400, 413, 422))
SEND_BACKOFF_BASE_SEC = _envnum("EDITORTRACK_SEND_BACKOFF_BASE_SEC", 60, float)
SEND_BACKOFF_MAX_SEC = _envnum("EDITORTRACK_SEND_BACKOFF_MAX_SEC", 900, float)
# a run of this many final rejections with no delivery in between = the server rejects everything
# (a server-side regression, not a bad record) -> stop dropping, keep records, back off ("reject hold")
REJECT_HOLD_STREAK = max(1, _envnum("EDITORTRACK_REJECT_HOLD_STREAK", 10))
REJECT_LOG_MAX_LINES = _envnum("EDITORTRACK_REJECT_LOG_MAX_LINES", 200)
REJECT_LOG_MAX_BYTES = _envnum("EDITORTRACK_REJECT_LOG_MAX_BYTES", 256 * 1024)
REJECT_LOG_KEEP_RECORD = 8 * 1024            # bytes; a bigger record is logged by length + sha256 only


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
    pu = safety.platform_unverified()            # Python/OS upgraded since install → verify first
    if pu:
        return pu
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
    rj = _reject_state()
    sb = _send_backoff_state()
    return {"version": safety.AGENT_VERSION, "python": platform.python_version(),
            "os": "%s %s" % (platform.system(), platform.release()), "priority": prio,
            "rss_mb": safety.peak_rss_mb(), "turn_ms": int((time.time() - t0) * 1000),
            "guard": raw.get("guard"), "tripped": safety.tripped(),
            "backoff_fails": b.get("fails", 0), "queue_lines": _queue_len(),
            # ET-5: records the server refused as unusable (cumulative, never reset) + sender state
            "rejected": int(rj.get("total", 0)), "reject_hold": _reject_hold(rj),
            "send_fails": int(sb.get("fails", 0))}


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is a configuration problem (http->https, proxy): never follow it. urllib would turn the
    POST into a GET (no body, Authorization header to the new host); the 3xx comes back as an HTTPError
    and is retried like any other non-final status."""
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _retry_after(e):
    try:
        v = float(str(e.headers.get("Retry-After") or "").strip())
        return v if v == v and v > 0 else None
    except Exception:
        return None


def _detail(e):
    try:
        return e.read(400).decode("utf-8", "replace")[:300]
    except Exception:
        return ""


def _send(rec):
    """Try each URL in turn. -> (kind, status, info)
       ("ok", status, None)          delivered (2xx)
       ("final", status, detail)     the server refused this record as unusable (FINAL_STATUS)
       ("retry", status|None, retry_after)  anything else; status None = no server answered"""
    data = json.dumps(rec, ensure_ascii=False).encode()
    order = _ordered()
    last = ("retry", None, None)
    for url in order:
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        try:
            with _OPENER.open(req, timeout=HTTP_TIMEOUT) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            if e.code in FINAL_STATUS:          # every URL is the same server: its answer is about the record
                return ("final", e.code, _detail(e))
            last = ("retry", e.code, _retry_after(e))
            continue                            # 5xx / auth / wrong URL on this route -> next URL
        except Exception:
            continue                            # offline / timeout / DNS -> next URL
        if not 200 <= status < 300:             # 1xx and anything odd: not delivered
            last = ("retry", status, None)
            continue
        if url != order[0] or not _last_ok().exists():
            try:
                _last_ok().parent.mkdir(parents=True, exist_ok=True)
                _last_ok().write_text(url, encoding="utf-8")
            except Exception:
                pass
        return ("ok", status, None)
    return last


# ---- send backoff (separate from safety's Resolve backoff: a server outage never gates Resolve) ----
def _send_backoff_state():
    b = safety._read("send_backoff.json")
    return b if isinstance(b, dict) and not b.get("_corrupt") else {}


def _send_backoff_active(now=None):
    now = now or time.time()
    try:
        nxt = float(_send_backoff_state().get("next_at", 0))
    except (TypeError, ValueError):
        return False
    if nxt > now + SEND_BACKOFF_MAX_SEC + 60:    # clock moved backwards: never wait longer than the cap
        return False
    return now < nxt


def _send_backoff_fail(status, retry_after=None, now=None):
    now = now or time.time()
    try:
        n = int(_send_backoff_state().get("fails", 0)) + 1
    except (TypeError, ValueError):
        n = 1
    wait = min(SEND_BACKOFF_BASE_SEC * (2 ** min(n - 1, 20)), SEND_BACKOFF_MAX_SEC)
    if retry_after:                              # 429 / 503 Retry-After: honour it, within the cap
        wait = min(max(wait, retry_after), SEND_BACKOFF_MAX_SEC)
    safety._write("send_backoff.json", {"fails": n, "next_at": now + wait, "status": status})
    return wait


def _send_backoff_ok():
    try:
        (safety.state_dir() / "send_backoff.json").unlink()
    except Exception:
        pass


# ---- rejected records: count + bounded local log ----
def _reject_state():
    r = safety._read("rejected.json")
    return r if isinstance(r, dict) and not r.get("_corrupt") else {}


def _reject_hold(st=None):
    st = _reject_state() if st is None else st
    try:
        return int(st.get("streak", 0)) >= REJECT_HOLD_STREAK
    except (TypeError, ValueError):
        return False


def _delivered():
    """a 2xx: the server takes records again -> clear the backoff and the reject streak"""
    _send_backoff_ok()
    st = _reject_state()
    if st.get("streak"):
        st["streak"] = 0
        safety._write("rejected.json", st)


def _reject(line, status, reason, local=False):
    """drop one record for good: count it (total -> heartbeat) and append a bounded log entry.
    local=True: the agent's own check refused it before sending (does not count toward the hold streak)."""
    st = _reject_state()
    try:
        total, streak = int(st.get("total", 0)), int(st.get("streak", 0))
    except (TypeError, ValueError):
        total, streak = 0, 0
    st = {"total": total + 1, "streak": streak if local else streak + 1,
          "last": {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": status,
                   "reason": str(reason)[:200]}}
    safety._write("rejected.json", st)
    raw = line.encode("utf-8", "replace")
    ent = dict(st["last"])
    if len(raw) <= REJECT_LOG_KEEP_RECORD:
        ent["record"] = line
    else:
        import hashlib
        ent["record_bytes"], ent["record_sha256"] = len(raw), hashlib.sha256(raw).hexdigest()
    _append_bounded(safety.state_dir() / "rejected.log", json.dumps(ent, ensure_ascii=False),
                    REJECT_LOG_MAX_LINES, REJECT_LOG_MAX_BYTES)


def _append_bounded(path, line, max_lines, max_bytes):
    """append one line, then keep only the newest max_lines / max_bytes. Never raises."""
    try:
        if max_lines <= 0 or max_bytes <= 0:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if path.stat().st_size <= max_bytes and _count_lines(path) <= max_lines:
            return
        keep = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        while keep and sum(len(l.encode("utf-8")) + 1 for l in keep) > max_bytes:
            keep = keep[len(keep) // 10 or 1:]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _count_lines(path):
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def _ts_problem(rec):
    """the agent never produces or forwards a naive timestamp: ts must be ISO with a UTC offset.
    None = fine, else the reason (same words as the server's explain.record_problem)."""
    if not isinstance(rec, dict):
        return "not_object"
    ts = rec.get("ts")
    if not isinstance(ts, str):
        return "no_ts"
    try:
        d = datetime.datetime.fromisoformat(ts)
    except ValueError:
        return "bad_ts"
    if d.tzinfo is None:
        return "ts_without_offset"
    if d.utcoffset() != datetime.timedelta(0):
        return "ts_not_utc"
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
    """send this turn's record, then the backlog. -> HTTP status, or None when it was queued / refused locally."""
    line = json.dumps(rec, ensure_ascii=False)
    why = _ts_problem(rec)
    if why:                                     # an agent bug: never queue or send it (the server would 400 it)
        _reject(line, "local", why, local=True)
        return None
    if _send_backoff_active():                  # server failing: no network this turn, keep the record
        _enqueue([line])
        return None
    kind, status, info = _send(rec)
    if kind == "final" and _reject_hold():
        kind, info = "retry", None              # everything is being refused: keep it, back off
    if kind == "retry":
        _send_backoff_fail(status, info)
        _enqueue([line])
        return None
    if kind == "ok":
        _delivered()
    else:
        _reject(line, status, info)             # this record only; the server is up -> backlog may go
        _send_backoff_ok()
    _flush_queue()
    return status


def _flush_queue():
    """send backlog records, oldest first: at most FLUSH_MAX requests / FLUSH_BUDGET_SEC per turn (no storm).
    A refused record is dropped (counted + logged) and the next one is sent; the first retryable failure
    stops the flush, keeps that record and the rest, and starts the send backoff."""
    lines = _read_queue()
    if not lines:
        return
    t0 = time.time()
    tried = 0
    for i, ln in enumerate(lines):
        if tried >= FLUSH_MAX or time.time() - t0 > FLUSH_BUDGET_SEC:
            _rewrite_queue(lines[i:])
            return
        try:
            rec = json.loads(ln)
        except Exception:
            _reject(ln, "local", "bad_json", local=True)
            continue
        why = _ts_problem(rec)
        if why:
            _reject(ln, "local", why, local=True)
            continue
        tried += 1
        kind, status, info = _send(rec)
        if kind == "final" and _reject_hold():
            kind, info = "retry", None
        if kind == "ok":
            _delivered()
        elif kind == "final":
            _reject(ln, status, info)
        else:                                   # server failing mid-flush: keep this and the rest
            _send_backoff_fail(status, info)
            _rewrite_queue(lines[i:])
            return
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

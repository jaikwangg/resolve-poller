#!/usr/bin/env python3
"""A3 — poll (collector ตาม stage) → resolve job → POST (หลาย URL สำรองได้ + offline queue). record schema 3.
stage จาก EDITORTRACK_STAGE (หรือ EDITORTRACK_ROLE เดิม): data/conform/color/subtitle/master
"""
import json, os, sys, time, socket, datetime, platform, urllib.request, urllib.error, pathlib, uuid
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
        return v if v == v and 0 <= v < float("inf") else cast(default)   # NaN / inf / negative -> default
    except (TypeError, ValueError):
        return cast(default)


# ---- sender policy (ET-5, docs/research/AGENT_SEND_POLICY.md) ----
# Final (the record is dropped from the queue, counted, logged locally (bounded) and reported in the next
# heartbeat) ONLY when OUR server says this record is unusable: HTTP 400 carrying its refusal marker,
# the X-Editortrack-Reject header (explain.record_problem reason) or the body {"detail": "record ใช้ไม่ได้ (..."}.
# A 400 without the marker (a proxy, a wrong port, a foreign host behind a backup URL), 413, 422 and every
# other status (401/403, 404/405/410, 408/409/425/429, 3xx, 5xx) and every network error keep the record,
# try the next URL and back off: they describe the server, proxy or configuration, not the record, and a
# "final" reading would drop every record behind them.
FINAL_STATUS = frozenset((400,))
REJECT_HEADER = "X-Editortrack-Reject"
REJECT_BODY_PREFIX = "record ใช้ไม่ได้ ("
SEND_BACKOFF_BASE_SEC = _envnum("EDITORTRACK_SEND_BACKOFF_BASE_SEC", 60, float)
# cap below the server's "silent" warning (agent_health THRESH silent_min = 10 min)
SEND_BACKOFF_MAX_SEC = _envnum("EDITORTRACK_SEND_BACKOFF_MAX_SEC", 300, float)
# a run of this many final rejections with no delivery in between = the server rejects everything
# (a server-side regression, not a bad record) -> stop dropping, keep records, back off ("reject hold")
REJECT_HOLD_STREAK = max(1, _envnum("EDITORTRACK_REJECT_HOLD_STREAK", 10))
REJECT_LOG_MAX_LINES = _envnum("EDITORTRACK_REJECT_LOG_MAX_LINES", 200)
REJECT_LOG_MAX_BYTES = _envnum("EDITORTRACK_REJECT_LOG_MAX_BYTES", 256 * 1024)
REJECT_LOG_KEEP_RECORD = 8 * 1024            # bytes; a bigger record is logged by length + sha256 only


# data minimisation (docs/DATA_SHEET.md): activity keeps only these keys. No login name, no
# frontmost app name/bundle id (only "is Resolve in front", plus "screen locked" as a bool).
ACTIVITY_KEYS = ("idle_sec", "resolve_frontmost", "platform", "unavailable", "error")
LOCK_SCREEN_APPS = ("loginwindow",)


def host_id():
    """the name this agent reports: EDITORTRACK_HOST (the name its token was issued for, set by make-env.sh)
    or, when unset, the live hostname. The server compares names case-insensitively and without a domain
    suffix, so only a really different name is refused (403: records stay queued, logged, shown on /health)."""
    return (os.environ.get("EDITORTRACK_HOST") or "").strip() or socket.gethostname()


def minimise_activity(act, idle_threshold=None):
    act = act or {}
    out = {k: act[k] for k in ACTIVITY_KEYS if k in act}
    if (act.get("frontmost_name") or "") in LOCK_SCREEN_APPS:
        out["locked"] = True
    idle = act.get("idle_sec")
    thr = IDLE_THRESHOLD if idle_threshold is None else idle_threshold
    out["active"] = bool(idle is not None and idle < thr)
    return out


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
    raw = col.get("raw") or {}
    rec = {
        "schema": 3,
        # idempotency id: set once here, kept in the queue line, so every re-send carries the same id and the
        # server stores the record once (server/ingest_ids.py)
        "rid": uuid.uuid4().hex,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": host_id(),
        "stage": STAGE,
        "activity": minimise_activity(act),
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
            "rejected": _int0(rj.get("total")), "rejected_local": _int0(rj.get("local")),
            "reject_hold": _reject_hold(rj), "send_fails": _int0(sb.get("fails"))}


def _int0(v):
    """a state-file number; anything odd (null, text, bool, negative) -> 0. Never raises."""
    try:
        return int(v) if not isinstance(v, bool) and int(v) >= 0 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


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


def _refusal(e):
    """-> reason text if this HTTPError is OUR server refusing the record, else None."""
    if e.code not in FINAL_STATUS:
        return None
    try:
        h = (e.headers.get(REJECT_HEADER) or "").strip() if e.headers is not None else ""
    except Exception:
        h = ""
    body = ""
    try:
        body = e.read(2000).decode("utf-8", "replace")
    except Exception:
        pass
    finally:
        try:
            e.close()
        except Exception:
            pass
    detail = ""
    try:
        d = json.loads(body).get("detail")
        detail = d if isinstance(d, str) else ""
    except Exception:
        pass
    if h:
        return (h + " " + detail).strip()[:300]
    if detail.startswith(REJECT_BODY_PREFIX):
        return detail[:300]
    return None


def _send(rec):
    """Try each URL in turn. -> (kind, status, info)
       ("ok", status, None)          delivered (2xx)
       ("final", status, reason)     our server refused this record as unusable (_refusal) and no URL took it
       ("retry", status|None, retry_after)  anything else; status None = no server answered
    Every URL is tried until one delivers, so a backup URL still gets the record when another URL refuses it."""
    data = json.dumps(rec, ensure_ascii=False).encode()
    order = _ordered()
    last = ("retry", None, None)
    final = None
    for url in order:
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        try:
            with _OPENER.open(req, timeout=HTTP_TIMEOUT) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            why = _refusal(e)
            if why is not None:
                final = final or ("final", e.code, why)
                continue                        # still try the other URLs
            last = ("retry", e.code, _retry_after(e))
            if e.code == 403:
                _note_host_refused(e, rec.get("host"), url)
            try:
                e.close()
            except Exception:
                pass
            continue                            # 5xx / auth / wrong URL / foreign 400 on this route -> next URL
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
    return final or last


HOST_REFUSED_LOG_EVERY_SEC = 3600


def _note_host_refused(e, sent, url):
    """403 from /ingest: the server does not accept this host name for this token. The record stays queued
    (403 is never final). Leaves ~/.editortrack/host_refused.json and one line in the agent log (stderr ->
    agent.log), at most once an hour or when something changes. Never raises."""
    try:
        token_host = ""
        try:
            token_host = (e.headers.get("X-Editortrack-Host-Mismatch") or "").strip()[:100] if e.headers else ""
        except Exception:
            pass
        now = time.time()
        old = safety._read("host_refused.json")
        old = old if isinstance(old, dict) and not old.get("_corrupt") else {}
        new = {"at": now, "sent": str(sent)[:100], "token_host": token_host, "url": url,
               "count": _int0(old.get("count")) + 1, "logged_at": old.get("logged_at")}
        try:
            logged = float(old.get("logged_at") or 0)
        except (TypeError, ValueError):
            logged = 0
        changed = (old.get("sent"), old.get("token_host")) != (new["sent"], new["token_host"])
        if changed or now - logged >= HOST_REFUSED_LOG_EVERY_SEC or now < logged:
            new["logged_at"] = now
            hint = (" (token is for %r: set EDITORTRACK_HOST=%s in deploy/env.sh)" % (token_host, token_host)
                    if token_host else "")
            print("editortrack: server %s refused host %r with HTTP 403%s; records stay in the queue (%d)"
                  % (url, new["sent"], hint, _queue_len()), file=sys.stderr)
        safety._write("host_refused.json", new)
    except Exception:
        pass


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
    n = _int0(_send_backoff_state().get("fails")) + 1
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
    return _int0(st.get("streak")) >= REJECT_HOLD_STREAK


def _delivered():
    """a 2xx: the server takes records again -> clear the backoff, the reject streak and a host refusal"""
    _send_backoff_ok()
    try:
        (safety.state_dir() / "host_refused.json").unlink()
    except Exception:
        pass
    st = _reject_state()
    if st.get("streak"):
        st["streak"] = 0
        safety._write("rejected.json", st)


def _reject(line, status, reason, local=False):
    """drop one record for good: count it and append a bounded log entry.
    total = refused by the server (heartbeat "rejected"); local = refused by the agent's own check before
    sending (heartbeat "rejected_local"; does not count toward the hold streak)."""
    st = _reject_state()
    total, nloc, streak = _int0(st.get("total")), _int0(st.get("local")), _int0(st.get("streak"))
    st = {"total": total + (0 if local else 1), "local": nloc + (1 if local else 0),
          "streak": streak if local else streak + 1,
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
        d = datetime.datetime.fromisoformat(ts[:-1] + "+00:00" if ts.endswith("Z") else ts)   # Z: py < 3.11
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
        # a record queued under another spelling of this machine's name (e.g. before EDITORTRACK_HOST was set
        # after a host-name refusal) is sent under the current one: it was taken on this machine either way.
        # rid and ts stay, so a re-send is still recognised by the server.
        if rec.get("host") != host_id():
            rec["host"] = host_id()
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
    safety.refresh_lease(SERVERS, TOKEN, host_id())
    if not safety.lease_ok():
        return None                                 # remote kill / ยังไม่ได้รับอนุญาต / lease หมดอายุ
    return post(build_record(prio, t0))


if __name__ == "__main__":
    try:
        print(main())
    except Exception as e:                          # ห้าม traceback วน — log บรรทัดเดียวแล้วจบ
        print("error", type(e).__name__)

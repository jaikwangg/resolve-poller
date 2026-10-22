#!/usr/bin/env python3
"""server: ingest + query + serve · record schema 3 (stage-based, 5 stage)
stage: data → conform → color → subtitle → master
job_id/stage หลัก = ERP booking (room+time) · fallback = agent-side (record.job / record.stage)
"""
import os, json, glob, datetime, collections, pathlib, logging, threading
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse, FileResponse

import auth
import store as _store

log = logging.getLogger("editortrack")
app = FastAPI()
STORE = _store.ensure_dir(_store.default_store())   # default /srv/editortrack/data (700) · EDITORTRACK_STORE ทับได้
if not os.environ.get("EDITORTRACK_STORE") and any((pathlib.Path(__file__).parent / "data").glob("*.jsonl")):
    log.warning("พบข้อมูลเก่าใน server/data แต่ที่เก็บตอนนี้คือ %s — ย้ายไฟล์ (ดู server/README.md) "
                "หรือตั้ง EDITORTRACK_STORE=data ชั่วคราว", STORE)
MAX_BODY = int(os.environ.get("EDITORTRACK_MAX_BODY", str(1024 * 1024)))   # byte ต่อ record — เกิน = 413
# cadence ของ agent (นาที) — record 1 ตัวแทน min(ช่องว่างถึงตัวถัดไป, INTERVAL_MIN × 1.5) ไม่ใช่ค่าคงที่ (explain.assign_credit)
INTERVAL_MIN = float(os.environ.get("EDITORTRACK_INTERVAL_MIN", "1"))
HERE = pathlib.Path(__file__).parent
STAGES = ["data", "conform", "color", "subtitle", "master"]
# NOTE: ระบบนี้ไม่จัด queue/dependency (subtitle รอ color ฯลฯ) — ERP จัดการเอง
# หน้าที่ระบบ = วัด progress ต่อ stage แล้วส่งให้ ERP

try:
    import erp
except Exception:
    erp = None
import explain as _ex
import report as _rep
import projects as _proj
import pms_sync

_last_pushed = {}          # (job_id, stage) -> % ล่าสุดที่ส่ง PMS สำเร็จ
# ---- push % → PMS ใน background (ไม่อยู่ใน request /ingest: ERP ล่ม/ช้าไม่ทำให้ agent timeout) ----
_pending = {}              # (job_id, stage) -> (pct, extra) ล่าสุดที่ยังไม่ได้ส่ง (รวมค่าเก่าทับ = คิวไม่โต)
_pending_lock = threading.Lock()
_pending_ev = threading.Event()
_pusher = {"thread": None, "failures": 0, "last_error_at": None}
PUSH_RETRY_SEC = int(os.environ.get("EDITORTRACK_PUSH_RETRY_SEC", "60"))


def _push_worker():
    while True:
        _pending_ev.wait()
        with _pending_lock:
            items = dict(_pending)
            _pending.clear()
            _pending_ev.clear()
        failed = False
        for k, (pct, extra) in items.items():
            if _last_pushed.get(k) == pct:
                continue
            ok = False
            try:
                ok = erp.push_progress(k[0], k[1], round(float(pct), 2), extra)
            except Exception:
                ok = False
            if ok:
                _last_pushed[k] = pct
            else:
                failed = True
                _pusher["failures"] += 1
                _pusher["last_error_at"] = _ex.now_local().isoformat()
                with _pending_lock:
                    _pending.setdefault(k, (pct, extra))       # ค่าใหม่กว่ามาแล้วก็ใช้ค่าใหม่
        if failed:                                             # ERP ล่ม → พักก่อนลองใหม่ ไม่ยิงถี่
            threading.Event().wait(PUSH_RETRY_SEC)
            _pending_ev.set()


def _queue_push(k, pct, extra):
    with _pending_lock:
        _pending[k] = (pct, extra)
        if _pusher["thread"] is None or not _pusher["thread"].is_alive():
            _pusher["thread"] = threading.Thread(target=_push_worker, name="pms-progress", daemon=True)
            _pusher["thread"].start()
    _pending_ev.set()
OPEN_PATHS = {"/healthz"}  # ไม่ต้อง login (ไม่มีข้อมูล)


# ---------------- auth: ทุกหน้า + /api/* ต้อง login (Basic) · /ingest ใช้ token ต่อเครื่อง ----------------
@app.middleware("http")
async def _read_auth(request: Request, call_next):
    path = request.url.path
    if path == "/ingest" or path in OPEN_PATHS:
        return await call_next(request)
    if not auth.check_read(request.headers.get("authorization")):
        return JSONResponse({"detail": "ต้อง login (ดู server/README.md §auth)"}, status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="editortrack", charset="UTF-8"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


async def _read_body(req):
    """อ่าน body ไม่เกิน MAX_BODY → bytes · เกิน = 413"""
    try:
        if int(req.headers.get("content-length") or 0) > MAX_BODY:
            raise HTTPException(status_code=413, detail=f"record ใหญ่เกิน {MAX_BODY} byte")
    except ValueError:
        raise HTTPException(status_code=400, detail="content-length ไม่ถูกต้อง")
    buf = bytearray()
    async for chunk in req.stream():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise HTTPException(status_code=413, detail=f"record ใหญ่เกิน {MAX_BODY} byte")
    return bytes(buf)


# ---------------- ingest ----------------
@app.post("/ingest")
async def ingest(req: Request, authorization: str = Header(default="")):
    body = await _read_body(req)
    try:
        rec = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="body ต้องเป็น JSON")
    # record ที่ไม่มี host/ts ใช้นับอะไรไม่ได้ (เทส 8 ต.ค. มี {"host":"selftest"} หลุดเข้ามาปน) → ไม่เก็บ
    host = rec.get("host") if isinstance(rec, dict) else None
    if not isinstance(host, str) or not host:
        raise HTTPException(status_code=400, detail="record ต้องมี host และ ts (ISO มี timezone)")
    ok, why = auth.check_agent(host, authorization)
    if not ok:                                      # token ผูกกับ host: ส่งแทนเครื่องอื่นไม่ได้
        log.warning("ingest rejected: host=%r reason=%s", host, why)
        raise HTTPException(status_code=401, detail=f"unauthorized ({why})")
    if why == "shared":
        log.warning("ingest from %r ใช้ token รวม (EDITORTRACK_TOKEN) — ออก token ต่อเครื่องด้วย manage_tokens.py", host)
    if _ex._dt(rec.get("ts")) is None:
        raise HTTPException(status_code=400, detail="record ต้องมี host และ ts (ISO มี timezone)")
    _store.minimise(rec)                            # ไม่เก็บ user / ชื่อแอป-หน้าต่าง แม้ agent เก่าจะส่งมา
    rec.pop("_job", None)                           # field ของ server — agent ใส่เองไม่ได้
    eff = None
    if erp:                                         # จับคู่ booking ตอนรับ (ใช้ cache อย่างเดียว ไม่รอ ERP) แล้วเก็บไว้กับ record
        try:                                        # → แก้ booking ใน ERP ทีหลังไม่เปลี่ยนประวัติ
            eff = _eff_job(rec, block=False)
            if eff.get("source") == "erp":
                rec["_job"] = {**{k: eff.get(k) for k in ("job_id", "title", "stage", "booking_id", "resource_code")},
                               "source": "erp", "matched_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        except Exception as e:
            log.warning("booking match at ingest failed: %s", e)
    if isinstance(rec, dict):                       # เวลา/IP ฝั่ง server — ใช้ดูว่ามาสดหรือมาจาก offline queue
        rec["_rx"] = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      "ip": req.client.host if req.client else None}
    day = _ex.today().isoformat()                   # ชื่อไฟล์ = วันที่รับตามเวลาท้องถิ่น (EDITORTRACK_TZ)
    with open(STORE / f"{day}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # push progress → ERP (best-effort; no-op ถ้ายังไม่ตั้ง ERP_PROGRESS_PATH) — นี่คือ "หน้าที่หลัก" ของระบบ
    # ส่งเฉพาะ job ที่ได้จาก ERP (project_no จริง — ชื่อ project ของ Resolve PMS ตอบ 422) และเฉพาะตอน % เปลี่ยน
    # ส่งใน background (pms-progress thread) — "pushed" คงไว้เพื่อเข้ากันได้ (false เสมอ) · ดูผลที่ /api/pms
    queued = False
    if erp and eff:
        try:
            pct = (rec.get("progress") or {}).get("pct")
            k = (eff.get("job_id"), eff.get("stage"))
            if (eff.get("source") == "erp" and all(k) and pct is not None and _last_pushed.get(k) != pct
                    and erp.ERP_PROGRESS_PATH and erp.ERP_BASE):
                _queue_push(k, pct, {"observed_at": rec.get("ts"), "host": rec.get("host")})
                queued = True
        except Exception:
            pass
    return {"ok": True, "pushed": False, "queued": queued}


# ---------------- load / helpers ----------------
def _load_recent(days):
    cutoff = _ex.today() - datetime.timedelta(days=days)      # +1 วันเผื่อไฟล์ที่ตั้งชื่อตาม TZ เดิม/ข้ามเที่ยงคืน
    recs = []
    for fp in sorted(glob.glob(str(STORE / "*.jsonl"))):
        try:
            d = datetime.date.fromisoformat(pathlib.Path(fp).stem)
        except ValueError:
            continue
        if d < cutoff:
            continue
        with open(fp, encoding="utf-8") as f:
            for ln in f:
                try:
                    recs.append(_store.minimise(json.loads(ln)))   # ไฟล์เก่าที่ยังมี user/ชื่อแอป → ไม่ส่งออกทาง API
                except Exception:
                    pass
    return _ex.assign_credit(_ex.dedupe(recs), INTERVAL_MIN)


def _day(ts):
    """วันที่ของ ts ตามเวลาท้องถิ่น (EDITORTRACK_TZ) — ตรงกับ /report, /projects และ "วันนี้" """
    return _ex.local_day(ts)


def _daylabel(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def _eff_job(rec, block=True):
    """job_id + stage: booking ที่จับคู่ไว้ตอน ingest (record._job) → ERP lookup สด (record เก่า) → agent-side
    block=False = ใช้ cache booking อย่างเดียว (ไม่รอ ERP)"""
    agent = rec.get("job") or {}
    st = rec.get("_job")
    if isinstance(st, dict) and st.get("job_id"):
        return {"job_id": st["job_id"], "title": st.get("title") or st["job_id"],
                "stage": st.get("stage") or rec.get("stage") or agent.get("role"), "source": "erp",
                "booking_id": st.get("booking_id"), "resource_code": st.get("resource_code")}
    e = erp.lookup(rec.get("host"), rec.get("ts"), (rec.get("source") or {}).get("project"), block) if erp else None
    if e and e.get("job_id"):
        return {"job_id": e["job_id"], "title": e.get("title") or agent.get("title") or e["job_id"],
                "stage": e.get("stage") or rec.get("stage") or agent.get("role"), "source": "erp",
                "booking_id": e.get("booking_id"), "resource_code": e.get("resource_code")}
    return {"job_id": agent.get("job_id"), "title": agent.get("title"),
            "stage": rec.get("stage") or agent.get("role"), "source": agent.get("resolved_by")}


def _group_latest(recs):
    """job_id -> {title, source, tracks: {stage: latest record}}"""
    jobs = {}
    for r in recs:
        eff = _eff_job(r)
        jid, stage = eff.get("job_id"), eff.get("stage")
        if not jid or not stage:
            continue
        d = jobs.setdefault(jid, {"job_id": jid, "title": eff.get("title") or jid,
                                  "source": eff.get("source"), "tracks": {}})
        cur = d["tracks"].get(stage)
        if cur is None or r.get("ts", "") > (cur.get("ts", "")):
            d["tracks"][stage] = r
    return jobs


# ---------------- /api/jobs : per-job dynamic stages ----------------
def build_jobs(days=7):
    jobs = _group_latest(_load_recent(days))
    out = []
    for jid, d in jobs.items():
        tracks, pcts = {}, []
        for stage, r in d["tracks"].items():
            prog = r.get("progress") or {}
            act = r.get("activity") or {}
            pct = prog.get("pct")
            tracks[stage] = {"pct": pct, "worker": r.get("host"), "host": r.get("host"),   # worker = เครื่อง (ไม่เก็บชื่อคน)
                             "page": r.get("page"), "active": _ex.is_active(r),
                             "last_seen": r.get("ts"), "detail": prog.get("detail")}
            if pct is not None:
                pcts.append(pct)
        order = [s for s in STAGES if s in tracks] + [s for s in tracks if s not in STAGES]
        flags = [f"{s}: ยังไม่วัด" for s in order if tracks[s]["pct"] is None]
        out.append({"job_id": jid, "title": d["title"], "source": d["source"],
                    "overall_pct": round(sum(pcts) / len(pcts), 1) if pcts else None,
                    "stage_order": order, "tracks": tracks, "flags": flags})
    out.sort(key=lambda x: (x["overall_pct"] is None, -(x["overall_pct"] or 0)))
    return {"generated_at": _ex.now_local().isoformat(), "stages": STAGES, "jobs": out}


@app.get("/api/jobs")
def api_jobs(days: int = 7):
    return JSONResponse(build_jobs(days))


# ---------------- /api/shots : reconcile ราย shot (conform vs color) ----------------
def build_shots(days=7):
    jobs = _group_latest(_load_recent(days))

    def shots_of(rec):
        return ((rec.get("source") or {}).get("shots") or []) if rec else []

    out = []
    for jid, d in jobs.items():
        conform = {s["code"] for s in shots_of(d["tracks"].get("conform")) if s.get("code")}
        color = {s["code"]: s.get("graded") for s in shots_of(d["tracks"].get("color")) if s.get("code")}
        if not conform and not color:
            continue
        codes = sorted(conform | set(color))
        shots = [{"code": c, "conformed": c in conform, "graded": bool(color.get(c))} for c in codes]
        out.append({"job_id": jid, "title": d["title"], "total_shots": len(codes),
                    "conformed": sum(1 for s in shots if s["conformed"]),
                    "graded": sum(1 for s in shots if s["graded"]), "shots": shots})
    return {"generated_at": _ex.now_local().isoformat(), "jobs": out}


@app.get("/api/shots")
def api_shots(days: int = 7):
    return JSONResponse(build_shots(days))


# ---------------- /api/dashboard : tiles + progress-over-time + phase-time + active/idle ----------------
def build_dashboard(days=7):
    recs = _load_recent(days)
    t0 = _ex.today()
    labels_days = [(t0 - datetime.timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    labels = [_daylabel(d) for d in labels_days]

    phase_time = collections.defaultdict(lambda: collections.Counter())   # host -> page/stage -> min (รายเครื่อง ไม่ใช่รายคน)
    ai = collections.defaultdict(lambda: {d: {"active": 0.0, "idle": 0.0} for d in labels_days})
    byjobday = collections.defaultdict(lambda: collections.defaultdict(dict))   # job -> day -> stage -> pct
    workers, active_recent = set(), set()
    now = datetime.datetime.now(datetime.timezone.utc)

    for r in sorted(recs, key=lambda x: x.get("ts", "")):
        who = r.get("host") or "?"                    # แยกตามเครื่อง — ไม่เก็บ/ไม่แสดงชื่อคน
        workers.add(who)
        act = r.get("activity") or {}
        day = _day(r.get("ts", ""))
        key = r.get("page") or r.get("stage")          # resolve=page · อื่น=stage
        cmin = _ex.credit_sec(r, INTERVAL_MIN) / 60.0
        if _ex.is_active(r) and key:
            phase_time["__team__"][key] += cmin
            phase_time[who][key] += cmin
        if day in ai["__team__"]:
            b = "active" if _ex.is_active(r) else "idle"
            ai["__team__"][day][b] += cmin / 60.0
            ai[who][day][b] += cmin / 60.0
        try:
            if _ex.is_active(r) and (now - datetime.datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))).total_seconds() < 1800:
                active_recent.add(who)
        except Exception:
            pass
        eff = _eff_job(r)
        pct = (r.get("progress") or {}).get("pct")
        if eff.get("job_id") and eff.get("stage") and day and pct is not None:
            byjobday[eff["job_id"]][day][eff["stage"]] = pct

    # progress over time: overall ต่อ job ต่อวัน (carry-forward)
    series = {}
    for jid, dd in byjobday.items():
        carry, row = {}, []
        for d in labels_days:
            if d in dd:
                carry.update(dd[d])
            row.append(round(sum(carry.values()) / len(carry), 1) if carry else 0)
        series[jid] = row

    jobs = build_jobs(days)["jobs"]
    pcts = [j["overall_pct"] for j in jobs if j["overall_pct"] is not None]
    today = t0.isoformat()

    def ai_shape(w):
        return {"active": [round(ai[w][d]["active"], 1) for d in labels_days],
                "idle": [round(ai[w][d]["idle"], 1) for d in labels_days]}

    return {
        "generated_at": now.astimezone(_ex.tz()).isoformat(), "sample": False,
        "summary": {"projects": len(jobs),
                    "avg_progress": round(sum(pcts) / len(pcts), 1) if pcts else 0,
                    "active_hours_today": round(ai["__team__"].get(today, {}).get("active", 0), 1),
                    "workers_active": len(active_recent), "workers_total": len(workers)},
        "progress_series": {"labels": labels, "series": series},
        "phase_time": {w: {k: int(round(v)) for k, v in c.items()} for w, c in phase_time.items()},
        "active_idle": {"labels": labels, "workers": {w: ai_shape(w) for w in ai}},
    }


@app.get("/api/dashboard")
def api_dashboard(days: int = 7):
    return JSONResponse(build_dashboard(days))


# ---------------- /api/inbox : record ที่รับมา + คำอธิบาย ----------------
def build_inbox(limit=50, host=None, days=2):
    recs = _load_recent(days)
    try:
        room_map = erp._room_map() if erp else None
    except Exception:
        room_map = None
    room_map = room_map or None                     # ยังไม่ตั้ง map → ไม่เตือนเรื่อง room
    now = datetime.datetime.now(datetime.timezone.utc)
    today = _ex.today().isoformat()

    hosts = {}
    for r in recs:
        h = r.get("host") or "?"
        d = hosts.setdefault(h, {"host": h, "count": 0, "today": 0, "late": 0, "last": None})
        d["count"] += 1
        d["today"] += _day(r.get("ts", "")) == today
        dl = _ex.delay_sec(r)
        d["late"] += bool(dl is not None and dl > _ex.LATE_SEC)
        if d["last"] is None or r.get("ts", "") > d["last"].get("ts", ""):
            d["last"] = r
    summary = []
    for d in hosts.values():
        last = d.pop("last")
        try:
            age = (now - datetime.datetime.fromisoformat(last["ts"].replace("Z", "+00:00"))).total_seconds()
        except Exception:
            age = None
        summary.append({**d, "stage": last.get("stage"),
                        "ip": (last.get("_rx") or {}).get("ip"), "last_ts": last.get("ts"),
                        "age_sec": age, "online": age is not None and age < _ex.STALE_SEC})
    summary.sort(key=lambda x: x["host"])

    rows = [r for r in recs if not host or r.get("host") == host]
    rows.sort(key=lambda r: (r.get("_rx") or {}).get("at") or r.get("ts", ""), reverse=True)
    out = []
    for r in rows[:max(1, min(limit, 500))]:
        out.append({"explain": _ex.explain(r, _eff_job(r), room_map), "delay_sec": _ex.delay_sec(r), "record": r})
    return {"generated_at": now.astimezone(_ex.tz()).isoformat(), "stale_sec": _ex.STALE_SEC,
            "hosts": summary, "total": len(rows), "records": out}


@app.get("/api/inbox")
def api_inbox(limit: int = 50, host: str = "", days: int = 2):
    return JSONResponse(build_inbox(limit, host or None, days))


@app.get("/inbox")
def inbox():
    return FileResponse(HERE / "inbox.html")


# ---------------- PMS: ส่งเวลาทำงานเป็นช่วง (pms_sync) ----------------
@app.on_event("startup")
def _start_retention():
    _store.start_retention(lambda: STORE, _ex.today)


@app.on_event("startup")
def _start_pms_sync():
    if erp:
        pms_sync.start(_load_recent, INTERVAL_MIN, _eff_job, erp)


@app.get("/api/pms")
def api_pms():
    return JSONResponse({**pms_sync.status, "window_min": pms_sync.WINDOW_MIN,
                         "progress_enabled": bool(erp and erp.ERP_PROGRESS_PATH and erp.ERP_BASE),
                         "last_pushed": [{"job": k[0], "stage": k[1], "pct": v} for k, v in _last_pushed.items()],
                         "progress_pending": len(_pending), "progress_failures": _pusher["failures"],
                         "progress_last_error_at": _pusher["last_error_at"],
                         "erp_cache": erp.cache_status() if erp else None})


# ---------------- /api/report : สรุปรายวันต่อเครื่อง ----------------
@app.get("/api/report")
def api_report(day: str = "", host: str = ""):
    day = day or _ex.today().isoformat()
    try:
        d = datetime.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="day ต้องเป็น YYYY-MM-DD")
    days = (_ex.today() - d).days + 2         # +1 วันเผื่อ record ข้ามเที่ยงคืน (ไฟล์ตั้งชื่อตามวันที่ server รับ)
    return JSONResponse(_rep.build_report(_load_recent(max(days, 2)), day, INTERVAL_MIN, _eff_job, host or None))


@app.get("/report")
def report():
    return FileResponse(HERE / "report.html")


# ---------------- /api/projects : ตัวเลขนับรายโปรเจค (คลิป / เกรด / shot) ----------------
@app.get("/api/projects")
def api_projects(days: int = 30):
    return JSONResponse(_proj.build_projects(_load_recent(max(1, min(days, 3650))), INTERVAL_MIN, _eff_job))


@app.get("/projects")
def projects_page():
    return FileResponse(HERE / "projects.html")


@app.get("/")
def index():
    return FileResponse(HERE / "dashboard.html")


@app.get("/deck")
@app.get("/deck.html")
def deck():
    return FileResponse(HERE / "deck.html")

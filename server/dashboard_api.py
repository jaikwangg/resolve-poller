#!/usr/bin/env python3
"""server: ingest + query + serve · record schema 3 (stage-based, 5 stage)
stage: data → conform → color → subtitle → master
job_id/stage หลัก = ERP booking (room+time) · fallback = agent-side (record.job / record.stage)
"""
import os, json, glob, datetime, collections, logging, pathlib
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse, FileResponse

import auth
import privacy

log = logging.getLogger("editortrack")
# no auto-generated /docs /redoc /openapi.json (they would list every route to anyone)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
# data dir from env; default outside any git checkout (docs/SECURITY.md has the migration command)
STORE = pathlib.Path(os.environ.get("EDITORTRACK_STORE") or "/srv/editortrack")
STORE.mkdir(mode=0o700, parents=True, exist_ok=True)
INTERVAL_MIN = int(os.environ.get("EDITORTRACK_INTERVAL_MIN", "1"))   # นาทีที่ 1 record แทน = cadence ของ agent (ปรับให้ตรง StartInterval/OnUnitActiveSec)
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
import agent_health

# ---------------- auth (server/auth.py, docs/SECURITY.md) ----------------
MACHINE_PATHS = {"/ingest", "/agent/control"}     # per-host machine token, checked in the route
OPEN_PATHS = {"/livez"}                           # liveness only, no data


@app.middleware("http")
async def _read_auth(request: Request, call_next):
    """every route except the machine and liveness paths needs read credentials (fail closed)."""
    path = request.url.path
    if path in MACHINE_PATHS or path in OPEN_PATHS:
        return await call_next(request)
    if not auth.read_ok(request.headers.get("authorization", "")):
        return JSONResponse({"detail": "unauthorized"}, status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="editortrack", charset="UTF-8"'})
    return await call_next(request)


@app.on_event("startup")
def _auth_startup_check():
    st = STORE.stat().st_mode & 0o077
    if st:
        log.warning("store %s is accessible by group/other (chmod 700 recommended)", STORE)
    for w in auth.startup_warnings():
        log.warning(w)


@app.get("/livez")
def livez():
    return {"ok": True}


def _append_record(rec):
    """append one record to today's file, created with mode 600."""
    day = datetime.date.today().isoformat()
    fd = os.open(str(STORE / f"{day}.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------- ingest ----------------
@app.post("/ingest")
async def ingest(req: Request, authorization: str = Header(default="")):
    host = auth.machine_host(authorization)         # token ต่อเครื่อง (token map ฝั่ง server) — ไม่รู้จัก = 401
    if host is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        rec = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body ต้องเป็น JSON")
    # record ที่ไม่มี host/ts ใช้นับอะไรไม่ได้ (เทส 8 ต.ค. มี {"host":"selftest"} หลุดเข้ามาปน) → ไม่เก็บ
    # ts ต้องมี timezone (agent ส่ง UTC "+00:00"): ts ไม่มี offset ทำให้ทุก /api/* 500 และ pms_sync หยุด → 400 ไม่เดาโซน
    rec = _ex.finite(rec)                           # NaN/Infinity -> null (cannot be served back as JSON)
    why = _ex.record_problem(rec)
    if why:
        # X-Editortrack-Reject: the marker that makes agent >= 0.9.1 drop THIS record instead of retrying it
        # (a 400 without it, e.g. from a proxy, is retried) · AGENT_SEND_POLICY.md
        raise HTTPException(status_code=400, detail=f"record ใช้ไม่ได้ ({why}): ต้องมี host และ ts (ISO มี timezone)",
                            headers={"X-Editortrack-Reject": why})
    # checked after record_problem: a record without a usable host gets the final 400 above, never this 403
    # (a 403 is retried by the agent, so a malformed record must not end up here and block the queue)
    if rec.get("host") != host:                     # token ของเครื่องหนึ่งส่งในนามเครื่องอื่นไม่ได้
        raise HTTPException(status_code=403, detail="token นี้ไม่ใช่ของ host นี้")
    privacy.scrub(rec)                              # agent รุ่นเก่ายังส่ง user/ชื่อแอปหน้าสุด → ตัดก่อนเขียนลง disk
    # เวลา/IP ฝั่ง server — ใช้ดูว่ามาสดหรือมาจาก offline queue
    rec["_rx"] = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "ip": req.client.host if req.client else None}
    _append_record(rec)
    # push progress → PMS (contract v2, pms_sync.progress_on_ingest + progress_v2): provisional ≤ 99 until the scope is
    # confirmed and stable, holds while unsure, only ERP-bound project_no · best-effort, no-op until ERP_PROGRESS_PATH
    pushed = pms_sync.progress_on_ingest(rec, _eff_job, erp, STORE) if erp else False
    return {"ok": True, "pushed": pushed}


# ---------------- agent fleet control + health ----------------
@app.get("/agent/control")
def agent_control(host: str = "", v: str = "", authorization: str = Header(default="")):
    """lease ที่ agent ต้องได้ก่อนแตะ Resolve — ไม่มีไฟล์/ไม่รู้จัก/เวอร์ชันไม่อนุญาต = ปิด (fail closed)"""
    who = auth.machine_host(authorization)
    if who is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if host != who:
        raise HTTPException(status_code=403, detail="token นี้ไม่ใช่ของ host นี้")
    return agent_health.control_for(STORE, host, v)


@app.get("/api/health")
def api_health(days: int = 2):
    rows = agent_health.health(_load_recent(days), control=agent_health.load_control(STORE))
    return JSONResponse({"hosts": sorted(rows.values(), key=lambda r: r["host"]),
                         "control": agent_health.load_control(STORE), "skipped": load_skipped.get(days)})


@app.get("/health")
def health_page():
    from fastapi.responses import HTMLResponse
    import html
    rows = agent_health.health(_load_recent(2), control=agent_health.load_control(STORE))
    tr = "".join(
        "<tr class='%s'><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % tuple(
            html.escape(str(x)) for x in (r["status"], r["host"], r["status"], r.get("version"), r.get("last_seen"),
                                          r.get("last_ok_poll"), r.get("max_rss_mb"), r.get("queue_lines"),
                                          "; ".join(r["alerts"] + r["warnings"])))
        for r in sorted(rows.values(), key=lambda r: r["host"]))
    return HTMLResponse("<!doctype html><meta charset=utf-8><title>Agent health</title>"
                        "<style>body{font:14px system-ui;margin:16px;background:#fff;color:#111}"
                        "td,th{padding:4px 8px;border-bottom:1px solid #ddd}.alert{background:#fde2e2}.warn{background:#fff4d6}</style>"
                        "<h1>Agent health</h1><table><tr><th>host</th><th>status</th><th>version</th><th>last seen</th>"
                        "<th>last Resolve poll</th><th>max RSS MB</th><th>queue</th><th>issues</th></tr>" + tr + "</table>")


# ---------------- load / helpers ----------------
# what the last load of each `days` window had to skip (bad JSON line, bad record already on disk), keyed by
# days so the pms_sync thread or /api/projects cannot overwrite what /api/health and /api/inbox show
load_skipped = {}


def _load_recent(days):
    cutoff = datetime.date.today() - datetime.timedelta(days=days - 1)
    recs, bad_json = [], 0
    for fp in sorted(glob.glob(str(STORE / "*.jsonl"))):
        try:
            d = datetime.date.fromisoformat(pathlib.Path(fp).stem)
        except ValueError:
            continue
        if d < cutoff:
            continue
        with open(fp, encoding="utf-8") as f:
            for ln in f:
                if not ln.strip():
                    continue
                try:
                    recs.append(_ex.finite(json.loads(ln)))
                except Exception:
                    bad_json += 1
    reasons = {}
    out = _ex.dedupe(recs, reasons)
    load_skipped[days] = {"days": days, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          "bad_json": bad_json, "bad_record": sum(reasons.values()), "reasons": reasons}
    return out


def _day(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def _daylabel(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def _eff_job(rec):
    """job_id + stage: ERP (zero-touch) ก่อน → fallback agent-side (record.job / record.stage)."""
    agent = rec.get("job") or {}
    e = erp.lookup(rec.get("host"), rec.get("ts"), (rec.get("source") or {}).get("project")) if erp else None
    if e and e.get("job_id"):
        return {"job_id": e["job_id"], "title": e.get("title") or agent.get("title") or e["job_id"],
                "stage": e.get("stage") or rec.get("stage") or agent.get("role"), "source": "erp"}
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
            tracks[stage] = {"pct": pct, "worker": r.get("host"), "host": r.get("host"),
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
    return {"generated_at": datetime.datetime.now().astimezone().isoformat(), "stages": STAGES, "jobs": out}


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
    return {"generated_at": datetime.datetime.now().astimezone().isoformat(), "jobs": out}


@app.get("/api/shots")
def api_shots(days: int = 7):
    return JSONResponse(build_shots(days))


# ---------------- /api/dashboard : tiles + progress-over-time + phase-time + active/idle ----------------
def build_dashboard(days=7):
    recs = _load_recent(days)
    labels_days = [(datetime.date.today() - datetime.timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    labels = [_daylabel(d) for d in labels_days]

    phase_time = collections.defaultdict(lambda: collections.Counter())   # room (host) -> page/stage -> min
    ai = collections.defaultdict(lambda: {d: {"active": 0.0, "idle": 0.0} for d in labels_days})
    byjobday = collections.defaultdict(lambda: collections.defaultdict(dict))   # job -> day -> stage -> pct
    workers, active_recent = set(), set()
    now = datetime.datetime.now(datetime.timezone.utc)

    for r in sorted(recs, key=lambda x: x.get("ts", "")):
        user = r.get("host", "?")       # ต่อห้อง/เครื่อง ไม่ใช่ต่อคน (ไม่เก็บชื่อ login แล้ว)
        workers.add(user)
        act = r.get("activity") or {}
        day = _day(r.get("ts", ""))
        key = r.get("page") or r.get("stage")          # resolve=page · อื่น=stage
        if _ex.is_active(r) and key:
            phase_time["__team__"][key] += INTERVAL_MIN
            phase_time[user][key] += INTERVAL_MIN
        if day in ai["__team__"]:
            b = "active" if _ex.is_active(r) else "idle"
            ai["__team__"][day][b] += INTERVAL_MIN / 60.0
            ai[user][day][b] += INTERVAL_MIN / 60.0
        try:
            if _ex.is_active(r) and (now - datetime.datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))).total_seconds() < 1800:
                active_recent.add(user)
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
    today = datetime.date.today().isoformat()

    def ai_shape(w):
        return {"active": [round(ai[w][d]["active"], 1) for d in labels_days],
                "idle": [round(ai[w][d]["idle"], 1) for d in labels_days]}

    return {
        "generated_at": now.astimezone().isoformat(), "sample": False,
        "summary": {"projects": len(jobs),
                    "avg_progress": round(sum(pcts) / len(pcts), 1) if pcts else 0,
                    "active_hours_today": round(ai["__team__"].get(today, {}).get("active", 0), 1),
                    "workers_active": len(active_recent), "workers_total": len(workers)},
        "progress_series": {"labels": labels, "series": series},
        "phase_time": {w: dict(c) for w, c in phase_time.items()},
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
    today = datetime.date.today().isoformat()

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
        try:                                        # last line of defence: one odd record never 500s the inbox
            ex = _ex.explain(r, _eff_job(r), room_map)
        except Exception as e:
            ex = {"summary": f"อธิบาย record นี้ไม่ได้ ({type(e).__name__})", "lines": [], "warnings": [], "fields": []}
        out.append({"explain": ex, "delay_sec": _ex.delay_sec(r), "record": r})
    return {"generated_at": now.astimezone().isoformat(), "stale_sec": _ex.STALE_SEC,
            "hosts": summary, "total": len(rows), "records": out, "skipped": load_skipped.get(days)}


@app.get("/api/inbox")
def api_inbox(limit: int = 50, host: str = "", days: int = 2):
    return JSONResponse(build_inbox(limit, host or None, days))


@app.get("/inbox")
def inbox():
    return FileResponse(HERE / "inbox.html")


# ---------------- PMS: ส่งเวลาทำงานเป็นช่วง (pms_sync) ----------------
@app.on_event("startup")
def _start_pms_sync():
    if erp:
        pms_sync.start(_load_recent, INTERVAL_MIN, _eff_job, erp)


@app.get("/api/pms")
def api_pms():
    return JSONResponse({**pms_sync.status, "window_min": pms_sync.WINDOW_MIN,
                         "progress_enabled": bool(erp and erp.ERP_PROGRESS_PATH and erp.ERP_BASE),
                         "progress": pms_sync.progress_status})


# ---------------- /api/report : สรุปรายวันต่อเครื่อง ----------------
@app.get("/api/report")
def api_report(day: str = "", host: str = ""):
    day = day or datetime.date.today().isoformat()
    try:
        d = datetime.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="day ต้องเป็น YYYY-MM-DD")
    days = (datetime.date.today() - d).days + 2         # +1 วันเผื่อ record ข้ามเที่ยงคืน (ไฟล์ตั้งชื่อตามวันที่ server รับ)
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

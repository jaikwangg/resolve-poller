#!/usr/bin/env python3
"""server: ingest + query + serve · record schema 3 (stage-based, 5 stage)
stage: data → conform → color → subtitle → master
job_id/stage หลัก = ERP booking (room+time) · fallback = agent-side (record.job / record.stage)
"""
import os, json, glob, datetime, collections, pathlib
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse, FileResponse

app = FastAPI()
STORE = pathlib.Path(os.environ.get("EDITORTRACK_STORE", "data"))
STORE.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get("EDITORTRACK_TOKEN", "")
INTERVAL_MIN = int(os.environ.get("EDITORTRACK_INTERVAL_MIN", "10"))
HERE = pathlib.Path(__file__).parent
STAGES = ["data", "conform", "color", "subtitle", "master"]
# NOTE: ระบบนี้ไม่จัด queue/dependency (subtitle รอ color ฯลฯ) — ERP จัดการเอง
# หน้าที่ระบบ = วัด progress ต่อ stage แล้วส่งให้ ERP

try:
    import erp
except Exception:
    erp = None


# ---------------- ingest ----------------
@app.post("/ingest")
async def ingest(req: Request, authorization: str = Header(default="")):
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")
    rec = await req.json()
    day = datetime.date.today().isoformat()
    with open(STORE / f"{day}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # push progress → ERP (best-effort; no-op ถ้ายังไม่ตั้ง ERP_PROGRESS_PATH) — นี่คือ "หน้าที่หลัก" ของระบบ
    pushed = False
    if erp:
        try:
            eff = _eff_job(rec)
            prog = rec.get("progress") or {}
            if eff.get("job_id") and eff.get("stage") and prog.get("pct") is not None:
                pushed = erp.push_progress(eff["job_id"], eff["stage"], prog["pct"])
        except Exception:
            pass
    return {"ok": True, "pushed": pushed}


# ---------------- load / helpers ----------------
def _load_recent(days):
    cutoff = datetime.date.today() - datetime.timedelta(days=days - 1)
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
                    recs.append(json.loads(ln))
                except Exception:
                    pass
    return recs


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
    e = erp.lookup(rec.get("host"), rec.get("ts")) if erp else None
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
            tracks[stage] = {"pct": pct, "worker": r.get("user"), "host": r.get("host"),
                             "page": r.get("page"), "active": act.get("active"),
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

    phase_time = collections.defaultdict(lambda: collections.Counter())   # worker -> page/stage -> min
    ai = collections.defaultdict(lambda: {d: {"active": 0.0, "idle": 0.0} for d in labels_days})
    byjobday = collections.defaultdict(lambda: collections.defaultdict(dict))   # job -> day -> stage -> pct
    workers, active_recent = set(), set()
    now = datetime.datetime.now(datetime.timezone.utc)

    for r in sorted(recs, key=lambda x: x.get("ts", "")):
        user = r.get("user", "?")
        workers.add(user)
        act = r.get("activity") or {}
        day = _day(r.get("ts", ""))
        key = r.get("page") or r.get("stage")          # resolve=page · อื่น=stage
        if act.get("active") and key:
            phase_time["__team__"][key] += INTERVAL_MIN
            phase_time[user][key] += INTERVAL_MIN
        if day in ai["__team__"]:
            b = "active" if act.get("active") else "idle"
            ai["__team__"][day][b] += INTERVAL_MIN / 60.0
            ai[user][day][b] += INTERVAL_MIN / 60.0
        try:
            if act.get("active") and (now - datetime.datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))).total_seconds() < 1800:
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


@app.get("/")
def index():
    return FileResponse(HERE / "dashboard.html")

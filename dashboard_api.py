#!/usr/bin/env python3
"""
dashboard_api.py — รวม ingest + query + serve dashboard ไว้ในตัวเดียว (แทน server.py เดิม)

  POST /ingest         รับ record จาก runner.py
  GET  /api/dashboard  aggregate JSONL → JSON ที่ dashboard.html กินพอดี
  GET  /               ส่ง dashboard.html

รัน:
  EDITORTRACK_TOKEN=xxx uvicorn dashboard_api:app --host 0.0.0.0 --port 8000

หลักการ aggregate: แต่ละ record คือ snapshot ทุก ~INTERVAL_MIN นาที
→ 1 sample ≈ INTERVAL_MIN นาทีของสถานะนั้น (วิธีมาตรฐานแปลง snapshot เป็นเวลา)
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


# ---------------- ingest ----------------
@app.post("/ingest")
async def ingest(req: Request, authorization: str = Header(default="")):
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")
    rec = await req.json()
    day = datetime.date.today().isoformat()
    with open(STORE / f"{day}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True}


# ---------------- load ----------------
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


# ---------------- aggregate ----------------
def build_dashboard(days=7):
    recs = _load_recent(days)
    labels_days = [(datetime.date.today() - datetime.timedelta(days=days - 1 - i)).isoformat()
                   for i in range(days)]
    labels = [_daylabel(d) for d in labels_days]

    latest_by_proj = {}                      # project -> ล่าสุด (สำหรับ projects[] + summary)
    prog_day = collections.defaultdict(dict) # project -> {day: overall_pct ล่าสุดของวัน}
    phase_time = collections.defaultdict(lambda: collections.Counter())   # worker -> page -> min
    ai = collections.defaultdict(lambda: {d: {"active": 0.0, "idle": 0.0} for d in labels_days})
    workers, active_recent = set(), set()
    now = datetime.datetime.now(datetime.timezone.utc)

    for r in recs:
        ts = r.get("ts", "")
        day = _day(ts)
        user = r.get("user", "unknown")
        res = r.get("resolve", {}) or {}
        act = r.get("activity", {}) or {}
        prog = r.get("progress", {}) or {}
        workers.add(user)

        # phase-time (นับเฉพาะตอนอยู่หน้า Resolve)
        if act.get("resolve_frontmost") and res.get("page"):
            phase_time["__team__"][res["page"]] += INTERVAL_MIN
            phase_time[user][res["page"]] += INTERVAL_MIN

        # active vs idle รายวัน
        if day in ai["__team__"]:
            bucket = "active" if act.get("active_in_resolve") else "idle"
            ai["__team__"][day][bucket] += INTERVAL_MIN / 60.0
            ai[user][day][bucket] += INTERVAL_MIN / 60.0

        # ใครกำลัง active (30 นาทีล่าสุด)
        try:
            if act.get("active_in_resolve") and (now - datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() < 1800:
                active_recent.add(user)
        except Exception:
            pass

        # progress ต่อโปรเจค
        proj = res.get("project")
        if proj and res.get("ok"):
            latest_by_proj[proj] = r
            if prog.get("bounded") and day:
                prog_day[proj][day] = prog.get("overall_pct")

    # projects[]
    projects = []
    pcts = []
    for proj, r in latest_by_proj.items():
        prog = r.get("progress", {}) or {}
        tl = (r.get("resolve", {}) or {}).get("timeline", {}) or {}
        scope = r.get("scope", {}) or {}
        ph = prog.get("phases", {}) if prog.get("bounded") else {}
        phases = {k: ph.get(k) for k in ("edit", "color", "audio", "deliver")}
        projects.append({
            "project": proj,
            "overall_pct": prog.get("overall_pct") if prog.get("bounded") else None,
            "target_sec": scope.get("target_duration_sec"),
            "current_sec": tl.get("duration_sec"),
            "phases": phases,
            "unmeasured": prog.get("unmeasured_phases", []),
            "flags": prog.get("flags", []),
        })
        if prog.get("bounded") and prog.get("overall_pct") is not None:
            pcts.append(prog["overall_pct"])

    # progress_series (ค่าล่าสุดของแต่ละวัน; วันไม่มีข้อมูล = ต่อจากค่าเดิม)
    series = {}
    for proj in latest_by_proj:
        row, last = [], 0
        for d in labels_days:
            if d in prog_day.get(proj, {}) and prog_day[proj][d] is not None:
                last = prog_day[proj][d]
            row.append(round(last, 1))
        series[proj] = row

    today = datetime.date.today().isoformat()
    active_today = round(sum(ai["__team__"].get(today, {}).get("active", 0) for _ in [0]), 1)

    def ai_shape(w):
        return {"active": [round(ai[w][d]["active"], 1) for d in labels_days],
                "idle": [round(ai[w][d]["idle"], 1) for d in labels_days]}

    return {
        "generated_at": now.astimezone().isoformat(),
        "sample": False,
        "summary": {
            "projects": len(projects),
            "avg_progress": round(sum(pcts) / len(pcts), 1) if pcts else 0,
            "active_hours_today": active_today,
            "workers_active": len(active_recent),
            "workers_total": len(workers),
        },
        "projects": projects,
        "progress_series": {"labels": labels, "series": series},
        "phase_time": {w: dict(c) for w, c in phase_time.items()},
        "active_idle": {"labels": labels, "workers": {w: ai_shape(w) for w in ai}},
    }


@app.get("/api/dashboard")
def api_dashboard(days: int = 7):
    try:
        return JSONResponse(build_dashboard(days))
    except Exception as e:
        # ให้ dashboard.html fallback ไป SAMPLE ได้ ถ้ายังไม่มีข้อมูล
        raise HTTPException(status_code=503, detail=f"no_data: {e}")


# ---------------- join: จับ conform-project + color-project เป็น deliverable เดียว ----------------
# metric #1 เป็น 2 track ขนาน (คนละ project + overlap) join ด้วย job_id
# job_id/role หลัก = ERP booking (room+time) · fallback = agent-side (registry/marker/naming) ใน record["job"]
JOB_WEIGHTS = {"conform": 0.45, "color": 0.40, "deliver": 0.15}

try:
    import erp
except Exception:
    erp = None


def _eff_job(rec):
    """resolve job/role ของ record: ERP (zero-touch) ก่อน → fallback agent-side."""
    agent = rec.get("job") or {}
    e = erp.lookup(rec.get("host"), rec.get("ts")) if erp else None
    if e and e.get("job_id"):
        return {"job_id": e["job_id"], "title": e.get("title") or agent.get("title") or e["job_id"],
                "role": e.get("role") or agent.get("role"),   # ผสม: task ใน booking > role เครื่อง
                "linked": True, "source": "erp"}
    return {"job_id": agent.get("job_id"), "title": agent.get("title"), "role": agent.get("role"),
            "linked": agent.get("linked", False), "source": agent.get("resolved_by")}


def build_jobs(days=7):
    recs = _load_recent(days)
    jobs = {}
    for r in recs:
        eff = _eff_job(r)
        jid = eff.get("job_id")
        if not jid:
            continue
        role = (eff.get("role") or "").lower()
        slot = "conform" if role == "conform" else ("color" if role in ("colorist", "color") else None)
        d = jobs.setdefault(jid, {"job_id": jid, "title": eff.get("title") or jid,
                                  "linked": eff.get("linked", False), "source": eff.get("source"),
                                  "conform": None, "color": None})
        if slot and (d[slot] is None or r.get("ts", "") > (d[slot].get("ts", ""))):
            d[slot] = r   # เก็บ record ล่าสุดของแต่ละ role

    def track(rec, phase):
        if not rec:
            return None
        prog = rec.get("progress") or {}
        res = rec.get("resolve") or {}
        act = rec.get("activity") or {}
        ph = prog.get("phases") or {}
        return {"project": res.get("project"), "worker": rec.get("user"),
                "page": res.get("page"), "last_seen": rec.get("ts"),
                "active": act.get("active_in_resolve"),
                "render_pct": ((res.get("render") or {}).get("max_completion") or 0),
                "pct": ph.get(phase)}   # conform→edit fill · color→graded ratio

    out = []
    for jid, d in jobs.items():
        cf = track(d["conform"], "edit")
        co = track(d["color"], "color")
        deliver = max((cf or {}).get("render_pct", 0), (co or {}).get("render_pct", 0))
        avail = {}
        if cf and cf["pct"] is not None:
            avail["conform"] = cf["pct"] / 100.0
        if co and co["pct"] is not None:
            avail["color"] = co["pct"] / 100.0
        if deliver:
            avail["deliver"] = deliver / 100.0
        wsum = sum(JOB_WEIGHTS[k] for k in avail) or 1.0
        overall = round(sum(JOB_WEIGHTS[k] * v for k, v in avail.items()) / wsum * 100, 1) if avail else None
        flags = []
        if not d["linked"]:
            flags.append("unlinked")
        if cf and not co:
            flags.append("color_not_started")
        if co and not cf:
            flags.append("conform_missing")
        if co and co["pct"] is None:
            flags.append("color_unmeasured")   # ต้องเปิด DETECT_GRADE หรือ fallback
        out.append({"job_id": jid, "title": d["title"], "linked": d["linked"], "source": d.get("source"),
                    "conform": cf, "color": co, "deliver_pct": deliver,
                    "overall_pct": overall, "measured": sorted(avail), "flags": flags})
    out.sort(key=lambda x: (x["overall_pct"] is None, -(x["overall_pct"] or 0)))
    return {"generated_at": datetime.datetime.now().astimezone().isoformat(), "jobs": out}


@app.get("/api/jobs")
def api_jobs(days: int = 7):
    return JSONResponse(build_jobs(days))


def build_shots(days=7):
    """reconcile ราย shot ข้าม conform-project กับ color-project (จับคู่ด้วย shot code จาก marker).
    ตอบ open question 'per-reel/shot granularity' — conformed? graded? ต่อ shot."""
    recs = _load_recent(days)
    jobs = {}
    for r in recs:
        eff = _eff_job(r)
        jid = eff.get("job_id")
        if not jid:
            continue
        role = (eff.get("role") or "").lower()
        slot = "conform" if role == "conform" else ("color" if role in ("colorist", "color") else None)
        d = jobs.setdefault(jid, {"job_id": jid, "title": eff.get("title") or jid, "conform": None, "color": None})
        if slot and (d[slot] is None or r.get("ts", "") > (d[slot].get("ts", ""))):
            d[slot] = r

    def shots_of(rec):
        return ((rec.get("resolve") or {}).get("shots") or []) if rec else []

    out = []
    for jid, d in jobs.items():
        conform = {s["code"] for s in shots_of(d["conform"]) if s.get("code")}
        color = {s["code"]: s.get("graded") for s in shots_of(d["color"]) if s.get("code")}
        codes = sorted(conform | set(color))
        shots = [{"code": c, "conformed": c in conform, "graded": bool(color.get(c))} for c in codes]
        out.append({"job_id": jid, "title": d["title"], "total_shots": len(codes),
                    "conformed": sum(1 for s in shots if s["conformed"]),
                    "graded": sum(1 for s in shots if s["graded"]),
                    "shots": shots})
    return {"generated_at": datetime.datetime.now().astimezone().isoformat(), "jobs": out}


@app.get("/api/shots")
def api_shots(days: int = 7):
    return JSONResponse(build_shots(days))


@app.get("/")
def index():
    return FileResponse(HERE / "dashboard.html")

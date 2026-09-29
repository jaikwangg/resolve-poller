# Track A — Resolve / Deterministic Scaffold

ระบบเก็บ **ตัวเลขล้วน** (ไม่ใช้ LLM) สำหรับติดตามงานตัดต่อ DaVinci Resolve บน macOS
เก็บได้ 2 metric: **#1 % ความคืบหน้า (proxy)** และ **#2 เวลา active-in-Resolve**

> ทั้งหมดเป็น deterministic — เชื่อถือได้ เบา ต้นทุนต่ำ และ (สำคัญ) ส่งแต่ตัวเลข ไม่ใช่ภาพ → ความเสี่ยง privacy ต่ำสุด

---

## สารบัญ

1. [Prerequisites](#0-prerequisites)
2. [Directory layout](#directory-layout)
3. [A1 — `resolve_poller.py`](#a1--resolve_pollerpy)
4. [A2 — `activity_sampler.py`](#a2--activity_samplerpy)
5. [A5 — `scope.py` (เจาะลึก)](#a5--scopepy-เจาะลึก)
6. [A3 — `runner.py` (glue) + JSON schema](#a3--runnerpy-glue)
7. [A3 — LaunchAgent packaging](#a3--launchagent-packaging)
8. [A4 — `server.py` (ingest ขั้นต่ำ)](#a4--serverpy-ingest-ขั้นต่ำ)
9. [วิธีรัน](#วิธีรัน)
10. [Probe checklist — สิ่งที่ต้องยืนยันบนเครื่องจริง](#probe-checklist)

---

## 0. Prerequisites

- **DaVinci Resolve Studio** (external scripting เปิดได้เฉพาะตัว Studio ที่เสียเงิน — ตัวฟรี script จากภายนอกไม่ได้)
- เปิด external scripting: `Resolve → Preferences → System → General → External scripting using → Local`
- **Resolve ต้องเปิดค้างไว้** ขณะ poll (API ต่อกับ instance ที่รันอยู่ ไม่ได้อ่าน `.drp` แบบ offline)
- Python 3.9+ และ deps:
  ```bash
  pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa   # สำหรับ A2 sampler
  pip install fastapi uvicorn                                   # สำหรับ A4 server
  ```
- env สำหรับหา Resolve scripting module (ใส่ใน LaunchAgent ด้วย):
  ```bash
  export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
  export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
  export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
  ```

---

## Directory layout

```
editortrack/
├── resolve_poller.py     # A1  ดึง metric จาก Resolve API
├── activity_sampler.py   # A2  idle + frontmost (ไม่ต้องขอ permission)
├── scope.py              # A5  นิยาม target + คำนวณ %
├── runner.py             # A3  รวม A1+A2+A5 → POST
├── server.py             # A4  รับ JSON → เก็บ time-series
├── scopes/
│   └── PROMO_2026Q4.json # A5  registry ตัวอย่าง (target ต่อ job)
└── com.kantana.editortrack.agent.plist   # A3  packaging
```

---

## A1 — `resolve_poller.py`

```python
#!/usr/bin/env python3
"""A1 — snapshot metric จาก DaVinci Resolve Studio ที่รันอยู่ (ไม่ใช้ LLM)."""
import os, sys, datetime

# เปิดหลัง probe ยืนยัน GetNodeGraph/GetVersionNameList บนเวอร์ชันคุณแล้วเท่านั้น
DETECT_GRADE = False


def _load_resolve():
    api = os.environ.get(
        "RESOLVE_SCRIPT_API",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
    )
    mod = os.path.join(api, "Modules")
    if mod not in sys.path:
        sys.path.append(mod)
    try:
        import DaVinciResolveScript as bmd
    except ImportError:
        return None
    return bmd.scriptapp("Resolve")


def snapshot():
    resolve = _load_resolve()
    if resolve is None:
        return {"ok": False, "reason": "resolve_not_running"}

    pm = resolve.GetProjectManager()
    proj = pm.GetCurrentProject() if pm else None
    if proj is None:
        return {"ok": False, "reason": "no_project_open",
                "page": resolve.GetCurrentPage()}

    out = {
        "ok": True,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "page": resolve.GetCurrentPage(),          # media/cut/edit/fusion/color/fairlight/deliver
        "project": proj.GetName(),
        "timeline_count": proj.GetTimelineCount(),
        "rendering": bool(proj.IsRenderingInProgress()),
    }

    tl = proj.GetCurrentTimeline()
    if tl:
        try:
            fps = float(proj.GetSetting("timelineFrameRate")) or 24.0
        except Exception:
            fps = 24.0
        frames = (tl.GetEndFrame() or 0) - (tl.GetStartFrame() or 0)
        vtracks = tl.GetTrackCount("video") or 0
        atracks = tl.GetTrackCount("audio") or 0

        vclips, graded = 0, 0
        for i in range(1, vtracks + 1):
            items = tl.GetItemListInTrack("video", i) or []   # NOTE: transitions ไม่รวม
            vclips += len(items)
            if DETECT_GRADE:
                for it in items:
                    if _is_graded(it):
                        graded += 1

        out["timeline"] = {
            "name": tl.GetName(),
            "duration_sec": round(frames / fps, 2) if fps else None,
            "fps": fps,
            "video_tracks": vtracks,
            "audio_tracks": atracks,
            "video_clips": vclips,
            "graded_clips": graded if DETECT_GRADE else None,   # None = ยังวัดไม่ได้
            "markers": len(tl.GetMarkers() or {}),
        }
        out["scope_hint"] = _scope_from_markers(tl)

    # render queue — สัญญาณชัดว่าเข้าขั้น deliver
    jobs = proj.GetRenderJobList() or []
    comps = []
    for j in jobs:
        jid = j.get("JobId")                        # PROBE: ยืนยันชื่อ key
        st = proj.GetRenderJobStatus(jid) if jid else {}
        comps.append(st.get("CompletionPercentage", 0))   # PROBE: ยืนยันชื่อ key
    out["render"] = {
        "jobs": len(jobs),
        "max_completion": max(comps) if comps else 0,
        "any_complete": any(c >= 100 for c in comps),
    }
    return out


def _is_graded(item):
    """HEURISTIC + version-dependent — ห้ามเชื่อจนกว่าจะ probe.
    เกณฑ์: มี color node มากกว่า 1 (default grade = 1 node)."""
    try:
        g = item.GetNodeGraph()
        n = g.GetNumNodes() if g else 0
        return bool(n and n > 1)
    except Exception:
        return False


def _scope_from_markers(tl):
    """อ่าน marker ที่ note ขึ้นต้น '@scope' เช่น '@scope duration=30 shots=12'.
    ให้ PM ตั้ง target จากใน Resolve ได้เลยโดยไม่ต้องแตะไฟล์."""
    for _, m in (tl.GetMarkers() or {}).items():
        text = f"{m.get('name', '')} {m.get('note', '')}"
        if "@scope" in text:
            kv = {}
            for tok in text.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    kv[k.strip()] = v.strip()
            return kv
    return None


if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
```

---

## A2 — `activity_sampler.py`

```python
#!/usr/bin/env python3
"""A2 — idle time + frontmost app บน macOS. ไม่ต้องขอ permission ใด ๆ."""
from Quartz import (
    CGEventSourceSecondsSinceLastEventType,
    kCGEventSourceStateHIDSystemState,   # hardware-only → ทน mouse-jiggler แบบ post event
    kCGAnyInputEventType,
)
from AppKit import NSWorkspace

# PROBE: ยืนยัน bundle id จริงด้วยการ focus Resolve แล้วดูค่า frontmost_bundle
RESOLVE_BUNDLE_IDS = {"com.blackmagic-design.DaVinciResolve"}
RESOLVE_NAME_HINTS = ("davinci", "resolve")   # fallback เผื่อ bundle id ต่างเวอร์ชัน


def sample():
    idle = CGEventSourceSecondsSinceLastEventType(
        kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
    )
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    bid = (app.bundleIdentifier() or "") if app else ""
    name = (app.localizedName() or "") if app else ""
    is_resolve = bid in RESOLVE_BUNDLE_IDS or any(h in name.lower() for h in RESOLVE_NAME_HINTS)
    return {
        "idle_sec": round(float(idle), 1),
        "frontmost_bundle": bid,
        "frontmost_name": name,
        "resolve_frontmost": bool(is_resolve),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(sample(), ensure_ascii=False, indent=2))
```

> **หมายเหตุ state:** ใช้ `kCGEventSourceStateHIDSystemState` (input ฮาร์ดแวร์จริง) แทน `CombinedSessionState`
> เพราะทน mouse-jiggler แบบ software (post event) ได้ดีกว่า — แต่ dongle ที่ขยับเมาส์จริงยังโกงได้ ถือเป็นเพดานที่รับได้

---

## A5 — `scope.py` (เจาะลึก)

**ปัญหาแกนของ Metric #1:** DaVinci API รู้ *ตัวเศษ* (วางไปเท่าไหร่) แต่ **ไม่รู้ *ตัวส่วน*** (งานนี้ควรมีเท่าไหร่ถึงเรียกว่าเสร็จ) — A5 คือกลไกป้อน "target" เข้ามาให้ % มีความหมาย

### 3 หลักการออกแบบ

**(ก) target มาได้ 2 ทาง — เร็ว + ทางการ**

| วิธี | ใครใช้ | ข้อดี | เป็น authority |
|---|---|---|---|
| **marker ใน Resolve** `@scope duration=30 shots=12` | editor/PM ตั้งจากในโปรแกรม | เร็ว ไม่ต้องออกจาก Resolve | overlay |
| **registry file** `scopes/<project>.json` | PM/ระบบ | ครบ (weight, deliverable) แก้/version ได้ | ✅ authority |

รวมกัน: registry เป็นตัวจริง, marker เป็น shortcut ที่ overlay ทับได้

**(ข) % ไม่ใช่ "duration filled / target" เฉย ๆ** — เพราะ color/audio/deliver ไม่ได้สะท้อนในความยาว timeline
ใช้ **phase-weighted completion**:

```
overall = Σ (นน.เฟส × ความคืบหน้าเฟส)   เฉพาะเฟสที่ "วัดได้จริง"
```

**(ค) ความซื่อสัตย์ = แยก "วัดได้" ออกจาก "ทั้งงาน"**
เฟสที่ยังวัดไม่ได้ (เช่น color เมื่อ `DETECT_GRADE=False`, audio ที่ยังไม่ทำ) จะ**ไม่ถูกนับเป็น 0** แต่ถูก**ตัดออกแล้ว renormalize** น้ำหนักเฉพาะเฟสที่วัดได้ — พร้อมส่ง `measured_phases` / `unmeasured_phases` กลับไปเสมอ เพื่อไม่ให้ใครเข้าใจผิดว่านี่คือ % ของทั้งงาน

### scope schema (`scopes/PROMO_2026Q4.json`)

```json
{
  "scope_version": 1,
  "project": "PROMO_2026Q4",
  "job_id": "PROMO_Q4_30s",
  "role": "conform",
  "target_duration_sec": 30,
  "expected_shots": 12,
  "deliverables": 2,
  "phase_weights": { "edit": 0.45, "color": 0.40, "deliver": 0.15 },
  "set_by": "pm@kantana",
  "created_at": "2026-09-24"
}
```
> `phase_weights` รวมกัน = 1.0 · ปรับตามชนิดงานได้ (สารคดีเน้น edit, MV เน้น color)
> **`job_id` = key join ข้าม project** — project ของ conform กับของ colorist ใช้ `job_id` เดียวกัน dashboard ถึง join เป็น deliverable เดียว (2 track ขนาน) · **`role` = conform|colorist ต่อ project/เครื่อง** (conform วัด edit/deliver, colorist วัด color)

### `scope.py`

```python
#!/usr/bin/env python3
"""A5 — โหลด target scope และคำนวณ % แบบ phase-weighted (renormalize เฉพาะเฟสที่วัดได้)."""
import json, os, re

SCOPE_DIR = os.environ.get("EDITORTRACK_SCOPES", os.path.expanduser("~/.editortrack/scopes"))
DEFAULT_WEIGHTS = {"edit": 0.45, "color": 0.40, "deliver": 0.15}  # งานหลัก = ตัดต่อ+ปรับสี (audio ตัดออก)


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "unknown")


def load_scope(project, marker_hint=None):
    """ลำดับ: registry file (authority) → marker hint overlay → None."""
    scope = None
    path = os.path.join(SCOPE_DIR, f"{_safe(project)}.json")
    if project and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            scope = json.load(f)

    if marker_hint:                       # overlay/สร้างจาก marker
        scope = scope or {"project": project, "phase_weights": DEFAULT_WEIGHTS, "source": "marker"}
        if "duration" in marker_hint:
            scope["target_duration_sec"] = float(str(marker_hint["duration"]).rstrip("s"))
        if "shots" in marker_hint:
            scope["expected_shots"] = int(marker_hint["shots"])

    if scope and "phase_weights" not in scope:
        scope["phase_weights"] = DEFAULT_WEIGHTS
    return scope


def compute_progress(snap, scope):
    tl = snap.get("timeline", {}) or {}

    # ไม่มี target → รายงานค่าดิบ ไม่ปั้นเป็น %
    if not scope or not scope.get("target_duration_sec"):
        return {
            "bounded": False,
            "note": "ยังไม่ได้ตั้ง target scope — รายงานค่าดิบแทน %",
            "absolute": {"duration_sec": tl.get("duration_sec"),
                         "video_clips": tl.get("video_clips")},
        }

    phases = {}
    # edit — สัดส่วนความยาวที่วางแล้วเทียบเป้า
    if tl.get("duration_sec") is not None:
        phases["edit"] = _clamp(tl["duration_sec"] / scope["target_duration_sec"])
    # deliver — % render สูงสุดในคิว
    phases["deliver"] = _clamp(snap.get("render", {}).get("max_completion", 0) / 100.0)
    # color — best-effort เฉพาะเมื่อ probe ยืนยันแล้ว
    if tl.get("graded_clips") is not None and tl.get("video_clips"):
        phases["color"] = _clamp(tl["graded_clips"] / tl["video_clips"])
    # audio — TODO: ต้องดึง audio coverage (ดู Probe checklist)

    w = scope.get("phase_weights", DEFAULT_WEIGHTS)
    measured = {k: v for k, v in phases.items() if v is not None}
    wsum = sum(w.get(k, 0) for k in measured) or 1.0
    overall = sum(w.get(k, 0) * v for k, v in measured.items()) / wsum

    all_phases = set(w) | set(phases)
    unmeasured = sorted(all_phases - set(measured))
    flags = []
    if tl.get("duration_sec", 0) > scope["target_duration_sec"]:
        flags.append("over_target_duration")   # วางเกินเป้า — อาจต้องตัดออก

    return {
        "bounded": True,
        "overall_pct": round(overall * 100, 1),          # % ของ "เฟสที่วัดได้" (ไม่ใช่ทั้งงาน)
        "phases": {k: round(v * 100, 1) for k, v in measured.items()},
        "measured_phases": sorted(measured),
        "unmeasured_phases": unmeasured,                 # โปร่งใส: อะไรที่ยังไม่นับ
        "flags": flags,
    }
```

### ตัวอย่างคำนวณ (ให้เห็นภาพ)

เป้า 30 วิ · timeline วางแล้ว 24 วิ · ยังไม่ queue render · color/audio ยังวัดไม่ได้

```
edit    = 24/30            = 0.80
deliver = 0/100            = 0.00
measured = {edit, deliver} → wsum = 0.45 + 0.15 = 0.60
overall = (0.45×0.80 + 0.15×0.00) / 0.60 = 0.36/0.60 = 0.600

→ overall_pct = 60.0%   (measured: edit, deliver | unmeasured: color ← เฟสหลัก!)
```
อ่านว่า: *"ในเฟสที่วัดได้ (ตัดต่อ+ส่งออก) ~60% — ยังไม่นับ color ซึ่งเป็นเฟสหลัก"*
ไม่ใช่ "งานเสร็จ 60%" — dashboard ต้องโชว์ `unmeasured_phases` กำกับเสมอ
(→ ยิ่งตอกย้ำว่า **detect สีให้ได้สำคัญมาก** เพราะ color นน .40 ถ้าไม่วัด % จะเพี้ยนสูง)

---

## A3 — `runner.py` (glue)

```python
#!/usr/bin/env python3
"""A3 — poll → merge → คำนวณ → POST (มี offline queue)."""
import json, os, socket, getpass, datetime, urllib.request, pathlib
from resolve_poller import snapshot
from activity_sampler import sample
from scope import load_scope, compute_progress

IDLE_THRESHOLD = int(os.environ.get("EDITORTRACK_IDLE", "90"))   # วิ; เกินนี้ = idle
SERVER = os.environ.get("EDITORTRACK_SERVER", "http://localhost:8000/ingest")
TOKEN = os.environ.get("EDITORTRACK_TOKEN", "")
QUEUE = pathlib.Path(os.path.expanduser("~/.editortrack/queue.jsonl"))


def build_record():
    snap = snapshot()
    act = sample()
    rec = {
        "schema": 1,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "resolve": snap,
        "activity": {
            **act,
            # active จริง = Resolve อยู่หน้า + มี input ภายใน threshold
            "active_in_resolve": bool(act["resolve_frontmost"] and act["idle_sec"] < IDLE_THRESHOLD),
        },
    }
    if snap.get("ok"):
        scope = load_scope(snap.get("project"), snap.get("scope_hint"))
        rec["scope"] = scope
        rec["progress"] = compute_progress(snap, scope)
    return rec


def post(rec):
    data = json.dumps(rec, ensure_ascii=False).encode()
    req = urllib.request.Request(
        SERVER, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"},
    )
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
```

### JSON schema ที่ POST ขึ้น server (schema 1)

```json
{
  "schema": 1,
  "ts": "2026-09-24T09:10:00Z",
  "host": "edit-bay-03",
  "user": "somchai",
  "resolve": {
    "ok": true, "page": "color", "project": "PROMO_2026Q4",
    "timeline_count": 3, "rendering": false,
    "timeline": {
      "name": "PROMO v7", "duration_sec": 24.0, "fps": 25.0,
      "video_tracks": 3, "audio_tracks": 4, "video_clips": 11,
      "graded_clips": null, "markers": 5
    },
    "scope_hint": {"duration": "30", "shots": "12"}
  },
  "activity": {
    "idle_sec": 4.2, "frontmost_bundle": "com.blackmagic-design.DaVinciResolve",
    "frontmost_name": "DaVinci Resolve", "resolve_frontmost": true,
    "active_in_resolve": true
  },
  "scope": { "target_duration_sec": 30, "phase_weights": {"edit":0.40,"color":0.25,"audio":0.20,"deliver":0.15} },
  "progress": {
    "bounded": true, "overall_pct": 58.2,
    "phases": {"edit": 80.0, "deliver": 0.0},
    "measured_phases": ["deliver", "edit"],
    "unmeasured_phases": ["audio", "color"], "flags": []
  }
}
```

---

## A3 — LaunchAgent packaging

`~/Library/LaunchAgents/com.kantana.editortrack.agent.plist`
> ต้องเป็น **LaunchAgent (per-user)** ไม่ใช่ LaunchDaemon (root) — ไม่งั้น idle/frontmost อ่านเพี้ยนเพราะไม่มี GUI session

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.kantana.editortrack.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/opt/editortrack/runner.py</string>
  </array>
  <key>StartInterval</key><integer>600</integer>          <!-- ทุก 10 นาที -->
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/editortrack.out.log</string>
  <key>StandardErrorPath</key><string>/tmp/editortrack.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>RESOLVE_SCRIPT_API</key><string>/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting</string>
    <key>RESOLVE_SCRIPT_LIB</key><string>/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so</string>
    <key>PYTHONPATH</key><string>/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules</string>
    <key>EDITORTRACK_SERVER</key><string>https://track.internal/ingest</string>
    <key>EDITORTRACK_TOKEN</key><string>__PER_DEVICE_TOKEN__</string>
  </dict>
</dict>
</plist>
```
```bash
launchctl load ~/Library/LaunchAgents/com.kantana.editortrack.agent.plist
```

---

## A4 — `server.py` (ingest ขั้นต่ำ)

```python
#!/usr/bin/env python3
"""A4 — รับ record แล้ว append เป็น JSONL รายวัน (durable, ง่ายสุด). อัปเกรดเป็น sqlite/TSDB ทีหลังได้."""
import json, datetime, pathlib, os
from fastapi import FastAPI, Request, Header, HTTPException

app = FastAPI()
STORE = pathlib.Path(os.environ.get("EDITORTRACK_STORE", "data"))
STORE.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get("EDITORTRACK_TOKEN", "")   # ตั้งให้ตรงกับ agent


@app.post("/ingest")
async def ingest(req: Request, authorization: str = Header(default="")):
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")
    rec = await req.json()
    day = datetime.date.today().isoformat()
    with open(STORE / f"{day}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True}
```
```bash
EDITORTRACK_TOKEN=xxx uvicorn server:app --host 0.0.0.0 --port 8000
```

---

## วิธีรัน

```bash
# 1) ทดสอบ A1 (เปิด Resolve + โปรเจคไว้ก่อน)
python3 resolve_poller.py

# 2) ทดสอบ A2
python3 activity_sampler.py

# 3) ตั้ง target แล้วยิงทั้ง record 1 ครั้ง
mkdir -p ~/.editortrack/scopes && cp scopes/PROMO_2026Q4.json ~/.editortrack/scopes/
EDITORTRACK_SERVER=http://localhost:8000/ingest EDITORTRACK_TOKEN=xxx python3 runner.py

# 4) รันอัตโนมัติ
launchctl load ~/Library/LaunchAgents/com.kantana.editortrack.agent.plist
```

---

## Probe checklist

สิ่งที่ต้อง**ยืนยันบนเครื่อง+เวอร์ชันจริง**ก่อน trust (ผมทำ probe script ให้ได้):

- [ ] `DaVinciResolveScript` import ผ่าน (env ถูก, external scripting = Local, เป็น Studio)
- [ ] **bundle id ของ Resolve** — focus Resolve แล้วดู `frontmost_bundle` จริง → ใส่ใน `RESOLVE_BUNDLE_IDS`
- [ ] `GetRenderJobList()` คืน key ชื่อ `JobId` และ `GetRenderJobStatus()` มี `CompletionPercentage` จริงไหม
- [ ] color detection: `TimelineItem.GetNodeGraph().GetNumNodes()` มีบนเวอร์ชันคุณไหม → ถ้ามีค่อยเปิด `DETECT_GRADE=True`
- [ ] audio coverage: หา method ดึงความยาว audio ที่วางจริง เพื่อเปิดเฟส `audio`
- [ ] `GetCurrentPage()` คืนค่าตรงตามหน้าที่เปิด (ใช้ track เวลาต่อเฟส)

---

## ⚠️ ข้อจำกัดที่ยังติดตัว Track A

- **ต้องเปิด Resolve + เป็น Studio** — เครื่องที่ปิดโปรแกรมอยู่จะไม่มีข้อมูล #1 (แต่ #2 activity ยังเก็บได้)
- **% เป็น proxy ของเฟสที่วัดได้เท่านั้น** — ห้ามเอาไปผูกเงินเดือน/ประเมินตรง ๆ
- **mouse-jiggler แบบ dongle** ยังปลอม active ได้ (ยอมรับเป็นเพดาน)
- ต้องมี **PDPA policy + แจ้งล่วงหน้า** (แม้ส่งแต่ตัวเลข ก็เป็นข้อมูลส่วนบุคคล)
```

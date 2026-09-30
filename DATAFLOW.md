# Dataflow — editortrack (ละเอียด + คำสั่ง)

end-to-end ตั้งแต่ Resolve/ERP → agent → server → dashboard พร้อมคำสั่งทุก stage

---

## ภาพรวม

```
                    ┌───────────────────────────────────────────┐
   ERP (KPP)        │  workstation ×7 (1 edit/conform + 6 color) │
 booking/schedule   │  agent: LaunchAgent/Task รันทุก ~10 นาที    │
        │           │                                            │
        │           │   resolve_poller.py ──┐  (read-only,       │
        │           │   activity_sampler.py ┘   ไม่เปลี่ยนหน้า)   │
        │           │            │ runner.py รวม + คิด %          │
        │           │            ▼ POST /ingest (Bearer token)   │
        │           └────────────┼───────────────────────────────┘
        │  (server ดึงเป็นรอบ)     ▼
        └────────────►  ┌──────────────────────────────────┐
                        │  server: dashboard_api.py          │
                        │   /ingest → JSONL รายวัน (data/)    │
                        │   erp.py: join room+time → job     │
                        │   build_dashboard/jobs/shots       │
                        │   /api/* + serve dashboard.html    │
                        └───────────────┬────────────────────┘
                                        ▼
                        [browser] dashboard: per-job 2-track
                        Conform ▓▓▓ / Color ▓░ + phase-time + active/idle
```

---

## Stage 1 — Agent poll (ต่อ workstation)

**ทำอะไร:** ทุก ~10 นาที อ่านสถานะ Resolve + กิจกรรมเครื่อง → ส่ง record ขึ้น server

| ไฟล์ | function | อ่าน/ทำอะไร |
|---|---|---|
| `resolve_poller.py` | `snapshot()` | Resolve API (read-only): `page`, timeline (duration/clips/**graded**/scan_ms/markers), **shots** (จาก marker), `scope_hint`/`job_hint` · POLL_RENDER off (ไม่เด้ง Deliver) · จำ+คืนหน้า |
| `activity_sampler.py` | `sample()` | idle-time + frontmost app (mac/win/linux) → `active_in_resolve` |
| `scope.py` | `compute_progress()` | คิด % ต่อเฟส: edit = avg(duration/target, shots/expected) · color = graded/total |
| `joblink.py` | `resolve_job()` | ผูก project → job_id/role (marker > registry > naming > fallback + host role) |
| `runner.py` | `build_record()` → `post()` | รวมทั้งหมด → record schema 2 → POST /ingest (มี offline queue) |

**record ที่ส่ง (schema 2):**
```json
{ "schema":2, "ts":"<UTC>", "host":"grade-03", "user":"...",
  "resolve":{ "ok":true, "page":"color", "project":"...",
              "timeline":{"duration_sec":1421.9,"video_clips":309,"graded_clips":278,
                          "markers":59,"scan_ms":156},
              "shots":[{"code":"PST_R04_00010","graded":true}], "job_hint":null },
  "activity":{ "idle_sec":3.2,"resolve_frontmost":true,"active_in_resolve":true,"platform":"win32" },
  "job":{ "job_id":"...","role":"colorist","linked":true },
  "progress":{ "bounded":true,"overall_pct":90.0,"phases":{"color":90.0},
               "edit_detail":null,"shots_seen":57 } }
```

**คำสั่ง — ทดสอบ agent แบบ manual (บนเครื่องมี Resolve):**
```bash
cd agent                      # โค้ด agent อยู่ในโฟลเดอร์ agent/
# env (per OS — ดู §Config)
python resolve_poller.py      # ดู snapshot จริง
python activity_sampler.py --watch   # ดู idle/frontmost สด
EDITORTRACK_SERVER=http://<server>:8000/ingest EDITORTRACK_TOKEN=xxx python runner.py
```

---

## Stage 2 — Ingest (server)

**`dashboard_api.py` → `POST /ingest`**: verify Bearer token → append `data/YYYY-MM-DD.jsonl`

```bash
cd server && pip install -r requirements.txt     # โค้ด server อยู่ในโฟลเดอร์ server/
EDITORTRACK_TOKEN=xxx uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
```

---

## Stage 3 — ERP join (server, zero-touch)

**`erp.py`**: server ดึง booking จาก ERP แล้ว join `room(=host) + time(=ts)` → `job_id/role/title`

```
login (POST /api/auth/login) → access_token (8h, cache+refresh)
  → GET /api/schedule?start_dt&end_dt → data.bookings[]
  → match: resources[is_primary].resource_code == ROOM_MAP[host]  และ  start_dt ≤ ts ≤ end_dt
  → job_id = project_no · role = TASK_ROLE[activity]  (Grading→colorist, Conform/Edit→conform)
```
ใช้ใน `build_jobs._eff_job()`: **ERP ก่อน → fallback agent-side (joblink)**

**คำสั่ง — เทส ERP:**
```bash
# login เอา token
curl -s -X POST http://<erp>:8010/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"<user>","password":"<pass>"}'
# ดึง schedule
curl -s "http://<erp>:8010/api/schedule?start_dt=2026-09-30T00:00:00Z&end_dt=2026-10-01T00:00:00Z" \
  -H "Authorization: Bearer <access_token>"
```

---

## Stage 4 — Aggregate + serve (server)

| endpoint | function | คืนอะไร |
|---|---|---|
| `GET /api/dashboard` | `build_dashboard()` | tiles, progress-over-time, phase-time, active/idle |
| `GET /api/jobs` | `build_jobs()` | ต่อ job: 2 track (conform/color) + overall + flags |
| `GET /api/shots` | `build_shots()` | reconcile ราย shot ข้าม conform/color (conformed? graded?) |
| `GET /` | serve `dashboard.html` | หน้า dashboard |

**คำสั่ง — เทส endpoints:**
```bash
curl -s localhost:8000/api/jobs | python3 -m json.tool
curl -s localhost:8000/api/shots | python3 -m json.tool
```

**/api/jobs (ย่อ):**
```json
{"jobs":[{"job_id":"...","conform":{"worker":"...","pct":80,"page":"edit","active":true},
          "color":{"worker":"...","pct":35,"page":"color","active":true},
          "overall_pct":57.5,"source":"erp","flags":[]}]}
```

---

## Stage 5 — Dashboard (browser)

เปิด `http://<server>:8000/` → หน้า per-job 2-track (Conform/Color) + ชาร์ต · ดึง `/api/jobs`+`/api/dashboard` เอง

---

## Config (env) — สรุปครบ

**Agent (ต่อ workstation):**
```
# Resolve scripting (per OS)
RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB / PYTHONPATH
# role + behaviour
EDITORTRACK_ROLE=conform|colorist          # 1 เครื่อง = 1 role
EDITORTRACK_DETECT_GRADE=1                  # color room=1 · edit room=0
EDITORTRACK_EXTRACT_SHOTS=1                 # ดึง shots จาก marker
EDITORTRACK_POLL_RENDER=0                   # ห้ามเปิด (เด้งหน้า Deliver)
EDITORTRACK_RESTORE_PAGE=1                  # กันเหนียว คืนหน้า
EDITORTRACK_IDLE=90                         # วิ, เกิน=idle
# ปลายทาง
EDITORTRACK_SERVER=http://<server>:8000/ingest
EDITORTRACK_TOKEN=<token>
```

**Server:**
```
EDITORTRACK_TOKEN=<token>        # ต้องตรงกับ agent
EDITORTRACK_STORE=data           # dir เก็บ JSONL
EDITORTRACK_INTERVAL_MIN=10      # 1 sample ≈ กี่นาที (ให้ตรง poll)
# ERP (creds — อย่า commit)
ERP_BASE_URL=http://<erp>:8010
ERP_USERNAME=<user>  ERP_PASSWORD=<pass>
ERP_LOGIN_PATH=/api/auth/login
ERP_BOOKINGS_PATH=/api/schedule?start_dt={start}&end_dt={end}
ERP_RECORDS_PATH=data.bookings
ERP_ROOM_MAP={"grade-03":"RM-03","conform-01":"RM-07"}   # host↔resource_code (IT กรอก)
```

**Resolve scripting paths ต่อ OS:**
```
# Windows
RESOLVE_SCRIPT_API=C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting
RESOLVE_SCRIPT_LIB=C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll
# macOS
RESOLVE_SCRIPT_API=/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting
RESOLVE_SCRIPT_LIB=/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so
# Linux
RESOLVE_SCRIPT_API=/opt/resolve/Developer/Scripting
RESOLVE_SCRIPT_LIB=/opt/resolve/libs/Fusion/fusionscript.so
# ทุก OS: PYTHONPATH += <RESOLVE_SCRIPT_API>/Modules
```

---

## Deploy — รัน agent อัตโนมัติ

**macOS (LaunchAgent):** `~/Library/LaunchAgents/com.kantana.editortrack.agent.plist` (ตัวอย่างใน TRACK_A.md) → `launchctl load ...`

**Windows (Task Scheduler):**
```cmd
schtasks /create /tn editortrack /tr "python C:\path\resolve-poller\agent\runner.py" /sc minute /mo 10 /ru <user>
```

**Linux (systemd user timer หรือ cron):**
```
*/10 * * * * cd ~/resolve-poller/agent && /usr/bin/python3 runner.py    # crontab -e (ใน X11 session)
```
> Linux ต้อง `libxss1` (idle) + (option) `xdotool` (frontmost) · X11 เท่านั้น

---

## ลำดับ deploy จริง (checklist)
1. รัน `probe.py` ต่อรุ่น Resolve → ยืนยัน DETECT_GRADE/keys
2. server: ตั้ง env (TOKEN + ERP creds + ROOM_MAP) → `uvicorn dashboard_api:app`
3. ทดสอบ ERP: curl login + schedule
4. แต่ละ workstation: ตั้ง env (ROLE, DETECT_GRADE, SERVER, TOKEN, RESOLVE_*) → เทส `python runner.py` 1 ครั้ง
5. ตั้ง scheduler (LaunchAgent/Task/cron) ทุก 10 นาที
6. เปิด dashboard `http://<server>:8000/`

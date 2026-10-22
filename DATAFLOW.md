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
| `activity_sampler.py` | `sample()` | idle-time + "Resolve อยู่หน้าสุดไหม" (bool, ตัดสินจาก process identity — ไม่ส่งชื่อแอป/หน้าต่าง) mac/win/linux |
| `scope.py` | `compute_progress()` | คิด % ต่อเฟส: edit = avg(duration/target, shots/expected) · color = graded/total |
| `joblink.py` | `resolve_job()` | ผูก project → job_id/role (marker > registry > naming > fallback + host role) |
| `runner.py` | `build_record()` → `post()` | รวมทั้งหมด → record schema 3 → POST /ingest (มี offline queue) |

**record ที่ส่ง (schema 3 — ตัวอย่าง stage color):**
```json
{ "schema":3, "ts":"<UTC>", "host":"grade-03", "stage":"color", "page":"color",
  "activity":{ "idle_sec":3.2, "active":true, "resolve_frontmost":true, "locked":null, "platform":"linux" },
  "source":{ "ok":true, "page":"color", "project":"KPP-2026-0005_grade",
             "timeline":{"name":"R04_v3","duration_sec":1421.9,"video_clips":309,"graded_clips":278,
                         "markers":59,"scan_ms":156,"grade":{"method":"lut"}},
             "job_hint":null, "scope_hint":null },
  "job":{ "job_id":"KPP-2026-0005","resolved_by":"naming","linked":true },
  "progress":{ "stage":"color","pct":90.0,"detail":{"graded_clips":278,"video_clips":309} } }
```

### ข้อมูลที่เก็บ / ไม่เก็บ (data sheet — ตรงกับโค้ด ณ 2026-10-22)
| เก็บ | ใช้ทำอะไร |
|---|---|
| `host` (ชื่อเครื่อง) | แยกห้อง → join booking (ไม่ใช่ตัวคน) |
| `ts`, `_rx.at`, `_rx.ip` (server เติม) | เวลา, ดูว่ามาจาก offline queue ไหม |
| `activity.idle_sec`, `active`, `resolve_frontmost` (bool), `locked` (bool) | เวลาทำงานใน Resolve |
| ชื่อ project / timeline ใน Resolve, จำนวน clip/clip ที่เกรด/marker, หน้า Resolve | % ความคืบหน้า + ผูก booking |

| **ไม่เก็บ** (ตั้งแต่ 2026-10-22) | หมายเหตุ |
|---|---|
| `user` (ชื่อ login) | agent ไม่ส่ง · server ตัดทิ้งแม้ agent เก่าส่งมา |
| ชื่อแอป/ชื่อหน้าต่างหน้าสุด (`frontmost_name`, `frontmost_bundle`) | agent ส่งแค่ boolean · ตัดสินจาก bundle id (mac) / executable ของ process (Linux `/proc/<pid>/exe`, Windows `QueryFullProcessImageName`) ตรงตัว — แท็บ browser ที่มีคำว่า "resolve" ไม่นับ |
| shot code จาก marker | ปิดเป็น default (`EDITORTRACK_EXTRACT_SHOTS=0`) — เปิดเมื่อตกลงกับเจ้าของงานแล้วเท่านั้น |

ที่เก็บ: `/srv/editortrack/data` (สิทธิ์ 700, นอก git checkout) · ลบอัตโนมัติเมื่อเก่ากว่า `EDITORTRACK_RETENTION_DAYS` (default 180 วัน) · ไฟล์เดิมที่ยังมี user/ชื่อแอป: `python store.py scrub <dir>` · ทุกหน้า/API ต้อง login

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

**`dashboard_api.py` → `POST /ingest`**: body ≤ 1 MB + ต้องเป็น JSON → token ต้องเป็นของ `host` ใน record (hash ใน `agent_tokens.json`) → ตัด `user`/ชื่อแอป → append `$EDITORTRACK_STORE/YYYY-MM-DD.jsonl` (ชื่อไฟล์ = วันที่ server รับ)

```bash
cd server && pip install -r requirements.txt     # โค้ด server อยู่ในโฟลเดอร์ server/
python manage_tokens.py issue <host> && python manage_tokens.py read-user <ชื่อ> && python run.py   # bind 127.0.0.1 · LAN: proxy TLS หรือ --lan (server/README.md §ความปลอดภัย)
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
EDITORTRACK_EXTRACT_SHOTS=0                 # ดึง shot code จาก marker — default ปิด (ข้อมูลลูกค้า)
EDITORTRACK_POLL_RENDER=0                   # ห้ามเปิด (เด้งหน้า Deliver)
EDITORTRACK_RESTORE_PAGE=1                  # กันเหนียว คืนหน้า
EDITORTRACK_IDLE=90                         # วิ, เกิน=idle
# ปลายทาง
EDITORTRACK_SERVER=http://<server>:8000/ingest
EDITORTRACK_TOKEN=<token ของเครื่องนี้>    # ผูกกับ hostname — ไม่มี default
```

**Server:**
```
EDITORTRACK_TOKENS_FILE=/srv/editortrack/agent_tokens.json   # token ต่อเครื่อง (hash) — python manage_tokens.py issue <host>
EDITORTRACK_READ_USERS_FILE=/srv/editortrack/read_users.json # user อ่าน dashboard — python manage_tokens.py read-user <ชื่อ>
EDITORTRACK_READ_AUTH=basic      # ทุกหน้า + /api/* ต้อง login · none = ให้ proxy ทำ (bind 127.0.0.1 เท่านั้น)
EDITORTRACK_BIND=127.0.0.1       # run.py · LAN ผ่าน reverse proxy TLS หรือ --lan
EDITORTRACK_STORE=/srv/editortrack/data   # dir เก็บ JSONL (700) — default $EDITORTRACK_HOME/data · ของเดิม =data ยังใช้ได้
EDITORTRACK_RETENTION_DAYS=180   # ลบไฟล์เก่ากว่านี้อัตโนมัติ (0 = ไม่ลบ)
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
2. server: `manage_tokens.py issue <host>` ทุกเครื่อง + `read-user` + ERP creds + ROOM_MAP → `python run.py` (+ Caddy TLS — server/README.md §ความปลอดภัย)
3. ทดสอบ ERP: curl login + schedule
4. แต่ละ workstation: ตั้ง env (ROLE, DETECT_GRADE, SERVER, TOKEN, RESOLVE_*) → เทส `python runner.py` 1 ครั้ง
5. ตั้ง scheduler (LaunchAgent/Task/cron) ทุก 10 นาที
6. เปิด dashboard `http://<server>:8000/`

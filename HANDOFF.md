# EDITORTRACK — Handoff / Continuation Brief

> **session ถัดไป: อ่านไฟล์นี้ก่อนเป็นอันดับแรก** แล้วค่อยดูไฟล์อื่นใน `~/editortrack/`
> เอกสารนี้ตั้งใจให้ครบพอทำต่อได้โดยไม่ต้องมี context จากแชทเดิม
> (จะ `cp HANDOFF.md AGENTS.md` เพื่อให้ agent อ่านอัตโนมัติก็ได้)

---

## 0. ภารกิจ

ระบบติดตาม **ความคืบหน้า + เวลาทำงานจริง** ของทีมตัดต่อ **DaVinci Resolve บน macOS** ระดับองค์กร แบบ **DIY + ใช้ local LLM**
เก็บ 2 metric: **#1** % ความคืบหน้างานตัดต่อ (proxy) · **#2** เวลา active-in-Resolve
ปลายทาง: dashboard ให้หัวหน้า/PM เห็นภาพรวม + (เฟสต่อไป) report เชิงเล่าเรื่องต่อ session

> **งานหลักของทีม = ตัดต่อ (edit) + ปรับสี (color)** — ไม่เน้น audio/fusion → phase weights = `edit 0.45 / color 0.40 / deliver 0.15` (audio ตัดออก)
> ผลคือ **การ detect สีให้ได้เป็น priority สูง ไม่ใช่ optional** (color นน .40 ถ้าวัดไม่ได้ % เพี้ยนหนัก) — ดู §7 ข้อ 1
>
> ⚠️ **โครงสำคัญ: edit กับ color เป็นคนละ module / คนละคน — ฝ่าย conform (ทำ edit/conform) vs colorist (ทำสี).**
> ดังนั้น metric #1 ต้องมองเป็น **pipeline 2 สเตจ: Conform → Color** แต่ละสเตจมีเจ้าของคนละ role ไม่ใช่ % ก้อนเดียวของคนเดียว →
> (a) **tag role ต่อ workstation/user** (conform | colorist) ใน config — โดยปกติ 1 เครื่อง = 1 role (color มี suite เฉพาะ)
> (b) รายงานเป็น **conform_pct / color_pct แยกกัน** (แสดงเป็น pipeline) ไม่ใช่เลขเดียว
> (c) attribute เวลา/activity ตาม role (มี `user` + `GetCurrentPage` รองรับแล้ว)
> **ตอบแล้ว (2026-09-29):** conform & color อยู่ **คนละ project/ไฟล์** + **overlap ต่อ reel/shot** (ทำคู่กัน — color เริ่มได้ก่อน conform เสร็จ ไม่ใช่ pipeline เรียงต่อ) ดังนั้น:
> - **แต่ละ workstation = 1 role** poll Resolve project ของตัวเอง (conform bay อ่าน project conform · color bay อ่าน project color) — เข้ากับ architecture เดิมพอดี แค่ tag role ต่อ host
> - ต้องมี **job-linking key** join 2 project เป็น deliverable เดียว → แนะนำ **registry** map `project → {job_id, role, target}` + marker `@job <id>` เป็น shortcut inline
> - dashboard **join by `job_id`** แสดง **2 track ขนาน** (conform_pct จาก project conform · color_pct จาก project color) ไม่ใช่เลขก้อนเดียว
> - ค้างจุดเล็ก: นิยาม job key แบบไหน + per-reel ละเอียดแค่ไหน (§8)

---

## 1. 🚨 กฎเหล็ก — ห้ามละเมิด (อ่านก่อนเขียนโค้ด)

1. **เก็บแบบเปิดเผย (transparent) — worker รู้เท่านั้น ห้ามแอบ** → ต้องทำ PDPA ให้ครบ: policy ลายลักษณ์อักษร + แจ้งล่วงหน้า + **data minimization (จับเฉพาะแอปงาน อย่าจับทั้งจอ/ชีวิตส่วนตัว)** + retention
2. **% เป็น proxy/advisory เท่านั้น — ห้ามผูกเงินเดือน/ประเมิน/ตัดสินคน** (ค่าต่ำมักคือ deep work ทุก metric โกงได้)
3. **ไม่ใช่คำแนะนำกฎหมาย** — ต้องให้ทนายไทยยืนยันฐานทางกฎหมาย + ความต่าง employee vs contractor ก่อน deploy
4. ตอนนี้ **ยังไม่ใช่ git repo** — อย่า commit/init อะไรโดยไม่ถาม user
5. 🚫 **worker zero-touch — worker (colorist/conform) ต้องไม่อยู่ใน workflow tracking เลย**: ไม่ใส่ marker, ไม่ตั้งชื่อพิเศษ, ไม่กรอก scope, ไม่กดอะไร. การผูกงานทั้งหมด = **ดึงจาก ERP booking/schedule อัตโนมัติ (server-side join by room+time) — ทั้ง worker และ PM ไม่ต้องกรอกอะไร** · role = **IT ตั้งต่อเครื่อง หรือจาก ERP** · agent รัน background เงียบ (read-only ไม่รบกวน session). **marker/naming/registry = fallback เท่านั้น** ถ้า ERP ไม่มี booking → `unlinked`. ห้ามบังคับ worker/PM ทำอะไรเพื่อ tracking

---

## 2. สถาปัตยกรรม (2 track อิสระ)

| | Track A — Deterministic (**กำลังทำ**) | Track B — LLM/Vision (**ยังไม่เริ่ม**) |
|---|---|---|
| ให้อะไร | ตัวเลข: % + เวลา active | เรื่องเล่า + หลักฐานภาพ |
| ข้อมูลจาก | Resolve Scripting API + macOS OS API | screenshot → local vision LLM |
| คุณสมบัติ | แม่น เบา privacy ต่ำ | advisory เป็นรูปธรรม อธิบายง่าย |
| ลำดับ | **ทำก่อน** | ทำทีหลัง |

**กฎการแบ่งบทบาท:** ตัวเลข←A, เล่าเรื่อง/หลักฐาน←B — **อย่าเอา screenshot ไปปั้นเป็น %**

**Data flow:**
```
per-user LaunchAgent (poll ~10 นาที)
  → runner รวม A1(Resolve API) + A2(idle/frontmost) + A5(scope→%)
  → POST /ingest  → JSONL รายวัน
  → GET /api/dashboard (aggregate)  → dashboard.html (SVG charts)
```

---

## 3. ไฟล์ใน `~/editortrack/`

| ไฟล์ | คือ | สถานะ |
|---|---|---|
| `HANDOFF.md` | ไฟล์นี้ — เริ่มที่นี่ | — |
| `TRACK_A.md` | **สเปค + โค้ดเต็มของ Track A** (A1 poller, A2 sampler, A5 scope, A3 runner+plist, A4 server) + probe checklist | reference หลัก |
| `API_REFERENCE.md` | catalog method ของ Resolve API จัดหมวด + tag ความมั่นใจ + read vs mutating + กฎ "ใช้ Get* อย่างเดียว" | reference |
| `probe.py` | introspect Resolve จริง เช็ค checklist อัตโนมัติ | ⚠️ ยังไม่รันบน Mac |
| `dashboard.html` | A6 dashboard — SVG charts + hover, fallback เป็น sample เมื่อ fetch ไม่ได้ | ✅ published เป็น artifact |
| `dashboard_api.py` | ingest + query + serve + **`/api/jobs` (join conform↔color, ERP-backed)** (**แทน server.py เดิม**) | ✅ **endpoint + join + ERP ทดสอบเขียว** |
| `erp.py` | ERP connector (server-side) **ตรง KPP ERP จริง**: login token 8ชม (auto-refresh) → `/api/schedule` range → `data.bookings` → room จาก `resources[is_primary].resource_code` · `activity`→role · ข้าม CANCELLED · cache+mock | ✅ **เทสตาม response จริงผ่าน** · ยังไม่ยิง LAN จริง |
| `joblink.py` | resolve `job_id`+`role` ต่อ project (marker>registry>naming>fallback + host role) | ✅ **เทสผ่าน** |
| `resolve_poller.py` | A1 — snapshot Resolve (+ ดึง @scope/@job/@role markers) | ✅ แตกจาก TRACK_A + syntax ok · ยังไม่รันบน Resolve จริง |
| `activity_sampler.py` | A2 — idle+frontmost (defensive import → รันบน Linux ได้) | ✅ แตกแล้ว · ยังไม่รันบน macOS จริง |
| `scope.py` | A5 — load scope + compute % (weights .45/.40/.15) | ✅ เทสผ่าน (ผ่าน runner) |
| `runner.py` | A3 — poll→joblink→scope→record schema 2→POST | ✅ **build_record เทสผ่าน (mock)** |
| `JOB_LINK.md` | design กลไก job-link: registry schema, record change, join, flags, เพดาน | reference |

> ⚠️ โค้ด A1–A5 ตอนนี้ยัง**ฝังอยู่ใน `TRACK_A.md`** ยังไม่แตกเป็น `.py` แยก — เป็นงานข้อ 2 ของ Next steps

---

## 4. ✅ Verified vs ❌ ยังไม่ได้ทำ/เทส

**✅ พิสูจน์แล้ว** (FastAPI TestClient + fake data บน Linux):
- `GET /api/dashboard` → 200, JSON ครบทุก key ที่ dashboard ต้องใช้, aggregate ถูก
- `GET /` เสิร์ฟ dashboard.html (200, text/html)
- auth `/ingest`: ไม่มี token → 401, มี token → 200
- `joblink.resolve_job()` — resolution order (marker>registry>naming>fallback + host role) ถูกทุกเคส
- `GET /api/jobs` — join 2 record คนละ project (conform+color, job_id เดียว) → conform_pct/color_pct/overall_pct ถูก (80% + 35% → overall 58.8%)
- `runner.build_record()` — wire ครบ (poller→joblink→scope→record **schema 2**): job ผูกถูก (registry + marker override), target ดึงจาก job registry, active/idle ถูก (mock snapshot/sample บน Linux)
- **ERP-backed join** (`erp.py` + `_eff_job`) — mock booking 2 ห้องคนละ role (Grading 3 + Conform Bay, job เดียว) → `/api/jobs` join เป็น job เดียว source=erp, role มาจาก task, agent ไม่ต้องรู้ job (unlinked ก็ได้) → **zero-touch พิสูจน์แล้ว**

**❌ ยังไม่ทำ/เทส:**
- ยังไม่รันบน **Mac จริง** (เครื่อง dev เป็น Linux ไม่มี Resolve) — ทั้ง poller/sampler/probe
- `probe.py` ยังไม่รัน → **ยังไม่ยืนยัน**: bundle id จริงของ Resolve, ชื่อ key ของ `GetRenderJobStatus`, `GetNodeGraph` มีบนเวอร์ชันนี้ไหม, วิธีดึง audio coverage
- browser hop (fetch จริงในเบราว์เซอร์) — same-origin มาตรฐาน ความเสี่ยงต่ำ แต่ยังไม่เทส
- Track B ยังไม่เริ่ม
- ยังไม่แตก A1–A5 เป็นไฟล์ `.py`

---

## 5. Schemas (ใช้ร่วมทุกไฟล์ — ห้ามเปลี่ยนพร่ำเพรื่อ)

### 5.1 JSONL record ที่ POST เข้า `/ingest` (schema 1)
```json
{
  "schema": 1, "ts": "ISO-UTC", "host": "bay-03", "user": "somchai",
  "resolve": {
    "ok": true, "page": "color", "project": "PROMO_2026Q4",
    "timeline_count": 3, "rendering": false,
    "timeline": {"name": "...", "duration_sec": 24.0, "fps": 25.0,
                 "video_tracks": 3, "audio_tracks": 4, "video_clips": 11,
                 "graded_clips": null, "markers": 5},
    "scope_hint": {"duration": "30", "shots": "12"}
  },
  "activity": {"idle_sec": 4.2, "frontmost_bundle": "...", "frontmost_name": "DaVinci Resolve",
               "resolve_frontmost": true, "active_in_resolve": true},
  "scope": {"target_duration_sec": 30, "phase_weights": {"edit":0.4,"color":0.25,"audio":0.2,"deliver":0.15}},
  "progress": {"bounded": true, "overall_pct": 58.2, "phases": {"edit":80,"deliver":0},
               "measured_phases": ["edit","deliver"], "unmeasured_phases": ["audio","color"], "flags": []}
}
```
เมื่อ Resolve ปิด/ไม่มีโปรเจค: `"resolve": {"ok": false, "reason": "..."}` และไม่มี `scope`/`progress` (แต่ `activity` ยังมี)

### 5.2 response ของ `GET /api/dashboard` (รูปที่ dashboard.html กิน)
```json
{
  "generated_at": "ISO", "sample": false,
  "summary": {"projects": 2, "avg_progress": 46.1, "active_hours_today": 1.7,
              "workers_active": 0, "workers_total": 2},
  "projects": [{"project": "...", "overall_pct": 58.2, "target_sec": 30, "current_sec": 24,
                "phases": {"edit":80,"color":null,"audio":null,"deliver":0},
                "unmeasured": ["color","audio"], "flags": []}],
  "progress_series": {"labels": ["9/18","..."], "series": {"PROMO_2026Q4": [0,0,0,0,50.2,54.2,58.2]}},
  "phase_time": {"__team__": {"color":180,"edit":180}, "somchai": {"...": 0}},
  "active_idle": {"labels": ["9/18","..."],
                  "workers": {"__team__": {"active": [...], "idle": [...]}, "somchai": {"...": []}}}
}
```

---

## 6. วิธีรัน / เทส

```bash
# deps (แนะนำ venv เพราะ macOS/Linux อาจเป็น managed env / PEP 668)
python3 -m venv venv && ./venv/bin/pip install fastapi uvicorn httpx \
  pyobjc-framework-Quartz pyobjc-framework-Cocoa

# 1) probe (บน Mac ที่เปิด Resolve Studio + โปรเจค+timeline+clip+render job)
python3 probe.py               # → เซฟ ~/.editortrack/probe_report.json + พ่นค่า config

# 2) server (ingest + dashboard)
EDITORTRACK_TOKEN=xxx ./venv/bin/uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
# เปิด http://localhost:8000/  → dashboard ดึง /api/dashboard เอง (ไม่มีข้อมูล = fallback sample)

# 3) ยิง record เทส 1 ครั้ง (ต้องมี Resolve เปิด)
EDITORTRACK_SERVER=http://localhost:8000/ingest EDITORTRACK_TOKEN=xxx python3 runner.py
```
env สำคัญ: `RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`, `PYTHONPATH` (ดู Prerequisites ใน `TRACK_A.md`),
`EDITORTRACK_STORE` (dir เก็บ JSONL, default `data/`), `EDITORTRACK_INTERVAL_MIN` (ให้ตรง `StartInterval`, default 10)

---

## 7. Next steps (เรียงลำดับที่ควรทำ)

1. **รัน `probe.py` บน Mac จริง** → เอาผลไปแก้ค่าใน poller/sampler: `RESOLVE_BUNDLE_IDS`, ชื่อ key ของ render status, เปิด `DETECT_GRADE` ถ้า `GetNodeGraph` ใช้ได้
   → ⭐ **color เป็นเฟสหลัก (นน .40)** → detect สีให้ได้คือ priority สูงสุดของ probe · ถ้า node-detect ไม่ได้บนเวอร์ชันนี้ ให้ใช้ **fallback: time-share บนหน้า color** (นับ sample ที่ `GetCurrentPage=="color"` ÷ เวลาที่คาด) เป็น proxy ของ color progress แทน
2. ✅ **[ทำแล้ว] แตก A1–A5 เป็น `.py` + wire joblink** — `resolve_poller.py` (+ ดึง `@scope/@job/@role` markers), `activity_sampler.py` (defensive import), `scope.py` (weights .45/.40/.15), `runner.py` (schema 2, เรียก `resolve_job`) · เทส `build_record` ผ่าน. **เหลือ:** ยังไม่รันบน Resolve/macOS จริง (poller/sampler ต้องรันบน Mac — gate = ข้อ 1)
3. บนเครื่องจริง: ตั้ง `~/.editortrack/jobs.json` (registry) + `EDITORTRACK_ROLE` ต่อเครื่อง → ติดตั้ง **LaunchAgent** รัน `runner.py` → `/ingest` เก็บข้อมูล 1–2 วัน (dashboard join by job_id ให้เอง)
4. **delta-log (สร้าง "ประวัติการทำงาน" เอง)** — Resolve API **ไม่มี edit history** (ดู §8) → เขียนตัวเทียบ snapshot ติดกันใน `dashboard_api.py` (หรือ module แยก) แล้วสรุปเป็นเหตุการณ์อ่านรู้เรื่อง เช่น *"10:00→10:10: +3 clips · ย้ายไปหน้า color · เริ่ม render 1 job"* → เพิ่ม endpoint `/api/events` + แสดงเป็น timeline เหตุการณ์ใน dashboard. ข้อมูลดิบมีอยู่แล้วใน JSONL (poll สะสม) แค่ยังไม่ compute delta
5. **Project Backups watcher (สัญญาณ history เสริม)** — Resolve auto-save ไฟล์ backup แบบ timestamp ทุก ~10 นาที → fs-watch โฟลเดอร์ backup แล้วบันทึกเวลาที่เซฟ = "ทำงาน/แก้ช่วงไหน" (ช่วยกรณี Resolve ไม่ได้ frontmost แต่ยังมีการเซฟ, และ cross-check metric #2). **ต้องหา/ยืนยัน path โฟลเดอร์ backup ก่อน** (Preferences → User → Project Save and Load → Project backups; path 🟠 ต้องยืนยันบนเครื่องจริง)
6. ✅ **[ทำแล้ว] dashboard หน้า per-job 2-track** (Conform→Color จาก `/api/jobs`, section บนสุด, sample+live fallback, JS syntax เทสผ่าน). **เหลือ:** จูน geometry กับข้อมูลจริง + (ถ้าต้อง) date-range/worker filter + real-time refresh
7. **Track B**: screenshot (ScreenCaptureKit, เฉพาะตอน Resolve frontmost — ผูกกับ A2) → local vision LLM → narrative report + retention. **พิจารณาใช้ `GetCurrentClipThumbnailImage()` จาก API แทน screenshot** (เห็นภาพงานจริง แต่ไม่ต้องขอ screen-recording permission + footprint privacy เล็กกว่า = PDPA-friendly กว่า; ต้อง probe ยืนยันว่าใช้ได้+คุณภาพพอ)
8. **PDPA**: ร่าง monitoring policy + notice ให้ทนายรีวิว ก่อน deploy จริง

---

## 8. Open questions / risk (จากงานวิจัย — ต้องจัดการ)

- **นิยาม "scope เป้าหมาย" (ตัวหารของ %)** ต่อ job — API รู้แค่ "วางไปเท่าไหร่" ไม่รู้ "ควรมีเท่าไหร่" → ต้องกรอกเอง (registry `scopes/<project>.json` + marker `@scope`)
- **color/audio detection ยังไม่ยืนยัน** (version-dependent) → ตอนนี้ % renormalize เฉพาะเฟสที่วัดได้ ส่ง `measured/unmeasured` กำกับเสมอ
- **[แก้แล้ว] job-linking:** กลไกออกแบบ+เทสเสร็จ → `joblink.py` (marker>registry>naming>fallback) + `/api/jobs` join. ดู `JOB_LINK.md`. **ค้างจริง ๆ เหลือ: per-reel/shot granularity** — ยังไม่มีวิธี reconcile ว่า shot ไหน conform แล้ว vs เกรดแล้ว ข้าม 2 ไฟล์ (track เป็นระดับ project) · และ **team ops (ทำโดย PM/IT ไม่ใช่ worker — ดู §1 ข้อ 5)**: ตั้ง `jobs.json` registry (PM) + `EDITORTRACK_ROLE` ต่อเครื่อง (IT) · marker/naming เป็น optional ไม่บังคับ worker
- **⭐ linking source = ERP booking/schedule (ไม่ใช่ registry manual)** — ERP รู้อยู่แล้วว่าห้องไหนทำ job อะไร ช่วงเวลาไหน. **server-side join**: record จาก agent มี `host`(=ห้อง)+`ts`+`project`+`role` → server lookup ERP booking ที่ `room==host` และ `start<=ts<=end` → ได้ `job_id`(+client/title/target). agent ยังโง่เหมือนเดิม, ERP creds อยู่แค่ server. **[ตอบ+ทำแล้ว]** REST API · role ผสม (task>ห้อง) · booking มี job code → สร้าง `erp.py` (cache+mock) + wire `build_jobs._eff_job` (ERP ก่อน→fallback agent) **เทส mock ผ่าน**. **erp.py ตรงกับ KPP ERP แล้ว** (endpoint/field/auth ครบใน erp.py docstring · เวลาเป็น UTC ยืนยันแล้ว). **เหลือทำจริงบน server ใน LAN:** (1) ตั้ง env creds (`ERP_USERNAME/ERP_PASSWORD/ERP_BASE_URL` — **ห้าม commit — ใส่ผ่าน env เท่านั้น; ตอนนี้ใช้บัญชี dev, UAT/prod ต้องบัญชี+URL จริง**) (2) `ERP_ROOM_MAP` = {host→`RM-xx`} (คนที่รู้ว่าเครื่องไหนอยู่ห้องไหนกรอก) (3) รัน + verify login/`/api/schedule` จริง (LAN + firewall :8010) (4) `activity` มีแค่ค่าเช่น "Grading"/"Conform" — เพิ่ม mapping ใน `TASK_ROLE` ถ้ามีค่าอื่น
- **facility topology: 6 ห้อง grade (colorist) + 1 ห้อง conform** → 7 workstation แต่ละเครื่องรัน runner เอง. แยกโปรเจคด้วย 3 ฟิลด์ที่มีในทุก record: `job_id` (deliverable ไหน) · `host` (ห้องไหน) · `role`. **แนะนำ central registry** (jobs.json ที่เดียว serve จาก server หรือ shared drive) แทนไฟล์แยก 7 เครื่อง เพื่อให้ job_id consistent. **เคสต้องต่อยอด: job เดียวเกรดหลายห้องพร้อมกัน** → build_jobs ตอนนี้เก็บ record ล่าสุด 1 ห้อง ต้อง aggregate color ข้าม host (ผูกกับ per-reel granularity)
- **employee vs contractor** ต่างกันทางกฎหมาย
- **mouse-jiggler แบบ dongle** ยังปลอม active ได้ (เพดานที่ยอมรับ)
- **ต้อง Resolve Studio + เปิดค้าง** ถึงจะได้ metric #1 (เครื่องที่ปิดโปรแกรม = ได้แค่ #2)

---

## 9. งานวิจัยอ้างอิง (condensed) + honesty

- screenshot + LLM = **advisory ห้ามใช้เป็น % ชี้ขาด** ([arXiv 2609.08589](https://arxiv.org/abs/2609.08589)) — timeline เต็ม ≠ งานเสร็จ
- แกน metric #1 = **Resolve Scripting API** (timeline fill + render %); metric #2 = **`CGEventSourceSecondsSinceLastEventType` + `NSWorkspace.frontmostApplication`**
- commercial tools (Time Doctor/Hubstaff/Teramind/ActivTrak) วัด **input presence / app-identity ไม่ใช่ผลงาน** → under-count งาน low-input → **อย่าลอกโมเดล**
- ⚠️ **อย่าอ้างเป็นข้อเท็จจริง (ถูก verify ตกทั้งหมด):** ค่าปรับ PDPA "5 ล้านบาท", ScreenSpot-Pro "0.9%", "VLM นับ clip ไม่ได้", legitimate-interest proportionality argument
- legal findings มาจากแหล่งทนายทุติยภูมิแหล่งเดียว (ตัวบท PDPC จริงดึงไม่ได้) → **ต้องให้ทนายยืนยัน**

**Artifacts:**
- รายงานวิจัยเต็ม: https://claude.ai/artifact/TFuXgjsC5aM9FDVSBVftdL
- dashboard (preview sample data): https://claude.ai/artifact/32w9gK57rpjEBXwaRrrFwt

---

*สร้างโดย session วันที่ 2026-09-24 · ทุกอย่างยังไม่รันบน Mac จริง — ข้อ 1 ของ Next steps คือ gate สำคัญ*

# server — รันบนเครื่องกลาง (1 เครื่อง)

รับ record จาก agent → เก็บ JSONL → join กับ ERP (room+time→job) → serve dashboard

## ต้องมี
```
pip install -r requirements.txt
```

## env
```
EDITORTRACK_TOKEN=<token>          # ต้องตรงกับ agent
EDITORTRACK_STORE=data             # dir เก็บ JSONL (default: ./data)
EDITORTRACK_INTERVAL_MIN=1         # 1 sample ≈ กี่นาที (ให้ตรง poll; default 1)
# ERP (creds — อย่า commit; ดู erp.py docstring / ../DATAFLOW.md §Config)
ERP_BASE_URL / ERP_USERNAME / ERP_PASSWORD
ERP_BOOKINGS_PATH / ERP_RECORDS_PATH / ERP_ROOM_MAP
```

### เชื่อม KPP-PMS (สัญญา: `~/github/pms` docs/integrations/editortrack.md)
```
ERP_BASE_URL=http://127.0.0.1:8081                    # PMS UAT บนเครื่องนี้ (ผ่าน nginx)
ERP_TOKEN=kpp_int_…                                   # integration key — ผู้ดูแล PMS ออกให้ อย่า commit
ERP_BOOKINGS_PATH=/api/integrations/editortrack/schedule?start_dt={start}&end_dt={end}
ERP_RECORDS_PATH=data.bookings
ERP_ACTIVITY_PATH=/api/integrations/editortrack/activity   # เวลาทำงานเป็นช่วง 10 นาที (pms_sync.py)
ERP_PROGRESS_PATH=/api/integrations/editortrack/progress   # % ส่งเมื่อเปลี่ยน เฉพาะ job ที่มาจาก booking
ERP_ROOM_MAP={"conform-01":"RM-…"}                    # host → resource_code ห้องใน PMS
```

#### % ความคืบหน้า: สัญญา v2 (`progress_v2.py`, PMS 9e67f60 §3.1)
server คำนวณ % เอง (ไม่ใช้ % ของ agent ตรง ๆ สำหรับ color/conform) แล้วส่งฟิลด์ v2 ชื่อตรงตามสัญญา: `provisional`,
`total_confirmed`, `done`, `done_count`, `total`, `work_done` (เพิ่มอย่างเดียว), `reasons` · `ERP_PF_*` ไม่ใช้กับ % แล้ว
- ไม่ส่ง 100 หรือ `done=true` จนกว่าขอบเขตจะยืนยันและค่านิ่งตามเวลาที่ตั้ง · ก่อนนั้น `provisional=true` ไม่เกิน cap (99)
- พักค่าเดิม (ไม่ส่ง) ตอน: detector ยังอ่าน grade ไม่ครบ (`grade.pending`) · เปิด timeline offline/reference ·
  project ใน Resolve ไม่มี `project_no`/ชื่องานของ booking · record เก่ากว่าที่เคยรับจากเครื่องเดียวกัน (offline queue)
- ค่าที่ PMS จะตอบ 422 ไม่ถูกส่ง (นับใน `GET /api/pms` → `progress.refused_locally`) · state อยู่ที่ `<store>/progress_v2_state.json`

ข้อตัดสินใจของเจ้าของ (D1–D8) ยังเปิดอยู่ — ค่า default = ข้อเสนอของการศึกษา (`docs/research/STRATEGY_COMPARISON.md`):
```
EDITORTRACK_SCOPE_SOURCE=none        # D1: none = ไม่มีขอบเขตยืนยัน → ไม่มีวัน done · lock = คนล็อกจำนวน shot ต่อ project_no|stage
                                     #     python3 progress_v2.py lock <store> <project_no> <stage> <จำนวน>|clear  (นับเป็นคลิปบน video track)
EDITORTRACK_PROGRESS_DONE=advisory   # D2: advisory = ส่ง done ได้ (PMS ใช้อ้างอิงเท่านั้น) · never
EDITORTRACK_MASS_GUARD=on            # D4: เกรดหลายคลิปในรอบเดียว (LUT ทั้ง timeline) → ไม่ done จนกว่าจะล็อกใหม่
EDITORTRACK_JOB_GUARD=withhold       # D5: project ใน Resolve ไม่ตรง booking → ไม่ส่ง · off
EDITORTRACK_PROGRESS_CAP=99          # D6: เพดานค่า provisional (0–99.99)
EDITORTRACK_STABLE_MIN=30            # D6: นาทีที่ 100% บนขอบเขตยืนยันต้องนิ่งก่อน done (env ตั้งได้แค่ ≥ 30)
EDITORTRACK_STABLE_GAP_MIN=10        # poll ห่างเกินนี้ = เริ่มนับใหม่ (env ตั้งได้แค่ 0 < x ≤ 10)
EDITORTRACK_OFFLINE_MATCH=...        # D8: ตัวเดียวกับของ agent (default เหมือนกัน)
EDITORTRACK_LOCK_COUNTS=0            # lock จากจำนวนคลิปอย่างเดียว (poller วันนี้) ให้ done ได้ไหม · 1 = ได้ (ต้องรับข้อจำกัดด้านล่าง)
```
ค่าที่พิมพ์ผิด / nan / หลวมกว่าค่า default → ใช้ค่า default (env ทำให้เข้มขึ้นได้อย่างเดียว)

ข้อจำกัดของ `lock` ที่นับจากจำนวนคลิป (ไม่มีข้อมูลราย clip) — เหตุผลที่ต้องเปิด `EDITORTRACK_LOCK_COUNTS=1` แยกอีกชั้น:
- Replace Clip ที่เก็บ grade เดิมไว้มองไม่เห็น → อาจ done ทั้งที่ VFX เวอร์ชันใหม่ยังไม่ได้เกรด
- lock เป็นจำนวนต่อ project_no|stage ไม่ผูก timeline: reel/timeline อื่นของงานเดียวกันที่มีจำนวนคลิปเท่ากันก็ done ได้
- look ที่ apply ทีละไม่กี่คลิปหลายรอบ ดูเหมือนเกรดจริง (guard จับได้เฉพาะก้อนใหญ่ ≥ max(4, ครึ่งหนึ่ง) ต่อรอบ หรือเห็นครั้งแรกก็เกรดแล้ว)
ถ้า record มี `source.items` (ขั้น 3, poller ยังไม่ส่ง) ledger ราย shot แก้สามข้อแรกได้ และ `lock` ไม่ต้องใช้ `LOCK_COUNTS`
(ledger: timeline ที่เห็นครั้งแรกแล้วเกรดเกือบหมด — เช่น agent ติดตั้งกลางงาน — shot เหล่านั้นไม่นับจนกว่าจะถูกแตะอีกครั้ง ค่าจึงอาจค้างต่ำกว่า 100 ตามเจตนา)

ยังไม่ทำ: กฎ reopen (task กลับไป REVISION → เปิดค่าใหม่) — ต้องอ่าน `GET /api/integrations/editortrack/tasks` (§4 ของสัญญา,
ต้องให้เจ้าของอนุมัติการขยายสิทธิ์ key ก่อน) · ตอนนี้ engine รับ `pms_task` ได้แต่ server ยังไม่ดึง → หลัง done ถ้า task ถูกตีกลับ
แถว PMS ยังแสดง done จนกว่าตัวเลขจะเปลี่ยน (PMS ใช้ค่านี้อ้างอิงเท่านั้น) · ค่าเริ่มต้น D1 = none ไม่มีวันส่ง done อยู่แล้ว
ไม่ตั้ง = ไม่ส่งอะไรให้ PMS · ดูสถานะการส่งที่ `GET /api/pms` · ไม่ส่ง user/ชื่อคน
ทุกช่วงต้องผูก booking (กติกา PMS 15 ต.ค.) — ห้องมีหลาย booking พร้อมกัน → เลือกตัวที่ `project_no` หรือชื่อ project ตรงกับ project ที่เปิดใน Resolve · หาไม่ได้/ไม่ชัด = ไม่ส่ง นับไว้ที่ `unbound` ใน `/api/pms`
→ ตั้งชื่อ project ใน Resolve ให้มี `project_no` (เช่น `KPP-2026-0005_…`) จะผูกได้แม่นสุด
กติกาการเทียบชื่อ (ใช้ทั้งเลือก booking และ job guard D5 · `progress_v2.code_in/name_in`): ชื่อไทยใช้ได้ (NFKC, สระอำ 2 แบบ = ตัวเดียวกัน,
เลขไทย = เลขอารบิก, ไม่ต้องเว้นวรรค) · ไม่เคยจับคู่ด้วยตัวเลขอย่างเดียว (`project_no` ต้องมีตัวอักษร, ชื่องานต้องมีตัวอักษร ≥ 6) ·
`KPP-1` ไม่ตรงกับ `KPP-10` · ชื่อที่จับคู่ไม่ได้ = ไม่ส่ง
`/ingest` ตอบ 400 เมื่อ record ใช้ไม่ได้ (`explain.record_problem`: ไม่มี host, ts ไม่มี timezone เช่น `2026-10-14T10:05:00`,
ชนิดข้อมูลผิด) · record เสียที่อยู่บน disk แล้วถูกข้ามและนับไว้ที่ `skipped` ใน `/api/health` และ `/api/inbox` (ไม่ทำให้หน้าไหน 500)

## run
```
EDITORTRACK_TOKEN=xxx uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
```
เปิด `http://<server>:8000/` → dashboard

## endpoints
`POST /ingest` · `GET /api/report` + `/report` (รายวัน) · `GET /api/projects?days=` + `/projects` (ตัวเลขนับรายโปรเจค: คลิป เกรด shot) · `GET /api/pms` (สถานะส่ง PMS) · `GET /api/dashboard` · `GET /api/jobs` (2-track) · `GET /api/shots` (reconcile) · `GET /` (dashboard.html)

## เทส
`cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_replay -v` — replay ข้อมูลเทสจริง 7–14 ต.ค. (ลบชื่อ/IP แล้ว)

## ไฟล์
`dashboard_api.py` ingest+query+serve · `erp.py` ERP connector · `dashboard.html` UI

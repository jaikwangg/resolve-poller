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
ERP_PF_JOB=project_no  ERP_PF_STAGE=stage  ERP_PF_PCT=progress_pct
ERP_ROOM_MAP={"conform-01":"RM-…"}                    # host → resource_code ห้องใน PMS
```
ไม่ตั้ง = ไม่ส่งอะไรให้ PMS · ดูสถานะการส่งที่ `GET /api/pms` · ไม่ส่ง user/ชื่อคน

## run
```
EDITORTRACK_TOKEN=xxx uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
```
เปิด `http://<server>:8000/` → dashboard

## endpoints
`POST /ingest` · `GET /api/report` + `/report` (รายวัน) · `GET /api/pms` (สถานะส่ง PMS) · `GET /api/dashboard` · `GET /api/jobs` (2-track) · `GET /api/shots` (reconcile) · `GET /` (dashboard.html)

## ไฟล์
`dashboard_api.py` ingest+query+serve · `erp.py` ERP connector · `dashboard.html` UI

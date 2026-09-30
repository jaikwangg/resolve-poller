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
EDITORTRACK_INTERVAL_MIN=10        # 1 sample ≈ กี่นาที (ให้ตรง poll)
# ERP (creds — อย่า commit; ดู erp.py docstring / ../DATAFLOW.md §Config)
ERP_BASE_URL / ERP_USERNAME / ERP_PASSWORD
ERP_BOOKINGS_PATH / ERP_RECORDS_PATH / ERP_ROOM_MAP
```

## run
```
EDITORTRACK_TOKEN=xxx uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
```
เปิด `http://<server>:8000/` → dashboard

## endpoints
`POST /ingest` · `GET /api/dashboard` · `GET /api/jobs` (2-track) · `GET /api/shots` (reconcile) · `GET /` (dashboard.html)

## ไฟล์
`dashboard_api.py` ingest+query+serve · `erp.py` ERP connector · `dashboard.html` UI

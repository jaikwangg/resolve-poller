# server — รันบนเครื่องกลาง (1 เครื่อง)

รับ record จาก agent → เก็บ JSONL → join กับ ERP (room+time→job) → serve dashboard

## ต้องมี
```
pip install -r requirements.txt
```

## env
```
EDITORTRACK_HOME=/srv/editortrack  # ไฟล์ token/user (agent_tokens.json, read_users.json) — สิทธิ์ 700/600
EDITORTRACK_TOKENS_FILE=…          # (ไม่บังคับ) ทับ path ไฟล์ token ต่อเครื่อง
EDITORTRACK_READ_USERS_FILE=…      # (ไม่บังคับ) ทับ path ไฟล์ user ที่อ่าน dashboard
EDITORTRACK_READ_AUTH=basic        # basic (default) · none = ปิด login — ใช้ได้เฉพาะหลัง proxy ที่ทำ auth และ bind 127.0.0.1
EDITORTRACK_MAX_BODY=1048576       # byte ต่อ record ที่ /ingest — เกิน = 413
EDITORTRACK_BIND=127.0.0.1  EDITORTRACK_PORT=8000   # run.py · LAN ตรง = --lan
EDITORTRACK_ALLOW_SHARED_TOKEN=0   # 1 = ยอมรับ token รวมเดิม (EDITORTRACK_TOKEN) ชั่วคราวช่วงย้าย เฉพาะ host ที่ยังไม่มี token ของตัวเอง
EDITORTRACK_STORE=…                # dir เก็บ JSONL — default $EDITORTRACK_HOME/data = /srv/editortrack/data (สร้าง/ลดสิทธิ์เป็น 700) · ของเดิม =data ยังใช้ได้
EDITORTRACK_RETENTION_DAYS=180     # ลบไฟล์ YYYY-MM-DD.jsonl ที่เก่ากว่า N วัน (ตอน start + ทุก 24 ชม.) · 0 = ไม่ลบ
EDITORTRACK_INTERVAL_MIN=1         # cadence ของ agent (นาที, default 1) — record 1 ตัวนับ min(ช่องว่างถึง record ถัดไปของเครื่องเดียวกัน, interval × 1.5)
                                   #   ช่องว่างยาวกว่านั้น = ข้อมูลขาด (เครื่องหลับ/หลุด) ไม่นับ · 1 record ต่อ (host, นาที) — agent รันซ้อนไม่นับซ้ำ
EDITORTRACK_TZ=Asia/Bangkok        # ตัดวัน ("วันนี้", /api/dashboard, /report, /projects, ชื่อไฟล์ data) ตามเวลานี้ ไม่ขึ้นกับ TZ ของเครื่อง
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
**ERP/PMS ล่มหรือช้า:** `/ingest` ไม่รอ ERP เลย — จับคู่ booking จาก cache เท่านั้น (cache หมดอายุ → ดึงใหม่ใน background) และ push % ใน background thread (`pms-progress`: เก็บแค่ค่าล่าสุดต่อ job+stage, ล้มแล้วรอ `EDITORTRACK_PUSH_RETRY_SEC`=60 ค่อยลองใหม่)
ดึง booking ไม่ได้ → จำว่าล้ม (`ERP_FAIL_TTL`=60 วิ ไม่ลองใหม่) และใช้ cache เดิม · `ERP_TIMEOUT`=10 วิ/request · `ERP_CACHE_TTL`=300 · room map (`ERP_ROOM_MAP` หรือไฟล์ `ERP_ROOM_MAP_FILE`, default `~/.editortrack/room_map.json`) อ่านใหม่เมื่อไฟล์เปลี่ยนเท่านั้น
สถานะ: `/api/pms` → `erp_cache` (fetched_at / failed_at / error), `progress_pending`, `progress_failures` · response ของ `/ingest` = `{"ok", "pushed": false (คงไว้เพื่อเข้ากันได้), "queued"}`
**booking ที่จับคู่ตอนรับ record ถูกเก็บไว้กับ record** (`_job`: job_id, booking_id, stage, matched_at) → แก้/ลบ booking ใน ERP ทีหลังไม่เขียนประวัติใหม่ · record ที่ตอนรับยังจับคู่ไม่ได้ (ไม่มี booking, ERP ล่มตอน start) ยังจับคู่สดตอนอ่าน (booking ที่ลงย้อนหลังจึงยังผูกได้) · record เก่าก่อนเวอร์ชันนี้ไม่มี `_job` = จับคู่สดเหมือนเดิม (ERP ดึงย้อนหลังแค่ `ERP_LOOKBACK_DAYS`=8 วัน — เก่ากว่านั้นใช้ชื่อ project ของ Resolve)

**record มาช้า (offline queue):** PMS เก็บ window ด้วย key `(integration key, host, window_start)` แบบ `ON CONFLICT DO NOTHING` — ส่งซ้ำนับเป็น `duplicates` และ**ไม่อัปเดตค่าเดิม** (สัญญา PMS ไม่มีวิธีส่งแก้)
→ server **ถือ window ไว้จนนิ่ง**: ส่งเมื่อได้รับ record ของเครื่องนั้นที่หลังปลาย window มาแล้ว ≥ `PMS_SETTLE_SEC` (120) (= queue flush แล้ว) หรือปิดไปแล้ว ≥ `PMS_MAX_HOLD_SEC` (21600 = 6 ชม., เครื่องปิด/หาย)
→ ถ้ายังมี record มาช้ากว่านั้นจนเวลาเพิ่ม: ส่ง window เดิมซ้ำด้วย key เดิม (ปลอดภัย — PMS ไม่นับซ้ำ) และนับเวลาที่ PMS ไม่รับไว้ที่ `/api/pms` → `corrections.ignored_seconds` (gap ที่รู้แล้ว)
→ ถ้าฝั่ง PMS เปลี่ยนเป็น upsert (เช่น `ON CONFLICT DO UPDATE SET active_seconds = GREATEST(...)`) การส่งแก้จะมีผลทันทีโดยไม่ต้องแก้ฝั่งนี้ · `held` = จำนวน window ที่ยังรอ
→ **ห้ามเปลี่ยน `PMS_WINDOW_MIN` หลังเริ่มส่งจริง** — ขอบ window เปลี่ยน = key ใหม่ = PMS นับซ้ำช่วงที่ทับกัน · record ที่มาช้ากว่า `PMS_LOOKBACK_HOURS` (24) ไม่ถึง PMS
ทุกช่วงต้องผูก booking (กติกา PMS 15 ต.ค.) — ห้องมีหลาย booking พร้อมกัน → เลือกตัวที่ `project_no` หรือชื่อ project ตรงกับ project ที่เปิดใน Resolve · หาไม่ได้/ไม่ชัด = ไม่ส่ง นับไว้ที่ `unbound` ใน `/api/pms`
→ ตั้งชื่อ project ใน Resolve ให้มี `project_no` (เช่น `KPP-2026-0005_…`) จะผูกได้แม่นสุด

## run
```
python manage_tokens.py issue grade-03         # token ต่อเครื่อง (แสดงครั้งเดียว) → ใส่ env.sh ของเครื่องนั้น
python manage_tokens.py read-user director     # user/รหัสผ่านสำหรับเปิด dashboard
python run.py                                  # bind 127.0.0.1:8000 (default) — ให้ reverse proxy รับจาก LAN
python run.py --lan                            # bind 0.0.0.0 ตรง (HTTP ไม่เข้ารหัส — ใช้เมื่อยังไม่มี proxy)
```
เปิด `https://<server>/` (ผ่าน proxy) หรือ `http://<server>:8000/` (`--lan`) → browser ถาม user/รหัสผ่าน

## ความปลอดภัย (auth · TLS)
- **`POST /ingest`** — `Authorization: Bearer <token ของเครื่อง>` · server เก็บแค่ SHA-256 ต่อ host (`agent_tokens.json`)
  token ผูกกับ `host` ใน record: ส่งแทนเครื่องอื่นไม่ได้ · host ที่ไม่มี token = 401 · `kpp-et-test` (เคยอยู่ใน repo) ถูกปฏิเสธเสมอ
  ออก/ถอนมีผลทันทีไม่ต้อง restart · body เกิน `EDITORTRACK_MAX_BODY` = 413 · ไม่ใช่ JSON = 400
- **ทุกหน้า + `/api/*`** — HTTP Basic (PBKDF2-SHA256 ใน `read_users.json`) · ยังไม่มี user = เปิดไม่ได้เลย · `/healthz` เปิด (ไม่มีข้อมูล)
  สคริปต์: `curl -u director https://<server>/api/jobs`
- **TLS** — server ไม่ทำ TLS เอง: bind 127.0.0.1 แล้วให้ reverse proxy ทำ (Basic auth/token ส่งเป็นข้อความเปล่าถ้าไม่มี TLS) ตัวอย่าง Caddy (`/etc/caddy/Caddyfile`):
  ```
  editortrack.lan {
      tls internal                    # CA ภายในของ Caddy — แจก root cert (caddy trust) ให้ workstation/browser
      reverse_proxy 127.0.0.1:8000
  }
  ```
  agent ชี้ `EDITORTRACK_SERVER=https://editortrack.lan/ingest` (ถ้าใช้ CA ภายใน ต้องให้ python ของ agent เชื่อ root cert —
  macOS: เพิ่มใน System keychain + `SSL_CERT_FILE` · Linux: `/usr/local/share/ca-certificates` + `update-ca-certificates`)
  proxy อยู่เครื่องอื่น: ตั้ง `EDITORTRACK_BIND=<IP ภายใน>` + `EDITORTRACK_TRUSTED_PROXIES=<IP proxy>` + firewall ให้เข้าได้เฉพาะ proxy
  เลือกได้ว่าจะให้ proxy ทำ Basic auth แทน (`basic_auth` ของ Caddy) แล้วตั้ง `EDITORTRACK_READ_AUTH=none` — ต้อง bind 127.0.0.1 (run.py ไม่ยอม start ถ้าไม่ใช่)

### ข้อมูลที่เก็บ (ย้ายที่เก็บ — ครั้งเดียวตอนอัปเดต)
server ไม่เก็บ `user` และชื่อแอป/หน้าต่าง (ตัดตอน ingest แม้ agent เก่าจะส่งมา · ไฟล์เก่าก็ถูกตัดก่อนส่งออก API) — ตาราง field ดู `../DATAFLOW.md` §data sheet
1. `sudo install -d -m 700 -o <user ที่รัน server> /srv/editortrack /srv/editortrack/data`
2. หยุด server → `mv server/data/*.jsonl /srv/editortrack/data/` (ข้อมูลจริงอยู่นอก repo · ไม่ commit) → `python store.py scrub /srv/editortrack/data` (ตัด user/ชื่อแอปออกจากไฟล์เดิม) → start
   ยังไม่พร้อมย้าย: ตั้ง `EDITORTRACK_STORE=data` ชั่วคราว (server เตือนใน log ถ้าเจอข้อมูลใน server/data แต่ที่เก็บเป็นที่อื่น)
3. retention 180 วันเริ่มทำงานทันทีที่ start — ตัดสินระยะจริงกับทนาย/เจ้าของข้อมูล (NEXT_STEPS เฟส 0) แล้วตั้ง `EDITORTRACK_RETENTION_DAYS`
4. อัปเดต agent ทุกเครื่อง (ไม่ส่ง user/ชื่อแอป, shot code ปิด) — ไม่ต้องแก้ env ยกเว้นเครื่องที่ตั้งใจเปิด `EDITORTRACK_EXTRACT_SHOTS=1`

### เจ้าของระบบต้องทำ (เปลี่ยน token — ครั้งเดียวตอนอัปเดตเป็นเวอร์ชันนี้)
1. ถือว่า `kpp-et-test` หลุดแล้ว: เวอร์ชันนี้ปฏิเสธเสมอ — record ที่เคยรับด้วย token นั้นเชื่อไม่ได้ 100% (ใครใน LAN ก็ส่งได้)
2. บน server: `python manage_tokens.py issue <hostname>` ทีละเครื่อง (ชื่อต้องตรง `socket.gethostname()` ของเครื่อง เช่น `conform-01`) — ส่ง token ทางช่องทางที่ไม่ใช่แชต
3. แต่ละเครื่อง: แก้ `EDITORTRACK_TOKEN` ใน `agent/deploy/env.sh` (หรือ `sh deploy/make-env.sh` ใหม่) — ไม่ต้อง reinstall scheduler (run-agent.sh อ่าน env.sh ทุกรอบ)
4. ตั้ง user อ่าน: `python manage_tokens.py read-user <ชื่อ>` · แจ้ง user/รหัสกับคนที่มีสิทธิ์ดูเท่านั้น
5. เปลี่ยนคำสั่งรัน server เป็น `python run.py` (bind 127.0.0.1) + ตั้ง Caddy/TLS ตามด้านบน หรือ `python run.py --lan` ชั่วคราว
6. ช่วงย้าย (ถ้าลงไม่ครบทุกเครื่องในวันเดียว): `EDITORTRACK_ALLOW_SHARED_TOKEN=1` + `EDITORTRACK_TOKEN=<token รวมใหม่ ไม่ใช่ kpp-et-test>` — ปิดทันทีที่ทุกเครื่องมี token ของตัวเอง
7. รอบต่อไป: token เครื่องหลุด/เครื่องเลิกใช้ → `python manage_tokens.py revoke <host>` (หรือ `issue <host> --force` ออกใหม่) · ดู log server `ingest rejected: host=… reason=…`

## endpoints
`?days=` รับ 1–`EDITORTRACK_MAX_DAYS` (366) — นอกช่วง = 400 (`/api/projects` ตัดให้เหลือ 366 เงียบ ๆ เหมือนเดิม) · `/api/report?day=` ย้อนได้ไม่เกิน 366 วัน
`POST /ingest` · `GET /api/report` + `/report` (รายวัน) · `GET /api/projects?days=` + `/projects` (ตัวเลขนับรายโปรเจค: คลิป เกรด shot) · `GET /api/pms` (สถานะส่ง PMS) · `GET /api/dashboard` · `GET /api/jobs` (2-track) · `GET /api/shots` (reconcile) · `GET /` (dashboard.html)

## เทส
`cd server && ~/.editortrack-venv/bin/python -m unittest discover -s tests -t . -v` — offline ทั้งหมด (HOME ปลอม + temp dir ใน `tests/_env.py` · ไม่อ่าน `~/.editortrack` ไม่ยิง ERP) · `test_replay` = replay ข้อมูลเทสจริง 7–14 ต.ค. (ลบชื่อ/IP แล้ว)

## ไฟล์
`dashboard_api.py` ingest+query+serve · `auth.py` token ต่อเครื่อง + Basic auth · `manage_tokens.py` ออก/ถอน token/user · `run.py` รัน (bind 127.0.0.1) · `erp.py` ERP connector · `dashboard.html` UI

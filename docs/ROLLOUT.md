# ROLLOUT — เอา audit-fixes ขึ้น server จริง (llm-server, 192.168.140.126)

checklist จาก review อิสระ 22 ต.ค. ปรับให้ตรงกับโค้ดใน branch `audit-fixes` · ทำตามลำดับ ข้ามขั้นไม่ได้
ทุกคำสั่งรันบนเครื่อง server ด้วย user ที่รัน `editortrack-server.service` (systemd `--user`)

## สภาพตอนนี้ (ตรวจ 22 ต.ค.) — ทำไมลำดับถึงสำคัญ
| อะไร | ตอนนี้ | ปัญหาถ้า merge เลย |
|---|---|---|
| `~/tools/autodeploy/services/editortrack.conf` | `SMOKE_URL="http://127.0.0.1:8000/"` | หลัง merge `/` ต้อง login → 401 → autodeploy ถือว่าล้ม **ไม่เขียน `.deployed`** → ทุกนาทีถัดไป BUILD + restart + รอ 120 วิใหม่ = service ถูก restart วนทุก ~2 นาทีไม่จบ |
| checkout `~/github/editortrack` | อยู่บน `main` แต่ `NEXT_STEPS.md` แก้ค้าง | autodeploy เจอไฟล์ tracked ที่แก้ค้าง → SKIP ไม่ deploy อะไรเลย |
| unit `~/.config/systemd/user/editortrack-server.service` | `ExecStart=…/uvicorn dashboard_api:app --host 0.0.0.0` · `EDITORTRACK_TOKEN=kpp-et-test` · `EDITORTRACK_STORE=…/server/data` · `EnvironmentFile=-%h/.editortrack/server.env` | `main` ยังไม่มี `run.py` → เปลี่ยน ExecStart ก่อน merge ไม่ได้ (ดูขั้น 4) · `kpp-et-test` ถูกปฏิเสธเสมอในโค้ดใหม่ |
| `/srv/editortrack` | ไม่มี | ไม่ตั้ง `EDITORTRACK_HOME` → server หาไฟล์ token/user ที่ `/srv/editortrack/…` ไม่เจอ → ingest 401 ทุกเครื่อง และหน้าอ่าน 401 ทุกคน |
| agent ทุกเครื่อง | โค้ดเก่า (main) + `kpp-et-test` | โค้ดเก่าเก็บทุก error (รวม 401) ไว้ใน queue ไม่มีเพดาน — ข้อมูลไม่หายช่วงเปลี่ยน แต่ queue โต ~1,440 บรรทัด/วันจนกว่าจะเปลี่ยน token |

## 1. เตรียมเครื่อง server (ก่อน merge — ไม่กระทบ service ที่รันอยู่)
1. ที่เก็บ (นอก git checkout, 700):
   ```sh
   sudo install -d -m 700 -o "$USER" -g "$USER" /srv/editortrack /srv/editortrack/data
   ```
2. ใส่ใน `~/.editortrack/server.env` (EnvironmentFile ของ unit — อ่านใหม่ทุกครั้งที่ restart ไม่ต้อง daemon-reload):
   ```sh
   EDITORTRACK_HOME=/srv/editortrack
   EDITORTRACK_RETENTION_DAYS=0      # ปิดการลบไว้ก่อนจนกว่าจะตกลง retention (ขั้น 7) — default 180 ลบตอน start
   ```
   ยังไม่ต้องเอา `EDITORTRACK_STORE` ออกจาก unit (ย้ายข้อมูลทีหลังในขั้น 7)
3. ออก read user และ token ต่อเครื่อง ด้วย **env เดียวกับ server** (ไม่งั้นไฟล์ไปอยู่คนละที่กับที่ server อ่าน)
   `main` ยังไม่มี `manage_tokens.py` และห้ามสลับ branch ใน checkout ที่ deploy อยู่ → ใช้ worktree ชั่วคราว:
   ```sh
   git -C ~/github/editortrack worktree add ~/.cache/et-rollout audit-fixes
   cd ~/.cache/et-rollout/server
   set -a; . ~/.editortrack/server.env; set +a          # EDITORTRACK_HOME เดียวกับ service
   ~/.editortrack-venv/bin/python manage_tokens.py read-user <ชื่อ>      # ถามรหัสผ่าน
   for h in conform-01 grade-01 grade-02 grade-03 grade-04 grade-05 grade-06; do
     ~/.editortrack-venv/bin/python manage_tokens.py issue "$h"          # token แสดงครั้งเดียว — จดลงที่ปลอดภัย ไม่ส่งทางแชต
   done
   ~/.editortrack-venv/bin/python manage_tokens.py list
   ls -l /srv/editortrack          # agent_tokens.json / read_users.json ต้องเป็น -rw------- (600)
   cd ~ && git -C ~/github/editortrack worktree remove ~/.cache/et-rollout
   ```
   ชื่อ host ต้องเป็นชื่อที่ agent ส่งมา (`socket.gethostname()`) — server แปลงเป็นตัวเล็ก/ตัดช่องว่างก่อนเทียบ token
   ไม่แน่ใจ: ดูชื่อใน `/inbox` ปัจจุบัน หรือหลัง merge ดู log `ingest rejected: host=… reason=…`

## 2. autodeploy smoke → `/healthz`
แก้ `~/tools/autodeploy/services/editortrack.conf` (repo นี้ไม่ได้แก้ไฟล์นั้นให้):
```sh
SMOKE_URL="http://127.0.0.1:8000/healthz"
```
`/healthz` ตอบ `{"ok": true}` โดยไม่ต้อง login แต่ **มีเฉพาะในโค้ดใหม่** — แก้ทันทีก่อน merge ในรอบเดียวกัน
(ถ้ามี commit อื่นเข้า `main` ก่อน audit-fixes ระหว่างนั้น smoke จะ 404 และวน restart แบบเดียวกับตาราง — merge ต่อเลย)

## 3. checkout บน `main` และ `NEXT_STEPS.md` เคลียร์แล้ว
```sh
git -C ~/github/editortrack status --short      # ต้องว่าง (ไฟล์ tracked)
git -C ~/github/editortrack branch --show-current   # main
```
`NEXT_STEPS.md` ที่แก้ค้าง: commit เข้า main (แล้ว push ก่อน/พร้อม merge) หรือ `git stash` — ไม่งั้น autodeploy SKIP

## 4. unit: `ExecStart` → `run.py`
`main` ยังไม่มี `server/run.py` → **ห้ามเปลี่ยน unit แล้ว restart ก่อน merge** (service จะล้ม) · เลือกทางใดทางหนึ่ง:
- **(แนะนำ) หลัง merge** (ต่อจากขั้น 5): โค้ดใหม่ที่ยังรันด้วย uvicorn ตรงปลอดภัยพอชั่วคราว — หน้าอ่านยังต้อง login,
  `READ_AUTH=none` เปิดเฉพาะ connection ทาง loopback, และ log `!!! editortrack: start ด้วย uvicorn ตรง (ไม่ผ่าน run.py)` เตือนจนกว่าจะเปลี่ยน
- ก่อน merge ทันที: แก้ไฟล์ + `daemon-reload` **โดยไม่ restart** แล้ว merge ภายในไม่กี่นาที (ถ้า service ตายเองก่อน merge จะ restart ด้วย run.py ที่ยังไม่มี → ล้มวน)

แก้ `~/.config/systemd/user/editortrack-server.service`:
```ini
# ลบ: Environment=EDITORTRACK_TOKEN=kpp-et-test   (โค้ดใหม่ปฏิเสธเสมอ และ shared token ปิดเป็น default)
ExecStart=%h/.editortrack-venv/bin/python run.py --lan
```
`--lan` = bind 0.0.0.0 แบบที่ agent ใช้อยู่ตอนนี้ (`http://192.168.140.126:8000/ingest`, HTTP ไม่เข้ารหัส — run.py เตือนทุกครั้ง)
มี Caddy/TLS แล้ว (server/README.md §TLS) → `python run.py` เฉย ๆ (127.0.0.1) และเปลี่ยน `EDITORTRACK_SERVER` ของ agent เป็น https
- **gateway ของ IT-KHO มีชื่อ HTTPS ให้อยู่แล้ว**: `~/github/IT-KHO/deploy/gateway/Caddyfile` มี `import site editortrack 127.0.0.1:8000`
  → `https://editortrack.192.168.140.126.nip.io` (`tls internal` — root cert `kho-gateway-root.crt` ใน folder นั้น, README §5)
  และ `http://editortrack.192.168.140.126.nip.io` (port 80) · ไม่ต้องตั้ง Caddy เอง: agent ใช้
  `EDITORTRACK_SERVER=https://editortrack.192.168.140.126.nip.io/ingest` (python ของ agent ต้องเชื่อ root cert นั้น —
  `SSL_CERT_FILE` หรือ store ของ OS, ดู server/README.md §TLS) แล้ว server เลิก `--lan` ได้เมื่อทุกเครื่องย้ายมาแล้ว
- ⚠️ **หลัง gateway นั้นต้องใช้ `EDITORTRACK_READ_AUTH=basic` (default) เท่านั้น** — gateway ไม่ทำ auth และอยู่บน host network
  ทุก request จาก LAN จึงเข้ามาทาง loopback · `none` ต้องมี proxy ที่ทำ auth และส่ง `X-Editortrack-Proxy-Secret`
  (`EDITORTRACK_PROXY_SECRET`, server/README.md §TLS) — gateway ไม่ส่ง → `none` ที่นั่นตอบ 401 ทุกหน้า
```sh
systemctl --user daemon-reload && systemctl --user restart editortrack-server.service
journalctl --user -u editortrack-server -n 30 --no-pager    # ต้องไม่มี "!!! editortrack:" เหลือ
```

## 5. merge แล้วดู autodeploy log
merge `audit-fixes` → `main` แล้ว push (timer `autodeploy@editortrack` เช็คทุกนาที)
```sh
tail -f ~/.local/state/autodeploy/editortrack.log        # รอ "=== OK  now serving <sha>"
journalctl --user -u editortrack-server -f               # "!!! editortrack: …" = config ยังไม่ครบ
curl -s http://127.0.0.1:8000/healthz                    # {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/          # 401 (ต้อง login)
curl -s -o /dev/null -w '%{http_code}\n' -u <ชื่อ> http://127.0.0.1:8000/api/jobs   # 200
```
- `!!! editortrack: ยังไม่มี read user …` → ขั้น 1.3 ไม่ได้ใช้ `EDITORTRACK_HOME` เดียวกัน
- ตอนนี้ agent ทุกเครื่องยังส่ง `kpp-et-test` → log `ingest rejected: host=… reason=leaked_token` เป็นเรื่องปกติจนกว่าจะถึงขั้น 6 (agent เก็บไว้ใน queue)
- แล้วทำขั้น 4 ถ้ายังไม่ได้ทำ

## 6. agent ทีละเครื่อง — token ก่อน แล้วค่อยอัปเดตโค้ด
ทีละเครื่อง เริ่มจาก conform-01 · เครื่องถัดไปเมื่อเครื่องก่อนขึ้นเขียวใน `/inbox`
1. **token ก่อน** (ใช้ได้ทั้งกับ agent เก่าและใหม่): **แก้มือ** `EDITORTRACK_TOKEN=<token ของเครื่องนี้>` ใน `agent/deploy/env.sh` แล้ว
   ```sh
   chmod 600 agent/deploy/env.sh
   ```
   **อย่ารัน `make-env.sh` ตอนนี้** — เครื่องยังเป็นโค้ดเก่า (main): make-env เก่ามี default `kpp-et-test`, พิมพ์ token ออกจอ
   และไม่ลดสิทธิ์ไฟล์ที่มีอยู่แล้ว (env.sh ที่ `cp` มาจาก `stations/*.sh` = 644) · อยากใช้ make-env → หลังขั้น 3 (`git pull`) ค่อยรัน
   ไม่ต้อง reinstall scheduler (`run-agent.sh` อ่าน env.sh ทุกรอบ)
2. รอ 1–2 นาที: `/inbox` เห็นเครื่อง online · log server ไม่มี `ingest rejected: host=<เครื่องนี้>` · queue เก่าทยอยเข้า (ชิป "ย้อนหลัง")
   ยัง 401: agent เห็นแค่ `bad_token` (server ไม่บอกคนนอกว่า host ไหนมี token) แต่ **log server แยกให้**:
   - `ingest rejected: host='…' reason=unknown_host` → ชื่อ host ไม่ตรงกับที่ issue ไว้ (ดูชื่อใน log แล้ว `manage_tokens.py issue <ชื่อนั้น>`)
   - `reason=bad_token` → host ถูก แต่ token พิมพ์ผิด/ตกหล่น (หรือ issue `--force` ใหม่ทีหลัง)
   - `reason=leaked_token` → ยังเป็น `kpp-et-test` (env.sh ไม่ถูกแก้ / แก้ผิดไฟล์)
3. แล้วค่อยอัปเดตโค้ด agent (`git pull` ใน checkout ของเครื่องนั้น) — ไม่ส่ง user/ชื่อแอป, queue มีเพดาน, 401/403 เก็บใน queue,
   ส่ง backlog ก่อน record ใหม่ · backlog ใหญ่ทยอยส่งรอบละไม่เกิน 60 วิ / 2,000 บรรทัด (`EDITORTRACK_FLUSH_BUDGET_S` / `_MAX_LINES`)
   — queue ~10k บรรทัดจึงใช้หลายนาที (ระหว่างนั้น record ใหม่ต่อท้าย queue, `/inbox` ขึ้น "ย้อนหลัง") ไม่ใช่ค้าง
4. เครื่องถัดไป

## 7. เก็บกวาด (หลังครบทุกเครื่อง) — ตามลำดับนี้
1. **ปิด shared token**: ไม่มี `EDITORTRACK_ALLOW_SHARED_TOKEN` / `EDITORTRACK_TOKEN` ใน unit และ `server.env` · log ต้องไม่มี `ใช้ token รวม`
2. **ย้ายข้อมูล + scrub** (หยุด service สั้น ๆ):
   ```sh
   systemctl --user stop editortrack-server.service
   mv ~/github/editortrack/server/data/*.jsonl /srv/editortrack/data/
   cd ~/github/editortrack/server && set -a && . ~/.editortrack/server.env && set +a
   ~/.editortrack-venv/bin/python store.py scrub /srv/editortrack/data      # ตัด user/ชื่อแอปออกจากไฟล์เดิม
   # ลบ Environment=EDITORTRACK_STORE=… ออกจาก unit (default = $EDITORTRACK_HOME/data)
   systemctl --user daemon-reload && systemctl --user start editortrack-server.service
   ```
3. **ตกลง retention** กับเจ้าของข้อมูล/ทนาย (NEXT_STEPS เฟส 0) → ตั้ง `EDITORTRACK_RETENTION_DAYS=<N>` แทน 0 ใน `server.env`
4. **หลังจากนั้นเท่านั้น** เปิดส่งเวลาทำงานให้ PMS: `ERP_ACTIVITY_PATH=/api/integrations/editortrack/activity` (+ `ERP_ROOM_MAP` ที่ตรง resource_code จริง) ใน `server.env` แล้ว restart · ดู `/api/pms`
5. **ห้ามเปลี่ยน `PMS_WINDOW_MIN`** (default 10) หลังเริ่มส่งจริง — ขอบ window เปลี่ยน = key `(host, window_start)` ใหม่ = PMS นับช่วงที่ทับกันซ้ำ

## ⚠️ อย่าหมุน `ERP_TOKEN` ตอนเปิด activity sync อยู่
PMS กันส่งซ้ำด้วย unique `(integration key, host, window_start)` และ editortrack จำว่าส่ง window ไหนไปแล้วไว้ในหน่วยความจำเท่านั้น
→ restart ด้วย key ใหม่ = ส่ง window ย้อนหลัง `PMS_LOOKBACK_HOURS` (24 ชม.) ใหม่หมดด้วย key ที่ PMS ไม่เคยเห็น = **เวลาทำงาน 24 ชม. ถูกนับซ้ำ**
(key เดิม = ได้ `duplicates` ไม่มีผล) · ต้องหมุนจริง (key หลุด): ตกลงกับทีม PMS ก่อนว่าจะลบ/รวมแถวซ้ำของ key ใหม่ในช่วงนั้น
หรือรอให้ PMS ทำข้อเสนอด้านล่างข้อ 2 · progress (`/progress`) ไม่มีปัญหานี้ (ส่งเฉพาะค่าที่สังเกตใหม่)

## ข้อเสนอฝั่ง PMS ที่ยังค้าง
1. **upsert แทน ON CONFLICT DO NOTHING** ที่ `/activity`: key เดิมแต่ `active_seconds` มากขึ้น → อัปเดตเป็นค่าใหม่ (หรือ max)
   ตอนนี้ record ที่มาช้ากว่าการ settle ของ `pms_sync` ถูกส่งแก้ด้วย key เดิมแต่ PMS ตอบ duplicate ทิ้ง —
   เวลาที่ขาดนับไว้ที่ `/api/pms` → `corrections.ignored_seconds` · PMS ทำ upsert เมื่อไหร่ editortrack ไม่ต้องแก้โค้ด (ส่งแก้อยู่แล้ว)
2. (ถ้าทำได้) ผูก unique กับ integration (`editortrack`) แทนตัว key — หมุน key แล้วไม่นับซ้ำ

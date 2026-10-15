# NEXT STEPS — editortrack (อัปเดต 2026-10-15)

> สถานะหลังทดสอบ deploy บน Mac จริง (conform-01) · อ่าน `HANDOFF.md` สำหรับภารกิจ/กฎเหล็ก/architecture เต็ม
> repo: https://github.com/jaikwangg/resolve-poller (push แล้ว ถึง commit network diagram)

---

## ✅ พิสูจน์แล้ว (end-to-end บน Mac จริง)
- agent อ่าน DaVinci Resolve ได้ (timeline/clips), คำนวณ `pct`, metric 02 (idle/active ผ่าน pyobjc)
- pipeline ครบวง: **agent → POST /ingest → editortrack server → dashboard**
- scheduler ยิงเองทุก 60 วิ (gap ยืนยัน +60s)
- deploy ชุดเต็ม: resource cap (Nice/LowPriorityIO), station templates 7 เครื่อง, `make-env.sh`, README (Mac/Linux + test ladder)

## 🔴 ต้องทำก่อน "ใช้จริง" (blocking)
1. **ต่อ ERP จริง** — ตอนนี้ job = fallback `"Untitled Project"` เพราะยังไม่ได้ join
   - ใส่ `~/.editortrack/room_map.json` จริง (host → resource_code ของห้องใน ERP)
   - ตั้ง creds ERP (env) → ยืนยัน `erp.lookup(host,ts)` ได้ job จริงจาก `/api/schedule`
2. **server ถาวร** — ตอนนี้รันชั่วคราวในเครื่องทดสอบ (`192.168.140.126:8000`, ดับเมื่อปิด session)
   - ย้ายไปเครื่อง server จริง (ไม่ใช่ workstation) · รันถาวร (systemd) · token จริง · retention/หมุน JSONL · ใส่ TLS ถ้าข้าม network
3. **ลงครบ 7 เครื่อง** — เทสแล้วแค่ conform-01 (Mac) · เหลือ **6 ห้องเกรด (Linux)**: probe ต่อเครื่อง + เช็ค `$XDG_SESSION_TYPE=x11` + `libxss1`/`xdotool`
4. **validate ความแม่น metric บนงานจริง**
   - conform % = offline duration → สะท้อน progress จริงไหม + ชื่อ offline timeline ตรง `EDITORTRACK_OFFLINE_MATCH`
   - graded = node>1 → ตรงกับ "เกรดเสร็จ" ไหม (false pos: clip มี node tree มาก่อน / false neg: เกรด node เดียว)
   - idle threshold 90s ตอนดู playback = นับ idle ทั้งที่ทำงาน (metric 02 อาจ undercount)
5. **พิสูจน์ metric 01 ขยับ** — เกรด clip จริง → ดู `pct` เพิ่มจาก 0 (ยังไม่ได้ยืนยัน)

## 🟡 รอของ / later
6. **ERP progress write-back** — `push_progress` เป็น scaffold (เส้นประในไดอะแกรม) · รอ `ERP_PROGRESS_PATH` + field spec จากทีม ERP
7. **PDPA/คน** — แจ้งทีมเป็นทางการ + policy ว่าใครเห็น dashboard (กฎ advisory: ห้ามผูกประเมิน)
8. **data stage** — ⏸️ pause (รอ detail) · **subtitle/master** — ตั้งเป้าถ้าจะเปิด % (ตอนนี้ค่าดิบ)
9. **cross-network** — ถ้ามี agent คนละ subnet: routed+DNS (องค์กรเดียว) / Tailscale-WireGuard (ข้ามไซต์) / Caddy+TLS · แก้แค่ `EDITORTRACK_SERVER` ใน env.sh + ใส่ TLS ถ้าข้าม network นอก

---

## 🔬 สิ่งที่ข้อมูลเทส 7–14 ต.ค. บอก (264 record, conform-01 Mac · replay ได้ที่ `server/tests/test_replay.py`)
| เรื่อง | ข้อมูลบอก | ผลต่อเครื่องจริง |
|---|---|---|
| active ต้องมี Resolve อยู่หน้าสุด **และ** มี input | 12 ต.ค. Resolve อยู่หน้าสุด ~20 นาทีแต่ idle 139→1225 วิ · 14 ต.ค. มี input ใน Terminal 17 นาที | ถูกแล้ว — ถ้านับแค่ "Resolve อยู่หน้าสุด" จะเกินจริงมาก |
| idle threshold 90 วิ | Resolve หน้าสุด 57 sample: 60วิ→24 นาที · 90→30 · 120→30 · 300→38 | 90 กับ 120 ได้เท่ากัน เก็บ 90 · **ยังไม่มีข้อมูลตอนดู playback** (ไม่มี input แต่ทำงาน) — ต้องวัดในห้องเกรดจริง |
| Mac ล็อก/หลับ | ตื่นราวชั่วโมงละครั้ง (gap ~3,650 วิ) และ idle เพิ่มแค่ ~60 วิต่อครั้ง = เครื่องหลับระหว่างนั้น | ข้อมูลขาดเป็นชั่วโมง ไม่ใช่ agent พัง · อยากได้ต่อเนื่อง → ปิด sleep |
| agent รันซ้อน | 8 ต.ค. 2 record ห่างกัน 0.29 วิ (ช่วงติดตั้ง) | server ตัด record ซ้อน < 30 วิ แล้ว (`explain.dedupe`) |
| record ทดสอบหลุดเข้า data | 8 ต.ค. `{"host":"selftest"}` ไม่มี ts | `/ingest` ตอบ 400 ถ้าไม่มี host/ts แล้ว |
| เปิด project ใหม่ | `page` = null 1 รอบ (3 ครั้ง) ตอน Resolve กำลังโหลด | ปกติ — รอบถัดไปมาเอง |
| scan ราย timeline | 3–37 ms (6–8 clip) · 309 clip วัดบน Windows ได้ 156 ms | เกณฑ์ node เบามาก · เกณฑ์ LUT ใหม่ยังไม่ได้วัด (probe_grade.py) |
| % สี (เกณฑ์ node>1) | 1/6→6/6→5/6→6/8 ใน 5 นาที · blade ทำ clip 6→8 ความยาวเท่าเดิม | แก้เป็นเกณฑ์ LUT แล้ว (85bf2c8) · ตัวหาร = จำนวน clip ยังลดเมื่อ blade → ควรนับราย shot จาก marker |
| offline queue | ทุก record มาถึง server ภายใน 0 วิ — **ไม่เคยถูกใช้เลย** | ยังไม่ได้พิสูจน์: เครื่องจริงลองหยุด server 3 นาที แล้วดูว่า record ย้อนหลังมาครบ (`/inbox` แสดง "ส่งย้อนหลัง") |
| stage ของเครื่อง | conform-01 ส่ง `stage=color` ตลอด | ใช้ `deploy/stations/<host>.sh` ตอนลงจริง |

## ⚙️ บทเรียน deploy ที่เสียเวลาหาสุด (กันเครื่องอื่นเจอซ้ำ)
| อาการ | ราก | แก้ |
|---|---|---|
| scheduler เงียบ แต่รันมือได้ 200 · `last exit code = 126` | โค้ดอยู่ใน **`~/Downloads`** (TCC ปกป้อง → launchd โดนบล็อก) | วางใน `~/editortrack` หรือ `~/Library/Application Support/` |
| launchd รันแต่ไม่ POST (นับ run แต่ไม่มี record) | **PATH แคบ หา `python3` ไม่เจอ** | `run-agent.sh` เติม PATH แล้ว (homebrew/framework) + `PYTHON=` override ได้ |
| StartInterval ไม่ยิงตรงเวลา | macOS **coalesce timer** (App Nap) แม้เอา ProcessType Background ออก | เปลี่ยนเป็น **KeepAlive loop** (`run-loop.sh`) — mac เท่านั้น · Linux ใช้ systemd timer (เสถียร) |
| fix ที่ push ไปไม่ถึงเครื่อง | โหลดเป็น **ZIP ไม่ใช่ git clone** → `git pull` ไม่ได้ผล | ไม่มี git: `curl -L .../main.zip && unzip -o` · วางนอก Downloads |
| POST ได้ `None` | ไม่มี `env.sh` → default localhost + token ว่าง | `sh deploy/make-env.sh` |

## ▶️ จุดเริ่มถ้าเดินต่อ
**คุ้มสุด:** #1 (ต่อ ERP จริง) + #2 (server ถาวร) = เปลี่ยนจากเดโมเป็นใช้จริง · #4 validate ทำคู่ตอนลงห้องเกรด (#3)

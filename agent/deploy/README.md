# deploy agent — macOS / Linux

ติดตั้ง agent ให้รันอัตโนมัติทุก 1 นาที · priority ต่ำสุด (ไม่แย่งทรัพยากร Resolve) · log ต่อ user ที่ `~/.editortrack/agent.log`
(Windows ดู `../../DATAFLOW.md §Deploy` — ใช้ Task Scheduler)

> โค้ดเหมือนกันทุกเครื่อง ต่างแค่ **env.sh** (เลือกจาก `stations/`) + **hostname** · ดู `stations/README.md`

> 🛑 **macOS — ห้ามวางโค้ดใน `~/Downloads`, `~/Desktop`, `~/Documents`**
> โฟลเดอร์พวกนี้ถูก **TCC (privacy) ปกป้อง** → LaunchAgent (background) ถูกบล็อก ไม่ให้รันสคริปต์ (`last exit code = 126`)
> — อาการหลอก: **รันมือในเทอร์มินัลได้ 200 ปกติ** (เพราะ Terminal มีสิทธิ์) แต่ scheduler เงียบสนิท
> ✅ วางใน `~/editortrack` หรือ `~/Library/Application Support/editortrack` แทน

---

## ขั้นตอนร่วม (ทั้ง mac/linux)
> ⚠️ **ต้องมี `deploy/env.sh` ก่อน** — ไม่งั้น runner จะใช้ default (`localhost` + token ว่าง) แล้ว POST ไม่ติด → ได้ `None`

**วิธีสร้าง env.sh (เลือก 1):**
```sh
cd agent
# a) ตัวช่วย: ถามค่า + auto-detect OS (path Resolve ให้เอง) — ง่ายสุด
sh deploy/make-env.sh
#    ไม่อยากถาม:  STAGE=color SERVER=http://IP:8000/ingest TOKEN=xxx sh deploy/make-env.sh
# b) หรือก็อป template ของเครื่องนั้นแล้วแก้เอง
cp deploy/stations/<STATION>.sh deploy/env.sh    # grade-01..06 (color) หรือ conform-01
nano deploy/env.sh                               # แก้ SERVER_IP + TOKEN
```
จากนั้นติดตั้ง:
```sh
sh deploy/install.sh                             # ทดสอบ 1 รอบ → ติดตั้ง scheduler (per-user)
#   หลายกะ/หลาย user:  sh deploy/install.sh --system
```

### ทดสอบเร็วบนเครื่องเดียว (ยังไม่มี server จริง)
สร้าง env.sh ชี้ localhost + token ทดสอบ แล้วรัน server local ควบคู่ (ชั้น 5)

**macOS** — วางทั้งบล็อกใน `agent/`:
```sh
cat > deploy/env.sh <<'EOF'
export EDITORTRACK_STAGE=color
export EDITORTRACK_DETECT_GRADE=1
export EDITORTRACK_SERVER=http://localhost:8000/ingest
export EDITORTRACK_TOKEN=testtoken
export EDITORTRACK_HTTP_TIMEOUT=5
export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
EOF
```
**Linux** — เหมือนกัน เปลี่ยนแค่ 2 path:
```sh
cat > deploy/env.sh <<'EOF'
export EDITORTRACK_STAGE=color
export EDITORTRACK_DETECT_GRADE=1
export EDITORTRACK_SERVER=http://localhost:8000/ingest
export EDITORTRACK_TOKEN=testtoken
export EDITORTRACK_HTTP_TIMEOUT=5
export RESOLVE_SCRIPT_API="/opt/resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/opt/resolve/libs/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
EOF
```
จากนั้น Terminal นึงเปิด server:
```sh
cd ../server && pip3 install fastapi uvicorn && EDITORTRACK_TOKEN=testtoken uvicorn dashboard_api:app --port 8000
```
อีก Terminal ยิง 1 รอบ (ต้อง `. deploy/env.sh` ก่อนเสมอ):
```sh
cd agent && . deploy/env.sh && python3 runner.py    # ได้ 200 = ส่งเข้า server แล้ว (ไม่ใช่ None)
```
ถอน:
```sh
# macOS
launchctl bootout gui/$(id -u)/com.kantana.editortrack.agent ; rm ~/Library/LaunchAgents/com.kantana.editortrack.agent.plist
# Linux
systemctl --user disable --now editortrack-agent.timer
```

---

## 🍎 macOS

### เตรียมก่อน
1. **Resolve Studio** + Preferences → System → General → *External scripting using* = **Local** (ฟรีใช้ไม่ได้)
2. pyobjc (metric 02 — idle/frontmost):
   ```sh
   pip3 install pyobjc-framework-Quartz pyobjc-framework-Cocoa
   ```
3. ตั้ง hostname ให้ตรง room_map:
   ```sh
   sudo scutil --set HostName conform-01
   ```
4. ใน `env.sh` ใช้ path macOS (default ใน `conform-01.sh`)

### เทสเป็นชั้น (0-4 ไม่ต้องมี server)
```sh
# 0) pyobjc พร้อม
python3 -c "import Quartz, AppKit; print('pyobjc ok')"

# 1) metric 02 (ขยับเมาส์แล้วรันซ้ำ ดู idle_sec ลด)
python3 -c "import json,activity_sampler as a; print(json.dumps(a.sample(), default=str))"

# 2) ต่อ Resolve ได้ไหม (เปิด Resolve + โปรเจค + timeline หน้า Color)
. deploy/env.sh && python3 probe.py          # ดู §7: connect ✅ / Studio ✅ / COLOR

# 3) snapshot + เช็คไม่เด้งหน้า ("page" ควรคงเป็น color)
. deploy/env.sh && python3 resolve_poller.py

# 4) record เต็ม (ยังไม่ส่ง)
. deploy/env.sh && python3 -c "import json,runner; print(json.dumps(runner.build_record(), ensure_ascii=False, indent=2, default=str))"
```

### ยืนยัน + log (หลัง install)
macOS ใช้ **KeepAlive loop** (`run-loop.sh`) ยิงทุก 60 วิ — ไม่ใช้ StartInterval เพราะ macOS หน่วง timer (coalescing/App Nap)
```sh
pgrep -fl run-loop          # เห็น run-loop.sh = ลูปทำงาน (process ค้าง 1 ตัว, Nice 19)
tail -f ~/.editortrack/agent.log   # log เพิ่มทุก 60 วิ
```
ปรับช่วงเวลา: ใส่ `export EDITORTRACK_INTERVAL_SEC=120` ใน `deploy/env.sh` (ไม่ต้องแก้ plist)
> ถ้า log นิ่ง + `last exit code = 126` ใน `launchctl print ...` = โค้ดอยู่ในโฟลเดอร์ TCC (Downloads/Desktop/Documents) → ย้ายไป `~/editortrack`

---

## 🐧 Linux (ห้อง color ×6)

### เตรียมก่อน
1. **Resolve Studio** (`/opt/resolve/...`) + External scripting = **Local**
2. X11 libs (metric 02):
   ```sh
   sudo apt install -y libxss1 xdotool x11-utils
   ```
3. **ต้องเป็น X11** (ไม่ใช่ Wayland):
   ```sh
   echo $XDG_SESSION_TYPE        # ต้องได้ x11
   ```
4. ตั้ง hostname:
   ```sh
   sudo hostnamectl set-hostname grade-03
   ```
5. ใน `env.sh` ใช้ path Linux (default ใน `grade-0N.sh`)

### เทสเป็นชั้น (0-4 ไม่ต้องมี server)
```sh
# 0) X11 + libs
echo $XDG_SESSION_TYPE                         # x11
python3 -c "import ctypes; ctypes.CDLL('libXss.so.1'); print('libXss ok')"

# 1) metric 02
python3 -c "import json,activity_sampler as a; print(json.dumps(a.sample(), default=str))"

# 2) ต่อ Resolve (เปิด Resolve + โปรเจค + timeline หน้า Color)
. deploy/env.sh && python3 probe.py            # ดู §7: connect ✅ / Studio ✅ / COLOR

# 3) snapshot + เช็คไม่เด้งหน้า
. deploy/env.sh && python3 resolve_poller.py

# 4) record เต็ม (ยังไม่ส่ง)
. deploy/env.sh && python3 -c "import json,runner; print(json.dumps(runner.build_record(), ensure_ascii=False, indent=2, default=str))"
```

### force run + สถานะ + log (หลัง install)
```sh
systemctl --user start editortrack-agent.service       # รันเดี๋ยวนี้ 1 รอบ
systemctl --user list-timers | grep editortrack
journalctl --user -u editortrack-agent -n 30 --no-pager
tail -f ~/.editortrack/agent.log
```

### จุดที่ Linux งอแงบ่อย
- **XAUTHORITY** บาง display manager ไม่ใช่ `~/.Xauthority` (gdm อยู่ `/run/user/$(id -u)/gdm/Xauthority`) → ถ้า idle อ่านไม่ได้ แก้ `Environment=XAUTHORITY=` ใน service
- **Wayland** → เลือก session "X11/Xorg" ตอน login
- `CPUQuota`/`MemoryMax` ต้องมี cgroup v2 (distro ใหม่มีหมด)

---

## ⚙️ ชั้น 5-7 (ร่วม) — end-to-end + ไม่รบกวน

**ชั้น 5 — end-to-end** (รัน server ทดสอบบนเครื่องเดียวกันก่อน):
```sh
# Terminal A — server
cd ../server && pip3 install fastapi uvicorn && EDITORTRACK_TOKEN=testtoken uvicorn dashboard_api:app --port 8000
# Terminal B — ยิง 1 รอบไป localhost แล้วเปิด dashboard
cd agent && . deploy/env.sh && EDITORTRACK_SERVER=http://localhost:8000/ingest EDITORTRACK_TOKEN=testtoken python3 runner.py   # ได้ 200
```
เปิด `http://localhost:8000/`

**ชั้น 6 — เทสสำคัญสุด: ไม่รบกวนงาน**
เปิด playback จริง แล้วยิง `python3 resolve_poller.py` ตอนนั้น → ดูว่า**เฟรมตกไหม** + `scan_ms` ควร < 300ms

**ชั้น 7 — scheduler รันเอง** → ดู force run/log ของแต่ละ OS ข้างบน

> ลำดับแนะนำ: 0 → 1 → 2 → 3 → 4 → (5 ถ้าดู dashboard) → 6 (ก่อน deploy จริง) → 7

---

## resource cap (ใส่มาให้แล้ว — ไม่ต้องทำเอง)
| | macOS (plist) | Linux (service) |
|---|---|---|
| CPU priority | `Nice 19` + `ProcessType Background` | `Nice 19` + `CPUWeight 20` + `CPUQuota 20%` |
| I/O | `LowPriorityIO` | `IOSchedulingClass idle` |
| memory | — | `MemoryMax 200M` |
| ค้าง | StartInterval คุม | `TimeoutStartSec 120` |

## role ต่อห้อง (ตั้งใน env.sh — มีใน stations/ แล้ว)
| ห้อง | EDITORTRACK_STAGE | EDITORTRACK_DETECT_GRADE |
|---|---|---|
| conform/edit | `conform` | `0` |
| color (×6) | `color` | `1` |

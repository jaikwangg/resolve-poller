# deploy agent — macOS / Linux

ติดตั้ง agent ให้รันอัตโนมัติทุก 10 นาที (Windows ดู `../../DATAFLOW.md §Deploy` — ใช้ Task Scheduler)

## ขั้นตอน (เหมือนกันทั้ง mac/linux)
```sh
cd agent/deploy
cp env.example env.sh        # แล้วแก้: ROLE, DETECT_GRADE, SERVER, TOKEN, RESOLVE_* (เลือก OS)
sh install.sh                # ตรวจ OS → ทดสอบรัน 1 รอบ → ติดตั้ง scheduler
```
- **macOS** → LaunchAgent (`~/Library/LaunchAgents/com.kantana.editortrack.agent.plist`)
- **Linux** → systemd user timer (`~/.config/systemd/user/editortrack-agent.{service,timer}`)

## เตรียมก่อน (per OS)

**macOS**
- Resolve Studio + external scripting = Local
- `pip install -r ../requirements.txt`  (pyobjc สำหรับ idle/frontmost)
- ใน `env.sh` ใช้ path macOS (default)

**Linux (ห้อง color)**
- Resolve Studio (`/opt/resolve/...`) + external scripting = Local
- `sudo apt install -y libxss1`  (idle) · `xdotool` (option, frontmost)
- **ต้องเป็น X11**: `echo $XDG_SESSION_TYPE` → `x11` (Wayland ยังไม่รองรับ)
- ใน `env.sh` เปิด path Linux (คอมเมนต์ไว้)

## role ต่อห้อง
| ห้อง | EDITORTRACK_ROLE | EDITORTRACK_DETECT_GRADE |
|---|---|---|
| edit/conform | `conform` | `0` |
| color (×6) | `colorist` | `1` |

## เช็ค/แก้ปัญหา
```sh
# macOS
tail -f /tmp/editortrack.out.log /tmp/editortrack.err.log
# Linux
systemctl --user list-timers | grep editortrack
journalctl --user -u editortrack-agent -n 30 --no-pager
```
รันเทสมือ: `sh run-agent.sh` (รัน 1 รอบด้วย env.sh)

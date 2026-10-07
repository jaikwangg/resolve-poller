#!/bin/sh
# รัน agent 1 รอบ (scheduler เรียกทุก ~1 นาที) — source env.sh → runner.py ด้วย priority ต่ำ
# log ต่อ user ที่ ~/.editortrack/agent.log (cap ~1MB กันโตไม่จำกัด)
AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$AGENT_DIR/deploy/env.sh" ] && . "$AGENT_DIR/deploy/env.sh"

# launchd/systemd timer มี PATH แคบ (แค่ /usr/bin:/bin) → หา python3 ไม่เจอ รันเงียบ ๆ ไม่ POST
# เติมที่ ๆ python3 มักอยู่ (mac homebrew/framework + linux) · ตั้ง PYTHON=absolute ใน env.sh ได้ถ้าใช้ pyenv
PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PATH
PY="${PYTHON:-python3}"

LOG="$HOME/.editortrack/agent.log"
mkdir -p "$HOME/.editortrack"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt 1000000 ]; then
  tail -c 200000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
fi
echo "--- $(date '+%Y-%m-%d %H:%M:%S') poll ---" >> "$LOG"

cd "$AGENT_DIR"
# nice -19 = ให้ CPU ความสำคัญต่ำสุด (I/O ต่ำคุมโดย LowPriorityIO ใน plist / IOSchedulingClass ใน systemd)
exec nice -n 19 "$PY" runner.py >> "$LOG" 2>&1

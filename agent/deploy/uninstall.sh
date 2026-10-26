#!/bin/sh
# ถอน editortrack agent ออกจากเครื่องนี้ทั้งหมด — ไม่เหลืออะไร (zero-incident runbook "uninstall")
#   sh uninstall.sh            → หยุด + ลบ scheduler (user + system) + ลบ ~/.editortrack
#   sh uninstall.sh --purge    → + ลบโฟลเดอร์โค้ด agent เอง
#   sh uninstall.sh --dry-run  → พิมพ์สิ่งที่จะทำ ไม่ทำจริง
#   sh uninstall.sh --verify   → ตรวจว่าไม่เหลืออะไร (exit 1 ถ้ายังเหลือ)
# ไม่แตะ Resolve, project, preference ของ Resolve, หรือไฟล์อื่นนอกจากที่ agent สร้างเอง
AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.kantana.editortrack.agent"
STATE="${EDITORTRACK_STATE_DIR:-$HOME/.editortrack}"
OS="$(uname)"
DRY=0; PURGE=0; VERIFY=0
for a in "$@"; do
  case "$a" in --dry-run) DRY=1;; --purge) PURGE=1;; --verify) VERIFY=1;; esac
done
run() { if [ $DRY = 1 ]; then echo "DRY: $*"; else "$@"; fi; }
sudo_if() { if [ -w "$(dirname "$1")" ]; then run rm -f "$1"; else run sudo rm -f "$1"; fi; }

remnants() {
  left=""
  if [ "$OS" = "Darwin" ]; then
    for f in "$HOME/Library/LaunchAgents/$LABEL.plist" "/Library/LaunchAgents/$LABEL.plist"; do
      [ -e "$f" ] && left="$left $f"
    done
    launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && left="$left launchd:$LABEL"
  else
    for f in "$HOME/.config/systemd/user/editortrack-agent.service" "$HOME/.config/systemd/user/editortrack-agent.timer" \
             /etc/systemd/user/editortrack-agent.service /etc/systemd/user/editortrack-agent.timer; do
      [ -e "$f" ] && left="$left $f"
    done
    systemctl --user is-active editortrack-agent.timer >/dev/null 2>&1 && left="$left timer-active"
  fi
  pgrep -f "editortrack.*(run-loop|run-agent|runner.py)|deploy/run-loop.sh|deploy/run-agent.sh" >/dev/null 2>&1 && left="$left process"
  [ -e "$STATE" ] && left="$left $STATE"
  echo "$left"
}

if [ $VERIFY = 1 ]; then
  l="$(remnants)"
  if [ -n "$l" ]; then echo "✗ ยังเหลือ:$l"; exit 1; fi
  echo "✓ ไม่เหลือ agent บนเครื่องนี้"; exit 0
fi

# 1) kill switch ก่อน — แม้ขั้นต่อไปล้ม รอบถัดไปก็ไม่ทำอะไร
run mkdir -p "$STATE"; run touch "$STATE/DISABLED"

# 2) หยุด + ลบ scheduler
if [ "$OS" = "Darwin" ]; then
  run launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  for f in "$HOME/Library/LaunchAgents/$LABEL.plist" "/Library/LaunchAgents/$LABEL.plist"; do
    [ -e "$f" ] && sudo_if "$f"
  done
else
  run systemctl --user disable --now editortrack-agent.timer 2>/dev/null || true
  run systemctl --user stop editortrack-agent.service 2>/dev/null || true
  run rm -f "$HOME/.config/systemd/user/editortrack-agent.service" "$HOME/.config/systemd/user/editortrack-agent.timer"
  if [ -e /etc/systemd/user/editortrack-agent.timer ] || [ -e /etc/systemd/user/editortrack-agent.service ]; then
    run sudo systemctl --global disable editortrack-agent.timer 2>/dev/null || true
    sudo_if /etc/systemd/user/editortrack-agent.timer
    sudo_if /etc/systemd/user/editortrack-agent.service
  fi
  run systemctl --user daemon-reload 2>/dev/null || true
fi

# 3) process ที่ค้าง (loop/turn)
run pkill -f "deploy/run-loop.sh" 2>/dev/null || true
run pkill -f "deploy/run-agent.sh" 2>/dev/null || true

# 4) ข้อมูลของ agent (queue, cache, log, lock, lease) — ไม่มีอะไรของ Resolve อยู่ในนี้
run rm -rf "$STATE"

# 5) โค้ด
if [ $PURGE = 1 ]; then run rm -rf "$AGENT_DIR"; fi

[ $DRY = 1 ] && exit 0
l="$(remnants)"
if [ -n "$l" ]; then echo "✗ ถอนไม่ครบ ยังเหลือ:$l"; exit 1; fi
echo "✓ ถอน editortrack agent แล้ว (ไม่เหลือ scheduler / process / ~/.editortrack)"

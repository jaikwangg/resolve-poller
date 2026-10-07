#!/bin/sh
# ติดตั้ง scheduler ให้ agent รันทุก 1 นาที — ตรวจ OS อัตโนมัติ
#   macOS = LaunchAgent · Linux = systemd user timer
# โหมด:
#   sh install.sh            → ลงให้ user ปัจจุบัน (ทดสอบง่าย ไม่ต้อง sudo)
#   sh install.sh --system   → ลงให้ทุก user ที่ login (production หลายกะ — ต้อง sudo)
# เตรียมก่อน:  cp env.example env.sh && nano env.sh
set -e
AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$AGENT_DIR/deploy"
MODE="user"; [ "$1" = "--system" ] && MODE="system"

if [ ! -f "$DEPLOY/env.sh" ]; then
  echo "✗ ยังไม่มี env.sh — ทำก่อน:  cp \"$DEPLOY/env.example\" \"$DEPLOY/env.sh\"  แล้วแก้ค่า"
  exit 1
fi
chmod +x "$DEPLOY/run-agent.sh"

OS="$(uname)"

# --- prerequisite ต่อ OS ---
if [ "$OS" = "Darwin" ]; then
  python3 -c "import Quartz, AppKit" 2>/dev/null || \
    echo "⚠️  metric 02 (active/idle) ต้องมี pyobjc:  pip3 install pyobjc-framework-Quartz pyobjc-framework-Cocoa"
fi

echo "== ทดสอบรัน 1 รอบก่อน (ไม่ขึ้น error ร้ายแรง = ผ่าน) =="
( . "$DEPLOY/env.sh"; cd "$AGENT_DIR"; python3 runner.py ) || { echo "✗ runner.py รันไม่ผ่าน — แก้ก่อนค่อย install"; exit 1; }

if [ "$OS" = "Darwin" ]; then
  if [ "$MODE" = "system" ]; then DEST="/Library/LaunchAgents"; SUDO="sudo"; else DEST="$HOME/Library/LaunchAgents"; SUDO=""; fi
  PLIST="$DEST/com.kantana.editortrack.agent.plist"
  $SUDO mkdir -p "$DEST"
  sed "s#@AGENT_DIR@#$AGENT_DIR#g" "$DEPLOY/com.kantana.editortrack.agent.plist" | $SUDO tee "$PLIST" >/dev/null
  # โหลดเข้า GUI session ของ user ปัจจุบันทันที (ไม่ต้อง relogin) — รองรับทั้ง API ใหม่/เก่า
  launchctl bootout "gui/$(id -u)/com.kantana.editortrack.agent" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || { launchctl unload "$PLIST" 2>/dev/null || true; launchctl load "$PLIST"; }
  echo "✓ macOS ($MODE): โหลด LaunchAgent แล้ว (ทุก 1 นาที, priority ต่ำ)"
  [ "$MODE" = "system" ] && echo "  → ทุก user จะได้อัตโนมัติตอน login · user ปัจจุบัน bootstrap ให้แล้ว"
  echo "  log:   tail -f ~/.editortrack/agent.log"
  echo "  ถอน:   launchctl bootout gui/\$(id -u)/com.kantana.editortrack.agent ; $SUDO rm \"$PLIST\""

elif [ "$OS" = "Linux" ]; then
  if [ "$MODE" = "system" ]; then
    sudo cp "$DEPLOY/editortrack-agent.service" /etc/systemd/user/editortrack-agent.service
    sudo sed -i "s#@AGENT_DIR@#$AGENT_DIR#g" /etc/systemd/user/editortrack-agent.service
    sudo cp "$DEPLOY/editortrack-agent.timer" /etc/systemd/user/editortrack-agent.timer
    systemctl --global enable editortrack-agent.timer
    echo "✓ Linux (system): enable ให้ทุก user ที่ login (--global) · มีผลรอบ login ถัดไป"
    echo "  เริ่มเดี๋ยวนี้สำหรับ user ปัจจุบัน:  systemctl --user daemon-reload && systemctl --user start editortrack-agent.timer"
  else
    UDIR="$HOME/.config/systemd/user"; mkdir -p "$UDIR"
    sed "s#@AGENT_DIR@#$AGENT_DIR#g" "$DEPLOY/editortrack-agent.service" > "$UDIR/editortrack-agent.service"
    cp "$DEPLOY/editortrack-agent.timer" "$UDIR/editortrack-agent.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now editortrack-agent.timer
    echo "✓ Linux (user): enable systemd user timer แล้ว (ทุก 1 นาที)"
  fi
  echo "  สถานะ: systemctl --user list-timers | grep editortrack ; journalctl --user -u editortrack-agent -n 20"
  echo "  ⚠️ X11 เท่านั้น: ถ้า idle อ่านไม่ได้ เช็ค \$XDG_SESSION_TYPE=x11 + DISPLAY/XAUTHORITY ใน service"

else
  echo "✗ OS นี้ไม่รองรับสคริปต์ (Windows ใช้ schtasks — ดู ../../DATAFLOW.md §Deploy)"; exit 1
fi

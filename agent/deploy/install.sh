#!/bin/sh
# ติดตั้ง scheduler ให้ agent รันทุก 10 นาที — ตรวจ OS อัตโนมัติ (macOS=LaunchAgent, Linux=systemd user timer)
# ใช้:  cp env.example env.sh && nano env.sh   แล้ว  sh install.sh
set -e
AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$AGENT_DIR/deploy"

if [ ! -f "$DEPLOY/env.sh" ]; then
  echo "✗ ยังไม่มี env.sh — ทำก่อน:  cp \"$DEPLOY/env.example\" \"$DEPLOY/env.sh\"  แล้วแก้ค่า"
  exit 1
fi
chmod +x "$DEPLOY/run-agent.sh"

echo "== ทดสอบรัน 1 รอบก่อน =="
( . "$DEPLOY/env.sh"; cd "$AGENT_DIR"; python3 runner.py ) || { echo "✗ runner.py รันไม่ผ่าน — แก้ก่อนค่อย install"; exit 1; }

OS="$(uname)"
if [ "$OS" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.kantana.editortrack.agent.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  sed "s#@AGENT_DIR@#$AGENT_DIR#g" "$DEPLOY/com.kantana.editortrack.agent.plist" > "$PLIST"
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "✓ macOS: โหลด LaunchAgent แล้ว (ทุก 10 นาที) · log: /tmp/editortrack.*.log"
  echo "  ถอน:  launchctl unload \"$PLIST\" && rm \"$PLIST\""
elif [ "$OS" = "Linux" ]; then
  UDIR="$HOME/.config/systemd/user"; mkdir -p "$UDIR"
  sed "s#@AGENT_DIR@#$AGENT_DIR#g" "$DEPLOY/editortrack-agent.service" > "$UDIR/editortrack-agent.service"
  cp "$DEPLOY/editortrack-agent.timer" "$UDIR/editortrack-agent.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now editortrack-agent.timer
  echo "✓ Linux: enable systemd user timer แล้ว (ทุก 10 นาที)"
  echo "  สถานะ:  systemctl --user list-timers | grep editortrack ; journalctl --user -u editortrack-agent -n 20"
  echo "  ถอน:  systemctl --user disable --now editortrack-agent.timer"
  echo "  ⚠️ ถ้า idle อ่านไม่ได้: เช็ค \$XDG_SESSION_TYPE=x11 + แก้ DISPLAY/XAUTHORITY ใน service"
else
  echo "✗ OS นี้ไม่รองรับสคริปต์ (Windows ใช้ schtasks — ดู ../../DATAFLOW.md §Deploy)"; exit 1
fi

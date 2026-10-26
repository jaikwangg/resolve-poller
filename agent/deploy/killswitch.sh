#!/bin/sh
# kill switch ในเครื่อง — มีผลรอบถัดไป (≤ 1 นาที) และหยุด scheduler ทันที
#   sh killswitch.sh on      → agent ไม่ทำอะไรเลย (ไม่แตะ Resolve ไม่ใช้ network) + หยุด scheduler
#   sh killswitch.sh off     → เปิดกลับ (ต้องตั้งใจ — ลบไฟล์ + start scheduler)
#   sh killswitch.sh status
# ระดับเครื่อง (ทุก user, ต้อง sudo): ไฟล์ /etc/editortrack/DISABLED (Linux)
#   หรือ "/Library/Application Support/editortrack/DISABLED" (Mac) — agent เช็คทุกรอบ
# ระดับ server (ทุกเครื่องพร้อมกัน): ดู /agent/control ใน server (lease) — WORKSTATION_SAFETY.md §kill switch
STATE="${EDITORTRACK_STATE_DIR:-$HOME/.editortrack}"
LABEL="com.kantana.editortrack.agent"
case "$1" in
  on)
    mkdir -p "$STATE" && touch "$STATE/DISABLED"
    if [ "$(uname)" = "Darwin" ]; then launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    else systemctl --user stop editortrack-agent.timer editortrack-agent.service 2>/dev/null || true; fi
    pkill -f "deploy/run-loop.sh" 2>/dev/null || true
    echo "✓ ปิดแล้ว ($STATE/DISABLED)";;
  off)
    rm -f "$STATE/DISABLED"
    if [ "$(uname)" = "Darwin" ]; then
      P="$HOME/Library/LaunchAgents/$LABEL.plist"; [ -f "$P" ] || P="/Library/LaunchAgents/$LABEL.plist"
      launchctl bootstrap "gui/$(id -u)" "$P" 2>/dev/null || true
    else systemctl --user start editortrack-agent.timer 2>/dev/null || true; fi
    echo "✓ เปิดกลับ";;
  *)
    for f in "$STATE/DISABLED" /etc/editortrack/DISABLED "/Library/Application Support/editortrack/DISABLED"; do
      [ -e "$f" ] && echo "DISABLED: $f"
    done
    [ -e "$STATE/TRIPPED" ] && echo "TRIPPED (circuit breaker): $(cat "$STATE/TRIPPED")"
    exit 0;;
esac

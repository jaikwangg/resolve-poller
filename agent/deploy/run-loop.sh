#!/bin/sh
# ลูปเรียก run-agent.sh ทุก N วินาที (default 60) — ใช้กับ LaunchAgent แบบ KeepAlive
# เชื่อถือได้กว่า StartInterval บน macOS (ไม่โดน timer coalescing / App Nap)
# ตั้งช่วงเวลาได้ด้วย EDITORTRACK_INTERVAL_SEC ใน env.sh
AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$AGENT_DIR/deploy/env.sh" ] && . "$AGENT_DIR/deploy/env.sh"
INTERVAL="${EDITORTRACK_INTERVAL_SEC:-60}"
while :; do
  sh "$AGENT_DIR/deploy/run-agent.sh"
  sleep "$INTERVAL"
done

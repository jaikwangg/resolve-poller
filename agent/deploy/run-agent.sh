#!/bin/sh
# รัน agent 1 รอบ (scheduler เรียกทุก ~10 นาที) — source env.sh แล้วรัน runner.py
AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$AGENT_DIR/deploy/env.sh" ] && . "$AGENT_DIR/deploy/env.sh"
cd "$AGENT_DIR"
exec python3 runner.py

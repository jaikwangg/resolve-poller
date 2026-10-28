#!/bin/sh
# สร้าง deploy/env.sh — auto-detect OS (path Resolve ให้เอง) + ถามค่า
# ใช้แบบถาม:   sh deploy/make-env.sh
# ใช้แบบไม่ถาม: STAGE=color SERVER=http://IP:8000/ingest TOKEN=xxx sh deploy/make-env.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$DIR/env.sh"

ask() {  # ask VAR "prompt" "default" [secret] — ข้ามถ้าตั้งผ่าน env มาแล้ว · secret = ไม่แสดงค่า (token)
  cur=$(eval printf '%s' "\${$1:-}")
  if [ -n "$cur" ]; then
    if [ -n "${4:-}" ]; then echo "  $1 = ***(${#cur} ตัว)  (จาก env)"; else echo "  $1 = $cur  (จาก env)"; fi
    return
  fi
  printf "  %s [%s]: " "$2" "$3"
  ans=""
  _noecho=""
  if [ -n "${4:-}" ]; then          # พิมพ์ token ไม่ขึ้นจอ (ไม่มี stty/tty = อ่านตามปกติ)
    { stty -echo </dev/tty; } 2>/dev/null && _noecho=1 || _noecho=""
    # Ctrl-C / error at the prompt must not leave the terminal without echo
    [ -n "$_noecho" ] && trap '{ stty echo </dev/tty; } 2>/dev/null' EXIT INT TERM HUP
  fi
  { read ans </dev/tty; } 2>/dev/null || { ans=""; echo "(ไม่มี tty → default)"; }
  if [ -n "$_noecho" ]; then { stty echo </dev/tty; } 2>/dev/null; echo; _noecho=""; trap - EXIT INT TERM HUP; fi
  eval "$1=\${ans:-$3}"
}

echo "สร้าง env.sh (OS: $(uname)) — Enter = ใช้ค่า default"
ask STAGE "stage (color/conform/subtitle/master/data)" "color"
[ "$STAGE" = "color" ] && DG=1 || DG=0
ask DETECT_GRADE "detect grade (1=color, 0=อื่น)" "$DG"
ask SERVER "server ingest URL" ""
ask TOKEN "token (ต่อเครื่อง — ไม่มีค่า default)" "" secret
# ชื่อที่ token ออกให้ (server: tokens.py host-add <ชื่อ>) — agent ส่งชื่อนี้ ไม่ใช่ชื่อเครื่องจริง (กันชื่อไม่ตรงแล้วเวลาหาย)
ask AGENT_HOST "host (ชื่อที่ token ออกให้)" "$(hostname -s 2>/dev/null || hostname)"
ask VERIFIED "Resolve version ที่ผ่าน phase-3 แล้ว (เช่น 20.2) — ว่าง = agent ไม่สแกน" ""
# zero-incident: ไม่มี token/server default ใน repo อีกต่อไป (token เดิมหลุดใน repo public)
if [ -z "$SERVER" ] || [ -z "$TOKEN" ]; then
  echo "✗ ต้องใส่ SERVER และ TOKEN (ไม่มีค่า default)"; exit 1
fi

case "$(uname)" in
  Darwin)
    API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so" ;;
  *)
    API="/opt/resolve/Developer/Scripting"
    LIB="/opt/resolve/libs/Fusion/fusionscript.so" ;;
esac

umask 077                          # env.sh มี token → อ่านได้เฉพาะเจ้าของ (umask มีผลเฉพาะไฟล์ใหม่)
touch "$OUT" && chmod 600 "$OUT"   # มีอยู่แล้ว (เช่น 644 จาก cp stations/*.sh) → ลดสิทธิ์ก่อนเขียน token
cat > "$OUT" <<EOF
# สร้างโดย make-env.sh ($(date '+%Y-%m-%d %H:%M')) · OS: $(uname) · ⚠️ มี token อย่า commit
export EDITORTRACK_STAGE=$STAGE
export EDITORTRACK_DETECT_GRADE=$DETECT_GRADE
export EDITORTRACK_SERVER=$SERVER
export EDITORTRACK_TOKEN=$TOKEN
export EDITORTRACK_HOST=$AGENT_HOST
export EDITORTRACK_HTTP_TIMEOUT=5
export EDITORTRACK_RESOLVE_VERIFIED=$VERIFIED
# platform pin: Python/OS ที่ตรวจตอนติดตั้ง — อัปเกรดแล้วไม่ตรง → agent ไม่แตะ Resolve จนกว่าจะตรวจใหม่
export EDITORTRACK_PYTHON_VERIFIED=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
export EDITORTRACK_OS_VERIFIED="$(cd "$DIR/.." && python3 -c 'import safety; print(safety.platform_ids()["os"])')"
export RESOLVE_SCRIPT_API="$API"
export RESOLVE_SCRIPT_LIB="$LIB"
export PYTHONPATH="\$PYTHONPATH:\$RESOLVE_SCRIPT_API/Modules/"
EOF

chmod 600 "$OUT"
echo "✓ เขียน $OUT (600)"
echo "  ต่อ:  . deploy/env.sh && python3 runner.py"

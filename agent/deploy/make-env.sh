#!/bin/sh
# สร้าง deploy/env.sh — auto-detect OS (path Resolve ให้เอง) + ถามค่า
# ใช้แบบถาม:   sh deploy/make-env.sh
# ใช้แบบไม่ถาม: STAGE=color SERVER=http://IP:8000/ingest TOKEN=<token ของเครื่องนี้> sh deploy/make-env.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$DIR/env.sh"

ask() {  # ask VAR "prompt" "default" — ข้ามถ้าตั้งผ่าน env มาแล้ว
  cur=$(eval printf '%s' "\${$1:-}")
  if [ -n "$cur" ]; then echo "  $1 = $cur  (จาก env)"; return; fi
  printf "  %s [%s]: " "$2" "$3"
  ans=""
  { read ans </dev/tty; } 2>/dev/null || { ans=""; echo "(ไม่มี tty → default)"; }
  eval "$1=\${ans:-$3}"
}

echo "สร้าง env.sh (OS: $(uname)) — Enter = ใช้ค่า default"
ask STAGE "stage (color/conform/subtitle/master/data)" "color"
[ "$STAGE" = "color" ] && DG=1 || DG=0
ask DETECT_GRADE "detect grade (1=color, 0=อื่น)" "$DG"
ask SERVER "server ingest URL" "http://192.168.140.126:8000/ingest"
# token ต่อเครื่อง — ไม่มี default · ผู้ดูแล server ออกให้:  python manage_tokens.py issue <hostname ของเครื่องนี้>
ask TOKEN "token ของเครื่องนี้ (จาก manage_tokens.py issue $(hostname))" ""
if [ -z "$TOKEN" ]; then
  echo "✗ ต้องมี TOKEN ของเครื่องนี้ — ขอจากผู้ดูแล server (manage_tokens.py issue $(hostname)) แล้วรันใหม่"; exit 1
fi
case "$TOKEN" in
  kpp-et-test|CHANGE_ME)
    echo "✗ token นี้ใช้ไม่ได้ (เป็นค่าตัวอย่าง/เคยหลุดใน repo) — ขอ token ต่อเครื่องจากผู้ดูแล server"; exit 1 ;;
esac

case "$(uname)" in
  Darwin)
    API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so" ;;
  *)
    API="/opt/resolve/Developer/Scripting"
    LIB="/opt/resolve/libs/Fusion/fusionscript.so" ;;
esac

umask 077                       # env.sh มี token → อ่านได้เฉพาะเจ้าของ
cat > "$OUT" <<EOF
# สร้างโดย make-env.sh ($(date '+%Y-%m-%d %H:%M')) · OS: $(uname) · ⚠️ มี token อย่า commit
export EDITORTRACK_STAGE=$STAGE
export EDITORTRACK_DETECT_GRADE=$DETECT_GRADE
export EDITORTRACK_SERVER=$SERVER
export EDITORTRACK_TOKEN=$TOKEN
export EDITORTRACK_HTTP_TIMEOUT=5
export RESOLVE_SCRIPT_API="$API"
export RESOLVE_SCRIPT_LIB="$LIB"
export PYTHONPATH="\$PYTHONPATH:\$RESOLVE_SCRIPT_API/Modules/"
EOF

echo "✓ เขียน $OUT"
echo "  ต่อ:  . deploy/env.sh && python3 runner.py"

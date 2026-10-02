#!/bin/sh
# ============================================================
# editortrack env — STATION: grade-05  (ห้องเกรด / color / Linux)
# ใช้บนเครื่องนี้:
#   1) sudo hostnamectl set-hostname grade-05
#   2) cp deploy/stations/grade-05.sh deploy/env.sh
#   3) แก้ SERVER_IP + CHANGE_ME (TOKEN)  แล้ว  sh deploy/install.sh
# ⚠️ env.sh จริงมี token → .gitignore ไว้ อย่า commit
# ============================================================

# ---- per-station ----
export EDITORTRACK_STAGE=color
export EDITORTRACK_DETECT_GRADE=1

# ---- server (เหมือนกันทุกเครื่อง) ----
export EDITORTRACK_SERVER=http://SERVER_IP:8000/ingest
export EDITORTRACK_TOKEN=CHANGE_ME
export EDITORTRACK_HTTP_TIMEOUT=5

# ---- Resolve scripting (Linux) ----
export RESOLVE_SCRIPT_API="/opt/resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/opt/resolve/libs/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"

# ---- X11 เท่านั้น: ถ้า idle อ่านไม่ได้ ปรับให้ตรง display manager ----
# export DISPLAY=:0
# export XAUTHORITY="$HOME/.Xauthority"      # gdm อาจอยู่ /run/user/$(id -u)/gdm/Xauthority

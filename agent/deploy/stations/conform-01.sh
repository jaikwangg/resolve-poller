#!/bin/sh
# ============================================================
# editortrack env — STATION: conform-01  (ห้อง conform / Mac)
# ใช้บนเครื่องนี้:
#   1) sudo scutil --set HostName conform-01
#   2) cp deploy/stations/conform-01.sh deploy/env.sh
#   3) แก้ SERVER_IP + CHANGE_ME (TOKEN)  แล้ว  sh deploy/install.sh
# ⚠️ env.sh จริงมี token → .gitignore ไว้ อย่า commit
# ============================================================

# ---- per-station ----
export EDITORTRACK_STAGE=conform
export EDITORTRACK_DETECT_GRADE=0          # ห้อง edit/conform ไม่ต้องสแกนสี (poll เบา)

# ---- server (เหมือนกันทุกเครื่อง) ----
export EDITORTRACK_SERVER=http://SERVER_IP:8000/ingest
export EDITORTRACK_TOKEN=CHANGE_ME
export EDITORTRACK_HTTP_TIMEOUT=5

# ---- Resolve scripting (macOS) ----
export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"

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
# ชื่อที่ token ออกให้ (python3 tokens.py host-add conform-01) — agent ส่งชื่อนี้แทนชื่อเครื่องจริง
export EDITORTRACK_HOST=conform-01
export EDITORTRACK_HTTP_TIMEOUT=5

# ---- zero-incident gates (docs/research/WORKSTATION_SAFETY.md) ----
# Resolve version(s) verified on a dummy project on THIS kind of machine (phase 3). Empty = no scan.
export EDITORTRACK_RESOLVE_VERIFIED=
# ExportLUT (grade by LUT) stays off until probe_grade.py --dummy-project passed on this OS/version
export EDITORTRACK_ALLOW_EXPORT_LUT=0
# Python (major.minor) / OS verified on this machine — make-env.sh fills these; mismatch = no Resolve calls
export EDITORTRACK_PYTHON_VERIFIED=
export EDITORTRACK_OS_VERIFIED=

# ---- Resolve scripting (macOS) ----
export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"

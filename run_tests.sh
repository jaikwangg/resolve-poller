#!/bin/sh
# One command for every automated check:   ./run_tests.sh [safety|worstcase|replay|all]   (default all)
#
# Marker convention (shared with the progress worst-case suite): each test module declares
#   MARKERS = {"safety"}            workstation safety (API allowlist, fail-closed, budgets, deploy kit, lease/health)
#   MARKERS = {"worstcase"}         progress-metric worst cases
# Modules without MARKERS (e.g. server/tests/test_replay.py) run under "all" and "replay".
# Each module runs in its own process (agent modules read env at import; keeps RAM low).
# Every future workstation incident gets a regression test here before the fix ships.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
SEL="${1:-all}"
PY_AGENT="${PY_AGENT:-python3}"
PY_SERVER="${PY_SERVER:-$HOME/.editortrack-venv/bin/python}"
[ -x "$PY_SERVER" ] || PY_SERVER=python3
fail=0; ran=0; failed=""

has_marker() { grep -Eq "^MARKERS *= *\{[^}]*\"$2\"" "$1"; }

for side in agent server; do
  [ -d "$ROOT/$side/tests" ] || continue
  PY=$PY_AGENT; [ $side = server ] && PY=$PY_SERVER
  for f in "$ROOT/$side"/tests/test_*.py; do
    [ -f "$f" ] || continue
    mod="$(basename "$f" .py)"
    case "$SEL" in
      all) ;;
      replay) grep -q "^MARKERS" "$f" && continue ;;
      *) has_marker "$f" "$SEL" || continue ;;
    esac
    ran=$((ran + 1))
    printf '%-8s %-32s ' "$side" "$mod"
    out="$(cd "$ROOT/$side" && "$PY" -W ignore -m unittest "tests.$mod" 2>&1)"
    if [ $? -eq 0 ]; then
      echo "ok   $(echo "$out" | grep -E '^Ran ' | head -1)"
    else
      echo "FAIL"; echo "$out" | tail -30; fail=1; failed="$failed $side/$mod"
    fi
  done
done
echo "--- $ran module(s), selection=$SEL"
[ $fail = 0 ] && echo "ALL PASSED" || echo "FAILED:$failed"
exit $fail

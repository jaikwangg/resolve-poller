#!/bin/sh
# One command for every automated check:   ./run_tests.sh [safety|worstcase|security|privacy|testday|replay|research|all]   (default all)
#   all       = every agent/server/testday unittest module + the progress research suite (research/run.sh, fast mode)
#   research  = only research/run.sh (pytest "not slow" + sim.run_all summary); needs pytest + hypothesis:
#               PY_RESEARCH (default ./.venv-research/bin/python, else python3)
# RAM is tight: run the whole command under ONE outer lock, e.g.  flock /path/test.lock ./run_tests.sh all
# (research/run.sh is called with LOCK=none so it does not take a second lock and deadlock under the outer one).
#
# Marker convention (shared with the progress worst-case suite): each test module declares
#   MARKERS = {"safety"}            workstation safety (API allowlist, fail-closed, budgets, deploy kit, lease/health)
#   MARKERS = {"worstcase"}         progress-metric worst cases
#   MARKERS = {"security"}          server auth (token map, read auth), retention, server-side minimisation
#   MARKERS = {"privacy"}           record fields == docs/DATA_SHEET.md, minimisation defaults
#   MARKERS = {"testday"}           phase-3 test-day kit (testday/: generator, screen diff, measurements, drill)
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

for side in agent server testday; do
  [ "$SEL" = research ] && break
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
if [ "$SEL" = all ] || [ "$SEL" = research ]; then
  PY_RESEARCH="${PY_RESEARCH:-$ROOT/.venv-research/bin/python}"
  [ -x "$PY_RESEARCH" ] || PY_RESEARCH=python3
  ran=$((ran + 1))
  echo "research research/run.sh (fast)"
  if PY="$PY_RESEARCH" LOCK="${RESEARCH_LOCK:-none}" sh "$ROOT/research/run.sh"; then
    echo "research research/run.sh               ok"
  else
    echo "research research/run.sh               FAIL"; fail=1; failed="$failed research/run.sh"
  fi
fi
echo "--- $ran unit(s), selection=$SEL"
[ $fail = 0 ] && echo "ALL PASSED" || echo "FAILED:$failed"
exit $fail

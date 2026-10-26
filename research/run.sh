#!/bin/sh
# One command for the whole progress test system (CI and scheduled run).
#   research/run.sh            fast: pytest (not slow) + run_all summary with a small fuzz budget
#   research/run.sh --full     scheduled: + slow tests, bigger hypothesis/fuzz budgets, saves fuzzer finds
# Env: PY (python with pytest+hypothesis; default ../.venv-research/bin/python or python3)
#      LOCK (flock file; RAM is tight - one test process at a time), DATA (extra real-data dirs)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
PY=${PY:-$ROOT/.venv-research/bin/python}
[ -x "$PY" ] || PY=python3
LOCK=${LOCK:-${TMPDIR:-/tmp}/editortrack-research.lock}
OUT=${OUT:-$HERE/out/summary-$(date +%Y%m%d-%H%M).md}
cd "$HERE"
if [ "${1:-}" = "--full" ]; then
  HYPOTHESIS_EXAMPLES=${HYPOTHESIS_EXAMPLES:-400} FUZZ_N=${FUZZ_N:-1000} \
    flock "$LOCK" "$PY" -m pytest
  flock "$LOCK" "$PY" -m sim.run_all --fuzz "${FUZZ_N:-1000}" --save-found --out "$OUT" ${DATA:+--data $DATA}
else
  flock "$LOCK" "$PY" -m pytest -m "not slow"
  flock "$LOCK" "$PY" -m sim.run_all --fuzz "${FUZZ_N:-150}" --out "$OUT" ${DATA:+--data $DATA}
fi

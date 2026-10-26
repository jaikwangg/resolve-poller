# The progress test system: incident -> scenario -> failing test -> fix -> stays in the suite

The worst-case study is not a one-off. `research/sim/` is a standing test system for "is the
progress figure we send to PMS honest?". It keeps three kinds of evidence and runs them together:

| evidence | where | finds |
|---|---|---|
| scenario library | `research/sim/scenarios/library/*.json` | every known worst case, each with the PMS output it should produce |
| generative tests | `research/sim/fuzz.py`, `research/tests/test_generative.py` (hypothesis) | new worst cases: random edit histories that break an invariant, shrunk to a minimal scenario |
| real-data replay | `research/sim/replay.py` over `server/data*` and fixtures | anomalies in what was actually recorded: premature 100, drops, jumps |

## The invariants (what "honest" means)

Checked on every scenario and every generated history for the recommended strategy
(`strategies.RECOMMENDED`, now C3), and reported for every other strategy:

| id | rule |
|---|---|
| INV1 | 0 <= pct <= 100 with at most 2 decimals (PMS stores NUMERIC(5,2)) |
| INV2 | never done or 100% unless the scope is confirmed and the value is not provisional |
| INV3 | a drop of more than 1 point always carries a reason code (scope growth is labelled, never silent) |
| INV4 | `work_done` never decreases |
| INV5 | provisional implies not done |
| INV6 | a poll whose only change is added clips never carries `regression` (growth is not regression) |
| INV7 | done implies the work is really done, for histories made only of observable edits |
| (determinism) | the same input gives the same output, for every strategy |

## The loop

1. **Incident or field observation.** Someone sees a wrong figure in PMS or the dashboard, or a
   colorist describes a workflow. Find the records in `server/data/<day>.jsonl`; run
   `python3 -m sim.replay <dir>` to see what each strategy would have sent.
2. **Scenario.** Write it as `scenarios/library/WCnn_name.json` (how: `research/sim/README.md`).
   For a real incident use the `observations` kind with the sanitised records (template: WC01);
   otherwise tell it as `steps`. Write the expectation for the PMS output you want
   (`{"C3": {"false_completion": false}}`) and, if it is a failure of today's code, lock that too
   (`{"S0": {"false_completion": true}}`).
3. **Failing test.** `pytest` picks the file up automatically (`tests/test_library.py` is
   parametrised over the library). It fails until the strategy handles the case.
4. **Fix.** Change the strategy (or the poller field it needs). Re-run everything: the fix must not
   break any other scenario, any invariant, or the fuzzer.
5. **Stays in the suite.** The scenario is now a regression test. If the case cannot be fixed (e.g.
   needs a decision or data that does not exist), set `status: "known_limitation"` with a
   `known_limits` note; it is still run and its documented behaviour is still asserted.
6. **Generated finds.** The scheduled run saves fuzzer failures to `scenarios/found/FZ_*.json` with
   `status: "new"`. They are reported but not asserted until someone triages them: give it a
   `WC` id and a story and move it to `library/` (step 2), or delete it if it is a harness bug (then
   fix the harness).

## Commands

From `research/` (on this host always through `flock`, one test process at a time):

```sh
./run.sh                 # CI: pytest -m "not slow" + run_all (fuzz 150, replay) -> research/out/summary-*.md
./run.sh --full          # scheduled: all tests incl. slow, hypothesis 400 examples, fuzz 1000, saves finds
PY=python3 FUZZ_N=50 ./run.sh                     # without the venv; hypothesis tests are skipped
LOCK=/path/to/lock DATA="/srv/editortrack/data" ./run.sh --full   # replay another data dir too
python3 -m sim.run_all --fuzz 300 --seed 12       # same summary, chosen seed
python3 -m sim.docs [--check]                     # refresh / check the research documents
```

`pytest -m sim` runs the fast suite only (every test not marked `slow`). `run_all` exits 1 when an
expectation fails, a scenario file is invalid, the recommended strategy breaks an invariant, or
the fuzzer finds something new, so a CI job or a cron entry can alert on the exit code. The fuzz
seed defaults to the day of the year, so the scheduled run explores a different stream each day
and any find is reproducible from the seed printed in the summary.

Suggested cron (daily, after the night's records are written; not installed):

```
30 6 * * *  cd ~/github/editortrack/research && LOCK=/tmp/editortrack-research.lock ./run.sh --full >> out/cron.log 2>&1
```

## When to re-run (and what to look at)

| change | run | look at |
|---|---|---|
| progress strategy (server or agent) | `./run.sh --full` | expectation failures, invariants, fuzz finds; then `python3 -m sim.docs` and read the diff of STRATEGY_COMPARISON.md |
| Resolve version / poller change (`resolve_poller.py`, `grade_detect.py`) | `./run.sh --full` with `DATA=` pointing at fresh records from a real room | replay anomalies; `test_s0_reproduces_the_recorded_2026_10_14_values` and `test_s0_matches_agent_scope_compute_progress` tie S0 to the real code; add a scenario for any new behaviour |
| PMS contract (`~/github/pms/docs/integrations/editortrack.md`) | `./run.sh` | `test_every_output_has_the_pms_fields`; update the output dict in `strategies._out` and INV1 if field rules change |
| a new real incident | the loop above | the new scenario fails first, then passes |

## Keeping it cheap

Everything is stdlib Python and runs in about a second (44 scenarios x 12 strategies, 150 fuzz
histories, the 263-record replay). No network, no Resolve, no PMS: real data is read from JSONL
files only. hypothesis runs without its example database (`database=None`), so it writes nothing.

# research/sim - progress-strategy harness

Stdlib-only Python. Tests need `pytest` (and `hypothesis` for the property tests).
Run from `research/`; on this host wrap every run in `flock` (RAM is tight, one process at a time).

| file | what |
|---|---|
| `engine.py` | expands a scenario into the observation stream the server would receive, with ground truth |
| `strategies.py` | the candidate strategies (S0..S6, C1..C4, Q1); `RECOMMENDED = "C3"` |
| `metrics.py` | scoring (false completion, false regression, error, time to correct ...) and invariants |
| `library.py` | loads/validates scenarios, evaluates them, checks `expect` blocks |
| `fuzz.py` | random edit histories, invariant check, shrinking, saves finds to `scenarios/found/` |
| `replay.py` | replays real JSONL records (server/data*, fixtures) and flags anomalies |
| `run_all.py` | one command: everything above plus a short summary |
| `docs.py` | regenerates docs/research/WORST_CASES.md and the tables in STRATEGY_COMPARISON.md |
| `scenarios/library/` | hand-written and real-incident scenarios (asserted in CI) |
| `scenarios/found/` | fuzzer finds (status `new` until triaged) |

```sh
cd research
python3 -m sim.run_all --fuzz 150              # summary; exit 1 on any failure
python3 -m sim.fuzz --n 500 --seed 7 --save    # hunt for new worst cases
python3 -m sim.replay ../server/data           # anomalies in real data
python3 -m sim.docs                            # refresh the research documents
../.venv-research/bin/python -m pytest -m "not slow"
```

## How to add a scenario

Every new incident, field observation or "what if" becomes one JSON file in
`scenarios/library/`. File name = `id` + `.json`. Use the next free `WCnn_` number.

### 1. Pick the kind

- **steps** - you tell the story as edits; the engine computes the counts and the truth. Use this for
  anything you can describe.
- **observations** - you paste what the server actually received (one object per record) and write the
  truth yourself. Use this for a real incident from `server/data/*.jsonl`; `WC01` is the template
  (built from the sanitised fixture; strip user names, IPs and client project names first).

### 2. Required fields

```json
{
 "id": "WC44_short_name",
 "title": "One line: what happens",
 "category": "scope_growth | scope_shrink | replacement | regrade | rework | look_all | versions | multi_room | conform | clip_kinds | identity | outage | attribution | stage_overlap | detector | ...",
 "severity": ["false_completion", "false_regression", "wrong_job", "lost_data", "never_completes", "false_under_report", "flapping", "stale_value"],
 "status": "active",
 "stage": "color",
 "job": "KPP-2026-0101",
 "project_name": "BrandX TVC",
 "story": "Who does what in Resolve / the facility.",
 "signals": "What the poller sees: clip counts, graded counts, durations, timelines ...",
 "steps": [ ... ],
 "truth": {"KPP-2026-0101": {"color": ["R1_010", "R1_020"]}},
 "expect": {"S0": {"false_completion": true}, "C3": {"false_completion": false}}
}
```

Optional: `hosts` (`{"grade-01": {"stage": "color", "booking": {...}}}`; default one host
`grade-01` booked on `job`), `booking` (default booking for all hosts; `null` = unbound),
`identity` (`name` default, `marker`, `none`), `resolve_unverified`, `known_limits`
(`{"C3": "why it is accepted"}`), `truth_note`.

`truth` is the hindsight scope: the shots of the final deliverable for each job and stage. A shot
counts as done when an op marks it done (see below).

### 3. Steps and ops

A step is `{"t": minutes, "host": "grade-01", "ops": [[op, args], ...], "poll": true}`. After the
ops, the host polls once (`"poll": false` = no record, e.g. Mac asleep; `"poll": "all"` = every host;
a list = those hosts).

| op | args | effect |
|---|---|---|
| `open` | `project, timeline` | host opens that project/timeline (created if new) |
| `add` | `shots, kind=video, track=1, dur=4, graded=false, done=false, version=1, online=true, ident, sid, project, timeline` | clips appended. kind: video, vfx, placeholder, title, generator, adjustment, compound, still, disabled, audio (track 101) |
| `grade` | `shots, true=true, visible=true` | clips graded (new grade signature); truth done unless `true:false`; `visible:false` = truth only, detector blind |
| `ungrade` / `crash_revert` | `shots` | grade removed (truth too) |
| `notes` | `shots` | client notes: truth not done, signal unchanged |
| `lut_all` | `level=clip` | every clip reads graded, truth unchanged |
| `replace` | `shot, version, keep_grade=true, new_item=false, kind, truth={"color": false}` | new media version |
| `blade` | `shot` | splits a clip in two |
| `remove` | `shots` | cut from the timeline (ripple) |
| `copy_timeline` | `from, to, from_project, to_project, open=true` | duplicate / Save As / restore in another room |
| `new_timeline` | `name, project, open` | empty timeline (offline refs are found by the poller's regex) |
| `conform` | `shots` | media online, placeholder becomes vfx, conform truth done |
| `booking` | booking dict or `null` | what the server's booking lookup returns for this host |
| `confirm_scope` | `stage, source, shots` or `total` | an external confirmed scope appears |
| `pms_task` | `status` | PMS task status (TODO, IN_PROGRESS, REVISION, DONE) |
| `skew` | `sec` | host clock offset |
| `down` / `up` | - | Resolve not running |
| `truth_scope`, `truth_done`, `timeline_job`, `set_stage` | see engine.py | adjust ground truth, map a timeline to another job |

### 4. Expectations

`expect` maps a strategy name to metric checks: `"metric": value` (exact), `"max_metric": n`,
`"min_metric": n`, or `"invariants": ["INV2_no_unconfirmed_done", ...]` (must not be violated).
Metric names are in `metrics.METRICS`. Write what PMS *should* get: usually
`{"C3": {"false_completion": false}}`, plus a lock on today's behaviour if the scenario is about a
known failure (`{"S0": {"false_completion": true}}`) so a later change to today's code is noticed.
The recommended strategy must never break an invariant on an asserted scenario.

### 5. Run

```sh
cd research && python3 -m sim.run_all --fuzz 0 && python3 -m sim.docs
```

If the recommended strategy fails, either fix it (the scenario stays as a regression test) or set
`status: "known_limitation"` and explain in `known_limits`. Commit the JSON and the regenerated docs.

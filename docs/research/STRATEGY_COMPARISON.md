# How editortrack should compute progress: strategy comparison

Research branch `research/revision-worst-cases`, 2026-10-26. Evidence comes from the scenario
library in `research/sim/scenarios/library/` (catalog: [WORST_CASES.md](WORST_CASES.md)) and the
harness in `research/sim/`. The tables between `GENERATED` markers are rebuilt by
`python -m sim.docs` (run from `research/`). Everything else in this file is hand-written.

## The question

Today `color_pct = graded clips / clips on the open timeline` and `conform_pct = timeline duration
/ offline timeline duration`. PMS receives only `progress_pct`. The owner confirmed a real false
"complete": 2026-10-14, job TEST, conform-01, 16.7 -> **100** -> 83.3 -> 75, because clips were
added after the 100% was sent (scenario `WC01`, an exact replay of the stored records). Clips get
added late in real jobs, so this is a structural problem, not a one-off.

## Method

1. 44 worst cases (43 authored from the code, the docs and facility practice, plus the exact replay
   of the 2026-10-14 incident) are written as executable scenarios. Each expands into the
   observation stream the server would receive (the counts the poller sends today plus per-item
   fields a richer poller could send) and a ground truth per poll (hindsight: the share of the final
   deliverable's shots that are really finished).
2. 12 strategies (below) turn the same stream into what would be sent to PMS.
3. Every scenario x strategy is scored: false completion, false regression (with/without a reason
   code), max error, time to correct, never-done, wrong job, nothing sent, what PMS displays (it
   shows the latest `observed_at`), and invariant violations.
4. A fuzzer and hypothesis tests compose thousands of random edit histories and check the
   invariants; failures shrink to minimal scenarios (see [TESTING_SYSTEM.md](TESTING_SYSTEM.md)).
   During this study the fuzzer found real defects in the recommended strategy (multi-room "latest
   view wins", blade halves changing the grade signature) and two harness bugs, all fixed.

## Results

<!-- BEGIN GENERATED:aggregate -->
Over 44 library scenarios. FC = scenarios with a false completion (done or 100% while not done); FR = scenarios with a false regression without reason code (with reason code in brackets); missed = truth done at the end but never claimed; err = max |reported - truth| per scenario (median / worst); uncorr = error episodes still open at the end; ttc = longest wrong episode (min); inv = invariant violations.

| strategy | FC | FC polls | FR (expl.) | missed | wrong job | silent | err med/worst | uncorr | ttc | PMS stale | inv |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S0 | 20 | 43 | 17 (0) | 4 | 3 | 2 | 25.8/100.0 | 4 | 1320 | 7 | 173 |
| S1 | 18 | 40 | 12 (5) | 12 | 3 | 2 | 25.8/100.0 | 12 | 1320 | 3 | 149 |
| S2 | 3 | 9 | 15 (0) | 35 | 3 | 2 | 25.3/100.0 | 4 | 1320 | 1 | 21 |
| S3 | 14 | 33 | 1 (9) | 4 | 3 | 1 | 14.3/100.0 | 4 | 1320 | 2 | 146 |
| S4 | 13 | 28 | 2 (0) | 8 | 3 | 2 | 24.9/100.0 | 4 | 1380 | 0 | 87 |
| S5 | 20 | 43 | 16 (0) | 4 | 3 | 2 | 25.8/100.0 | 4 | 1320 | 1 | 171 |
| S6 | 3 | 3 | 0 (0) | 37 | 3 | 1 | 68.8/100.0 | 38 | 4380 | 2 | 20 |
| C1 | 12 | 26 | 4 (0) | 14 | 3 | 2 | 24.9/100.0 | 9 | 1380 | 0 | 79 |
| C2 | 11 | 24 | 4 (0) | 7 | 3 | 1 | 20.0/100.0 | 4 | 1380 | 2 | 89 |
| C3 | 0 | 0 | 0 (7) | 35 | 0 | 3 | 1.0/90.0 | 5 | 1320 | 2 | 0 |
| C4 | 1 | 1 | 0 (6) | 33 | 0 | 3 | 1.0/90.0 | 5 | 1320 | 2 | 0 |
| Q1 | 0 | 0 | 0 (14) | 38 | 0 | 4 | 22.6/99.0 | 4 | 1320 | 1 | 0 |
| SV | 0 | 0 | 0 (14) | 38 | 0 | 4 | 22.6/99.0 | 4 | 1320 | 1 | 0 |
| SVL | 0 | 0 | 0 (13) | 38 | 0 | 4 | 22.6/99.0 | 4 | 1320 | 1 | 0 |
<!-- END GENERATED:aggregate -->

What the numbers say:

- **Today (S0) is wrong in about half the cases.** It shows 100% before the work is done in 20 of
  44 scenarios and shows unexplained drops in 17. Every way the scope moves produces one or the
  other: late VFX, extra reels or projects, duplicated timelines, a second room, TV/cinema
  versions, color starting before conform finishes, a look applied to every clip, Replace Clip, an
  XML import at full length, client notes after "done".
- **No single in-room signal fixes false completion.** High-water mark (S1) still has 18 and adds
  "never completes" when shots are cut (12). Hysteresis (S4) gets to 13 by waiting, but a wrong
  100% that stays put for 30 minutes still goes out. A per-shot ledger (S3) fixes multi-room,
  duplicates and Replace Clip and labels its drops, yet still has 14: a machine in the grade room
  cannot see shots that have not arrived (WC02, WC04, WC33, WC35, WC36) or client notes (WC10,
  WC11). False completion is a **scope-knowledge** problem.
- **Only "never claim done without a confirmed scope" removes it.** C3 has 0 false completions,
  0 unexplained regressions, 0 wrong-job values and 0 invariant violations across the library and
  the fuzzer. The price: with no confirmed scope it parks at 99 (provisional) and never says done;
  35 of 44 scenarios end "missed", by design. Q1 (the quick fix) has the same safety with no
  poller or PMS change, but keeps today's large errors (median max error 22.6 vs 1.0 for C3).
- **A confirmed scope source is what turns "safe" into "useful".** With an authoritative shot list
  from the first poll (table below), C3 reaches done correctly in all but 7 scenarios and never
  falsely; the 7 are identity limits (no readable clip names, compound clips, a shared source clip,
  renamed markers), a one-click copy-grade, graphics stills, and grades the LUT detector cannot see.
- C4 (trust the offline timeline as confirmed scope, no human input) has 1 false completion: the
  stale offline v1 in WC20 (conform before picture lock). Automatic scope is only as good as the
  timeline naming discipline.
- Booked time (S6) is not progress: it is wrong almost everywhere (median max error 69).

### With a confirmed shot list

<!-- BEGIN GENERATED:confirmed -->
Same library, but every scenario gets an authoritative shot list for its main job at the first poll (upper bound of what a confirmed scope source buys).

| strategy | FC | FR (expl.) | missed | err med/worst | uncorr |
|---|---|---|---|---|---|
| S0 | 20 | 17 (0) | 4 | 25.8/100.0 | 4 |
| S2 | 3 | 1 (1) | 5 | 0.0/100.0 | 5 |
| C3 | 0 | 0 (1) | 7 | 1.0/100.0 | 6 |
| C4 | 0 | 0 (1) | 7 | 1.0/100.0 | 6 |
| SVL | 0 | 0 (9) | 38 | 17.3/99.0 | 10 |

C3 false completions with a confirmed list: none.
C3 still never done with a confirmed list: WC13_copy_grade_to_scene, WC14_invisible_grade_group_or_nr, WC15b_duplicated_timeline_no_names, WC24_compound_nesting, WC26_stills_graphics, WC27_reused_source_and_blade, WC28_marker_codes_missing_dup_renamed.
SVL (shipped server path, D1 = lock, counts only) false completions with a confirmed list: none.
<!-- END GENERATED:confirmed -->

### Strategies, the data they need, and whether that data exists today

Checked on 2026-10-26 against `agent/resolve_poller.py`, `agent/grade_detect.py`, `server/*.py` and
`~/github/pms/kpp-pms-api` (read only). PMS has no shot count, shot list or runtime field
(`DeliverableSpec` is name/format/quantity/notes; bookings carry `duration_hours`, `task_id`,
`activity`). PMS does have a human completion rule: task DONE plus a QC PASS in the current round
(`services/production/task_progress.py`), and a REVISION status that reopens a task.

<!-- BEGIN GENERATED:strategies -->
| strategy | what it does | data it depends on | exists today? | human input | cost |
|---|---|---|---|---|---|
| S0 | today: graded / current clips (conform: duration / offline) | clip counts, graded count, duration, offline timeline | yes (poller today) | none | 0 (exists) |
| S1 | high-water-mark denominator (max clips / max offline target ever seen) | same as S0 + server state | yes | none | S (server only) |
| S2 | confirmed external scope; provisional (capped 99) until confirmed | confirmed scope per job (shot list or count / runtime) | NO: PMS has no shot/runtime field; deliverables = name/format/quantity only | someone records the scope (shot count/list or runtime) per job | M (PMS field or shot-list import + server) |
| S3 | per-shot identity ledger across rooms/timelines (rework/replace aware) | per-item identity (clip name / unique id), kind, grade signature, media version | partly: poller reads uid + node/LUT signature in grade_detect but does not send them; names only with EXTRACT_CLIP_META (off) | none | M-L (poller sends per-item identity + server ledger) |
| S4 | S0 + hysteresis: 100% only after stable 30 min, drops debounced 3 polls | same as S0 + server state | yes | none | S (server only) |
| S5 | S0 current % plus a separate monotonic work-done counter | graded count deltas (exact: per-item signatures) | yes (approx) / partly (exact) | none | S-M (server; exact with item sigs) |
| S6 | straw man: booked time elapsed / booking length | booking start/end | yes (PMS schedule) | none | S |
| C1 | S1 + S4 (high-water mark with hysteresis) | as S1 | yes | none | S (server only) |
| C2 | S3 + S4 (shot ledger with hysteresis) | as S3 | partly | none | M-L |
| C3 | S2+S3+S4+S5 + job guard: confirmed scope, shot ledger, stable done, work counter | as S3 + confirmed scope + booking name + grade.pending + PMS task status | partly: grade.pending yes; items need poller change; scope needs PMS field or EDL import; task status not in /schedule | scope confirmation per job (shot list/count) - or owner accepts the offline timeline as scope (C4) | L (poller items + server ledger + PMS fields) |
| C4 | C3, but the offline/editorial timeline's shot list counts as confirmed scope (no human input) | as S3 + offline timeline item list | partly: offline timeline is read today for duration only | none (trusts the offline timeline found by name) | L (poller items + server ledger + PMS fields) |
| Q1 | quick fix: S0 capped at 99 + holds (pending, reference timeline, job mismatch, late) + count reasons | same as S0 + grade.pending (sent today) + booking project_no/name | yes | none | S (server only, this week) |
| SV | shipped server/progress_v2.py, default decisions (D1 none: provisional, cap 99, holds) | same as Q1 (counts + grade.pending + booking project_no/name) | yes (shipped on night/progress-v2) | none | shipped (night/progress-v2) |
| SVL | shipped server/progress_v2.py with D1 = lock (a human-set shot count confirms the scope) | as SV + a human scope lock (shot count) per project_no/stage | yes (scope_locks.json; off by default) | someone locks the shot count per job and stage (scope_locks.json) | shipped (night/progress-v2) |
<!-- END GENERATED:strategies -->

### Every scenario x strategy

Cell: F false completion, R unexplained false regression, r labelled drop, M never claims done
though finished, W value lands on the wrong project, L nothing sent; number = max error (points).

<!-- BEGIN GENERATED:matrix -->
| scenario | S0 | S1 | S2 | S3 | S4 | S5 | S6 | C1 | C2 | C3 | C4 | Q1 | SV | SVL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WC01_replay_2026_10_14_TEST | FR 25 | Fr 25 | R 24 | Fr 25 | R 36 | FR 25 | ok 53 | R 36 | R 36 | r 24 | r 24 | r 24 | r 24 | r 24 |
| WC02_vfx_late_no_placeholder | FR 17 | Fr 17 | RM 16 | Fr 17 | F 17 | FR 17 | F 58 | F 17 | F 17 | rM 16 | rM 16 | rM 16 | rM 16 | rM 16 |
| WC02b_vfx_late_confirmed_list | FR 17 | Fr 17 | ok 0 | Fr 17 | F 17 | FR 17 | F 58 | F 17 | F 17 | ok 1 | ok 1 | rM 16 | rM 16 | M 1 |
| WC03_vfx_placeholder_slug | ok 0 | ok 0 | M 1 | ok 0 | ok 1 | ok 0 | F 71 | ok 1 | ok 1 | M 1 | M 1 | M 1 | M 1 | M 1 |
| WC04_reels_as_timelines | FR 56 | FRM 56 | RM 56 | Fr 44 | ok 43 | FR 56 | M 43 | M 43 | R 43 | rM 43 | rM 43 | rM 56 | rM 56 | rM 56 |
| WC05_shots_cut_ungraded | ok 13 | M 17 | M 13 | ok 13 | ok 13 | ok 13 | M 68 | M 17 | ok 13 | M 13 | M 13 | M 13 | M 13 | M 13 |
| WC06_shots_cut_graded | R 3 | RM 17 | RM 3 | r 3 | ok 3 | R 3 | M 68 | M 17 | ok 3 | rM 3 | rM 3 | rM 3 | rM 3 | rM 3 |
| WC07_vfx_new_version_grade_kept | F 10 | F 10 | M 9 | ok 0 | F 10 | F 10 | M 88 | F 10 | FR 10 | M 1 | M 1 | M 9 | M 9 | M 9 |
| WC08_reconform_new_timeline | ok 70 | ok 70 | M 70 | ok 0 | F 30 | ok 70 | M 88 | F 30 | F 30 | M 1 | M 1 | M 70 | M 70 | M 70 |
| WC09_node_reset_start_over | ok 0 | ok 0 | M 1 | ok 0 | F 30 | ok 0 | M 88 | F 30 | F 30 | M 1 | M 1 | M 1 | M 1 | M 1 |
| WC10_client_notes_rework | F 40 | F 40 | F 40 | F 40 | F 40 | F 40 | M 88 | F 40 | F 40 | ok 39 | ok 39 | M 39 | M 39 | M 39 |
| WC11_revision_days_after_delivery | F 20 | F 20 | F 20 | F 20 | F 20 | F 20 | M 88 | F 20 | F 20 | ok 19 | ok 19 | M 19 | M 19 | M 19 |
| WC12_look_lut_on_all_clips | F 100 | F 100 | F 100 | F 100 | F 100 | F 100 | M 50 | F 100 | F 100 | ok 1 | ok 1 | M 99 | M 99 | M 99 |
| WC13_copy_grade_to_scene | ok 0 | ok 0 | ok 0 | ok 0 | ok 1 | ok 0 | M 94 | ok 1 | ok 1 | M 90 | M 90 | M 1 | M 1 | M 1 |
| WC14_invisible_grade_group_or_nr | M 80 | M 80 | M 80 | M 80 | M 80 | M 80 | M 75 | M 80 | M 80 | M 80 | M 80 | M 80 | M 80 | M 80 |
| WC15_duplicated_timeline_v2 | R 40 | R 40 | RM 40 | ok 0 | ok 1 | R 40 | M 69 | ok 1 | ok 1 | M 1 | M 1 | rM 40 | rM 40 | rM 40 |
| WC15b_duplicated_timeline_no_names | ok 0 | ok 0 | M 1 | M 20 | ok 1 | ok 0 | M 69 | ok 1 | M 20 | M 20 | M 20 | M 1 | M 1 | M 1 |
| WC16_project_saved_as_final | R 30 | R 30 | RM 30 | ok 0 | ok 1 | R 30 | M 69 | ok 1 | ok 1 | M 1 | M 1 | rM 30 | rM 30 | rM 30 |
| WC17_offline_ref_and_trailer_timelines | FR 17 | RM 50 | RM 16 | rM 25 | F 17 | FR 17 | M 58 | RM 50 | M 25 | rM 25 | M 29 | rM 16 | rM 16 | rM 16 |
| WC18_two_rooms_one_job | FR 44 | FRM 44 | RM 43 | ok 0 | M 43 | FR 44 | M 65 | M 43 | M 1 | M 1 | M 1 | rM 43 | rM 43 | rM 43 |
| WC19_handover_between_rooms | R 40 | R 40 | RM 40 | ok 0 | M 1 | R 40 | M 62 | M 1 | ok 1 | M 1 | M 1 | rM 40 | rM 40 | rM 40 |
| WC20_offline_changes_after_conform | F 17 | F 17 | M 16 | F 58 | F 17 | F 17 | M 58 | F 17 | F 57 | M 16 | FM 17 | M 16 | M 16 | M 16 |
| WC21_xml_import_instant_full_duration | F 100 | F 100 | M 99 | ok 0 | F 99 | F 100 | M 62 | F 99 | ok 1 | M 1 | ok 1 | M 99 | M 99 | M 99 |
| WC22_offline_timeline_misnamed | L 0 | L 0 | L 0 | F 50 | L 0 | L 0 | M 75 | L 0 | ok 49 | M 49 | M 49 | L 0 | L 0 | L 0 |
| WC22b_wrong_timeline_matches_regex | F 50 | F 50 | M 49 | F 50 | ok 49 | F 50 | M 75 | ok 49 | ok 49 | M 49 | M 49 | M 49 | M 49 | M 49 |
| WC23_slate_and_audio_inflate_duration | F 100 | F 100 | M 99 | F 50 | ok 99 | F 100 | M 75 | ok 99 | ok 49 | M 1 | ok 1 | M 99 | M 99 | M 99 |
| WC24_compound_nesting | ok 41 | M 45 | M 41 | ok 41 | ok 41 | ok 41 | M 62 | M 45 | ok 41 | M 41 | M 41 | M 41 | M 41 | M 41 |
| WC25_titles_generators_adjustment | M 27 | M 27 | M 27 | ok 0 | M 27 | M 27 | M 75 | M 27 | ok 1 | M 1 | M 1 | M 27 | M 27 | M 27 |
| WC26_stills_graphics | M 23 | M 23 | M 23 | M 23 | M 23 | M 23 | M 88 | M 23 | M 23 | M 23 | M 23 | M 23 | M 23 | M 23 |
| WC26b_disabled_alt_takes | M 29 | M 29 | M 29 | ok 0 | M 29 | M 29 | M 88 | M 29 | ok 1 | M 1 | M 1 | M 29 | M 29 | M 29 |
| WC27_reused_source_and_blade | ok 0 | ok 0 | M 1 | ok 0 | ok 1 | ok 0 | M 88 | ok 1 | ok 1 | M 1 | M 1 | M 1 | M 1 | M 1 |
| WC28_marker_codes_missing_dup_renamed | ok 0 | ok 0 | M 1 | ok 4 | ok 1 | ok 0 | M 62 | ok 1 | ok 4 | M 4 | M 4 | M 1 | M 1 | M 1 |
| WC29_crash_and_sleep_gaps | ok 0 | ok 0 | M 1 | ok 0 | ok 30 | ok 0 | M 48 | ok 30 | ok 30 | M 1 | M 1 | M 1 | M 1 | M 1 |
| WC30_clock_skew_two_rooms | FR 44 | FRM 44 | RM 43 | ok 0 | M 43 | FR 44 | M 87 | M 43 | M 1 | M 1 | M 1 | rM 43 | rM 43 | rM 43 |
| WC31_offline_queue_backlog | R 13 | ok 0 | M 1 | ok 0 | ok 1 | ok 0 | M 89 | ok 1 | ok 1 | M 1 | M 1 | M 1 | M 1 | M 1 |
| WC32_two_jobs_one_project | FW 50 | RW 10 | MW 49 | W 14 | W 49 | FW 50 | MW 62 | W 1 | W 14 | L 0 | L 0 | L 0 | L 0 | L 0 |
| WC32b_ambiguous_bookings_unbound | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 | L 0 |
| WC33_one_job_many_projects | FR 67 | FR 67 | RM 67 | Fr 67 | ok 66 | FR 67 | M 38 | ok 66 | ok 66 | rM 66 | rM 66 | rM 67 | rM 67 | rM 67 |
| WC34_room_swap_booking_mismatch | W 0 | W 0 | MW 0 | W 0 | W 0 | W 0 | MW 0 | W 0 | W 0 | L 0 | L 0 | L 0 | L 0 | L 0 |
| WC34b_overtime_outside_booking | ok 0 | ok 0 | ok 0 | ok 0 | ok 0 | ok 0 | ok 35 | ok 0 | ok 0 | ok 0 | ok 0 | ok 0 | ok 0 | ok 0 |
| WC35_tv_vs_cinema_versions | FR 23 | Fr 23 | RM 22 | Fr 23 | F 23 | FR 23 | M 64 | F 23 | F 23 | rM 22 | rM 22 | rM 22 | rM 22 | rM 22 |
| WC36_color_starts_before_conform_done | FR 44 | Fr 44 | RM 43 | Fr 44 | F 44 | FR 44 | M 62 | F 44 | F 44 | M 1 | M 1 | rM 43 | rM 43 | rM 43 |
| WC37_detector_warmup_reopen | R 100 | R 100 | RM 100 | R 100 | RM 73 | R 100 | ok 100 | RM 73 | RM 73 | M 1 | M 1 | M 1 | M 1 | M 1 |
| WC43_other_project_opened_briefly | FRW 70 | FRMW 82 | RMW 69 | W 59 | W 69 | FRW 70 | MW 71 | RMW 82 | W 59 | M 1 | M 1 | M 1 | M 1 | M 1 |
<!-- END GENERATED:matrix -->

### Replay of the stored real records (fixture)

<!-- BEGIN GENERATED:replay -->
Replay of 263 real records from server/tests/fixtures/replay

| strategy | premature_100 | drop | unexplained_drop | jump | switch | sent |
|---|---|---|---|---|---|---|
| S0 | 1 | 2 | 2 | 1 | 0 | 236 |
| S1 | 1 | 2 | 1 | 1 | 0 | 236 |
| S2 | 0 | 2 | 2 | 1 | 0 | 236 |
| S3 | 1 | 2 | 1 | 1 | 0 | 236 |
| S4 | 0 | 1 | 1 | 1 | 0 | 236 |
| S5 | 1 | 2 | 2 | 1 | 0 | 236 |
| S6 | 0 | 0 | 0 | 0 | 0 | 0 |
| C1 | 0 | 1 | 1 | 1 | 0 | 236 |
| C2 | 0 | 1 | 1 | 1 | 0 | 236 |
| C3 | 0 | 2 | 0 | 1 | 0 | 236 |
| C4 | 0 | 2 | 0 | 1 | 0 | 236 |
| Q1 | 0 | 2 | 0 | 1 | 0 | 236 |
| SV | 0 | 2 | 0 | 1 | 0 | 236 |
| SVL | 0 | 2 | 0 | 1 | 0 | 236 |
<!-- END GENERATED:replay -->

## Recommendation

**Adopt C3 in four steps; ship step 1 now.** Each step is safe on its own.

1. **Now, editortrack server only (Q1).** Never send 100 or done: cap at 99 and mark everything
   provisional; hold the previous value while `timeline.grade.pending > 0` (detector warm-up,
   WC37), while an offline/reference timeline is open, and while the Resolve project does not match
   the booking (WC34, WC43); ignore records older than the newest one from the same host (offline
   queue backlog, WC31). This removes every false completion in the library at almost no cost. It
   does not shrink the errors.
2. **PMS contract (needs a PMS change, below).** Send `provisional`, `total_confirmed`,
   `done_count`/`total`, `work_done` and `reasons` with each value, so PMS can show "about 75%
   (provisional, scope grew)" instead of a bare number, and a drop reads as "scope grew" or
   "rework" rather than lost work.
3. **Poller sends per-item identity; server keeps the shot ledger (S3 inside C3).** Per video item:
   Resolve unique id, clip name (normalised: extension and `_vNNN` stripped), item kind, enabled
   flag, media version, and the grade signature `grade_detect` already computes. The ledger merges
   the rooms, timelines and projects of one job ("most recent change wins"; a view seen for the
   first time never overrides work done elsewhere), treats a new media version with a carried grade
   as not done, counts rework as work (`work_done` is monotonic), and flags a whole-timeline apply
   within a minute as `mass_grade`. This is what fixes multi-room, duplicates, Save As, re-conform
   and Replace Clip.
4. **Confirmed scope, then done.** Only when a confirmed shot list (or count) exists, all its shots
   are done on their newest version, no unknown shots are present, and that state has held 30
   minutes, may editortrack send `done=true`, and PMS still treats it as advisory. A stage is
   complete in PMS only through its existing human rule (task DONE + QC PASS); a task moved back to
   REVISION reopens the machine value too.

Trade-offs the owner accepts with this recommendation:

- Without a confirmed scope the machine value never says done (it shows 99, provisional). That is
  the honest answer: the grade room cannot know about shots that have not arrived.
- The mass-grade guard stops "LUT on everything" from reading as finished (WC12) but under-counts a
  legitimate one-click copy-grade to a scene (WC13) until those shots are touched again. It can be
  switched off (D4).
- The job guard withholds values when the Resolve project name matches neither the booking's
  `project_no` nor its name (WC32, WC34). Without a naming rule or a registry this means silence,
  not a wrong number (D5).
- Once scope is confirmed the error is small (median 1 point), but the identity limits listed
  above remain until clip names or shot codes are readable on the facility's timelines.

## Decisions needed from the owner

- **D1 - Which scope source is authoritative?** In the order this study would try them: (a) the
  conform room's timeline / offline EDL shot list for the same job, read automatically (C4; no
  human input, but WC20 shows a stale offline is then trusted, so it needs a rule for which offline
  version counts); (b) a shot count or shot list on the PMS project or task, entered by the producer
  or conform lead at hand-over (S2/C3; human input per job); (c) none: accept that the machine never
  says done and PMS completion stays human only.
- **D2 - Does editortrack ever send `done=true`?** Recommended: yes, but advisory; PMS completion
  stays task DONE + QC PASS. Alternative: never send done at all.
- **D3 - What is a unit of color work?** Shots (recommended) or clips. Do titles, generators,
  adjustment layers, disabled clips, graphics stills and compound clips count? (WC24 to WC26b.)
- **D4 - Mass-grade guard on or off?** (WC12 vs WC13.)
- **D5 - A naming rule or registry linking a Resolve project to a PMS project** (project name
  contains `project_no`, or a mapping table)? Without it the job guard must either withhold or
  trust the single booking in the room (today's behaviour, which mis-attributes WC34 and WC43).
- **D6 - Stability window (30 min) and provisional cap (99).**
- **D7 - Multi-room conflict rule:** most recent change wins (implemented). Alternative: a shot is
  done only if it is done in the room booked for it.
- **D8 - Which timeline names are reference/offline** (excluded from progress)? Today's regex is
  `offline|editorial|_ref\b|reference|picture\s*lock|ผ่านตัด` (WC22 shows a real offline it misses,
  WC22b a wrong one it matches).

## What PMS must change so a provisional value is never treated as done

From a read-only review of `kpp-pms-api` (`routers/integrations/editortrack.py`,
`services/integrations/workstation.py`, `models/production/workstation.py`) and
`kpp-pms/src/projects/workstation.ts`. Today `/progress` takes `project_no, stage, progress_pct,
observed_at, host`, stores NUMERIC(5,2), and the project page shows the row with the latest
`observed_at` per stage as a bare number, so 100 looks finished.

1. Accept and store `provisional` (bool), `total_confirmed` (bool), `done` (bool), `done_count`,
   `total`, `work_done` and `reasons` (text array) on `workstation_progress`. All optional, so the
   current sender keeps working.
2. Display a provisional value as "about N% (provisional)" with the reason codes in plain words;
   never render 100 or a "complete" badge from editortrack unless `done` and `total_confirmed`.
3. Never feed the machine value into task status, baseline, variance or the evidence-based project
   progress. That is already the owner's rule of 2026-10-09; keep it explicit for `done`.
4. For the reopen rule, expose to editortrack the task status and current QC round of the task a
   booking belongs to (`/schedule` returns bookings, not task status, today).
5. If D1 = (b): a scope field per project or task (shot count, optional shot list, optional runtime)
   and an endpoint for editortrack to read it.
6. Clock skew (WC30): PMS orders by `observed_at`, so a room whose clock is slow can hide a newer
   value. Either agents run NTP, or PMS orders by `received_at` within a short window.

## Not verifiable here (needs a real Resolve machine)

- Whether the per-item fields can be read cheaply on a 300 to 1500 clip timeline: clip name, kind
  (title, generator, adjustment, compound), `GetClipEnabled`, media version, media online/offline.
- Whether `ExportLUT` on a clip includes group pre/post-clip and timeline nodes and NR/blur (WC14),
  and how fast it is (that sets how long the detector warm-up lasts, WC37).
- Whether Replace Clip keeps the timeline item and its grade on the facility's Resolve version
  (WC07), and whether a duplicated timeline or a restored project archive keeps clip names (WC15,
  WC19).
- How the facility actually names offline/reference timelines and Resolve projects (D5, D8).
- The real 6 -> 8 clip change on 2026-10-14 kept the duration at 161.56 s on a 2-track timeline:
  either clips were added on V2 or a clip was bladed. The owner confirmed clips are added later;
  the replay assumes 8 clips is the true scope.

## Limits of this study

- Apart from WC01 (real) and the replay of the 263 stored records, scenarios are authored from the
  code, the docs and common facility practice; how often each happens is unknown. Weigh decisions
  by severity, not by counts alone.
- Ground truth is a modelling choice (documented per scenario): shots in the final deliverable,
  hindsight scope; a shot is done once its newest version is graded; client notes reopen shots.
- Polls in authored scenarios are coarse (minutes to hours); real polls are every 60 s.
  Time-based rules (stability window, mass-grade window) were checked against both.

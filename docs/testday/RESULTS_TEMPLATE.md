# ผลการทดสอบเฟส 3 · Phase 3 test results — <HOST> (copy once per machine)

> Copy this file to `~/ET_TESTDAY/results/RESULTS_<host>.md` on the test day. Every step records the evidence
> file (relative to `~/ET_TESTDAY/results/`). One FAIL stops the protocol (CHECKLIST section 1).
> DUMMY project only — if anything from a client project appears in the evidence, delete that file and record an
> abort.

## Machine

| Field | Value |
|---|---|
| Host / room | |
| Role | Mac conform / Linux grade |
| Resolve Studio version (exact string) | |
| OS / kernel / Python | |
| Agent version (tag) + SHA-256 of the release zip | |
| Station env (`deploy/stations/<host>.sh`) + `EDITORTRACK_RESOLVE_VERIFIED` | |
| Server lease for this host (`allow_resolve`, `allowed_versions`, lease hours) | |
| PMS push off? (yes/no) | |
| Project library / project | `ET_DUMMY` / `ET_DUMMY_TESTDAY` |
| Dummy media: where generated, ffmpeg build + SHA-256, `MANIFEST.json` created | |
| Screen-watch masks used (from the baseline) | |
| Test lead / operator | |

## Results

| Step | Date / time | Result (PASS / FAIL / n.a.) | Measured value(s) | Evidence file(s) | Init |
|---|---|---|---|---|---|
| Baseline (agent OFF, 30 min idle) | | — | changes: ___ ; hotspots: ___ | `baseline/analysis.md` | |
| S0 scripting Local, no LAN port | | | | `S0_ports.txt`, screenshot | |
| S1 8 h working soak | | | unexplained@turn ___, explain ___, turns ___ | `S1/analysis.md`, `agent.log`, records | |
| S2 colorist 2 h | | | unexplained@turn ___ ; colorist report ___ | `S2/analysis.md` | |
| S3 idle 8 h | | | undo ___ ; save prompt ___ ; modified time before/after ___ | `S3/analysis.md`, screenshots | |
| S4 probes on dummy | | | page before/after ___ | `S4_probe.txt`, `S4_probe_grade.txt` | |
| P1 playback 4K | | | OFF drops ___ ; ON drops ___ ; ON turns ___ reasons ___ ; max calls ___ | `P1.csv`, `P1_analysis.json` | |
| P1 playback 8K | | | (as above; INVALID if OFF drops > 0) | | |
| P2 300 clips | | | p95 guard.ms ___ ; calls p95 ___ ; scans ___ ; lag noticed? ___ | `P2_300.json` | |
| P2 3000 clips | | | p95 guard.ms ___ ; scan_budget skips ___ ; user_returned ___ | `P2_3000.json` | |
| P3 render 3× off / 3× on | | | off mean ___ s (sd ___) ; on mean ___ s ; delta ___ % | `P3.csv`, `P3.json` | |
| P4 1 h cost | | | avg CPU ___ % ; max turn CPU ___ s ; RSS max ___ MB ; state ___ MB ; net ___ B/min | `P4.csv`, `P4_turns.json` | |
| P5 7-day trend | | | RSS trend ___ ; turn_ms trend ___ ; alerts ___ | `/api/health` exports | |
| C1 kill -9 ×50 | | | crashes ___ ; dialogs ___ | notes | |
| C2 modal dialog 5 turns | | | | | |
| C3 quit / splash / closed 1 h | | | skip reasons ___ ; zombies ___ | | |
| C4 switch mid-scan | | | `state_changed_during_scan` suspect=false ___ ; trips ___ | | |
| C5 sleep / reboot | | | resume after ___ min | | |
| C6 server 3 min / 1 day | | | max flush per turn ___ ; silent after lease ___ | | |
| K1 local kill switch | | | | `drill.csv` | |
| K2 system kill file | | | silent after ___ s | `drill.csv` | |
| K3 fleet switch | | | silent after ___ s | `drill.csv` | |
| K4 lease expiry | | | silent after ___ | `drill.csv` | |
| K5 stopwatch uninstall (non-developer) | | | ___ s (limit 300) ; person ___ | `drill.csv` | |
| U1 prefs byte-identical | | | strict diffs ___ ; control churn ___ ; leftovers ___ | `U1_compare.json` | |
| U2 purge | | | | `drill.csv` | |
| R1 upgrade + rollback | | | rollback ___ s | | |
| R2 unverified Resolve version | | | reason seen ___ | | |

## Progress v2 observations (steps during S1; values from `progress_replay.py` afterwards)

| Step | Time | Timeline / clips | Replayed pct | provisional / done | reasons | Expected (DUMMY_PROJECT.md §3) | OK? |
|---|---|---|---|---|---|---|---|
| R0 import | | T0300_v1 / 300 | | | | provisional, 0 % | |
| R1 grade 1–40 | | | | | | rises, provisional | |
| R1 grade 41–80 | | | | | | | |
| R1 grade 81–120 | | | | | | | |
| R2 add 10 late VFX | | / 310 | | | | `scope_change` | |
| R3 grade + relink VFX | | | | | | no done, no unexplained drop | |
| R4 reset 70–84 | | | | | | `rework` / `regression` | |
| R4 regrade 70–84 | | | | | | back up | |
| R5 cut 20 | | / 290 | | | | `scope_shrink` | |
| R6 look on all | | | | | | not 100, not done | |
| R7 reconform | | T0300_v2_reconform / 295 | | | | labelled change, no false 0/100 | |
| R8 grade 1 | | T0006_WC01 / 6 | | | | 16.7 provisional | |
| R8 grade all 6 | | | | | | 99 `mass_grade`, not done | |
| R8 reset clip 6 | | | | | | 83.33 `regression` | |
| R8 +2 clips on V2 | | / 8 | | | | 75 `scope_change` | |
| R9 T1500 +50 / −50 | | | | | | as R2 / R5 | |
| R9 T3000 +50 / −50 | | | | | | as R2 / R5, no scan_budget storm | |

## Incidents / anything noticed

| Time | Who noticed | What | Agent turn at that time? | Action (kill switch / abort / explained) |
|---|---|---|---|---|
| | | | | |

## Sign-off

| | Name | Signature | Date |
|---|---|---|---|
| Operator: "I noticed nothing" / "ไม่รู้สึกว่ามีอะไรรบกวน" | | | |
| Test lead: all steps PASS | | | |
| Owner: stage R2 approved | | | |

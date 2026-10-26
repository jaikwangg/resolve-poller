# testday/ — phase-3 test-day kit

For **one test day on two test workstations (Mac conform + Linux grade) with DUMMY projects only**, run by a
person at the machine. Nothing here is installed by the agent, nothing contacts any machine by itself, nothing
talks to DaVinci Resolve's scripting API, and the kit never runs `sudo`: it prints the command for the operator,
and `drill.py uninstall` stops with instructions when a system-wide install would make `uninstall.sh` use sudo.
Verdicts are strict: a step without the evidence it needs (no records for the host, the agent not demonstrably
on, a short capture) is INCOMPLETE/FAIL, never PASS.
Checklist: `docs/testday/CHECKLIST.md` · dummy project: `docs/testday/DUMMY_PROJECT.md` · results:
`docs/testday/RESULTS_TEMPLATE.md`. Protocol and thresholds: `docs/research/WORKSTATION_SAFETY.md` §7.

Requirements on the workstation: Python 3.8+ (the agent's own), and for screen capture macOS `screencapture` +
`sips` (built in; Terminal needs Screen Recording permission for the day) or Linux X11 `ffmpeg` (x11grab) or
ImageMagick `import`. No pip packages. Copy this folder to `~/ET_TESTDAY/kit/` and write results to
`~/ET_TESTDAY/results/`.

| Tool | Steps | What it does |
|---|---|---|
| `make_dummy_project.py` | setup, P1–P3, V | Synthetic media (ffmpeg test patterns) + FCP7 XML / EDL timelines of 30/300/1500/3000 clips, 4K/8K playback, revision scenarios R0–R9 |
| `screen_watch.py` | baseline, S1, S2, S3 | `capture`: screenshot every 2 s, exact row-by-row diff, masks, keeps frames only for unexplained changes, disk budget, every macOS display · `analyze`: change vs OS idle vs agent turn → FAIL / INCOMPLETE / PENDING / PASS · `replay`: same diff over saved frames |
| `playback_trials.py` | P1 | Alternating OFF/ON 2-min trials (kill switch), operator enters Resolve's dropped-frame readout; `analyze` checks drops + every turn during playback is a ≤ 12-call skip |
| `turn_stats.py` | P2, P4, all | From server records: reasons, p95 `guard.ms`, calls, scan_ms, RSS, bytes/min, forbidden/tripped |
| `render_timer.py` | P3 | Times each render by the output folder (read-only `stat`), 3× off / 3× on, ≤ 1 % and within 2σ |
| `agent_cost.py` | P4 | Agent CPU per turn (systemd journal "Consumed … CPU time" / macOS `ps -S` on the launchd loop), `~/.editortrack` size |
| `prefs_snapshot.py` | U1 | SHA-256 of every Resolve preference file + scheduler/footprint lists; `compare` = byte-identical or FAIL; control-run allow list |
| `progress_replay.py` | V | Offline replay of the day's records through a progress-v2 checkout's engine with a fake PMS (no network): the values PMS would have got, never done/100 without a confirmed scope, every drop explained |
| `drill.py` | K1, K2, K3/K4, K5, U1, U2 | Guided, timed kill-switch and uninstall drill around the agent's own `killswitch.sh` / `uninstall.sh` |

## Commands on the day

```sh
K=~/ET_TESTDAY/kit; R=~/ET_TESTDAY/results; A=~/editortrack/agent; H=$(hostname)
# setup
python3 $K/make_dummy_project.py --out ~/ET_DUMMY_TESTDAY --uhd --dry-run
python3 $K/drill.py --agent-dir $A --log $R/drill.csv --operator "<name>" before        # Resolve quit, BEFORE install
python3 $K/screen_watch.py capture --out $R/baseline --hours 0.5                        # agent OFF, nobody touches
python3 $K/screen_watch.py analyze --run $R/baseline --no-operator    # INCOMPLETE by design: read the hotspots
# S1 / S2 / S3 (masks from the baseline hotspots)
python3 $K/screen_watch.py capture --out $R/S1 --hours 8 --mask 0.9,0,0.1,0.03
python3 $K/screen_watch.py analyze --run $R/S1 --agent-log ~/.editortrack/agent.log --records <day>.jsonl --host $H \
    [--first-frame-ok]     # only if the capture-time question was skipped and the lead checked the first key frame
python3 $K/screen_watch.py analyze --run $R/S3 --agent-log ~/.editortrack/agent.log --records <day>.jsonl --host $H --no-operator
# P1-P4
python3 $K/playback_trials.py run --csv $R/P1.csv --timelines PB_4K,PB_8K --agent-dir $A
python3 $K/playback_trials.py analyze --csv $R/P1.csv --records <day>.jsonl --host $H --rate 24 --json $R/P1_analysis.json
python3 $K/turn_stats.py --records <day>.jsonl --host $H --from <ISO> --to <ISO> --step P2 --json $R/P2_3000.json
python3 $K/render_timer.py watch --dir ~/ET_DUMMY_TESTDAY/renders/off1 --label off1 --agent off --csv $R/P3.csv
python3 $K/render_timer.py analyze --csv $R/P3.csv --records <day>.jsonl --host $H --json $R/P3.json
python3 $K/agent_cost.py --minutes 60 --csv $R/P4.csv
# V (after the day, on any machine with the records and a progress-v2 checkout)
python3 $K/progress_replay.py --server-dir ~/github/editortrack/server --records <day>.jsonl --host $H --csv $R/V.csv
# K / U
python3 $K/drill.py --agent-dir $A --log $R/drill.csv k1
python3 $K/drill.py --agent-dir $A --log $R/drill.csv k2
python3 $K/drill.py --agent-dir $A --log $R/drill.csv silence --tag K3
python3 $K/drill.py --agent-dir $A --log $R/drill.csv silence --tag K4 --grace lease     # server admin makes it unreachable
python3 $K/drill.py --agent-dir $A --log $R/drill.csv --operator "<not the developer>" uninstall [--purge]
python3 $K/drill.py --agent-dir $A --log $R/drill.csv after [--expected editortrack]     # after reboot, Resolve quit
```

`<day>.jsonl` = the server's `data/<YYYY-MM-DD>.jsonl`, copied read-only by the server admin. The agent log is in
the workstation's local time; when analysing on another machine pass `--tz-offset 7`.

## Tests (on the build server, no workstation needed)

`./run_tests.sh testday` → `testday/tests/test_testday.py` (95 tests): PNG/BMP readers and the frame diff on
sample images, an end-to-end replay → analyze where a page jump at an agent turn is a FAIL and a static screen
with a masked clock is a PASS, generator XML/EDL structure, the P1/P2/P3/P4 evaluators, the prefs compare
(one-byte change, added file, control churn, leftover unit), and kit-safety checks (no Resolve scripting import,
no network module, no sudo, Python 3.8 syntax).

Not tested here (needs the test day): real screenshots (`screencapture`/`sips`, x11grab, `import`), the macOS
`ioreg` and X11 idle readings, real ffmpeg media generation, Resolve importing the XML, `journalctl` / `ps -S`
CPU sampling on the workstation, and the agent's real `killswitch.sh` / `uninstall.sh` on launchd/systemd
(those two scripts already have their own stubbed tests in `agent/tests/test_deploy_kit.py`).

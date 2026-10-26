# Maintenance — keeping the agent zero-incident for months

Companion to [WORKSTATION_SAFETY.md](WORKSTATION_SAFETY.md). Most failures come later, not on
install day: a Resolve or macOS update, a forgotten config, a disk filling up, a "quick fix"
pushed straight to a workstation. Each section names what exists on the branch
`research/workstation-safety` and what still has to be built (**TO BUILD**).

## 1. Health monitoring (every installed agent, from the server)

**Heartbeat.** Every agent record now carries an `agent` block (`agent/runner.py:94`):
`version`, `python`, `os`, `priority` applied, `rss_mb` (peak of the turn), `turn_ms`, `guard`
(Resolve calls, ms, aborted, violations), `tripped` (breaker reason), `backoff_fails`,
`queue_lines`. A skipped turn still sends a heartbeat. Its `source.reason` says why it skipped
(`user_active`, `playback`, `rendering`, `resolve_version_unverified`, `python_unverified:…`,
`tripped:…`, `lease_no_resolve`, …). Only the kill switch and a missing lease make an agent
fully silent; that silence is itself an alert.

**Server side** (`server/agent_health.py`, endpoints in `server/dashboard_api.py`):
- `GET /api/health` returns one row per workstation: last seen, version, last successful
  Resolve poll, skip reasons in the last 24 h, max RSS / turn time / Resolve-call time,
  breaker, backoff, queue, status `ok | warn | alert` with the reasons.
- `GET /health` is a plain table with one row per workstation (red = alert, yellow = warn).
- Thresholds (`agent_health.py:THRESH`):

  | Condition | Status |
  |---|---|
  | Breaker tripped; `forbidden_call` or `state_changed_during_scan` seen; version not allowed; RSS > 150 MB; turn > 60 s; Resolve calls > 2000 ms in one poll | **alert** |
  | No record for > 10 min; no heartbeat block (old agent); queue > 1000; backoff ≥ 3 | warn |

**Alerts to the owner.** `server/health_alert.py` sends a Telegram message **only when a host's
status changes** (including recovery), using the same Bot API pattern as
`~/github/server-temp-bot/telegram_api.py`. Env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
(empty means it only prints). LINE: the Messaging API client in `server-temp-bot/line_api.py`
can be used the same way if the owner prefers LINE (quota counts per recipient).
**TO BUILD (install, owner approval needed):** a user systemd timer on the server, e.g.

```ini
# ~/.config/systemd/user/editortrack-health.service
[Service]
Type=oneshot
WorkingDirectory=%h/github/editortrack/server
EnvironmentFile=%h/.editortrack/health.env      # TELEGRAM_*, EDITORTRACK_STORE
ExecStart=%h/.editortrack-venv/bin/python health_alert.py
# editortrack-health.timer: OnCalendar=*:0/5
```

Tests: `server/tests/test_agent_health.py` (status rules, alert only on transition, end-to-end
fleet kill switch with the real server).

## 2. Update policy

**Rules**
1. **No automatic code pushes to workstations, ever.** The server checkout autodeploys from
   git every minute (`autodeploy@editortrack.timer`). That channel must never reach a
   workstation. Workstation code changes only by a person running the install from a release
   artifact. The server can only grant or refuse the lease; the agent reads nothing else from
   it (`agent/safety.py:252`), so even a bad server push cannot change workstation behaviour.
2. **Releases are tagged and pinned.** A release = git tag `agent-vX.Y.Z` + a zip of `agent/`
   (without `probe*.py` and `tests/` for client rooms) + `SHA256SUMS`, signed (`git tag -s`,
   or `minisign` on the zip). `safety.AGENT_VERSION` must equal the tag (**TO BUILD:** a CI
   check).
3. **The server pins versions.** `agent_control.json` → `"allowed_versions": ["0.9.0-safety"]`.
   An agent whose version is not listed gets no lease and stays silent. Rolling out = adding
   the new version for **one host**; rolling back = removing it.
4. **Staged update.** New version → CI green → the spare machine (R1) for 2 days → one room for
   1 week → the rest one room at a time. Same evidence + owner sign-off as
   WORKSTATION_SAFETY.md §8.
5. **Rollback in one command (TO BUILD before R2):** keep versions side by side
   (`/opt/editortrack/versions/<ver>`, `current` symlink). `sh deploy/switch-version.sh <ver>`
   verifies the checksum, repoints `current`, restarts the scheduler and runs
   `uninstall.sh --verify`-style checks. Until then: `killswitch.sh on` → unzip the previous
   release over the install dir → `install.sh` → set `allowed_versions` back.

**When the workstation changes underneath the agent** (all fail closed already):

| Change | What the agent does by itself | What a person does |
|---|---|---|
| Resolve upgraded (any part of the version string not on `EDITORTRACK_RESOLVE_VERIFIED`) | Skips every poll with `resolve_version_unverified` (heartbeat continues; health shows it) | Run WORKSTATION_SAFETY.md §7 S1–S4 + P1–P2 on a dummy project with the new version on one machine → add the version to the station env (the pre-upgrade check) |
| Python upgraded (major.minor) | `python_unverified:<ver>`, no Resolve calls | Re-run `./run_tests.sh safety` with that Python + §7 S1 short soak → update `EDITORTRACK_PYTHON_VERIFIED` (`make-env.sh` writes it) |
| macOS / Linux upgraded | With `EDITORTRACK_OS_VERIFIED` set: `os_unverified:…` | Same as Python; also re-check X11/TCC permissions |
| Resolve Free installed instead of Studio | `not_studio` | — |
| Machine renamed | Host not in `agent_control.json` → no lease → silent | Update the control file + room map |

**Pre-upgrade check (facility IT procedure):** before upgrading Resolve/OS on a room, run
`sh deploy/killswitch.sh on` (or rely on the automatic skip above), upgrade, verify on the
spare machine first, then update the env and `killswitch.sh off`.

**Compatibility matrix (to keep in this file, one row per verified combination):**

| Agent | Resolve | OS | Python | Verified on | Steps passed | Signed |
|---|---|---|---|---|---|---|
| 0.9.0-safety | — | — | — | not yet verified on any machine | — | — |
| 0.9.1-safety (ET-5 send policy, `AGENT_SEND_POLICY.md`) | — | — | — | not yet verified on any machine; stage 1 = phase-3 test day (C7) | — | — |

## 3. Runbooks

**R-1 Agent misbehaving** (alert, or a user reports anything odd)
1. Fleet stop first, investigate second: set `"default": {"enabled": false}` and every host to
   `enabled:false` in `agent_control.json` (≤ 60 s). On the reported machine also run
   `sh deploy/killswitch.sh on` (immediate).
2. Collect: `/api/health` row, the host's records (`server/data/<day>.jsonl`, grep host),
   `~/.editortrack/agent.log`, `~/.editortrack/TRIPPED`, the user's account of what they saw
   and when.
3. Decide whether to uninstall (R-4). Write the incident up; add a regression test to
   `./run_tests.sh safety`; restart from the previous rollout stage.

**R-2 Resolve slow / stuttering** (agent suspected)
1. `killswitch.sh on` on that room at once; ask the user whether the symptom stops.
2. Check the host's `guard.ms`, `guard.calls`, `turn_ms` at the reported time; was a scan
   running (not a skip)?
3. If it correlates: keep the room off, lower `EDITORTRACK_SCAN_BUDGET_MS`/`MAX_CLIPS` on the
   spare machine and redo §7 P2. If it doesn't: leave the agent off until the real cause is
   found anyway (zero-incident means the agent is not the first suspect to clear).

**R-3 Emergency stop (everything, now)**
`agent_control.json` → `{"default":{"enabled":false},"allowed_versions":[],"hosts":{}}`. Every
agent that reaches the server is silent within one turn; agents that can't reach it go silent
when their lease expires. For a single machine: `sh deploy/killswitch.sh on`. Machine-wide
(all users): `sudo touch /etc/editortrack/DISABLED` (Linux) or
`sudo touch "/Library/Application Support/editortrack/DISABLED"` (mac).

**R-4 Uninstall**
`sh deploy/uninstall.sh` (kill file → stop scheduler → remove units/plists → kill leftover
processes → delete `~/.editortrack`) then `sh deploy/uninstall.sh --verify` (exit 0 = nothing
left). `--purge` also deletes the code; `--dry-run` shows the actions first. Remove the host
from `agent_control.json`. For a `--system` install, run it with an account that can sudo.

**R-5 Add a new room**
Hostname → station env from `deploy/stations/` → `make-env.sh` (no defaults: server + per-host
token + the verified Resolve version; Python/OS recorded automatically) → `install.sh` → add
the host to `agent_control.json` with `allow_resolve:false` → 1 week heartbeat-only → §7 short
form (S1, P1, K1, U1 dry run) → `allow_resolve:true` with owner sign-off. Add the room to the
compatibility matrix.

**R-6 Rotate keys**
- *editortrack ingest token* (agent → server; **needed now**, the current one is public):
  generate per-host tokens, put them in each station `env.sh` (never in git), set them on the
  server (`~/.editortrack/server.env`, not in the unit file), restart the server, confirm
  `/health` is green, then remove the old token.
- *PMS integration key* (server → PMS): set the new key in `~/.editortrack/server.env`, restart
  the server, run one read-only `GET …/schedule` check, confirm `/api/pms` shows success, then
  ask PMS to revoke the old key. Never print the key; never commit it.

## 4. Retention and disk caps

| Where | What | Cap / retention | Where enforced |
|---|---|---|---|
| Workstation `~/.editortrack/agent.log` | one line per turn | 1 MB, then cut to the last 200 KB | `deploy/run-agent.sh:15` (Linux/mac; **Windows TO BUILD**) |
| Workstation `queue.jsonl` | records not yet delivered | 5000 lines / 10 MB, oldest dropped; flush ≤ 300 requests per turn; a record our server refuses (400 with its X-Editortrack-Reject marker) is dropped, never blocks the queue | `agent/runner.py` (`_flush_queue`, `AGENT_SEND_POLICY.md`) |
| Workstation `rejected.log` / `rejected.json` | records the server refused or the agent's ts check dropped | 200 lines / 256 KB, newest kept; record kept only if ≤ 8 KB | `agent/runner.py:_reject` |
| Workstation `send_backoff.json` | sender backoff | tiny | `agent/runner.py:_send_backoff_fail` |
| Workstation `grade_cache.json` | per-clip grade results (clip UIDs) | 8 timelines | `agent/grade_detect.py:MAX_TIMELINES` |
| Workstation `tmp/` | ExportLUT temp (only if gated on) | deleted per clip and per poll | `grade_detect.py:_export_dev`, `finish` |
| Workstation total | | < 15 MB expected (§7 P4 verifies) | — |
| Server `data/*.jsonl` | all records (client project names!) | **TO BUILD (phase 2):** move out of the git checkout to `/srv/editortrack` (700), delete after N days (owner/data sheet), backup decision | — |
| Server `health_state.json` | last alert state | tiny | — |

## 5. Ownership and periodic review

| Role | Who (owner to fill) | Duties |
|---|---|---|
| System owner | | Signs rollout stages, controls `agent_control.json`, receives alerts |
| Deputy | | Can run R-3 emergency stop and R-4 uninstall |
| Facility IT contact | | Announces Resolve/OS upgrades before they happen |

**Weekly (10 min):** `/health` all green? any `user_returned`/`scan_budget`/`playback` skips
trending up? queue sizes? any host silent?
**Monthly:** re-run the kill-switch drill on one room (K1 + K3); check the compatibility matrix
against the real versions in the heartbeats; check `~/.editortrack` sizes; confirm CI nightly
is green.
**Quarterly:** review this file and WORKSTATION_SAFETY.md §4 (any new hazard?); rotate tokens;
confirm data retention is deleting; re-confirm consent/data sheet still matches the record
fields; test restore of the server from backup.
**After every Resolve major/minor release:** §7 S1–S4, P1–P2 on the spare machine before any
room upgrades.

## 6. The safety checks in the ongoing test system

- **One command:** `./run_tests.sh [safety|worstcase|replay|all]` (repo root). Each test module
  runs in its own process.
- **Shared marker convention** (for the progress worst-case suite in `editortrack-wc` too): a
  test module declares `MARKERS = {"safety"}` or `MARKERS = {"worstcase"}` at module level.
  `run_tests.sh <marker>` selects by it; `all` runs everything. Tests are plain `unittest`, so
  no pytest is needed on the server venv. If pytest is adopted later, register the same names as
  `pytest.mark.safety` / `pytest.mark.worstcase`.
- **CI:** `.github/workflows/safety.yml` runs `./run_tests.sh safety` on Ubuntu and macOS on
  every push and PR, and **nightly** (02:17 Bangkok), plus `./run_tests.sh all` on Ubuntu.
  (Not pushed from this branch; it takes effect when the branch is merged and pushed.)
  **TO BUILD:** a Windows job once a Windows kit exists. A local nightly on the server via a
  user timer running `./run_tests.sh all` and sending a failure through `health_alert.send` is
  an alternative if GitHub Actions is not wanted.
- **Every incident becomes a test.** The two historical incidents are already encoded:
  GetRenderJobList page jump → `test_api_allowlist.py` (denied name, removed flag, simulated
  regression) and `test_fail_closed.py::Incidents`; Windows 32-bit idle tick →
  `activity_sampler.idle_from_ticks` + `Incidents::test_windows_idle_tick_wraparound`.
- **Current result:** `./run_tests.sh all` → 6 modules, 84 tests (67 safety), all pass (2026-10-26).

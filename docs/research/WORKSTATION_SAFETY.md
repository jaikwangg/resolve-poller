# Workstation safety — editortrack agent on DaVinci Resolve workstations

Branch `research/workstation-safety` · 2026-10-26 · status: **research + guardrails on the
branch, nothing installed anywhere, nothing pushed.**

The owner's requirement: if the agent ever damages anything on a workstation, the system can
never be trusted again. This document treats that as a **zero-incident requirement, not a
risk budget**. It gives evidence (tests, measurements, code references), not reassurance.

Run every automated check described here with one command from the repo root:

```sh
./run_tests.sh safety      # API allowlist, fail-closed, resource budget, deploy kit, lease/health (67 tests)
./run_tests.sh all         # + replay (and the progress worst-case suite once it lands)
```

Contents: [1 Zero-incident criteria](#1-zero-incident-criteria) ·
[2 Architecture comparison](#2-architecture-comparison-less-intrusive-options-first) ·
[3 How the agent fails closed](#3-how-the-agent-fails-closed-what-was-built-on-this-branch) ·
[4 Hazard analysis (FMEA)](#4-hazard-analysis-fmea) ·
[5 The API allowlist](#5-the-resolve-api-allowlist) ·
[6 Measurements on mocks](#6-measurements-on-mocks) ·
[7 Real-machine verification protocol](#7-real-machine-verification-protocol-extends-next_steps-phase-3) ·
[8 Staged rollout and removal](#8-staged-rollout-with-sign-off-and-instant-removal) ·
[9 Decisions needed from the owner](#9-decisions-needed-from-the-owner) ·
[MAINTENANCE.md](MAINTENANCE.md)

---

## 1. Zero-incident criteria

**Incident** = anything an editor or colorist could notice, or anything that touches client
work. Examples: a page, timeline, playhead, selection or viewer changes; a dialog appears;
playback drops a frame; Resolve stutters, hangs or crashes; a project gets an undo entry, a
"modified" state, a lock, a save, a render-queue change; a render gets slower; disk, network or
GPU use is visible; client data (project names, shot codes, frames) leaves the machine beyond
what the signed data sheet allows.

Rules applied in this document:

1. **Rank by "could an editor/colorist notice it, or could it touch client work".** Any hazard
   in that class (class **A** in §4) is a **blocker** unless it is **impossible by
   construction**: an allowlist enforced in code, no write path, or fail-closed. "Unlikely"
   does not count.
2. **Fail closed.** On any doubt the agent does nothing to Resolve that turn and stays quiet.
   That covers an unknown Resolve, Python or OS version, an unexpected API result, Resolve busy,
   rendering or playing back, the user active, the scan over budget, a kill-switch file it
   cannot read, a missing or expired server lease, and a tripped breaker.
3. **A class-A hazard is closed only when both are true:**
   (a) an automated test proves the construction (it runs in CI and nightly, see
   MAINTENANCE.md §6), **and**
   (b) the real-machine protocol in §7 passed on every OS + Resolve version combination in use.
   The automated test alone is not enough, because the mock cannot prove that a Resolve getter
   has no side effects.
4. **Any incident in any stage:** fleet kill switch (seconds), roll back one stage, and the
   incident becomes a regression test before anything widens again.
5. **Widening needs evidence plus the owner's signature** (§8). Nothing auto-promotes.

### Where things stand today

| | |
|---|---|
| Class-A hazards closed by construction + automated test | Render-queue page jump, page "restore" (OpenPage), any non-allowlisted API call, writes outside the agent's own folder, retry storms, overlapping runs, unbounded queue/log, hung turns, running while disabled/unleased/unverified |
| Class-A hazards that **only a real machine can close** | Allowlisted getters really are side-effect free on each Resolve version · scripting calls do not stall the UI or playback · playback detection works · ExportLUT harmless (gated off) · kill -9 / Resolve hang / quit mid-scan harmless · OS idle source works (X11, macOS, Windows) |
| Blockers outside the agent | The leaked test token is still the live server token (and is in the public repo history) · read pages unauthenticated · no data sheet/consent yet (phase 0) · no Windows deploy kit or test at all |

**Verdict:** the agent on this branch is safe *by construction* against every failure we can
model without Resolve. It is **not yet proven safe on a workstation**, and it must not be
installed on a client machine until §7 passes and the owner signs §8 stage R2.

---

## 2. Architecture comparison (less intrusive options first)

The current design (a per-user agent calling Resolve's scripting API every minute) is the
**most intrusive** option. Before investing in proving it safe, check whether a design that
never touches Resolve gives enough data. The table uses the same hazard dimensions as §4.

| | **T0-a Server reads the project DB (read-only replica)** | **T0-b Server parses Resolve's own project backups (.drp)** | **T0-c File-system / watch-folder signals** | **T0-d OS-only activity sampler on the workstation** | **T1 Guarded scripting agent (this branch)** |
|---|---|---|---|---|---|
| Software on workstation | none | none (one Resolve preference set by facility) | none (server watches shares) | small: idle + "is Resolve frontmost", no Resolve calls | agent + Resolve scripting connection every minute |
| Facility setup needed | Projects on a **PostgreSQL project server** (not local disk / Blackmagic Cloud); a streaming **replica** or nightly dump restore; read-only DB role on the replica | "Project backups" enabled in Resolve Preferences → Project Save and Load, backup location on a share the server can read | Renders/exports/ingest land on known share paths | macOS: pyobjc · Linux: X11 + libXss · Windows: nothing extra | Resolve **Studio**, External scripting = **Local**; agent per user; phase-3 verification per OS + Resolve version |
| Can the editor notice it? (UI, playback, stutter) | **No** (never touches the workstation). The DB server is shared, so query it only on a replica | **No contact by us.** Resolve itself writes the backup (its own feature, its own I/O), so set the interval with the facility | **No.** Only a server-side directory listing; keep it off the playback SAN or rate-limit it | Practically no: two OS calls per minute, no Resolve calls | **Possible.** Every call runs inside Resolve (main thread, likely). Bounded and gated, but only a real machine can prove it (§7) |
| Can it touch client work? | No, with a replica. Without one, long SELECTs on the primary can block Resolve's DB maintenance or upgrade (ACCESS SHARE vs ACCESS EXCLUSIVE locks) | No (we read copies) | No | No | Only through the allowlist. Writes are impossible by construction; side effects of getters need verifying |
| Progress data it **can** give | Project list, last-modified times; **timelines, clips, markers, grades only if the undocumented, version-specific schema/blobs can be decoded** (unverified) | Timelines, clip lists, markers, probably grade/node data (format undocumented; needs a parser per version); a snapshot every backup interval | Delivery events (renders exist, sizes, times), ingest counts, subtitle files | **Metric #2 (active time) fully**; which project is open only via window title (fragile) | Metric #1 (conform duration, graded clips, shots via markers) live; page; Metric #2 together with T0-d |
| It **cannot** give | Active time; live state between saves; anything if the facility uses local DBs | Active time; anything between backups; anything if backups are off | Edit/grade progress inside Resolve | Edit/grade progress | Completion "quality"; history; anything outside Resolve |
| Main risk | Undocumented schema; breaks on Resolve upgrades; DB credentials on the server | Backup interval vs freshness; parser maintenance; backup disk usage | Weak signal | Privacy: window titles (send only `resolve_frontmost`, phase 1) | Unproven side effects / UI-thread contention; per-version verification forever |
| Hazards from §4 that disappear | All of A1–A12, P1, P2, S6 | All of A1–A12, P1, P2, S6 | All of A1–A12 | All of A1–A12 (A13 remains, small) | none, only mitigated |

**Recommendation (for the owner's decision, §9-D1):**

1. **First find out the facility's setup.** Are projects on a PostgreSQL project server? Are
   project backups on, and where do they go? (This is 10 minutes with the facility IT; nothing
   can be done here without it.)
2. If either T0-a or T0-b is available, prototype it **off-line on a copy** (a restored dump or
   copied .drp files of a **dummy** project) and check it shows conform duration and graded
   clips. If it does, the target architecture becomes **T0-a/b (server side) + T0-d (OS-only
   sampler)**. The scripting agent then never needs to run on a client workstation.
   `allow_resolve=false` in the server lease already turns this branch's agent into exactly
   T0-d (heartbeat + activity only, zero Resolve calls), which is tested in
   `agent/tests/test_fail_closed.py::Lease::test_allow_resolve_false_sends_heartbeat_only`.
3. Keep T1 only for rooms or data that T0 cannot cover, and only after §7 and §8.

---

## 3. How the agent fails closed (what was built on this branch)

One turn, in order. Each gate that says "no" ends the turn's contact with Resolve.

| # | Gate | Effect when it says no | Code | Test |
|---|---|---|---|---|
| 1 | Hard watchdog: the process exits after 90 s whatever happens (hung IPC, hung network) | Turn killed (exit 3); the scheduler starts a fresh one next minute | `agent/safety.py:75` | `test_fail_closed.py::Watchdog` |
| 2 | Own priority lowered (nice 19 + idle IO on Linux, IO throttle on macOS, background mode on Windows), on top of systemd/launchd caps | none | `agent/safety.py:86`, `deploy/editortrack-agent.service:11-16`, plist | `test_deploy_kit.py::Kit::test_resource_caps_present` |
| 3 | **Kill switch**: `EDITORTRACK_DISABLE`, `~/.editortrack/DISABLED`, `<agent>/DISABLED`, system file (`/etc/editortrack/DISABLED`, `/Library/Application Support/editortrack/DISABLED`, `%ProgramData%\editortrack\DISABLED`). **A file it cannot stat counts as disabled.** | Fully silent: no Resolve, no network | `agent/safety.py:56`, `runner.py:220` | `test_fail_closed.py::KillSwitch` (4) |
| 4 | Single-instance lock | Silent | `agent/safety.py:134` | `Breaker::test_lock_held_skips` |
| 5 | **Server lease** (`GET /agent/control`): needs an explicit `enabled:true` for this host **and** this agent version. A missing, corrupt or expired lease means silent. A short server outage is ridden out until the lease expires (default 6 h) | Silent | `agent/safety.py:252-290`, `server/agent_health.py:44` | `Lease` (7), `server/tests/test_agent_health.py` incl. an end-to-end run with the real server |
| 6 | Circuit breaker `~/.editortrack/TRIPPED` (set by a forbidden call, or by the page changing while the user was idle) | Heartbeat only, until a human clears it | `agent/safety.py:205`, `runner.py:32,61-63` | `Breaker` |
| 7 | Backoff after an exception or a budget abort: 60 s → 1 h exponential, no retry storm | Heartbeat only | `agent/safety.py:220`, `runner.py:56,65` | `Breaker::test_exception_backs_off_no_retry_storm` (10 turns → 1 Resolve attempt) |
| 8 | Platform pin: Python major.minor (and optionally OS) must match what was verified at install | Heartbeat only | `agent/safety.py:184` | `Breaker::test_python_or_os_upgrade_stops_resolve_calls` |
| 9 | Pre-flight (cheap reads): Studio · **Resolve version on the verified list** · project open · page known (not loading) · page ≠ Deliver · not rendering · **user idle ≥ 5 s** (unknown idle = no scan) · **playhead not moving** (two `GetCurrentTimecode` reads 250 ms apart) | Skip with a reason, ≤ 12 calls made | `resolve_poller.py:105-138` | `PreChecks::test_skip_reasons` (9 cases) |
| 10 | Scan budget: ≤ 1500 ms and ≤ 20 000 calls per poll; > 5000 clips = counts only; every 200 clips yield 10 ms and **abort if the user touched the machine** | Poll dropped, nothing partial sent | `resolve_guard.py:194`, `resolve_poller.py:30-35,191-205` | `test_resource_budget.py::ScanBudget`, `PreChecks::test_user_returns_mid_scan…` |
| 11 | **API allowlist**: every Resolve object is wrapped; any name not on the allowlist raises *before* the remote object is touched → breaker trips | Turn ends, breaker trips | `resolve_guard.py:211-258` | `test_api_allowlist.py` (16) |
| 12 | Post-check: page / project / current timeline unchanged across the scan. If they changed, the result is discarded. **The agent never "restores" the page.** If the user was idle the whole time, the agent is presumed responsible and the breaker trips | Discard / trip | `resolve_poller.py:156-172` | `Breaker::test_page_change_*` |
| 13 | Local data bounded: queue ≤ 5000 lines / 10 MB, flush ≤ 300 records or 20 s per turn, send backoff 60 s → 5 min with no request while it lasts, refused records dropped not retried (rejected.log ≤ 200 lines / 256 KB, AGENT_SEND_POLICY.md), log ≤ 1 MB, disk-full tolerated; every write goes under `~/.editortrack` (ExportLUT temp included) | — | `runner.py` (`post`, `_flush_queue`, `_send_backoff_*`), `run-agent.sh:15` | `QueueBounds`, `NoForeignWrites` (audit-hook proof), `test_send_queue` |

Removed on this branch, and impossible to turn back on by env:
`EDITORTRACK_POLL_RENDER` (GetRenderJobList/GetRenderJobStatus jumped the real workstation to
Deliver) and `EDITORTRACK_RESTORE_PAGE` (it called `OpenPage`, a UI change in itself; it could
also yank an editor back to the old page if they switched pages during a scan).
`resolve_poller.py:25-27`, `test_api_allowlist.py::test_removed_flags_stay_removed`.

`ExportLUT` (the LUT-based "graded" check from 85bf2c8) is **gated off**. It runs only with
`EDITORTRACK_ALLOW_EXPORT_LUT=1`; without it the agent counts with nodes (2 calls per clip).
The probes need `--dummy-project`, and the render section of `probe.py` needs `--unsafe-render`.

---

## 4. Hazard analysis (FMEA)

Columns: **L** = likelihood without the control (H/M/L) · **I** = impact (H = noticed by
editor/colorist or touches client work) · **Now** = mitigation in code on this branch ·
**Gap** = what remains · **Verify** = AUTO (CI test) / REAL (§7 step) · **State** = CLOSED
(by construction + test) / REAL-ONLY (needs §7 to close) / OPEN (blocker).

Ranked by the owner's rule: class **A** (could be noticed by an editor/colorist or touch
client work) first, then **P** privacy, **S** security, **O** operations.

### Class A — could be noticed / could touch client work (each one is a blocker until CLOSED)

| ID | Hazard | L | I | Now (file:line) | Gap | Required control | Verify | State |
|---|---|---|---|---|---|---|---|---|
| A1 | **A scripting call changes the UI**: page jump (real incident with `GetRenderJobList`), current timeline, playhead, viewer, open dialog | H (happened) | H | Default-deny allowlist `resolve_guard.py:35-145`; render poll and OpenPage removed `resolve_poller.py:25`; post-check discards and trips on change `resolve_poller.py:156-172` | The mock can't prove that an allowlisted **getter** has no side effects. `GetCurrentVideoItem`, `GetCurrentTimecode`, `GetNodeGraph` on a non-Color page and `GetTimelineByIndex` are the most suspicious | Allowlist (done) + per-version proof on a real machine | AUTO `test_api_allowlist.py` · REAL §7 S1–S3 | REAL-ONLY |
| A2 | **Selection / current clip changes** (e.g. getters that walk items moving the Color-page "current clip") | M | H | Only getters; nothing selects | The API can't read selection, so we can't self-check it | Screen-recording diff + colorist observation | REAL §7 S2 | REAL-ONLY |
| A3 | **Project modified**: undo entry, "unsaved" flag, autosave triggered, DB lock held, project or timeline loaded/switched | M | H | No Set/Load/Save/Create/Delete/Import/Export name can pass the guard; `GetTimelineByIndex` is used instead of switching timelines `resolve_poller.py:338` | Getters could still mark a project dirty in some version (unknown) | Idle-project soak: no undo history, no save prompt, Project Manager modified-time unchanged | REAL §7 S3 | REAL-ONLY |
| A4 | **Render queue / render affected**: a job added or removed, a render slowed down | H (render API caused the page jump) | H | Render APIs denied; skip while `IsRenderingInProgress` `resolve_poller.py:126` and on the Deliver page | The cost of `IsRenderingInProgress` itself during a render is unknown | Render time with vs without the agent ≤ 1 % / within noise | REAL §7 P3 | REAL-ONLY |
| A5 | **Playback drops frames** because a scan runs during 4K/8K playback | M | H | Skip while the playhead moves `resolve_poller.py:132-138`; skip while the user is active; chunk yield | **Whether `GetCurrentTimecode` updates during playback is unverified.** If it doesn't, the idle gate alone lets a scan run during hands-off review playback | Real playback test; if timecode doesn't move, add another signal (e.g. skip when Resolve is frontmost and the user has been idle < 10 min) | AUTO `PreChecks` · REAL §7 P1 | REAL-ONLY |
| A6 | **UI stutter / main-thread blocking** during a large-timeline scan (3000 clips ≈ 12 000 IPC calls) | M | H | Budget 1500 ms + 20k calls + > 5000 clips counts-only + 200-clip chunks with 10 ms yield + abort on user input `resolve_poller.py:30-35,191-205`; scan only after ≥ 5 s idle | Real per-call latency unknown (only data point: 309 clips = 156 ms on Windows ≈ 130 µs/call; at that rate 3000 clips = 1.6 s, **above the budget → never measured**, see §6) | Measure on real machines; owner chooses between budget, chunk size and incremental scans across polls (§9-D5) | AUTO `ScanBudget` · REAL §7 P2 | REAL-ONLY |
| A7 | **Resolve crash or hang** caused by the scripting connection (connect/disconnect every minute, agent killed mid-call, Resolve modal dialog, Resolve starting or quitting) | L–M | H | Process-per-turn (no long-lived connection), watchdog 90 s `safety.py:75`, skip when the page is unknown | Unknown how fusionscript behaves when the client dies mid-call or while a modal dialog is open | kill -9 drill ×50, dialog-open test, quit-during-scan test | REAL §7 C1–C4 | REAL-ONLY |
| A8 | **ExportLUT**: renders a grade, does GPU work, writes a file; effect on undo/cache/UI unknown | M | H | Gated off `resolve_guard.py:86`, `grade_detect.py:23,123`; temp file only in `~/.editortrack/tmp` | Unverified | Keep off; enable only per OS + version after `probe_grade.py --dummy-project` + §7 S1–S3 with it on | AUTO `test_every_flag_combination` · REAL | CLOSED while gated (feature off) |
| A9 | **Agent competes for CPU/RAM/IO/network** while Resolve plays or renders | L | M–H | nice 19 / idle IO / CPUQuota 20 % / MemoryMax 200 M (Linux) `service:11-16`, Nice + LowPriorityIO (mac), own priority `safety.py:86`; ~28 MB RSS per turn (mock); one ≈ 2 KB POST per minute | macOS and Windows have no hard memory cap; fusionscript RSS unknown | Real measurement thresholds §7 P4 | AUTO `NoForeignWrites`/`ScanBudget` · REAL | REAL-ONLY |
| A10 | **Writes into Resolve project/library/cache/media folders** | L | H | Every write goes under `~/.editortrack` (queue, cache, lock, lease, ExportLUT temp moved off `/tmp`) | — | Audit-hook proof of every `open`/rename/remove/mkdir in a full turn | AUTO `test_resource_budget.py::NoForeignWrites` (3) | CLOSED |
| A11 | **Data/master watch-folder walk** hits the SAN that grade rooms play from | M (if those stages are enabled) | H | Walk capped at 50 000 entries / 5 s `fs_collector.py:15,35` | Still IO on shared storage every minute | Prefer server-side watching (T0-c); never enable on grade/conform workstations | review | OPEN if enabled (not used on the 7 Resolve rooms) |
| A12 | **Probe tools run on a client project** (probe.py touched the render queue and OpenPage) | M | H | `--dummy-project` required, render section only with `--unsafe-render` `probe.py:21-26`, `probe_grade.py:22` | A human can still pass the flags | Runbook: probes only in §7 on dummy projects; remove probes from the kit installed on client rooms (§9-D9) | AUTO `test_probe_names_on_probe_allowlist` | CLOSED for scheduled runs; human-procedure for manual |
| A13 | OS idle sampler side effects (X11 connection, `xdotool`/`xprop` subprocess on Linux) | L | L | 3 s timeout per subprocess | Two subprocesses per minute on grade rooms | Measure; drop the frontmost window name (P1) and with it the subprocesses | REAL §7 P4 | REAL-ONLY (low) |

### Class P — privacy / client data (blockers for consent, phase 0–1)

| ID | Hazard | L | I | Now | Gap | Required control | Verify | State |
|---|---|---|---|---|---|---|---|---|
| P1 | Record carries the login `user` and the **frontmost window title** of any app `runner.py:81` | H | H (PDPA) | — | phase 1 not done | Send only `resolve_frontmost: bool`; drop `user` | AUTO (record-schema test, to add in phase 1) | OPEN |
| P2 | **Client names / shot codes / timeline names** leave the machine (`EXTRACT_SHOTS` on by default, project + timeline name) | H | H (NDA) | — | — | Shot codes off by default; hash or drop timeline names; data sheet lists every field | AUTO | OPEN |
| P3 | Screenshots / frame thumbnails (Track B) | — | H | `GetCurrentClipThumbnailImage` denied `resolve_guard.py:99`; no screenshot code exists | — | Separate consent + design; never in Track A | AUTO | CLOSED (not implemented) |
| P4 | Local copies of client data at rest (queue, grade cache with clip UIDs) | M | M | Bounded size; removed by uninstall | Not encrypted; readable by the same user | Accept (same user owns the project) or encrypt; state it in the data sheet | — | decision |

### Class S — security

| ID | Hazard | L | I | Now | Gap | Required control | State |
|---|---|---|---|---|---|---|---|
| S1 | **Leaked token**: the shared test token is in the public repo's history and is **still the live server token** (it is set in `~/.config/systemd/user/editortrack-server.service` on this host). Anyone on the LAN can post fake records | H | M (data integrity; can't reach workstations) | No token or server default left in tracked files (`make-env.sh`; AUTO `test_no_shared_token…`) | Live token not rotated; history not purged | Rotate now; per-host tokens; consider purging history | OPEN |
| S2 | **Unauthenticated read pages** (`/`, `/report`, `/inbox`, `/api/*`, new `/health`) show project names and users | H | H (client data) | — | phase 2 | Reverse-proxy auth (Caddy basic auth / SSO) | OPEN |
| S3 | **Update channel**: could a git push reach workstations? The server autodeploys from git every minute (`autodeploy@editortrack.timer`). Workstations got code as a manual zip | M | H | Agent code on workstations is never auto-updated. A compromised or buggy server can only grant or deny the lease: the agent parses nothing else from the server (`safety.py:252`), and its allowlist is local | Nothing prevents someone adding auto-update later | Policy + review rule: no auto-update of workstation code; signed release zip + checksum; MAINTENANCE.md §2 | CLOSED by design, keep by policy |
| S4 | Agent code writable by the workstation user (installed in `~/editortrack`); tampering would run with the user's rights | L | H | — | — | Install to a root-owned read-only dir (`/opt/editortrack`, `/Library/Application Support/editortrack`) + checksum manifest checked by install/verify | OPEN (decision §9-D7) |
| S5 | Privilege: the agent runs as the logged-in user, the same user as Resolve, with access to client media | — | M | No sudo; user-level units | It *could* read client files | Least privilege; can't separate the user while scripting needs the user's Resolve session | accept + document |
| S6 | Resolve "External scripting = **Network**" exposes Resolve control to the LAN | M | H | Docs say Local | Not checked automatically | Pre-flight in §7: verify Local; check `ss -ltnp` / `lsof -i` for Resolve scripting listeners | REAL-ONLY |
| S7 | `/agent/control` and `/ingest` share one token; anyone holding it can read leases (harmless) or flood ingest | M | L | 401 without a token | Per-host tokens, rate limits | phase 2 | OPEN (low) |

### Class O — operations / stability

| ID | Hazard | L | I | Now | Gap | Verify | State |
|---|---|---|---|---|---|---|---|
| O1 | Crash loop / retry storm | M | M | One turn per minute, exceptions → backoff 60 s → 1 h `safety.py:220`; uncaught errors become one log line `runner.py:235` | — | AUTO | CLOSED |
| O2 | Memory leak over weeks | L | M | Process per turn (fresh Python each minute); the mac loop is a shell `while` + sleep | Windows scheduler config not written | AUTO (RSS per turn) · REAL 7-day trend | CLOSED (Linux/mac) |
| O3 | Log/queue/cache disk growth | M | M | Log ≤ 1 MB, queue ≤ 10 MB/5000 lines, grade cache ≤ 8 timelines | Windows log rotation missing | AUTO `QueueBounds` | CLOSED (Linux/mac) |
| O4 | Overlapping runs (slow turn + timer) | M | M | flock/msvcrt lock `safety.py:134` | — | AUTO | CLOSED |
| O5 | Resolve not running / starting / quitting / hung / project switch mid-scan | H | M–H | `resolve_not_running`, `resolve_busy` (no page), post-check discards on project/timeline switch, watchdog | Real behaviour of `scriptapp` while Resolve starts or hangs | AUTO + REAL §7 C1–C4 | REAL-ONLY |
| O6 | Sleep / reboot / network down | H | L | Lease rides out ≤ 6 h; queue bounded; `Persistent=true` timer | Data gap beyond the lease (by design: fail closed) | REAL §7 C5–C6 | REAL-ONLY |
| O7 | Uninstall leaves something behind | M | M | `uninstall.sh` + `--verify` + `--dry-run` + `--purge` | Real launchd/systemd + Resolve prefs untouched | AUTO (stubbed) `test_deploy_kit.py::Uninstall` · REAL §7 U1–U3 | REAL-ONLY |
| O8 | No instant stop | — | H | Local `killswitch.sh on` (scheduler stopped immediately + file); system file; fleet: one value in `agent_control.json` (≤ 1 min); lease expiry when the server is unreachable | Drill not done on real machines | AUTO `KillSwitchScript`, server e2e · REAL §7 K1–K5 | REAL-ONLY |
| O9 | Version drift / no pinning / no rollback | M | H | Server lease requires the agent version on `allowed_versions`; Resolve/Python/OS version pins fail closed | Release packaging not built | MAINTENANCE.md §2 | partial |
| O10 | **Windows rooms**: no deploy kit, no scheduler config, lock/priority code untested | — | H | Code paths exist (`msvcrt`, `SetPriorityClass`) | Everything | Build + test a Windows kit before any Windows install | OPEN (blocker for Windows) |
| O11 | Silent agent nobody notices (lease off, breaker tripped, broken X11 idle) | H | M | Heartbeat block in every record; `/health`, `/api/health`; Telegram alerts on change (`server/health_alert.py`) | Alert timer not installed | AUTO `test_agent_health.py` | partial |

---

## 5. The Resolve API allowlist

Source of truth: `agent/resolve_guard.py`. Anything not listed is refused (default deny), even
a name starting with `Get`.

| Tier | Methods | Evidence |
|---|---|---|
| CORE (default poll) | `GetProjectManager GetCurrentPage GetProductName GetVersionString GetCurrentProject GetName GetUniqueId GetTimelineCount GetTimelineByIndex GetCurrentTimeline IsRenderingInProgress GetSetting GetStartFrame GetEndFrame GetTrackCount GetItemListInTrack GetMarkers GetCurrentVideoItem GetCurrentTimecode GetStart GetEnd GetNodeGraph GetCurrentVersion GetNumNodes GetNodeLabel GetLUT GetToolsInNode` | "observed" = used on conform-01 (Mac) 7–14 Oct with no page change reported; the rest "unverified" |
| EXTENDED (only with `EXTRACT_CLIP_META=1`, off) | `GetDuration GetLeftOffset GetRightOffset GetClipColor GetFlagList GetMediaPoolItem GetClipProperty GetMetadata GetVersionNameList GetColorGroup GetFusionCompCount` | unverified |
| GATED (env gate + real-machine proof) | `ExportLUT` (`EDITORTRACK_ALLOW_EXPORT_LUT=1`) | unverified, not a getter |
| PROBE only (`--dummy-project`) | `GetCurrentRenderMode GetTrackName GetSourceStartFrame GetSourceEndFrame GetProperty GetNodeCacheMode GetFusionCompNameList GetCurrentVersionName` | manual diagnostics |
| PROBE_UNSAFE (`--unsafe-render`) | `GetRenderJobList GetRenderJobStatus OpenPage` | known page jump |
| DENIED (listed with reasons) | ~95 names: render queue, OpenPage, Set*/Load*/Save*/Create*/Delete*/Import*/Export*, markers, grades, versions, stills, layout presets, Quit, Fusion, GetCurrentClipThumbnailImage, GetMediaStorage, … | |

**What the allowlist test proves** (`agent/tests/test_api_allowlist.py`, 16 tests, passing):
- *Static:* every agent module is parsed. Every Resolve method name the code can call
  (`obj.Name(...)`, `_safe(obj, "Name")`, `getattr(obj, "Name")`, probe specs) is on the
  allowlist. No denied name appears in runtime code. No method name is computed at runtime
  outside the two wrappers. `scriptapp()` results go straight into `guard()`. The runtime
  import closure does not include the probes. `activity_sampler` never touches Resolve. The
  scanner is itself tested against a synthetic violation.
- *Dynamic:* with the mock Resolve logging every name it receives, the poller runs under all
  32 flag combinations (plus the removed legacy flags set to on), with an exception injected at
  **every** call position and a `None` return from every method. No denied or unlisted name
  ever reaches the mock, the page and current timeline never change, and ExportLUT is called
  only when its gate is on. A simulated future bug (`GetRenderJobList` added to a helper) is
  refused before reaching Resolve and trips the breaker.
- *Not proven:* that the allowlisted getters have no side effects inside real Resolve. Only
  §7 can show that, per version.

---

## 6. Measurements on mocks

Fresh process per measurement (`python3 -m tests.test_resource_budget --report`). The mock
busy-waits to simulate per-call IPC latency. "Nodes + shots" path (the default with the LUT
gate off): 4 calls per clip.

| Clips | Simulated latency per call | API calls per poll | Resolve-side time (guard) | Python heap peak | Process peak RSS |
|---|---|---|---|---|---|
| 300 | 0 | 1 237 | 4 ms | 0.06 MB | 22.8 MB |
| 3 000 | 0 | 12 037 | 38 ms | 0.51 MB | 24.6 MB |
| 3 000 | 50 µs | 12 037 | 648 ms | 0.51 MB | 24.4 MB |
| 3 000 | 130 µs (≈ the Windows data point) | 12 037 | **1 611 ms (> 1500 ms budget → aborted in production)** | 0.51 MB | 24.4 MB |

A full turn with 3000 clips (lease, scan with 15 yields, POST to a local server) took 206 ms
wall time at 0 latency, peak RSS 27.7 MB, record size ≈ 1.7 KB. Real RSS adds fusionscript
(unknown; §7 P4 measures it).

Reading: the agent's own cost is trivial. The open question is **Resolve's side**: 12 000
calls is 12 000 requests served by Resolve. At the only latency we have, a 3000-clip timeline
needs 1.6 s of Resolve time per poll. The current default budget then drops every poll for
timelines above ~2800 clips (they become backoff, never measured). The owner chooses (§9-D5):
a larger budget, scanning incrementally across polls (e.g. 500 clips per poll and a
rotating cursor), or counts only for large timelines.

---

## 7. Real-machine verification protocol (extends NEXT_STEPS phase 3)

**Setup.** One **Mac conform** and one **Linux grade** workstation, each running the
production Resolve version, with **dummy projects only** (no client media, no client project
open in any library on that machine during the test, or a separate project library). Agent
from a tagged release with the env from `deploy/stations/<host>.sh`, plus
`EDITORTRACK_RESOLVE_VERIFIED` = the exact version under test. Server lease
`allow_resolve:true` for these two hosts only. Dummy material: (a) a 30-clip timeline, (b) a
300-clip timeline, (c) a 3000-clip timeline (built by EDL/XML import, never with scripting on
a client project), (d) a 4K and an 8K timeline that play in real time with the agent off,
(e) a render preset of ~10 minutes.

Each step records: date, host, Resolve/OS/Python versions, agent version, result, evidence
file (screen recording / CSV / screenshot). **Every PASS threshold must hold; one FAIL stops
the protocol** (fleet kill → fix → new automated test → restart from S1).

### S — UI and project state (class A1–A3)

| Step | Procedure | PASS threshold |
|---|---|---|
| S0 | Preferences → System → General → External scripting = **Local**; `ss -ltnp` (Linux) / `lsof -iTCP -sTCP:LISTEN` (mac) shows no Resolve scripting port on a non-loopback address | Local; nothing listening on a LAN address |
| S1 | 8 h working-hours soak per machine, agent at 60 s, operator works normally on dummy projects on every page (Media, Cut, Edit, Fusion, Color, Fairlight, Deliver). **Full-screen recording** (macOS screen recording / OBS) + agent log; afterwards compare every agent turn's timestamp against the recording ±2 s | **0** page / timeline / playhead / viewer / dialog / focus changes at agent turns; `source.reason` never `forbidden_call`; breaker never tripped |
| S2 | Colorist session, 2 h on the Color page: select clips, use versions, compare stills, grade groups, with the agent forced to scan every turn (`EDITORTRACK_SCAN_MIN_IDLE_SEC=0` **on the dummy project only**) | 0 changes to selected clip, current node, version, gallery, viewer wipe; colorist reports nothing |
| S3 | Idle-project soak: open a freshly saved dummy project, touch nothing, agent on for 8 h, then check Edit → History (undo list), the project's modified marker / save prompt on close, and the Project Manager modified time | No undo entries, **no save prompt**, modified time unchanged; same with `EXTRACT_CLIP_META=1` and (only if ExportLUT is wanted) `ALLOW_EXPORT_LUT=1` |
| S4 | Run `python3 probe.py --dummy-project` and `probe_grade.py --dummy-project` once per Resolve version, recording the page before and after | Page unchanged (probe_grade prints it); save the reports |

### P — performance (class A4–A6, A9)

| Step | Procedure | PASS threshold |
|---|---|---|
| P1 | Playback: 4K and 8K timelines, 10 × 2 min each, agent off vs on (alternating), hands off the keyboard (review-style playback). Resolve's dropped-frame indicator + recording; agent log must show `playback` skips | **0 additional dropped frames**; agent `guard.calls` ≤ 12 during playback turns; if any scan happened during playback (timecode didn't move) → FAIL → design change (A5) |
| P2 | Large timeline: 300 and 3000 clips, user idle, agent on; record `guard.ms`, `guard.calls`, `timeline.scan_ms` for 50 turns; operator scrubs immediately after a scan starts | p95 `guard.ms` ≤ 1500 (or the owner-chosen budget); no scan longer than one chunk after the user's input (`user_returned`); **operator notices no lag** |
| P3 | Render the ~10 min preset 3× agent off, 3× agent on (Deliver page, so the agent skips) | Mean render time delta ≤ 1 % and within 2σ of the off runs |
| P4 | 1 h normal use, sample every 5 s: agent CPU (`pidstat -C python`, mac `top -l`), peak RSS per turn (heartbeat `rss_mb`), disk under `~/.editortrack`, network bytes | Average CPU < 0.5 % of one core, any single turn < 2 s CPU; RSS < 100 MB; `~/.editortrack` < 15 MB; < 5 KB/min network |
| P5 | 7-day trend (from `/api/health`) | No RSS or turn-time growth; queue drains; zero alerts |

### C — stability (A7, O5, O6)

| Step | Procedure | PASS threshold |
|---|---|---|
| C1 | `kill -9` the agent at random points of a 3000-clip scan, 50 times | Resolve stays responsive, no error dialogs, no crash; next turn normal |
| C2 | Open a modal dialog (Project Settings, a Save dialog) and leave it open for 5 turns | No hang beyond the 90 s watchdog; the dialog is unaffected; Resolve responsive after it is closed |
| C3 | Quit Resolve during a scan; start Resolve and keep the splash/loading for 3 turns; Resolve not running for 1 h | No crash on quit; `resolve_busy`/`resolve_not_running` skips; no zombie processes |
| C4 | Switch project and timeline mid-scan (operator) | Turn discarded (`state_changed_during_scan`, `suspect_agent:false`); no breaker trip; nothing restored |
| C5 | Sleep/wake (mac) and reboot (both) | Agent resumes ≤ 2 min after login; no duplicate turns; no backlog storm |
| C6 | Server stopped 3 min, then 1 day | Queue fills and drains ≤ 300 records/turn; after > lease time the agent goes silent, and resumes when the server returns |

### K, U, R — kill switch, uninstall, rollback drills

| Step | Procedure | PASS threshold |
|---|---|---|
| K1 | Local: `sh deploy/killswitch.sh on` | Scheduler stopped at once; no Resolve call afterwards (log); `status` shows DISABLED |
| K2 | System file (`sudo touch /etc/editortrack/DISABLED` or the mac path) | Next turn silent (≤ 60 s) |
| K3 | Fleet: set `"enabled": false` in `agent_control.json` | Every test agent silent within 60 s (heartbeats stop) |
| K4 | Server unreachable longer than the lease | Agent silent at lease expiry |
| K5 | Stopwatch: from "owner decides" to "agent gone" with uninstall on both machines | ≤ 5 min per machine, by someone other than the developer, from the runbook alone |
| U1 | Before install: snapshot Resolve prefs (`~/Library/Preferences/Blackmagic Design/`, `~/.local/share/DaVinciResolve/configs` or the install's `configs`), crontab, launchd/systemd unit list, `~` file list. After install + 1 day + `uninstall.sh` + reboot: diff | `uninstall.sh --verify` passes; Resolve prefs byte-identical; no new units; only expected files differ (none) |
| U2 | `uninstall.sh --purge` | Agent directory gone too |
| R1 | Install release N, upgrade to N+1 (allowed_versions [N+1]), roll back to N with one command (reinstall the N zip + allowed_versions [N]) | Rollback ≤ 5 min; no Resolve impact; health shows N |
| R2 | Upgrade Resolve (or set a fake unverified version) | Agent skips with `resolve_version_unverified` until the owner adds the version |

**Exit criteria for phase 3:** every step PASS on both machines, the colorist/editor sign that
they noticed nothing, the owner signs §8 stage R2.

### What cannot be verified on this Linux server

Everything that needs real Resolve: whether getters have side effects; IPC latency and
main-thread contention; GPU, playback and render interaction; `GetCurrentTimecode` during
playback; ExportLUT; fusionscript behaviour when killed, during modal dialogs, or when Resolve
starts or hangs; External scripting port exposure. Also: macOS launchd/TCC/App Nap/sleep;
real systemd user sessions with X11 (`DISPLAY`/`XAUTHORITY`, Wayland); Windows entirely (no
kit; the `msvcrt` lock and background-priority code have never run); X11/macOS idle
accuracy; fusionscript RSS. The mock proves our code's behaviour, not Resolve's.

---

## 8. Staged rollout with sign-off and instant removal

Each stage widens only after a **clean soak** and an **owner sign-off** recorded in
`docs/research/SIGNOFF.md` (date, stage, hosts, evidence links, signature). "Clean" means:
0 class-A events (no `forbidden_call`, no `state_changed_during_scan` with
`suspect_agent:true`, no tripped breaker), 0 operator reports, `/health` all green for the
whole soak, and the kill-switch drill re-run at the end of the stage.

| Stage | Where | Agent mode (server lease) | Soak | Widen when |
|---|---|---|---|---|
| R0 | CI only | — | every push + nightly | `./run_tests.sh all` green on Linux + macOS runners |
| R1 | 1 spare / non-client machine per OS, dummy projects | `allow_resolve:false` (OS sampler + heartbeat only) for 3 days, then `true` | 5 working days | clean + §7 S/P subset |
| R2 | Mac conform + Linux grade (phase 3), dummy projects | `allow_resolve:true`, these hosts only | 1 week | **all of §7 PASS**, the workers sign "not noticed", owner signs |
| R3 | Consent obtained (phase 0–2 done) → **one real room**, client work | first `allow_resolve:false` for 1 week (if T0-a/b covers progress, **stop here**: that is the target), then `true` | 1 + 2 weeks | clean, director confirms the data is useful, owner signs |
| R4 | Remaining rooms, one at a time | as R3 | 1 week each | clean, owner signs per room |

**Any incident at any stage:** (1) fleet kill switch (`agent_control.json` → all
`enabled:false`), (2) uninstall on the affected room if the owner asks, (3) post-mortem, (4) a
regression test in `./run_tests.sh safety`, (5) restart from the previous stage.

**Instant, tested removal**

| Scope | How | Time to effect | Tested by |
|---|---|---|---|
| One machine, now | `sh deploy/killswitch.sh on` (stops the scheduler + kill file) | immediate | `test_deploy_kit.py::KillSwitchScript`, §7 K1 |
| One machine, for good | `sh deploy/uninstall.sh` then `--verify` (add `--purge` for the code) | < 1 min | `test_deploy_kit.py::Uninstall` (3), §7 U1–U2 |
| All machines | `agent_control.json`: `"default":{"enabled":false}` and remove the host entries, or empty `allowed_versions` | ≤ 1 turn (60 s) | `server/tests/test_agent_health.py::EndToEnd`, §7 K3 |
| Server unreachable / gone | lease expires | ≤ lease (6 h default; owner may shorten) | `Lease::test_lease_survives_short_outage_then_expires`, §7 K4 |
| Machine-wide (all users) | `/etc/editortrack/DISABLED` or `/Library/Application Support/editortrack/DISABLED` | ≤ 60 s | `KillSwitch` tests, §7 K2 |

---

## 9. Decisions needed from the owner

| # | Decision | Options / recommendation |
|---|---|---|
| D1 | **Architecture**: is a no-workstation design (T0-a project DB replica / T0-b project backups) acceptable if it is fresh enough? | Recommend: ask facility IT about the project server and backups first. If available, prototype T0 on a copy before any further T1 work |
| D2 | **Lease policy**: the agent is silent unless the server explicitly grants it (per host + per version). Lease length 6 h (outages longer than this leave a data gap) | Keep fail closed; choose 6 h or shorter |
| D3 | **ExportLUT** stays off (accuracy of "graded" vs. safety) until §7 S3 passes with it on | Keep off; accept the node-count criterion meanwhile |
| D4 | **Scan only when the user has been idle ≥ 5 s**, plus abort on input. Progress may lag while someone works continuously | Accept (it can't be noticed); revisit after P2 |
| D5 | **Large timelines**: budget 1500 ms vs ~1.6 s needed for 3000 clips at the measured latency | After P2, pick: a larger budget, incremental scans (N clips per poll), or counts only |
| D6 | **Rotate the server token now** (it is public and still live); per-host tokens; read pages behind auth (phase 2) | Required before R2 |
| D7 | Install location: root-owned read-only (`/opt/editortrack`) + checksum vs the current `~/editortrack` | Recommend root-owned for R3+ |
| D8 | Platform pin strictness: Python major.minor always; OS pin (blocks after kernel/macOS updates until re-verified) | Pin Python; pin the macOS major; Linux kernel no pin (the Resolve version pin covers the risk) |
| D9 | Ship `probe.py`/`probe_grade.py` to client rooms? | No: the kit for R3+ omits them |
| D10 | **Windows rooms**: no kit or test exists | No Windows installs until a Windows kit + CI job exist |
| D11 | Who receives alerts (Telegram chat), who may edit `agent_control.json`, who signs stages | Owner + one deputy |
| D12 | Data minimisation (phase 1): drop `user`, the frontmost window title and shot codes by default; keep the project name for the booking join | Required before R3 |

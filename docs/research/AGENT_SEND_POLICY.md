# Agent send policy: what the agent does with each server answer (ET-5, agent 0.9.1-safety)

Problem fixed: the server answers 400 when a record is unusable (branch `night/f3-tz-thai-names`, `explain.record_problem`).
Up to 0.9.0-safety the agent treated every non-2xx answer as "server offline" and re-queued the record. A refused
record at the head of the bounded offline queue was then retried forever and every record behind it waited, until the
5000-line cap pushed records out from the old end. The fix is in the sender and queue (`agent/runner.py`) only. It adds
no Resolve call: `resolve_guard.py` and the API allowlist test are unchanged and still pass.

## Policy per answer

| Answer | Meaning | What the agent does |
|---|---|---|
| 2xx | delivered | next record; clears the send backoff and the refusal streak |
| **400 with our refusal marker** | `explain.record_problem` refused this record (bad host, ts or type). Marker: the `X-Editortrack-Reject: <reason>` header, which `/ingest` sets from this branch on, or the body `{"detail": "record ใช้ไม่ได้ (…"}` that the F3 server already sends | **final**: dropped, counted, logged, after every URL has been tried |
| 400 without the marker | a proxy, a wrong port ("plain HTTP sent to HTTPS port") or a foreign host behind a backup URL | keep, next URL, back off |
| 413, 422 | only a proxy or a foreign server can send these (`/ingest` reads the raw body and has no size limit) | keep, next URL, back off |
| 401, 403 | token or proxy authorisation (token rotation, wrong env) | keep, back off |
| 404, 405, 410 | wrong URL, old server, proxy route | keep, back off. As final it would drop **every** record |
| 408, 425 | timeout or too early: transient | keep, back off |
| 409 | conflict: server state, not record content (our server never sends it) | keep, back off |
| 429 | rate limited | keep, back off, honour `Retry-After` up to the cap |
| any other 4xx | unknown: in our setup it can only come from a proxy or config | keep, back off (fail closed means keep the data) |
| 3xx | redirect (http to https, proxy) | **not followed** (urllib would resend the POST as a GET, without the body, carrying the Authorization header); keep, back off |
| 5xx (bad JSON is a 500 too), timeout, refused, DNS | server down or failing | keep, back off |

A record is final only when **our** server says so about **this** record (`runner._refusal`). Any answer that could
describe the server, a proxy or the configuration is retried, because a "final" reading would silently empty the queue.

**Several URLs** (`EDITORTRACK_SERVER=a,b`): every URL is tried until one delivers. A record is final only when at
least one URL refused it with the marker and no URL took it. A URL that refuses everything therefore cannot stop a
healthy backup from delivering (review H1, tested).

## What "final" does

- The record leaves the queue, and the records behind it are sent in the same flush.
- `~/.editortrack/rejected.json` holds:
  - `total`: refused by the server, cumulative, never reset.
  - `local`: dropped by the agent's own check before sending.
  - `streak`.
  - `last`: `at`, `status` and `reason`. For a server refusal the reason is the marker plus the server's text,
    cut to 300 characters (`bad_type:agent …`).
- `~/.editortrack/rejected.log` gets one JSON line per refused record: time, status, reason and the record itself
  when it is ≤ 8 KB, otherwise its length and sha256. The log is bounded: newest 200 lines and 256 KB at most
  (`EDITORTRACK_REJECT_LOG_MAX_LINES` / `_MAX_BYTES`), and a write failure is ignored.
- The **next heartbeat** carries `agent.rejected` (server refusals), `agent.rejected_local`, `agent.reject_hold`
  and `agent.send_fails`. A malformed value in a state file reads as 0 and never raises.
  `/api/health` shows `rejected`, `rejected_24h` (the increase over 24 h; a counter that drops after a reinstall
  counts from 0) and a warning: "server refused N record(s) in 24 h". An old agent shows `rejected: null`.

**Reject hold:** 10 final refusals in a row with no 2xx between them (`EDITORTRACK_REJECT_HOLD_STREAK`) means the
server refuses everything. That is a server regression, not a bad record. In that state the agent stops dropping:
a refused record is kept and the send backoff starts. Any later 2xx ends the hold. At most 10 records go to
`rejected.log` before the hold, and they can be recovered from there. The owner signs off on that loss.

**A hold shows on the server as silence.** No record is delivered during a hold, so the server sees the host as
"silent N min". `reject_hold: true` reaches `/api/health` only on the record that ends the hold. During the hold,
look at the local `~/.editortrack/rejected.json` (`streak` ≥ 10).

## Retry without storms

- One sender backoff state, `~/.editortrack/send_backoff.json`. It is separate from the Resolve backoff
  (`backoff.json`), so a server outage never gates Resolve and a Resolve error never gates sending.
- First failure: wait 60 s, then double each time up to **300 s** (`EDITORTRACK_SEND_BACKOFF_BASE_SEC` / `_MAX_SEC`).
  The cap stays below the server's 10-minute "silent" warning, so a short outage does not leave a false "silent"
  warning after recovery.
  `Retry-After` (429/503) lengthens the wait up to the cap and never shortens it.
- **While the backoff lasts, the turn makes no `/ingest` request at all.** The record is queued and the lease GET
  happens as before. After an outage, delivery therefore starts at most 5 min after the server returns.
- A flush stops at the first retryable failure and keeps that record and the rest. Each turn sends at most
  `FLUSH_MAX` (300) requests within `FLUSH_BUDGET_SEC` (20 s), with refused records counted in both. With a 5 s
  HTTP timeout, a hung server costs one timeout per URL per turn.
- A corrupt `send_backoff.json` never blocks: it is ignored and the count restarts at 1. A `next_at` further ahead
  than the cap (the clock went back) is ignored as well.

## No naive timestamp leaves the agent

`build_record` writes `datetime.now(timezone.utc).isoformat()`, which ends in `+00:00`. Before a record is sent or
queued, `post` checks that `ts` is ISO with a **zero UTC offset**. The flush checks every queued line the same way.
A record that fails is never sent or queued. It is dropped locally, counted in `local` and logged with reason
`ts_without_offset`, `ts_not_utc`, `bad_ts`, `no_ts` or `bad_json`. A trailing `Z` is read as `+00:00` on every
Python. These local drops do not count towards the hold streak. The agent never guesses a zone.

The check is deliberately stricter than the server, which accepts any offset: the agent refuses `+07:00`. Every
agent writes UTC, so a non-UTC ts can only mean a bug.

**Known limit:** the queue is rewritten at the end of a flush. If the 90 s watchdog kills a turn mid-flush, the next
turn re-sends the records that were already delivered; the server keeps a duplicate and its dedupe drops it on read.
Records that were already refused are refused and counted again. Both cases are rare and bounded by `FLUSH_MAX`.

## Tests

- `agent/tests/test_send_queue.py` (marker `safety`) uses a fake server scripted per request:
  - A marked 400 drops one queued record while the records behind it are delivered in order.
  - A 400 without the marker is kept.
  - 401/403/404/405/408/409/410/413/415/422/425/429/500/502/503/504 keep the record, start the backoff and do
    not flush.
  - A primary URL that refuses everything does not stop a healthy backup (15 turns, nothing dropped).
  - Odd values in the state files never raise.
  - A 302 is not followed.
  - During the backoff no request is made; the backoff doubles; `Retry-After` is honoured and capped; the queue drains
    in order once the server returns.
  - A failure mid-flush keeps the rest.
  - A server that hangs costs one timeout per turn.
  - The reject hold works.
  - Requests stay bounded when every record is refused.
  - `rejected.log` is bounded by lines and bytes and stores a big record as a hash.
  - Naive, +07:00, garbage and missing ts are refused locally.
  - The multi-URL cases.
- `server/tests/test_agent_health.py`:
  - The `rejected` and `rejected_24h` rows.
  - The real FastAPI `/ingest` and the real agent `runner.main()` end to end: one queued record gets a real 400 and
    is dropped, the next one is delivered, and `/api/health` shows `rejected: 1`.

## Rollout (staged; nothing is installed by this branch)

`AGENT_VERSION` is now **`0.9.1-safety`**. The server pins versions, so an agent of this version gets no lease (and
stays silent) until `agent_control.json` lists it. The server-side change, the `/api/health` fields, only reads
optional heartbeat fields and is safe with old agents. It can merge and deploy before any workstation changes.

1. **Stage 0 (done here):** CI only. `./run_tests.sh all` passes on this branch.
2. **Stage 1: phase-3 test day only**, on the two DUMMY test workstations from
   `editortrack-testday/docs/testday/CHECKLIST.md`, never on an editor's working machine:
   - Use 0.9.1-safety as the test-day agent. For the R1 drill it is release N+1, with 0.9.0-safety as N.
   - Server admin: add `"0.9.1-safety"` to `allowed_versions` **for the test hosts only** (`hosts` entries; the
     default stays disabled).
   - Order: the server with this branch deploys first, for the `X-Editortrack-Reject` header. The F3 body text
     alone is also recognised, so the order is not critical. An older server's 400 (the old "ต้องมี host และ ts"
     text, without the "record ใช้ไม่ได้ (" prefix) is retried, never dropped.
3. **Stage 2, after phase-3 exit:** the normal MAINTENANCE.md §2.4 path: spare machine for 2 days, one room for
   1 week, then one room at a time.

**Rollback:** remove `"0.9.1-safety"` from `allowed_versions`, so the agent is silent on its next turn. Then
reinstall 0.9.0-safety (CHECKLIST R1). The state files `rejected.json`, `rejected.log` and `send_backoff.json` are
ignored by 0.9.0 and removed by `uninstall.sh` with the rest of `~/.editortrack`.

### How to verify on the test day (proposed step **C7**, for the kit owner to add to CHECKLIST.md §3 C)

Run it on the DUMMY machine during or right after **C6** (server stopped 3 min). The test lead and the server admin
do it together.

1. Before: the `/api/health` row for the host shows `version 0.9.1-safety` and `rejected: 0`. `null` means an old
   agent: stop.
2. Stop the agent so that nothing races the queue file: `sh ~/editortrack/agent/deploy/killswitch.sh on` (K1).
   Make sure at least one record is queued; C6's outage leaves some.
3. Append one record that the server refuses but the agent accepts. It is a good UTC ts with a bad `agent` field,
   which gives `bad_type:agent`:

   `printf '{"host":"%s","ts":"%s","agent":"c7-refused"}\n' "$(hostname)" "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" >> ~/.editortrack/queue.jsonl`

4. `killswitch.sh off`. Let 2 or more turns run while the server is still down, so that good records queue up
   **behind** the bad one. Then the server admin starts the server.
5. **PASS** when, within the backoff (`next_at` in `~/.editortrack/send_backoff.json`, at most 5 min) plus 2 turns:
   - The queue drains: `~/.editortrack/queue.jsonl` is gone or empty.
   - `~/.editortrack/rejected.log` has exactly one line with `"status": 400` and `bad_type:agent`.
   - `rejected.json` has `total` 1 and `streak` 0.
   - The records queued behind the bad one are in the server's day file (grep host, ts after the injected line).
   - `/api/health` shows `rejected: 1`, `rejected_24h: 1` and the warning.
   - No turn sent anything to `/ingest` while the server was down. Nothing reached the server, and
     `send_backoff.json` `fails` matches the number of turns that tried.
6. **FAIL** if the queue stays with the bad line at its head, if a good record is missing, if `rejected` is wrong, or
   if the operator noticed anything. That stops the protocol under §1.
7. P4 still applies: `~/.editortrack` stays under 15 MB, and the new files are at most about 260 KB.

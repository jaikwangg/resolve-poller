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
| **400** | `record_problem`: bad host, ts or type, or unreadable JSON | **final**: dropped, counted, logged |
| **413** | this record is too large | **final** (the same bytes will never fit) |
| **422** | request validation of this record | **final** |
| 401, 403 | token or proxy authorisation (token rotation, wrong env) | keep, back off |
| 404, 405, 410 | wrong URL, old server, proxy route | keep, back off. As final it would drop **every** record |
| 408, 425 | timeout or too early: transient | keep, back off |
| 409 | conflict: server state, not record content (our server never sends it) | keep, back off |
| 429 | rate limited | keep, back off, honour `Retry-After` up to the cap |
| any other 4xx (411, 415, 431, …) | unknown: in our setup it can only come from a proxy or config | keep, back off (fail closed means keep the data) |
| 3xx | redirect (http to https, proxy) | **not followed** (urllib would resend the POST as a GET, without the body, carrying the Authorization header); keep, back off |
| 5xx, timeout, refused, DNS | server down or failing | keep, back off |

The final set is an allowlist (`runner.FINAL_STATUS`), not "all 4xx except …". A status is final only when the
answer is about this record's content. When the answer could describe the server, proxy or configuration, a "final"
reading would silently empty the queue.

**Several URLs** (`EDITORTRACK_SERVER=a,b`): they all point at the same server, so a final answer from any one of them
is final. A non-final answer moves on to the next URL, as before.

## What "final" does

- The record leaves the queue, and the records behind it are sent in the same flush.
- `~/.editortrack/rejected.json` holds `total` (cumulative, never reset), `streak` and `last` (`at`, `status`,
  `reason`). For a server refusal the reason is the server's error text, cut to 300 characters (`bad_type:agent`, …).
- `~/.editortrack/rejected.log` gets one JSON line per refused record: time, status, reason and the record itself
  when it is ≤ 8 KB, otherwise its length and sha256. The log is bounded: newest 200 lines and 256 KB at most
  (`EDITORTRACK_REJECT_LOG_MAX_LINES` / `_MAX_BYTES`), and a write failure is ignored.
- The **next heartbeat** carries `agent.rejected` (the total), `agent.reject_hold` and `agent.send_fails`.
  `/api/health` shows `rejected`, `rejected_24h` (the increase over 24 h; a counter that drops after a reinstall
  counts from 0) and a warning: "server refused N record(s) in 24 h". An old agent shows `rejected: null`.

**Reject hold:** 10 final refusals in a row with no 2xx between them (`EDITORTRACK_REJECT_HOLD_STREAK`) means the
server refuses everything. That is a server regression, not a bad record. In that state the agent stops dropping:
a refused record is kept and the send backoff starts. Any later 2xx ends the hold. At most 10 records go to
`rejected.log` before the hold, and they can be recovered from there.

## Retry without storms

- One sender backoff state, `~/.editortrack/send_backoff.json`. It is separate from the Resolve backoff
  (`backoff.json`), so a server outage never gates Resolve and a Resolve error never gates sending.
- First failure: wait 60 s, then double each time up to 900 s (`EDITORTRACK_SEND_BACKOFF_BASE_SEC` / `_MAX_SEC`).
  `Retry-After` (429/503) lengthens the wait up to the cap and never shortens it.
- **While the backoff lasts, the turn makes no `/ingest` request at all.** The record is queued and the lease GET
  happens as before. After an outage, delivery therefore starts at most 15 min after the server returns.
- A flush stops at the first retryable failure and keeps that record and the rest. Each turn sends at most
  `FLUSH_MAX` (300) requests within `FLUSH_BUDGET_SEC` (20 s), with refused records counted in both. With a 5 s
  HTTP timeout, a hung server costs one timeout per URL per turn.
- A corrupt `send_backoff.json` never blocks: it is ignored and the count restarts at 1. A `next_at` further ahead
  than the cap (the clock went back) is ignored as well.

## No naive timestamp leaves the agent

`build_record` writes `datetime.now(timezone.utc).isoformat()`, which ends in `+00:00`. Before a record is sent or
queued, `post` checks that `ts` is ISO with a **zero UTC offset**. The flush checks every queued line the same way.
A record that fails is never sent or queued. It is dropped locally, counted and logged with reason
`ts_without_offset`, `ts_not_utc`, `bad_ts`, `no_ts` or `bad_json`. These local drops do not count towards the
hold streak. The agent never guesses a zone.

## Tests

- `agent/tests/test_send_queue.py` (marker `safety`) uses a fake server scripted per request:
  - Each of 400, 413 and 422 drops one queued record while the records behind it are delivered in order.
  - 401/403/404/405/408/409/410/415/425/429/500/502/503/504 keep the record, start the backoff and do not flush.
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
5. **PASS** when, within the backoff (`next_at` in `~/.editortrack/send_backoff.json`, at most 15 min) plus 2 turns:
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

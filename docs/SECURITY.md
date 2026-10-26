# editortrack server security (phase 2) and token rotation runbook

Status 2026-10-26: code on branch `night/security-min`, **not deployed**. The live server
(`editortrack-server` user unit on this host, `0.0.0.0:8000`) still runs main `5790713` with the
shared token that was published in the public repo. Nothing in §3 has been run; every step there
waits for the owner.

## 1. What the code does now

| area | behaviour | config |
|---|---|---|
| machine auth (`POST /ingest`, `GET /agent/control`) | one token per workstation; the server keeps only `sha256:` hashes in a token map; constant-time compare; token valid only for its own `host` (record `host` / `?host=` must match, else 403); unknown, missing, short (<24 chars) or revoked token -> 401; the old shared token is on a hard-coded revoked list (by hash) | `EDITORTRACK_TOKEN_MAP` (JSON, mode 600). `EDITORTRACK_TOKEN` is ignored |
| read auth (every other route: `/`, `/report`, `/inbox`, `/projects`, `/deck`, `/health`, `/api/*`, unknown paths) | HTTP Basic (browser login prompt) or `Authorization: Bearer <read token>`; machine tokens never grant read; `/docs` `/redoc` `/openapi.json` removed | `EDITORTRACK_READ_USERS` (JSON, mode 600); `EDITORTRACK_READ_AUTH=required` (default) or `off` (only behind an authenticating proxy on 127.0.0.1) |
| fail closed | map/users file unset, missing, not JSON or readable by group/other -> nobody gets in (401), warning logged once | — |
| open route | `/livez` -> `{"ok": true}` only, for uptime monitors | — |
| data dir | `EDITORTRACK_STORE`, default `/srv/editortrack` (was `data` inside the git checkout); created mode 700; day files created mode 600 | `EDITORTRACK_STORE` |
| retention | `server/retention.py`: deletes `YYYY-MM-DD.jsonl` older than N days, nothing else; `--dry-run`; `--scrub-personal` rewrites kept files without `user`/frontmost app fields | `EDITORTRACK_RETENTION_DAYS` (default 90, min 1) |
| data minimisation | agent no longer sends `user`, `activity.frontmost_name/bundle` (only `resolve_frontmost` and `locked` as booleans); `EDITORTRACK_EXTRACT_SHOTS` off by default; LUT names never collected; `/ingest` also strips those fields from older agents before writing | `docs/DATA_SHEET.md` (a test keeps the record keys equal to its list) |

Files are re-read when they change, so adding/revoking a host or reader needs no restart.

## 2. Managing tokens (`server/tokens.py`)

Secrets are 32 random bytes (`secrets.token_urlsafe(32)`), printed once, stored only as a hash.

```sh
cd ~/github/editortrack/server
export EDITORTRACK_TOKEN_MAP=~/.editortrack/tokens.json EDITORTRACK_READ_USERS=~/.editortrack/readers.json
python3 tokens.py host-add grade-01          # prints the agent token once -> agent deploy/env.sh EDITORTRACK_TOKEN
python3 tokens.py host-rm  grade-01          # revoke now
python3 tokens.py hosts                      # list (no secrets)
python3 tokens.py reader-add director        # Basic-auth password for the browser, printed once
python3 tokens.py reader-add pms-dash --kind token   # read token for scripts (Authorization: Bearer)
python3 tokens.py readers
```

The host name in the map must equal what the agent reports: on the workstation run
`python3 -c 'import socket; print(socket.gethostname())'` (a Mac can report `name.local`).
Install the agent token with `TOKEN=<value> SERVER=http://<server>:8000/ingest sh agent/deploy/make-env.sh`
(make-env.sh has no default token since the safety branch).

## 3. PREPARED live switch (owner runs it; nothing here has been executed)

Current live state (read on 2026-10-26, values not copied here):

- `~/.config/systemd/user/editortrack-server.service`: `Environment=EDITORTRACK_TOKEN=<old shared token>`,
  `EDITORTRACK_STORE=/home/worakij/github/editortrack/server/data` (mode 775, one file `2026-10-14.jsonl`, 21 KB),
  `EnvironmentFile=-%h/.editortrack/server.env` (ERP settings only, mode 600), uvicorn on `0.0.0.0:8000`.
- autodeploy `autodeploy@editortrack` follows `origin/main` in `~/github/editortrack` and runs
  `systemctl --user restart editortrack-server.service`. It **skips while the checkout is dirty**
  (uncommitted `NEXT_STEPS.md` today), so merging to main does not deploy until that is resolved.
- No agent has sent a record since 2026-10-14; no agent is installed on a real machine.

Why the order below is safe: the new variables are ignored by the old code, and the new code fails
closed (401) if they are missing, so no step can expose data; the worst case is "pages need login /
ingest refused".

### Step A — revoke the leaked token now (works with the code that is live today)

The old code refuses every ingest when `EDITORTRACK_TOKEN` is empty. No agent is sending, so nothing breaks.

```sh
U=~/.config/systemd/user/editortrack-server.service
cp -p "$U" "$U.bak-$(date +%Y%m%d)"                      # rollback copy
sed -i '/^Environment=EDITORTRACK_TOKEN=/d' "$U"
systemctl --user daemon-reload && systemctl --user restart editortrack-server.service
# verify: old token refused, service up
printf 'old token: '; read -rs OLD; echo                    # typed by hand, never written to a file
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/ingest \
     -H "Authorization: Bearer $OLD" -H 'Content-Type: application/json' -d '{}'   # expect 401
unset OLD
systemctl --user is-active editortrack-server.service                       # active
```

Rollback A: `cp -p "$U.bak-YYYYMMDD" "$U" && systemctl --user daemon-reload && systemctl --user restart editortrack-server.service`.

### Step B — stage the new config (no effect on the old code)

```sh
sudo install -d -o worakij -g worakij -m 700 /srv/editortrack
( umask 077; [ -f ~/.editortrack/tokens.json ] || printf '{"hosts": {}}\n' > ~/.editortrack/tokens.json )
cd ~/github/editortrack-night-sec/server            # or ~/github/editortrack/server after the merge
EDITORTRACK_READ_USERS=~/.editortrack/readers.json python3 tokens.py reader-add director   # keep the password in the vault
# machine tokens only when an agent is actually installed (phase 3), e.g.:
# EDITORTRACK_TOKEN_MAP=~/.editortrack/tokens.json python3 tokens.py host-add conform-01
ls -l ~/.editortrack/tokens.json ~/.editortrack/readers.json          # both -rw-------
```

### Step C — switch to the new code + unit (one maintenance window, ~2 min)

Unit diff (old -> `server/deploy/editortrack-server.service`):

```diff
 WorkingDirectory=/home/worakij/github/editortrack/server
-Environment=EDITORTRACK_TOKEN=<old shared token>        (already gone after step A)
-Environment=EDITORTRACK_STORE=/home/worakij/github/editortrack/server/data
+Environment=EDITORTRACK_STORE=/srv/editortrack
+Environment=EDITORTRACK_TOKEN_MAP=%h/.editortrack/tokens.json
+Environment=EDITORTRACK_READ_USERS=%h/.editortrack/readers.json
+Environment=EDITORTRACK_READ_AUTH=required
+Environment=EDITORTRACK_RETENTION_DAYS=90
 Environment=EDITORTRACK_INTERVAL_MIN=1
 EnvironmentFile=-%h/.editortrack/server.env
+UMask=0077
 ExecStart=... uvicorn dashboard_api:app --host 0.0.0.0 --port 8000 --log-level warning
```

```sh
U=~/.config/systemd/user/editortrack-server.service
cp -p "$U" "$U.bak-stepC-$(date +%Y%m%d)"
# 1) code: after review + push approval, night/security-min reaches origin/main; then in the live checkout
#    (NEXT_STEPS.md must be committed or stashed first, owner decision #9):
git -C ~/github/editortrack status --short            # must be empty
systemctl --user stop editortrack-server.service
git -C ~/github/editortrack pull --ff-only
# 2) data: copy (not move) the old store, private modes
cp -a ~/github/editortrack/server/data/. /srv/editortrack/
chmod 700 /srv/editortrack && find /srv/editortrack -type f -exec chmod 600 {} +
python3 ~/github/editortrack/server/retention.py --store /srv/editortrack --scrub-personal --dry-run   # review, then without --dry-run
# 3) unit
cp ~/github/editortrack/server/deploy/editortrack-server.service "$U"
systemctl --user daemon-reload && systemctl --user start editortrack-server.service
```

Verify (from this host and from another LAN machine with `192.168.140.126`):

```sh
H=http://127.0.0.1:8000
curl -s -o /dev/null -w '%{http_code}\n' $H/livez                 # 200
curl -s -o /dev/null -w '%{http_code}\n' $H/api/jobs              # 401
curl -s -o /dev/null -w '%{http_code}\n' $H/                      # 401
curl -s -o /dev/null -w '%{http_code}\n' -u director $H/api/jobs  # 200 (password prompt)
curl -s -o /dev/null -w '%{http_code}\n' -X POST $H/ingest -H 'Authorization: Bearer old-or-random-value-xxxxxxxxxxxx' -d '{}'   # 401
journalctl --user -u editortrack-server -n 20 --no-pager           # warnings list anything missing
ls -la /srv/editortrack                                            # drwx------ / -rw-------
```

Rollback C (≤ 1 min): `systemctl --user stop editortrack-server.service`,
`cp -p "$U.bak-stepC-YYYYMMDD" "$U"`, `git -C ~/github/editortrack reset --hard <previous main sha>` only if the
owner agrees (the checkout is the autodeploy source; otherwise `git revert` the merge on main and push),
`systemctl --user daemon-reload && systemctl --user start editortrack-server.service`. The old data dir is
untouched by step C (copied, not moved), so the old unit finds it as before. Delete
`~/github/editortrack/server/data` only after a week of clean running.

## 4. Routine rotation

| event | action | effect |
|---|---|---|
| one workstation token exposed / machine reinstalled | `tokens.py host-add <host>` (replaces the hash), put the new value in that machine's `deploy/env.sh` | old token dead on the next request; the agent queues offline until it has the new token (queue is capped) |
| machine retired | `tokens.py host-rm <host>`; `agent/deploy/uninstall.sh --purge` on it | — |
| read password exposed / person leaves | `tokens.py reader-rm <name>`; `reader-add` a new one | immediate |
| scheduled | every 12 months, or when IT staff change: re-issue all hosts one by one during booked-free time | — |
| server file permissions changed by mistake | server refuses all (fail closed); `chmod 600 ~/.editortrack/tokens.json ~/.editortrack/readers.json` | immediate |

## 5. Retention timer (prepared, not installed)

```sh
cp ~/github/editortrack/server/deploy/editortrack-retention.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start editortrack-retention.service && journalctl --user -u editortrack-retention -n 5   # first run
systemctl --user enable --now editortrack-retention.timer
```
Dry run any time: `python3 server/retention.py --store /srv/editortrack --dry-run`.

## 6. Optional: Caddy in front (owner decision)

Not installed on this host today. If wanted (TLS + one login for all internal apps), bind uvicorn to
`127.0.0.1:8000`, keep `/ingest` reachable for agents through Caddy, and either keep the app's read auth
(recommended, defence in depth) or set `EDITORTRACK_READ_AUTH=off` only when Caddy checks the login:

```
editortrack.internal {
    @machine path /ingest /agent/control /livez
    handle @machine { reverse_proxy 127.0.0.1:8000 }
    handle { basic_auth { director <bcrypt hash from `caddy hash-password`> }
             reverse_proxy 127.0.0.1:8000 }
}
```
Basic auth over plain HTTP on the LAN sends the password base64-encoded; TLS (Caddy) fixes that.

## 7. Owner decisions

1. Repo `jaikwangg/resolve-poller` is public and its history contains the old token and test data: make it
   private and/or scrub history (force-push; all clones must re-clone).
2. When to run §3 step A (can be today; nothing is sending) and step C (needs the merge + the clean checkout).
3. Caddy + TLS in front, or app auth alone on the LAN.
4. Retention 90 days (data sheet says 90) and installing the retention timer.
5. Who gets read logins.

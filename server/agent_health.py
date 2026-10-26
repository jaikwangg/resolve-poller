#!/usr/bin/env python3
"""agent fleet control + health (docs/research/MAINTENANCE.md).

control  — GET /agent/control?host=&v= : the lease every agent needs before it may poll Resolve.
           Read from EDITORTRACK_CONTROL_FILE (default <store>/agent_control.json). Fail closed:
           missing/corrupt file, unknown host without a default, or an agent version not on
           "allowed_versions" -> {"enabled": false}. Flipping one JSON value is the fleet-wide
           kill switch (takes effect on the next turn, <= 1 min, for every agent that can reach
           the server; agents that cannot reach it stop when their lease expires).

           {"default": {"enabled": false},
            "allowed_versions": ["0.9.0-safety"],
            "lease_sec": 21600,
            "hosts": {"grade-03": {"enabled": true, "allow_resolve": true}}}

health   — per-host status from the records' "agent" heartbeat block: last seen, version,
           last successful Resolve poll, skip reasons, tripped breaker, backoff, queue, RSS.
"""
import datetime, json, os, pathlib

DEFAULT_LEASE = 6 * 3600
THRESH = {
    "silent_min": 10,          # no record for this long (agent installed + host on) -> alert
    "rss_mb": 150,             # peak RSS of one turn
    "turn_ms": 60000,          # one turn wall time
    "queue_lines": 1000,       # backlog = server unreachable for ~16 h
    "backoff_fails": 3,
    "guard_ms": 2000,          # Resolve calls in one poll
}


def control_file(store):
    return pathlib.Path(os.environ.get("EDITORTRACK_CONTROL_FILE") or (pathlib.Path(store) / "agent_control.json"))


def load_control(store):
    try:
        c = json.loads(control_file(store).read_text(encoding="utf-8"))
        return c if isinstance(c, dict) else None
    except Exception:
        return None


def control_for(store, host, version):
    c = load_control(store)
    if c is None:
        return {"enabled": False, "reason": "no_control_file"}
    allowed = c.get("allowed_versions")
    if not isinstance(allowed, list) or version not in allowed:
        return {"enabled": False, "reason": "version_not_allowed"}
    h = (c.get("hosts") or {}).get(host)
    if h is None:
        h = c.get("default") or {}
    if h.get("enabled") is not True:
        return {"enabled": False, "reason": "host_disabled"}
    lease = int(h.get("lease_sec") or c.get("lease_sec") or DEFAULT_LEASE)
    return {"enabled": True, "lease_sec": max(60, min(lease, 7 * 86400)),
            "allow_resolve": h.get("allow_resolve", False) is True}


def _dt(ts):
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def health(records, now=None, control=None):
    """records: iterable of ingest records (any order). -> {host: row}"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    allowed = set((control or {}).get("allowed_versions") or [])
    hosts = {}
    for r in sorted(records, key=lambda r: str(r.get("ts"))):
        h = r.get("host")
        t = _dt(r.get("ts"))
        if not h or t is None:
            continue
        row = hosts.setdefault(h, {"host": h, "records_24h": 0, "skips_24h": {}, "max_rss_mb": None,
                                   "max_turn_ms": None, "max_guard_ms": None, "last_ok_poll": None})
        row["last_seen"] = t.isoformat()
        a = r.get("agent") or {}
        src = r.get("source") or {}
        if (now - t).total_seconds() <= 86400:
            row["records_24h"] += 1
            if src.get("ok") is False and src.get("reason"):
                k = str(src["reason"]).split(":")[0]
                row["skips_24h"][k] = row["skips_24h"].get(k, 0) + 1
            for key, val in (("max_rss_mb", a.get("rss_mb")), ("max_turn_ms", a.get("turn_ms")),
                             ("max_guard_ms", (a.get("guard") or {}).get("ms"))):
                if isinstance(val, (int, float)):
                    row[key] = val if row[key] is None else max(row[key], val)
        if src.get("ok") is True or (src and "ok" not in src and r.get("progress")):
            row["last_ok_poll"] = t.isoformat()
        row.update({"version": a.get("version"), "python": a.get("python"), "os": a.get("os"),
                    "tripped": a.get("tripped"), "backoff_fails": a.get("backoff_fails", 0),
                    "queue_lines": a.get("queue_lines", 0), "has_heartbeat": bool(a)})
    for row in hosts.values():
        alerts, warns = [], []
        age_min = (now - _dt(row["last_seen"])).total_seconds() / 60
        row["silent_min"] = round(age_min, 1)
        if age_min > THRESH["silent_min"]:
            warns.append("silent %d min" % age_min)       # may be powered off — owner decides
        if row.get("tripped"):
            alerts.append("breaker tripped: %s" % row["tripped"])
        if not row["has_heartbeat"]:
            warns.append("old agent (no heartbeat block)")
        elif allowed and row.get("version") not in allowed:
            alerts.append("version %s not allowed" % row.get("version"))
        if (row["max_rss_mb"] or 0) > THRESH["rss_mb"]:
            alerts.append("rss %s MB" % row["max_rss_mb"])
        if (row["max_turn_ms"] or 0) > THRESH["turn_ms"]:
            alerts.append("turn %s ms" % row["max_turn_ms"])
        if (row["max_guard_ms"] or 0) > THRESH["guard_ms"]:
            alerts.append("Resolve calls %s ms in one poll" % row["max_guard_ms"])
        if (row.get("queue_lines") or 0) > THRESH["queue_lines"]:
            warns.append("queue %s" % row["queue_lines"])
        if (row.get("backoff_fails") or 0) >= THRESH["backoff_fails"]:
            warns.append("backoff x%s" % row["backoff_fails"])
        if row["skips_24h"].get("forbidden_call") or row["skips_24h"].get("state_changed_during_scan"):
            alerts.append("UI-state event: %s" % {k: v for k, v in row["skips_24h"].items()
                                                  if k in ("forbidden_call", "state_changed_during_scan")})
        row["alerts"], row["warnings"] = alerts, warns
        row["status"] = "alert" if alerts else ("warn" if warns else "ok")
    return hosts

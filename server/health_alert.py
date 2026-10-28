#!/usr/bin/env python3
"""Send owner alerts when an agent's health status changes (run every 5 min by a systemd timer).

Only transitions are sent (ok -> warn/alert, alert -> ok), so a stuck problem does not spam.
Channel: Telegram Bot API (same pattern as ~/github/server-temp-bot/telegram_api.py), env:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID            (both empty -> print only, dry run)
  EDITORTRACK_STORE                               records dir (same as the server)
  EDITORTRACK_HEALTH_STATE                        last-sent state (default <store>/health_state.json)

usage: python3 health_alert.py [--dry-run]
"""
import glob, json, os, pathlib, re, sys, urllib.request, datetime

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import agent_health   # noqa: E402
import explain        # noqa: E402


def load_records(store, days=2):
    recs = []
    cutoff = datetime.date.today() - datetime.timedelta(days=days - 1)
    for fp in sorted(glob.glob(str(pathlib.Path(store) / "*.jsonl"))):
        try:
            if datetime.date.fromisoformat(pathlib.Path(fp).stem) < cutoff:
                continue
        except ValueError:
            continue
        for ln in open(fp, encoding="utf-8"):
            try:
                recs.append(json.loads(ln))
            except Exception:
                pass
    return explain.dedupe(recs)        # same record check as the server: a bad stored line is skipped


def server_alerts(store):
    """PMS progress steps lost by the server (pms_sync.LOST_FILE); read from the store, no server import."""
    try:
        import pms_sync
        return pms_sync.server_alerts(store)
    except Exception as e:                    # the alert run must not die on this; say it instead
        return ["cannot read PMS progress loss state: %s" % type(e).__name__]


SERVER_ROW = "_server"          # not a valid host name (no "_" in host names), so it never collides with a workstation
_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def condition_key(text):
    """an alert/warning line without the parts that change while the condition goes on (times, counts, minutes):
    a host refused every minute, a PMS that keeps hanging or a growing "silent N min" is ONE condition, notified
    once when it starts and once when it clears (the 5-min timer must not repeat it)."""
    return _NUM.sub("#", _TS.sub("<t>", text))


def transitions(prev, rows):
    msgs, state = [], {}
    for h, r in sorted(rows.items()):
        key = r["status"] + "|" + ";".join(condition_key(x) for x in r["alerts"] + r["warnings"])
        state[h] = key
        if prev.get(h) != key and (r["status"] != "ok" or (prev.get(h) or "ok").split("|")[0] != "ok"):
            icon = {"alert": "🔴", "warn": "🟡", "ok": "🟢"}[r["status"]]
            name = "server (PMS progress)" if h == SERVER_ROW else h
            msgs.append("%s editortrack %s: %s %s" % (icon, name, r["status"], "; ".join(r["alerts"] + r["warnings"])))
    return msgs, state


def send(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        print(text)
        return False
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % tok,
                                 data=json.dumps({"chat_id": chat, "text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status == 200


def main(argv):
    store = os.environ.get("EDITORTRACK_STORE") or "/srv/editortrack"
    sp = pathlib.Path(os.environ.get("EDITORTRACK_HEALTH_STATE") or pathlib.Path(store) / "health_state.json")
    try:
        prev = json.loads(sp.read_text())
    except Exception:
        prev = {}
    rows = agent_health.health(load_records(store), control=agent_health.load_control(store),
                               refused=agent_health.load_host_refused(store))
    sa = server_alerts(store)
    rows[SERVER_ROW] = {"status": "alert" if sa else "ok", "alerts": sa, "warnings": []}
    msgs, state = transitions(prev, rows)
    for m in msgs:
        if "--dry-run" in argv:
            print(m)
        else:
            send(m)
    if "--dry-run" not in argv:
        sp.write_text(json.dumps(state))
    return msgs


if __name__ == "__main__":
    main(sys.argv[1:])

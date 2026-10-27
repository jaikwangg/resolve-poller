#!/usr/bin/env python3
"""Send owner alerts when an agent's health status changes (run every 5 min by a systemd timer).

Only transitions are sent (ok -> warn/alert, alert -> ok), so a stuck problem does not spam.
Channel: Telegram Bot API (same pattern as ~/github/server-temp-bot/telegram_api.py), env:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID            (both empty -> print only, dry run)
  EDITORTRACK_STORE                               records dir (same as the server)
  EDITORTRACK_HEALTH_STATE                        last-sent state (default <store>/health_state.json)

usage: python3 health_alert.py [--dry-run]
"""
import glob, json, os, pathlib, sys, urllib.request, datetime

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


def transitions(prev, rows):
    msgs, state = [], {}
    for h, r in sorted(rows.items()):
        key = r["status"] + "|" + ";".join(r["alerts"] + r["warnings"])
        state[h] = key
        if prev.get(h) != key and (r["status"] != "ok" or (prev.get(h) or "ok").split("|")[0] != "ok"):
            icon = {"alert": "🔴", "warn": "🟡", "ok": "🟢"}[r["status"]]
            msgs.append("%s editortrack %s: %s %s" % (icon, h, r["status"], "; ".join(r["alerts"] + r["warnings"])))
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
    store = os.environ.get("EDITORTRACK_STORE", "data")
    sp = pathlib.Path(os.environ.get("EDITORTRACK_HEALTH_STATE") or pathlib.Path(store) / "health_state.json")
    try:
        prev = json.loads(sp.read_text())
    except Exception:
        prev = {}
    rows = agent_health.health(load_records(store), control=agent_health.load_control(store))
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

#!/usr/bin/env python3
"""Idempotent /ingest: remembers which records are already in the store, so a re-sent record is
acknowledged (200) but written only once.

Why: an agent that times out (slow network, slow server) keeps the record in its queue and sends it
again later; before this, each re-send became another line in the store.

Key per record (always together with the token's host, so one host can never shadow another):
  rid      agent >= 0.9.2 sets a random id when it builds the record; the queue keeps it, so every
           re-send carries the same id
  ts:<ts>  older agents: the record's own timestamp (microsecond ISO, set once when the record was
           built and kept in the queue line as is), so their re-sends are caught too

Index on disk: <store>/ingest_ids/<YYYY-MM-DD>.txt (UTC day of the record's ts, so a record re-sent
days later still finds its day), one "host<TAB>key" line per record, mode 600. Days are loaded into
memory on demand (at most MAX_DAYS_CACHED at once). retention.py prunes old day files.

Order at /ingest: record appended to the store and fsynced FIRST (dashboard_api._append_record), then its key.
The key itself is not fsynced: losing a key only means one more stored copy, never lost time. A crash between the two can only
leave a record without its key (the re-send is then stored twice and the readers' 30 s same-host
dedupe in explain.dedupe still counts it once); it can never leave a key without its record, which
would make the server acknowledge a re-send that it never stored (lost time).
"""
import collections, datetime, os, pathlib, threading

DIRNAME = "ingest_ids"
MAX_DAYS_CACHED = 64


def key_of(rec):
    rid = rec.get("rid")
    if isinstance(rid, str) and rid:
        return rid
    return "ts:" + str(rec.get("ts"))


FUTURE_SEC = 86400            # a ts further ahead than this (bad workstation clock) is filed under today


def _day(ts, now=None):
    """UTC day file for a record's key. A ts more than FUTURE_SEC ahead of the server clock is filed under the
    server's own UTC day, so retention (which deletes by day) still reaches it; without the clamp a record dated
    2099 would create a day file nobody ever deletes. The record itself is stored unchanged (/ingest never drops
    it for its ts). Index.seen also looks in the record's own day and the last CLAMP_LOOKBACK_DAYS server days
    (_days_to_check), so a re-send after the ts has come within a day still finds the key filed earlier. Left: a
    re-send more than CLAMP_LOOKBACK_DAYS server days after the first send is stored again; both copies carry the
    same ts, so the readers' same-host dedupe (explain.dedupe) still counts it once."""
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is not None:
            now = now or datetime.datetime.now(datetime.timezone.utc)
            if (d - now).total_seconds() > FUTURE_SEC:
                d = now
            return d.astimezone(datetime.timezone.utc).date().isoformat()
    except (ValueError, OverflowError):
        pass
    return "undated"


def _real_day(ts):
    """the record's own UTC day (no clamp), or None"""
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d.astimezone(datetime.timezone.utc).date().isoformat() if d.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


CLAMP_LOOKBACK_DAYS = 3


def _days_to_check(ts, now=None):
    """day files that may hold this record's key: where it would be filed now, its own day, and - for a ts from
    yesterday on (it may have been sent earlier while > 1 day ahead, and filed under THAT server day) - the last
    CLAMP_LOOKBACK_DAYS server days. Extra lookups can only find the same key, never a different record."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    days = [_day(ts, now)]
    real = _real_day(ts)
    if real is not None:
        days.append(real)
        today = now.date()
        if real >= (today - datetime.timedelta(days=1)).isoformat():
            days += [(today - datetime.timedelta(days=k)).isoformat() for k in range(CLAMP_LOOKBACK_DAYS + 1)]
    return list(dict.fromkeys(days))


class Index:
    def __init__(self, store):
        self.store = pathlib.Path(store)
        self.dir = self.store / DIRNAME
        self._days = collections.OrderedDict()      # day -> set of "host\tkey"
        self._lock = threading.Lock()

    def _load(self, day):
        s = self._days.get(day)
        if s is not None:
            self._days.move_to_end(day)
            return s
        s = set()
        try:
            with open(self.dir / (day + ".txt"), encoding="utf-8") as f:
                s = {ln.rstrip("\n") for ln in f if ln.strip()}
        except FileNotFoundError:
            pass
        self._days[day] = s
        while len(self._days) > MAX_DAYS_CACHED:
            self._days.popitem(last=False)
        return s

    def seen(self, host, rec):
        line = host + "\t" + key_of(rec)
        with self._lock:
            return any(line in self._load(d) for d in _days_to_check(rec.get("ts")))

    def add(self, host, rec):
        """remember a stored record (call AFTER the store write). Never raises: a failure here only
        means a later re-send of this record is stored again (and still counted once by the readers)."""
        line = host + "\t" + key_of(rec)
        day = _day(rec.get("ts"))
        with self._lock:
            s = self._load(day)
            if line in s:
                return
            s.add(line)
            try:
                self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(str(self.dir / (day + ".txt")), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

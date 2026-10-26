#!/usr/bin/env python3
"""Retention for the record store (docs/DATA_SHEET.md: kept 90 days by default).

Records live in one file per server-receive day: <store>/YYYY-MM-DD.jsonl. A file is deleted
when its day is older than N days (strictly before today - N). Only files whose whole name is
a valid date + .jsonl are ever touched; agent_control.json and anything else is left alone.

  python3 retention.py --dry-run            print what would be deleted, delete nothing
  python3 retention.py                      delete
  python3 retention.py --scrub-personal [--dry-run]
                                            also rewrite kept files without the personal fields
                                            (user, activity.frontmost_name/bundle) that agents
                                            before the data-minimisation change sent

  N    = --days or EDITORTRACK_RETENTION_DAYS (default 90, minimum 1)
  store= --store or EDITORTRACK_STORE (default /srv/editortrack)
Exit 0 on success; prints one line per file.
"""
import argparse, datetime, json, os, pathlib, re, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import privacy  # noqa: E402

DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
DEFAULT_STORE = "/srv/editortrack"


def retention_days(arg=None):
    v = arg if arg is not None else os.environ.get("EDITORTRACK_RETENTION_DAYS", "90")
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise SystemExit(f"retention days must be an integer, got {v!r}")
    if n < 1:
        raise SystemExit("retention days must be >= 1")
    return n


def day_files(store):
    out = []
    for p in sorted(pathlib.Path(store).iterdir()):
        m = DAY_FILE.match(p.name)
        if not m or not p.is_file() or p.is_symlink():
            continue
        try:
            out.append((datetime.date.fromisoformat(m.group(1)), p))
        except ValueError:
            continue
    return out


def expired(store, days, today=None):
    cutoff = (today or datetime.date.today()) - datetime.timedelta(days=days)
    return [p for d, p in day_files(store) if d < cutoff]


def scrub_file(path, dry_run):
    """drop personal fields from every record of one file; returns number of records changed."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out, changed = [], 0
    for ln in lines:
        try:
            rec = json.loads(ln)
        except Exception:
            out.append(ln)
            continue
        if isinstance(rec, dict) and privacy.scrub(rec):
            changed += 1
            ln = json.dumps(rec, ensure_ascii=False)
        out.append(ln)
    if changed and not dry_run:
        fd, tmp = tempfile.mkstemp(prefix=".scrub-", dir=str(path.parent))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(x + "\n" for x in out))
        os.replace(tmp, path)
    return changed


def run(store, days, dry_run=False, scrub=False, today=None, echo=print):
    store = pathlib.Path(store)
    if not store.is_dir():
        raise SystemExit(f"store {store} is not a directory")
    gone = expired(store, days, today)
    for p in gone:
        echo(("would delete " if dry_run else "delete ") + str(p))
        if not dry_run:
            p.unlink()
    scrubbed = 0
    if scrub:
        for _, p in day_files(store):
            if p in gone:
                continue
            n = scrub_file(p, dry_run)
            if n:
                scrubbed += n
                echo(("would scrub " if dry_run else "scrub ") + f"{p} ({n} records)")
    echo(f"retention {days} days: {len(gone)} file(s) {'to delete' if dry_run else 'deleted'}"
         + (f", {scrubbed} record(s) {'to scrub' if dry_run else 'scrubbed'}" if scrub else ""))
    return gone


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days")
    ap.add_argument("--store")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--scrub-personal", action="store_true")
    a = ap.parse_args(argv)
    store = a.store or os.environ.get("EDITORTRACK_STORE") or DEFAULT_STORE
    run(store, retention_days(a.days), a.dry_run, a.scrub_personal)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Manage per-host machine tokens and read credentials (hashes only on disk, mode 600).

  python3 tokens.py host-add  <host> [--map PATH]     new token for a workstation (printed ONCE)
  python3 tokens.py host-rm   <host> [--map PATH]     revoke a workstation (takes effect on the next request)
  python3 tokens.py hosts            [--map PATH]     list hosts (no secrets)
  python3 tokens.py reader-add <name> [--users PATH] [--kind basic|token]
                                                       read login (Basic password) or read token, printed ONCE
  python3 tokens.py reader-rm  <name> [--users PATH]
  python3 tokens.py readers          [--users PATH]

PATH defaults: $EDITORTRACK_TOKEN_MAP / $EDITORTRACK_READ_USERS. Files are written atomically
with mode 600; the server re-reads them on change (no restart). Lost a token? Add the host again:
the old token stops working at once.
"""
import argparse, json, os, pathlib, re, secrets, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from auth import digest  # noqa: E402

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _load(path):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _path(arg, env):
    p = arg or os.environ.get(env)
    if not p:
        sys.exit(f"no file: pass --map/--users or set {env}")
    return pathlib.Path(p)


def new_secret():
    return secrets.token_urlsafe(32)          # 43 chars, 256 bits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["host-add", "host-rm", "hosts", "reader-add", "reader-rm", "readers"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--map")
    ap.add_argument("--users")
    ap.add_argument("--kind", choices=["basic", "token"], default="basic")
    a = ap.parse_args(argv)
    if a.cmd in ("host-add", "host-rm", "reader-add", "reader-rm") and not (a.name and NAME.match(a.name)):
        sys.exit("name must match " + NAME.pattern)

    if a.cmd.startswith("host"):
        path = _path(a.map, "EDITORTRACK_TOKEN_MAP")
        data = _load(path)
        hosts = data.setdefault("hosts", {})
        if a.cmd == "hosts":
            print("\n".join(sorted(hosts)) or "(none)")
            return 0
        if a.cmd == "host-rm":
            if hosts.pop(a.name, None) is None:
                sys.exit(f"{a.name}: not in {path}")
            _save(path, data)
            print(f"revoked {a.name}")
            return 0
        tok = new_secret()
        hosts[a.name] = digest(tok)
        _save(path, data)
        print(f"host {a.name}: put this in the agent's deploy/env.sh as EDITORTRACK_TOKEN (shown once):")
        print(tok)
        return 0

    path = _path(a.users, "EDITORTRACK_READ_USERS")
    data = _load(path)
    users, tokens = data.setdefault("users", {}), data.setdefault("tokens", {})
    if a.cmd == "readers":
        print("\n".join([f"basic {u}" for u in sorted(users)] + [f"token {t}" for t in sorted(tokens)]) or "(none)")
        return 0
    if a.cmd == "reader-rm":
        gone = users.pop(a.name, None) or tokens.pop(a.name, None)
        if gone is None:
            sys.exit(f"{a.name}: not in {path}")
        _save(path, data)
        print(f"removed {a.name}")
        return 0
    sec = new_secret()
    (users if a.kind == "basic" else tokens)[a.name] = digest(sec)
    _save(path, data)
    what = "password for user " + a.name if a.kind == "basic" else "read token " + a.name + " (Authorization: Bearer ...)"
    print(f"{what} (shown once):")
    print(sec)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Server auth (security phase 2, docs/SECURITY.md).

Machine tokens (POST /ingest, GET /agent/control)
    One token per workstation, kept server-side as a SHA-256 hash in the token map
    (EDITORTRACK_TOKEN_MAP, JSON, file mode 600):
        {"hosts": {"grade-01": "sha256:<64 hex>", "conform-01": "sha256:<64 hex>"}}
    A token is valid only for its own host: the record's "host" (or ?host= of the lease)
    must equal the map key. Unknown/missing/short token, a revoked token, a missing or
    group/other-readable map -> 401 (fail closed). There is no shared token any more and
    EDITORTRACK_TOKEN is ignored.

Read access (every other route: pages, /api/*, /health)
    EDITORTRACK_READ_AUTH = "required" (default) | "off"
      required: HTTP Basic (user + password) or "Authorization: Bearer <read token>", checked
                against EDITORTRACK_READ_USERS (JSON, mode 600):
                    {"users": {"director": "sha256:<hex>"}, "tokens": {"pms-dash": "sha256:<hex>"}}
                Machine tokens never grant read access.
      off:      only when a reverse proxy in front (Caddy basic_auth) does the check AND the
                server listens on 127.0.0.1. Logged as a warning at startup.
    /livez is the only open route ({"ok": true}, nothing else) for uptime monitors.

Secrets are generated (32 random bytes, server/tokens.py), so a plain SHA-256 is enough; all
comparisons use hmac.compare_digest. The files are re-read when their mtime changes, so
adding or revoking a host needs no restart.
"""
import base64, hashlib, hmac, json, logging, os, pathlib, stat

log = logging.getLogger("editortrack.auth")

MIN_SECRET_LEN = 24
# SHA-256 of the shared token that was published in the public repo (rotated 2026-10). Refused
# everywhere even if someone copies it back into a map. The value itself is not kept in the repo.
REVOKED_SHA256 = frozenset({
    "9381a6be0c5e696eb157be9a7e15e7edc1b209c356fb8b7fd5fb9d6e5e2744b1",
})
_DUMMY = "sha256:" + "0" * 64


def digest(secret):
    return "sha256:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _eq(a, b):
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def _revoked(secret):
    h = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    hit = False
    for r in REVOKED_SHA256:
        hit |= _eq(h, r)
    return hit


class _JsonFile:
    """JSON file that must be private (no group/other bits). Cached by (mtime, size).
    Anything wrong -> None (callers treat None as 'nobody is allowed')."""

    def __init__(self, env):
        self.env = env
        self._key = None
        self._val = None
        self._warned = set()

    def path(self):
        p = (os.environ.get(self.env) or "").strip()
        return pathlib.Path(p) if p else None

    def _warn(self, msg):
        if msg not in self._warned:
            self._warned.add(msg)
            log.warning(msg)

    def load(self):
        p = self.path()
        if p is None:
            self._warn(f"{self.env} not set: access denied (fail closed)")
            return None
        try:
            st = p.stat()
        except OSError:
            self._warn(f"{self.env}={p} missing: access denied (fail closed)")
            return None
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            self._warn(f"{p} is readable by group/other (mode {stat.S_IMODE(st.st_mode):o}); chmod 600: access denied")
            return None
        key = (str(p), st.st_mtime_ns, st.st_size)
        if key != self._key:
            try:
                val = json.loads(p.read_text(encoding="utf-8"))
                self._val = val if isinstance(val, dict) else None
            except Exception:
                self._val = None
                self._warn(f"{p} unreadable or not JSON: access denied")
            self._key = key
        return self._val


TOKEN_MAP = _JsonFile("EDITORTRACK_TOKEN_MAP")
READ_USERS = _JsonFile("EDITORTRACK_READ_USERS")


def _bearer(authorization):
    a = (authorization or "").strip()
    if a[:7].lower() != "bearer ":
        return None
    t = a[7:].strip()
    return t or None


def machine_host(authorization):
    """host whose token was presented, or None (-> 401). Scans every entry (no early exit)."""
    tok = _bearer(authorization)
    m = TOKEN_MAP.load()
    hosts = (m or {}).get("hosts") if isinstance(m, dict) else None
    if not tok or len(tok) < MIN_SECRET_LEN or not isinstance(hosts, dict) or not hosts:
        _eq(digest(tok or ""), _DUMMY)              # keep the work similar on every path
        return None
    if _revoked(tok):
        return None
    d = digest(tok)
    found = None
    for host, h in hosts.items():
        if isinstance(h, str) and h.startswith("sha256:") and _eq(d, h) and found is None:
            found = host
    return found


def norm_host(name):
    """the comparable form of a host name: case-insensitive, no trailing dot, no domain suffix.
    "GRADE-01", "grade-01.local", "grade-01.corp.example." -> "grade-01". Not a string / empty -> None."""
    if not isinstance(name, str):
        return None
    n = name.strip().rstrip(".").lower().split(".")[0]
    return n or None


def same_host(sent, token_host):
    """True when `sent` (what the agent reports) names the host the token was issued for. Only the
    spelling may differ (case, FQDN vs short name): the caller stores token_host, never `sent`, so a
    token can only ever write records under its own map key."""
    a, b = norm_host(sent), norm_host(token_host)
    return a is not None and a == b


def read_ok(authorization):
    """True if the request carries read credentials (Basic user/password or a read token)."""
    if read_auth_off():
        return True
    a = (authorization or "").strip()
    cfg = READ_USERS.load()
    if not a or not isinstance(cfg, dict):
        return False
    if a[:6].lower() == "basic ":
        try:
            user, _, pw = base64.b64decode(a[6:].strip(), validate=True).decode("utf-8").partition(":")
        except Exception:
            return False
        users = cfg.get("users") if isinstance(cfg.get("users"), dict) else {}
        want = users.get(user)
        ok = isinstance(want, str) and want.startswith("sha256:")
        same = _eq(digest(pw), want if ok else _DUMMY)
        return bool(ok and same and len(pw) >= MIN_SECRET_LEN and not _revoked(pw))
    tok = _bearer(a)
    if tok is None or len(tok) < MIN_SECRET_LEN or _revoked(tok):
        return False
    d = digest(tok)
    tokens = cfg.get("tokens") if isinstance(cfg.get("tokens"), dict) else {}
    hit = False
    for h in tokens.values():
        if isinstance(h, str) and h.startswith("sha256:"):
            hit |= _eq(d, h)
    return hit


def read_auth_off():
    return (os.environ.get("EDITORTRACK_READ_AUTH") or "required").strip().lower() == "off"


def startup_warnings():
    out = []
    if read_auth_off():
        out.append("EDITORTRACK_READ_AUTH=off: read pages are open; only safe behind an authenticating proxy on 127.0.0.1")
    if (os.environ.get("EDITORTRACK_TOKEN") or "").strip():
        out.append("EDITORTRACK_TOKEN is set but ignored (shared token removed; use EDITORTRACK_TOKEN_MAP)")
    if TOKEN_MAP.load() is None:
        out.append("no usable EDITORTRACK_TOKEN_MAP: every /ingest and /agent/control request gets 401")
    if not read_auth_off() and READ_USERS.load() is None:
        out.append("no usable EDITORTRACK_READ_USERS: every read page and /api/* request gets 401")
    return out

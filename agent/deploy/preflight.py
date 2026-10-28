#!/usr/bin/env python3
"""Pre-install check: does the server grant THIS agent version a lease for THIS host?

install.sh runs it (with deploy/env.sh loaded) before it installs anything. An agent whose version is not on
the server's "allowed_versions" (agent_control.json) gets no lease and records NOTHING, silently, from the
moment its current lease runs out. So the rollout order is fixed (agent/deploy/README.md "Rollout order"):

  1. server first (the server that knows the new agent's record format),
  2. agent_control.json "allowed_versions" lists BOTH the old and the new version,
  3. only then the agents, one host at a time (this check refuses the install otherwise).

It asks GET /agent/control?host=<EDITORTRACK_HOST or hostname>&v=<safety.AGENT_VERSION> exactly as the agent
does and passes only on {"enabled": true}. Anything else fails loudly with the reason and exit 1: version not
allowed, host disabled, no control file, wrong token (401), host-name mismatch (403), server unreachable.
Reads nothing but the env; writes nothing (no state file, no bytecode). The token is never printed.

  . deploy/env.sh && python3 deploy/preflight.py        exit 0 = the server will grant the lease
"""
import json, os, socket, sys, urllib.error, urllib.parse, urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import safety  # noqa: E402

REASONS = {
    "version_not_allowed": "server ไม่อนุญาตเวอร์ชันนี้ · this agent version is not on allowed_versions in the "
                           "server's agent_control.json. Add %(v)r there FIRST (keep the old version listed too), "
                           "then install. Rollout order: server -> allowed_versions -> agents.",
    "host_disabled": "host นี้ยังไม่ได้เปิดใน agent_control.json · host %(h)r is not enabled (hosts / default) in "
                     "the server's agent_control.json: the agent would record nothing.",
    "no_control_file": "server ไม่มี agent_control.json (หรืออ่านไม่ได้) · the server has no readable "
                       "agent_control.json: every agent is refused.",
}


def check(env=None, timeout=5):
    """-> (ok, message)"""
    env = os.environ if env is None else env
    servers = [u.strip() for u in (env.get("EDITORTRACK_SERVER") or "").split(",") if u.strip()]
    token = env.get("EDITORTRACK_TOKEN") or ""
    host = (env.get("EDITORTRACK_HOST") or "").strip() or socket.gethostname()
    v = safety.AGENT_VERSION
    if not servers or not token or token == "CHANGE_ME":
        return False, "EDITORTRACK_SERVER / EDITORTRACK_TOKEN ยังไม่ได้ตั้งใน deploy/env.sh · not set in deploy/env.sh"
    # same rule as safety.control_url (EDITORTRACK_CONTROL_URL, else next to the first /ingest URL)
    url = env.get("EDITORTRACK_CONTROL_URL") or next(
        (u.rstrip("/")[: -len("/ingest")] + "/agent/control" for u in servers if u.rstrip("/").endswith("/ingest")), None)
    if not url:
        return False, "cannot derive /agent/control from EDITORTRACK_SERVER (must end in /ingest)"
    q = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"host": host, "v": v})
    req = urllib.request.Request(q, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "server ตอบ 401: token ไม่ถูกต้อง · the server does not know this token (401)"
        if e.code == 403:
            th = (e.headers.get("X-Editortrack-Host-Mismatch") or "").strip()[:100]
            return False, ("server ตอบ 403: ชื่อเครื่องไม่ตรง token · host name %r does not match the token%s"
                           % (host, (" (token is for %r: set EDITORTRACK_HOST=%s)" % (th, th)) if th else ""))
        return False, "server answered HTTP %s on %s" % (e.code, url)
    except Exception as e:
        return False, "ติดต่อ server ไม่ได้ · cannot reach %s (%s): the lease cannot be verified" % (url, type(e).__name__)
    if not isinstance(body, dict):
        return False, "unexpected answer from %s" % url
    if body.get("enabled") is True:
        return True, ("ok: server grants agent %s a lease for host %r (%ss, allow_resolve=%s)"
                      % (v, host, body.get("lease_sec"), body.get("allow_resolve")))
    why = str(body.get("reason") or "refused")
    return False, "%s [%s]" % (REASONS.get(why, "server refused the lease") % {"v": v, "h": host}, why)


def main():
    ok, msg = check()
    if ok:
        print("✓ preflight " + msg)
        return 0
    print("✗ PREFLIGHT FAILED — agent %s ไม่ได้ติดตั้ง · not installed: %s" % (safety.AGENT_VERSION, msg), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

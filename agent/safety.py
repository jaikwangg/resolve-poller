#!/usr/bin/env python3
"""safety — fail-closed gates around every agent turn (see docs/research/WORKSTATION_SAFETY.md).

Order of a turn (runner.main):
  1. hard watchdog (process exits after HARD_TIMEOUT_SEC whatever happens)
  2. lower own CPU / IO priority
  3. kill switch   -> exit silently (no Resolve call, no network)
  4. single-instance lock -> exit silently if another turn is running
  5. lease from the server (remote kill switch); missing/expired/unknown -> no Resolve call
  6. circuit breaker (TRIPPED file) -> no Resolve call, heartbeat only
  7. backoff after errors -> no Resolve call until next_at
  Only then may resolve_poller run, and it has its own pre-checks (version, render,
  playback, user idle) and a per-poll budget enforced by resolve_guard.

Every state file lives in STATE_DIR (~/.editortrack). Nothing is ever written elsewhere,
in particular never inside Resolve's project library / disk database / cache folders.
"""
import json, os, pathlib, sys, time, threading

AGENT_VERSION = "0.9.1-safety"


def _int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def state_dir():
    return pathlib.Path(os.environ.get("EDITORTRACK_STATE_DIR") or os.path.expanduser("~/.editortrack"))


HARD_TIMEOUT_SEC = _int("EDITORTRACK_HARD_TIMEOUT_SEC", 90)
LEASE_TIMEOUT_SEC = _int("EDITORTRACK_LEASE_TIMEOUT_SEC", 3)
BACKOFF_BASE_SEC = 60
BACKOFF_MAX_SEC = 3600

SYSTEM_KILL_FILES = {
    "linux": ["/etc/editortrack/DISABLED"],
    "darwin": ["/Library/Application Support/editortrack/DISABLED"],
    "win32": [os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "editortrack", "DISABLED")],
}
AGENT_DIR = pathlib.Path(__file__).resolve().parent


def _plat():
    p = sys.platform
    return "linux" if p.startswith("linux") else ("win32" if p.startswith("win") else p)


def kill_files():
    return [str(state_dir() / "DISABLED"), str(AGENT_DIR / "DISABLED")] + SYSTEM_KILL_FILES.get(_plat(), [])


def kill_switch():
    """reason string if the agent must not run, else None. Any doubt -> disabled (fail closed)."""
    v = (os.environ.get("EDITORTRACK_DISABLE") or "").strip().lower()
    if v and v not in ("0", "false", "no", "off"):
        return "env"
    for f in kill_files():
        try:
            os.stat(f)
            return "file:" + f
        except FileNotFoundError:
            continue
        except NotADirectoryError:
            continue
        except OSError as e:                 # permission error, I/O error, ... -> cannot tell -> off
            return "unreadable:%s:%s" % (f, type(e).__name__)
    return None


# ---------- watchdog / priority ----------
def hard_timeout(sec=None):
    """Exit the whole process after `sec` seconds no matter what (hung Resolve IPC, hung network)."""
    sec = HARD_TIMEOUT_SEC if sec is None else sec
    if sec <= 0:
        return None
    t = threading.Timer(sec, lambda: os._exit(3))
    t.daemon = True
    t.start()
    return t


def lower_priority():
    """Best effort: lowest CPU priority + idle/throttled IO for this process. Returns what was applied."""
    done = []
    try:
        if _plat() == "win32":
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetCurrentProcess()
            if k.SetPriorityClass(h, 0x00100000):          # PROCESS_MODE_BACKGROUND_BEGIN (CPU+IO+memory)
                done.append("win_background_mode")
            elif k.SetPriorityClass(h, 0x00000040):        # IDLE_PRIORITY_CLASS
                done.append("win_idle_class")
        else:
            cur = os.nice(0)
            if cur < 19:
                os.nice(19 - cur)
            done.append("nice19")
    except Exception:
        pass
    try:
        if _plat() == "linux":
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            # ioprio_set(IOPRIO_WHO_PROCESS=1, 0=self, IOPRIO_CLASS_IDLE<<13)
            nr = {"x86_64": 251, "aarch64": 30}.get(os.uname().machine)
            if nr and libc.syscall(nr, 1, 0, 3 << 13) == 0:
                done.append("ionice_idle")
        elif _plat() == "darwin":
            import ctypes
            libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            # setiopolicy_np(IOPOL_TYPE_DISK=0, IOPOL_SCOPE_PROCESS=0, IOPOL_THROTTLE=3)
            if libc.setiopolicy_np(0, 0, 3) == 0:
                done.append("iopol_throttle")
    except Exception:
        pass
    return done


def peak_rss_mb():
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)
    except Exception:
        return None


# ---------- single instance ----------
class Lock:
    def __init__(self):
        self.f = None

    def acquire(self):
        try:
            d = state_dir()
            d.mkdir(parents=True, exist_ok=True)
            self.f = open(d / "agent.lock", "a+")
            if _plat() == "win32":
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except Exception:
            return False


# ---------- small json state ----------
def _read(name):
    try:
        return json.loads((state_dir() / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return {"_corrupt": True}


def _write(name, obj):
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (name + ".tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        tmp.replace(d / name)
        return True
    except Exception:
        return False


# ---------- platform pin (pre-upgrade check) ----------
def platform_ids():
    import platform
    rel = platform.release()
    osid = "%s %s" % (platform.system(), rel.split(".")[0] if platform.system() == "Darwin" else ".".join(rel.split(".")[:2]))
    return {"python": "%d.%d" % sys.version_info[:2], "os": osid}


def platform_unverified():
    """reason if Python or OS differ from what was verified on this machine (EDITORTRACK_PYTHON_VERIFIED /
    EDITORTRACK_OS_VERIFIED, written by make-env.sh at install). Unset = unverified -> no Resolve calls."""
    ids = platform_ids()
    py = (os.environ.get("EDITORTRACK_PYTHON_VERIFIED") or "").strip()
    if py != ids["python"]:
        return "python_unverified:%s" % ids["python"]
    o = (os.environ.get("EDITORTRACK_OS_VERIFIED") or "").strip()
    if o and o != ids["os"]:                     # OS pin optional (owner decision: kernel updates are frequent)
        return "os_unverified:%s" % ids["os"]
    return None


# ---------- circuit breaker ----------
def tripped():
    t = _read("TRIPPED")
    if t is None:
        return None
    return t.get("reason") or "corrupt"


def trip(reason, detail=None):
    """Stop all Resolve calls until a human removes ~/.editortrack/TRIPPED (or the runbook)."""
    if tripped():
        return
    _write("TRIPPED", {"reason": reason, "detail": detail, "at": time.time(), "version": AGENT_VERSION})


# ---------- backoff ----------
def backoff_active(now=None):
    b = _read("backoff.json") or {}
    if b.get("_corrupt"):
        return True
    return (now or time.time()) < float(b.get("next_at", 0))


def backoff_fail(reason, now=None):
    now = now or time.time()
    b = _read("backoff.json") or {}
    n = int(b.get("fails", 0)) + 1 if not b.get("_corrupt") else 1
    wait = min(BACKOFF_BASE_SEC * (2 ** (n - 1)), BACKOFF_MAX_SEC)
    _write("backoff.json", {"fails": n, "next_at": now + wait, "reason": reason})
    return wait


def backoff_ok():
    if (state_dir() / "backoff.json").exists():
        try:
            (state_dir() / "backoff.json").unlink()
        except Exception:
            pass


def backoff_state():
    return _read("backoff.json") or {}


# ---------- lease (remote kill switch) ----------
def control_url(servers):
    """/agent/control on the same server as the first ingest URL."""
    if os.environ.get("EDITORTRACK_CONTROL_URL"):
        return os.environ["EDITORTRACK_CONTROL_URL"]
    for u in servers:
        if u.rstrip("/").endswith("/ingest"):
            return u.rstrip("/")[: -len("/ingest")] + "/agent/control"
    return None


def refresh_lease(servers, token, host, fetch=None, now=None):
    """Ask the server whether this host may poll Resolve. Stores {enabled, until}.
    Network failure keeps the previous lease (which simply expires)."""
    now = now or time.time()
    url = control_url(servers)
    if not url:
        return None
    try:
        if fetch is None:
            import urllib.request, urllib.parse
            q = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"host": host, "v": AGENT_VERSION})
            req = urllib.request.Request(q, headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=LEASE_TIMEOUT_SEC) as r:
                body = json.loads(r.read().decode("utf-8"))
        else:
            body = fetch(url)
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    enabled = body.get("enabled") is True
    lease = max(0, min(int(body.get("lease_sec") or 0), 7 * 86400))
    rec = {"enabled": enabled, "until": now + lease if enabled else 0, "at": now,
           "allow_resolve": body.get("allow_resolve", True) is True}
    _write("lease.json", rec)
    return rec


def lease_ok(now=None):
    """True only with a fresh, explicit grant. Missing/corrupt/expired -> False (fail closed)."""
    if (os.environ.get("EDITORTRACK_REQUIRE_LEASE") or "1").strip() == "0":
        return True          # dev/test only; station templates must never set this (tested)
    l = _read("lease.json")
    if not l or l.get("_corrupt") or l.get("enabled") is not True:
        return False
    return (now or time.time()) < float(l.get("until", 0))


def lease_allows_resolve():
    if (os.environ.get("EDITORTRACK_REQUIRE_LEASE") or "1").strip() == "0":
        return True
    l = _read("lease.json") or {}
    return l.get("allow_resolve", True) is True

"""shared helpers: isolated env + state dir, module reload (agent modules read env at import)."""
import contextlib, importlib, os, sys, tempfile, pathlib

AGENT = pathlib.Path(__file__).resolve().parents[1]
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

BASE_ENV = {
    "EDITORTRACK_RESOLVE_VERIFIED": "20.2",
    "EDITORTRACK_REQUIRE_LEASE": "0",
    "EDITORTRACK_SCAN_MIN_IDLE_SEC": "5",
    "EDITORTRACK_PLAYBACK_PROBE_MS": "0",
    "EDITORTRACK_HARD_TIMEOUT_SEC": "0",
    "EDITORTRACK_SERVER": "http://127.0.0.1:9/ingest",
    "EDITORTRACK_HTTP_TIMEOUT": "1",
    "EDITORTRACK_LEASE_TIMEOUT_SEC": "1",
    "EDITORTRACK_PYTHON_VERIFIED": "%d.%d" % sys.version_info[:2],
}
MODULES = ["resolve_guard", "safety", "grade_detect", "resolve_poller", "scope", "collect", "runner"]


@contextlib.contextmanager
def agent_env(**overrides):
    """Isolated env (all EDITORTRACK_* cleared, BASE_ENV + overrides) and a temp state dir.
    yields (modules dict, state_dir). value None = unset."""
    saved = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="et-test-")
    try:
        for k in [k for k in os.environ if k.startswith("EDITORTRACK_")]:
            del os.environ[k]
        env = dict(BASE_ENV, EDITORTRACK_STATE_DIR=os.path.join(tmp, "state"))
        env.update(overrides)
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        mods = {}
        for m in MODULES:
            mods[m] = importlib.reload(sys.modules[m]) if m in sys.modules else importlib.import_module(m)
        yield mods, pathlib.Path(env["EDITORTRACK_STATE_DIR"])
    finally:
        os.environ.clear()
        os.environ.update(saved)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def idle(sec):
    return lambda: sec

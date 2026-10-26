"""SAFETY: the agent can only ever call allowlisted, read-only Resolve scripting methods.

Static half: parse every agent module and enumerate every Resolve method name the code can
call (obj.Name(...), _safe(obj, "Name"), getattr(obj, "Name"), probe specs ("Name", ...)).
Fails if a name is not on resolve_guard's allowlist, if a denied name appears in a runtime
module, or if a method name is computed at runtime (cannot be checked statically).

Dynamic half: run the poller against the mock Resolve under every feature-flag combination
and with a fault injected at every call site; the mock logs every name it receives, so the
test proves forbidden names never reach Resolve (not merely that they failed).

marker: safety  (run: ./run_tests.sh safety)
"""
import ast, itertools, os, pathlib, re, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tests.util import agent_env, idle, AGENT          # noqa: E402
from tests.mock_resolve import World, loader            # noqa: E402
import resolve_guard as RG                              # noqa: E402

SAFETY = True

RUNTIME = ["runner.py", "collect.py", "resolve_poller.py", "grade_detect.py", "scope.py", "joblink.py",
           "fs_collector.py", "subtitle_collector.py", "safety.py", "resolve_guard.py", "activity_sampler.py"]
PROBES = ["probe.py", "probe_grade.py"]

# CamelCase names that are OS / stdlib APIs, per file (never Resolve objects).
OS_NAMES = {
    "activity_sampler.py": {"CGEventSourceSecondsSinceLastEventType", "XOpenDisplay", "XDefaultRootWindow",
                            "XScreenSaverAllocInfo", "XScreenSaverQueryInfo", "XCloseDisplay", "GetTickCount",
                            "GetLastInputInfo", "GetForegroundWindow", "GetWindowTextLengthW", "GetWindowTextW",
                            "CDLL", "Structure", "POINTER"},
    "safety.py": {"GetCurrentProcess", "SetPriorityClass"},
    "probe.py": {"NSWorkspace"},
}
STDLIB = {"Path", "Request", "Timer", "Lock", "CDLL"}     # pathlib / urllib / threading / safety.Lock / ctypes
# Resolve objects are never handed to these helper names with a computed method name except the
# two generic wrappers whose bodies are checked by hand + the dynamic test.
DYNAMIC_OK = {("resolve_poller.py", "_safe"), ("grade_detect.py", "_safe"), ("probe.py", "call"),
              ("resolve_guard.py", "__getattr__"), ("resolve_guard.py", "call"), ("resolve_guard.py", "allowed_names"),
              ("probe.py", "probe_list")}
CAMEL = re.compile(r"^[A-Z][A-Za-z0-9]+$")


def _enclosing_funcs(tree):
    parent = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parent[ch] = node
    def func_of(n):
        while n in parent:
            n = parent[n]
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return n.name
        return None
    return func_of


def scan(fname, src=None):
    """-> (names, dynamic_sites) used against objects in this file."""
    src = (AGENT / fname).read_text(encoding="utf-8") if src is None else src
    tree = ast.parse(src)
    func_of = _enclosing_funcs(tree)
    names, dynamic = {}, []
    def add(n, node):
        names.setdefault(n, []).append(node.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and CAMEL.match(f.attr):
                add(f.attr, node)
            fn = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if fn in ("_safe", "getattr", "call") and len(node.args) >= 2:
                a = node.args[1]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if CAMEL.match(a.value) or a.value.isupper():
                        add(a.value, node)
                else:
                    dynamic.append((func_of(node), node.lineno))
        if isinstance(node, ast.Tuple) and node.elts and isinstance(node.elts[0], ast.Constant) \
                and isinstance(node.elts[0].value, str) and CAMEL.match(node.elts[0].value) \
                and node.elts[0].value.startswith(("Get", "Is", "Set", "Open", "Load", "Export")):
            add(node.elts[0].value, node)             # probe specs: ("GetName",), ("GetSetting", (...))
    return names, dynamic


ALL_ALLOWED = set(RG.ALLOWED_CORE) | set(RG.ALLOWED_EXTENDED) | set(RG.GATED) | RG.CONSTANTS


class StaticAllowlist(unittest.TestCase):
    def test_scanner_catches_violations(self):
        """the static scanner itself must see direct, _safe, getattr and dynamic forms."""
        names, dyn = scan("x.py", src="def f(tl, p, n):\n tl.SetCurrentTimecode('x')\n _safe(p, 'GetRenderJobList')\n"
                                     " getattr(p, 'OpenPage')()\n _safe(p, n)\n")
        self.assertTrue({"SetCurrentTimecode", "GetRenderJobList", "OpenPage"} <= set(names))
        self.assertEqual(len(dyn), 1)

    def test_allowlist_is_disjoint_from_denylist(self):
        self.assertFalse(ALL_ALLOWED & set(RG.DENIED))
        self.assertFalse(RG.PROBE_EXTRA & set(RG.DENIED))
        self.assertTrue(RG.PROBE_UNSAFE <= set(RG.DENIED))

    def test_allowlist_contains_only_getters(self):
        for n in set(RG.ALLOWED_CORE) | set(RG.ALLOWED_EXTENDED) | RG.PROBE_EXTRA:
            self.assertRegex(n, r"^(Get|Is)[A-Z]", n)

    def test_known_dangerous_names_are_denied(self):
        for n in ["GetRenderJobList", "GetRenderJobStatus", "OpenPage", "SetCurrentTimeline", "LoadProject",
                  "SaveProject", "SetCurrentTimecode", "StartRendering", "Quit", "GetCurrentClipThumbnailImage",
                  "SetSetting", "AddMarker", "ExportStills", "Fusion"]:
            self.assertIn(n, RG.DENIED)
            self.assertNotIn(n, ALL_ALLOWED)

    def test_runtime_modules_call_only_allowlisted_names(self):
        bad = {}
        for f in RUNTIME:
            names, _ = scan(f)
            for n, lines in names.items():
                if n in OS_NAMES.get(f, set()) or n in STDLIB or n in ALL_ALLOWED:
                    continue
                if f == "resolve_guard.py":
                    continue
                bad.setdefault(f, []).append((n, lines))
        self.assertEqual(bad, {}, "not on the read-only allowlist (resolve_guard.py): %r" % bad)

    def test_no_computed_method_names_outside_wrappers(self):
        bad = []
        for f in RUNTIME + PROBES:
            _, dyn = scan(f)
            bad += [(f, fn, ln) for fn, ln in dyn if (f, fn) not in DYNAMIC_OK]
        self.assertEqual(bad, [], "method name computed at runtime -> cannot be allowlisted")

    def test_denied_names_absent_from_runtime_source(self):
        hits = []
        for f in RUNTIME:
            if f == "resolve_guard.py":
                continue
            names, _ = scan(f)
            hits += [(f, n, names[n]) for n in names if n in RG.DENIED]
        self.assertEqual(hits, [])

    def test_probe_names_on_probe_allowlist(self):
        ok = ALL_ALLOWED | RG.PROBE_EXTRA | RG.PROBE_UNSAFE
        for f in PROBES:
            names, _ = scan(f)
            extra = {n for n in names if n not in ok and n not in OS_NAMES.get(f, set()) and n not in STDLIB}
            self.assertEqual(extra, set(), f)

    def test_only_guarded_resolve_handles(self):
        """scriptapp() result must go straight into guard(); only poller + probe load Resolve."""
        for f in RUNTIME + PROBES:
            src = (AGENT / f).read_text(encoding="utf-8")
            calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and n.func.attr == "scriptapp"]
            if not calls:
                continue
            self.assertIn(f, ("resolve_poller.py", "probe.py"), f)
            for line in src.splitlines():
                if "scriptapp(" in line and not line.strip().startswith(("#", '"', "'")) and "guard(" not in line:
                    self.assertRegex(line, r"raw = bmd\.scriptapp")
            if f == "resolve_poller.py":
                self.assertIn("return guard(raw,", src)

    def test_runtime_import_closure_excludes_probes(self):
        seen, todo = set(), ["runner"]
        local = {p.stem for p in AGENT.glob("*.py")}
        while todo:
            m = todo.pop()
            if m in seen:
                continue
            seen.add(m)
            tree = ast.parse((AGENT / (m + ".py")).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                todo += [x for x in mods if x in local]
        self.assertFalse(seen & {"probe", "probe_grade"}, seen)
        self.assertTrue(set(f[:-3] for f in RUNTIME if f != "activity_sampler.py") >= (seen - {"activity_sampler"}))

    def test_activity_sampler_never_touches_resolve(self):
        src = (AGENT / "activity_sampler.py").read_text(encoding="utf-8")
        for bad in ("DaVinciResolveScript", "resolve_poller", "resolve_guard", "scriptapp"):
            self.assertNotIn(bad, src)

    def test_removed_flags_stay_removed(self):
        src = (AGENT / "resolve_poller.py").read_text(encoding="utf-8")
        self.assertNotIn('_envflag("EDITORTRACK_POLL_RENDER"', src)
        self.assertNotIn('_envflag("EDITORTRACK_RESTORE_PAGE"', src)


FLAGS = {
    "EDITORTRACK_DETECT_GRADE": ["0", "1"],
    "EDITORTRACK_EXTRACT_SHOTS": ["0", "1"],
    "EDITORTRACK_EXTRACT_CLIP_META": ["0", "1"],
    "EDITORTRACK_GRADE_METHOD": ["lut", "nodes"],
    "EDITORTRACK_ALLOW_EXPORT_LUT": ["0", "1"],
}
LEGACY = {"EDITORTRACK_POLL_RENDER": "1", "EDITORTRACK_RESTORE_PAGE": "1"}   # must have no effect


class DynamicAllowlist(unittest.TestCase):
    def _run(self, env, world=None, idle_sec=30):
        """exceptions escaping snapshot() are allowed (runner._collect_safely turns them into a
        backoff) — what matters here is which names reached the mock."""
        with agent_env(**env) as (m, _):
            w = world or World(n_clips=6, tracks=2)
            allowed = RG.allowed_names()
            try:
                snap = m["resolve_poller"].snapshot(idle_fn=idle(idle_sec), load=loader(w))
            except Exception as e:
                snap = {"ok": False, "escaped": type(e).__name__}
            return snap, w, allowed

    def assertClean(self, w, allowed, page="color"):
        got = set(w.calls)
        self.assertFalse(got & set(RG.DENIED), got & set(RG.DENIED))
        self.assertTrue(got <= allowed, got - allowed)
        self.assertEqual(w.page, page, "page changed")
        self.assertEqual(w.current_timeline, 1, "current timeline changed")

    def test_every_flag_combination(self):
        keys = list(FLAGS)
        for combo in itertools.product(*[FLAGS[k] for k in keys]):
            env = dict(zip(keys, combo), **LEGACY)
            with self.subTest(env=combo):
                snap, w, allowed = self._run(env)
                self.assertTrue(snap["ok"], snap)
                self.assertClean(w, allowed)
                lut = env["EDITORTRACK_GRADE_METHOD"] == "lut" and env["EDITORTRACK_ALLOW_EXPORT_LUT"] == "1" \
                    and env["EDITORTRACK_DETECT_GRADE"] == "1"
                self.assertEqual("ExportLUT" in w.calls, lut)

    def test_fault_at_every_call_site(self):
        """an exception or a None at any single call never leads to an unsafe call or a crash."""
        for env in ({}, {"EDITORTRACK_EXTRACT_CLIP_META": "1", "EDITORTRACK_ALLOW_EXPORT_LUT": "1"}):
            _, base, _ = self._run(env)
            n = len(base.calls)
            for k in range(1, n + 1):
                w = World(n_clips=6, tracks=2)
                w.fault_at = (k, RuntimeError("boom"))
                with self.subTest(env=env, k=k):
                    snap, w, allowed = self._run(env, world=w)
                    self.assertClean(w, allowed)
            for name in sorted(set(base.calls)):
                w = World(n_clips=6, tracks=2)
                w.faults[name] = "none"
                with self.subTest(env=env, none=name):
                    snap, w, allowed = self._run(env, world=w)
                    self.assertClean(w, allowed)

    def test_forbidden_attempt_never_reaches_resolve(self):
        with agent_env() as (m, _):
            w = World()
            rp = m["resolve_poller"]
            orig = rp._offline_target

            def evil(proj, fps, name):              # a future bug: someone adds a render poll
                proj.GetRenderJobList()
                return orig(proj, fps, name)
            rp._offline_target = evil
            snap = rp.snapshot(idle_fn=idle(30), load=loader(w))
            self.assertEqual(snap["reason"], "forbidden_call")
            self.assertNotIn("GetRenderJobList", w.calls)
            self.assertEqual(w.page, "color")

    def test_guard_refuses_setattr_and_unknown_getters(self):
        ctx = RG.Ctx()
        r = RG.guard(__import__("tests.mock_resolve", fromlist=["Resolve"]).Resolve(World()), ctx)
        with self.assertRaises(RG.ForbiddenCall):
            r.GetMediaStorage()
        with self.assertRaises(RG.ForbiddenCall):
            r.Fusion()
        with self.assertRaises(RG.ForbiddenCall):
            r.foo = 1
        self.assertEqual(ctx.violations, ["GetMediaStorage", "Fusion"])


if __name__ == "__main__":
    unittest.main()

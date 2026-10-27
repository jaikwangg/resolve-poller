"""SECURITY phase 2: per-host machine tokens, read pages behind auth, retention, server-side
data minimisation, data dir default. marker: security
run: cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_security -v
"""
import datetime, json, os, pathlib, shutil, stat, subprocess, sys, tempfile, unittest

HERE = pathlib.Path(__file__).parent
TMP = tempfile.mkdtemp(prefix="et-sec-")
import atexit                                         # noqa: E402
atexit.register(shutil.rmtree, TMP, True)
os.environ["EDITORTRACK_STORE"] = str(pathlib.Path(TMP) / "store")
for k in [k for k in os.environ if k.startswith("ERP_")]:
    del os.environ[k]
sys.path.insert(0, str(HERE.parent))
from tests import authfix          # noqa: E402
TM, RU = authfix.install(hosts=("grade-01", "conform-01"), tmp=pathlib.Path(TMP) / "auth")

import auth                        # noqa: E402
import dashboard_api as api        # noqa: E402
import retention                   # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MARKERS = {"security"}
# the shared token that was published in the public repo; built here so the literal stays out of grep
LEAKED = "-".join(["kpp", "et", "test"])
READ_PATHS = ["/", "/report", "/inbox", "/projects", "/deck", "/health", "/api/jobs", "/api/shots",
              "/api/dashboard", "/api/inbox", "/api/pms", "/api/report", "/api/projects", "/api/health",
              "/docs", "/openapi.json", "/does-not-exist"]


def rec(host="grade-01", **extra):
    r = {"schema": 3, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "host": host,
         "stage": "color", "activity": {"idle_sec": 3.0, "active": True, "resolve_frontmost": True}}
    r.update(extra)
    return r


def stored():
    out = []
    for f in sorted(api.STORE.glob("*.jsonl")):
        out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(api.app)
        self.env = dict(os.environ)
        for f in api.STORE.glob("*.jsonl"):
            f.unlink()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        os.chmod(TM, 0o600)
        os.chmod(RU, 0o600)


class ReadAuth(Base):
    def test_every_read_route_needs_auth(self):
        for p in READ_PATHS:
            r = self.c.get(p)
            self.assertEqual(r.status_code, 401, p)
            self.assertIn("Basic", r.headers.get("www-authenticate", ""), p)

    def test_methods_slashes_and_encoded_paths_need_auth(self):
        for method, p in (("HEAD", "/api/jobs"), ("OPTIONS", "/api/jobs"), ("GET", "/api/jobs/"),
                          ("GET", "//api/jobs"), ("GET", "/%61pi/jobs"), ("GET", "/api%2Fjobs"),
                          ("GET", "/livez/"), ("GET", "/Ingest"), ("GET", "/agent/control/"),
                          ("POST", "/ingest/"), ("GET", "/deck.html")):
            self.assertEqual(self.c.request(method, p).status_code, 401, (method, p))

    def test_machine_token_does_not_grant_read(self):
        for p in ("/api/jobs", "/api/inbox", "/"):
            self.assertEqual(self.c.get(p, headers=authfix.machine("grade-01")).status_code, 401, p)

    def test_basic_and_read_token(self):
        for h in (authfix.basic(), authfix.reader()):
            self.assertEqual(self.c.get("/api/jobs", headers=h).status_code, 200)
            self.assertEqual(self.c.get("/", headers=h).status_code, 200)
        self.assertEqual(self.c.get("/api/jobs", headers=authfix.basic(pw="wrong-password-0123456789xx")).status_code, 401)
        self.assertEqual(self.c.get("/api/jobs", headers=authfix.basic(user="nobody")).status_code, 401)
        self.assertEqual(self.c.get("/api/jobs", headers={"Authorization": "Basic !!!"}).status_code, 401)
        self.assertEqual(self.c.get("/api/jobs", headers={"Authorization": "Bearer " + LEAKED}).status_code, 401)

    def test_docs_routes_are_gone(self):
        for p in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.c.get(p, headers=authfix.basic()).status_code, 404, p)

    def test_livez_open_and_empty(self):
        r = self.c.get("/livez")
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True}))

    def test_fail_closed_without_or_with_open_users_file(self):
        del os.environ["EDITORTRACK_READ_USERS"]
        self.assertEqual(self.c.get("/api/jobs", headers=authfix.basic()).status_code, 401)
        os.environ["EDITORTRACK_READ_USERS"] = self.env["EDITORTRACK_READ_USERS"]
        os.chmod(RU, 0o644)
        self.assertEqual(self.c.get("/api/jobs", headers=authfix.basic()).status_code, 401)

    def test_off_only_when_explicit(self):
        os.environ["EDITORTRACK_READ_AUTH"] = "off"
        self.assertEqual(self.c.get("/api/jobs").status_code, 200)
        os.environ["EDITORTRACK_READ_AUTH"] = "anything-else"
        self.assertEqual(self.c.get("/api/jobs").status_code, 401)


class MachineAuth(Base):
    def test_ingest_without_or_unknown_token_401(self):
        self.assertEqual(self.c.post("/ingest", json=rec()).status_code, 401)
        self.assertEqual(self.c.post("/ingest", json=rec(), headers={"Authorization": "Bearer x" * 30}).status_code, 401)
        self.assertEqual(self.c.post("/ingest", json=rec(), headers=authfix.basic()).status_code, 401)
        self.assertEqual(stored(), [])

    def test_leaked_shared_token_401_even_if_put_back_in_map(self):
        h = {"Authorization": "Bearer " + LEAKED}
        self.assertEqual(self.c.post("/ingest", json=rec(), headers=h).status_code, 401)
        os.environ["EDITORTRACK_TOKEN"] = LEAKED          # the old env var is ignored
        self.assertEqual(self.c.post("/ingest", json=rec(), headers=h).status_code, 401)
        m = json.loads(TM.read_text())
        bad = pathlib.Path(TMP) / "bad.json"
        bad.write_text(json.dumps({"hosts": dict(m["hosts"], **{"grade-01": auth.digest(LEAKED)})}))
        os.chmod(bad, 0o600)
        os.environ["EDITORTRACK_TOKEN_MAP"] = str(bad)
        self.assertEqual(self.c.post("/ingest", json=rec(), headers=h).status_code, 401)
        self.assertEqual(self.c.get("/agent/control?host=grade-01", headers=h).status_code, 401)
        self.assertEqual(stored(), [])

    def test_token_bound_to_its_host(self):
        self.assertEqual(self.c.post("/ingest", json=rec("conform-01"), headers=authfix.machine("grade-01")).status_code, 403)
        self.assertEqual(self.c.post("/ingest", json=rec("grade-01"), headers=authfix.machine("grade-01")).status_code, 200)
        self.assertEqual(self.c.get("/agent/control?host=conform-01", headers=authfix.machine("grade-01")).status_code, 403)
        self.assertEqual(self.c.get("/agent/control?host=grade-01", headers=authfix.machine("grade-01")).status_code, 200)
        self.assertEqual(self.c.get("/agent/control?host=grade-01").status_code, 401)

    def test_host_mismatch_is_not_final_but_a_bad_record_is(self):
        # merge of security-min with F9 (agent >= 0.9.1 drops a record only on a 400 carrying X-Editortrack-Reject):
        # a 403 (token of another host) describes the configuration, so it must not carry the marker; a malformed
        # record is refused with the marker before the host check, so it can never sit in a queue behind a 403
        r = self.c.post("/ingest", json=rec("conform-01"), headers=authfix.machine("grade-01"))
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("x-editortrack-reject", r.headers)
        for bad, why in ((dict(rec("grade-01"), host=""), "bad_host"),
                         ({k: v for k, v in rec("grade-01").items() if k != "host"}, "bad_host"),
                         (dict(rec("grade-01"), ts="2026-10-14T10:05:00"), "ts_without_offset")):
            r = self.c.post("/ingest", json=bad, headers=authfix.machine("grade-01"))
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.headers.get("x-editortrack-reject"), why)
        r = self.c.post("/ingest", content=b"{not json", headers=authfix.machine("grade-01"))
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("x-editortrack-reject", r.headers)   # not about a record: the agent keeps and retries
        self.assertEqual(stored(), [])

    def test_map_missing_or_group_readable_fails_closed(self):
        os.chmod(TM, 0o640)
        self.assertEqual(self.c.post("/ingest", json=rec(), headers=authfix.machine("grade-01")).status_code, 401)
        os.chmod(TM, 0o600)
        del os.environ["EDITORTRACK_TOKEN_MAP"]
        self.assertEqual(self.c.post("/ingest", json=rec(), headers=authfix.machine("grade-01")).status_code, 401)

    def test_bad_json_body_400(self):
        r = self.c.post("/ingest", content=b"{nope", headers=dict(authfix.machine("grade-01"), **{"Content-Type": "application/json"}))
        self.assertEqual(r.status_code, 400)

    def test_rotation_with_tool_no_restart(self):
        d = pathlib.Path(TMP) / "rot"
        d.mkdir(exist_ok=True)
        mp = d / "tokens.json"
        os.environ["EDITORTRACK_TOKEN_MAP"] = str(mp)
        tool = [sys.executable, str(HERE.parent / "tokens.py")]
        out = subprocess.run(tool + ["host-add", "grade-01", "--map", str(mp)], capture_output=True, text=True, check=True)
        tok1 = out.stdout.strip().splitlines()[-1]
        self.assertEqual(stat.S_IMODE(mp.stat().st_mode), 0o600)
        self.assertNotIn(tok1, mp.read_text())                      # only the hash is on disk
        h1 = {"Authorization": "Bearer " + tok1}
        self.assertEqual(self.c.post("/ingest", json=rec(), headers=h1).status_code, 200)
        out = subprocess.run(tool + ["host-add", "grade-01", "--map", str(mp)], capture_output=True, text=True, check=True)
        tok2 = out.stdout.strip().splitlines()[-1]
        self.assertEqual(self.c.post("/ingest", json=rec(), headers=h1).status_code, 401)   # old token dead
        self.assertEqual(self.c.post("/ingest", json=rec(), headers={"Authorization": "Bearer " + tok2}).status_code, 200)
        subprocess.run(tool + ["host-rm", "grade-01", "--map", str(mp)], capture_output=True, check=True)
        self.assertEqual(self.c.post("/ingest", json=rec(), headers={"Authorization": "Bearer " + tok2}).status_code, 401)


class Minimisation(Base):
    def test_ingest_drops_personal_fields_and_writes_private_file(self):
        r = rec(user="somebody", activity={"idle_sec": 400.0, "active": False, "resolve_frontmost": False,
                                           "frontmost_name": "loginwindow", "frontmost_bundle": "com.apple.loginwindow"})
        self.assertEqual(self.c.post("/ingest", json=r, headers=authfix.machine("grade-01")).status_code, 200)
        [s] = stored()
        self.assertNotIn("user", s)
        self.assertNotIn("frontmost_name", s["activity"])
        self.assertNotIn("frontmost_bundle", s["activity"])
        self.assertTrue(s["activity"]["locked"])
        self.assertEqual(sorted(s["_rx"]), ["at", "ip"])
        [f] = list(api.STORE.glob("*.jsonl"))
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        blob = json.dumps(s)
        self.assertNotIn("somebody", blob)

    def test_server_added_fields_match_data_sheet(self):
        import re
        sheet = (HERE.parents[1] / "docs" / "DATA_SHEET.md").read_text(encoding="utf-8")
        block = re.search(r"<!-- fields:server:begin -->(.*?)<!-- fields:server:end -->", sheet, re.S).group(1)
        self.assertEqual(set(re.findall(r"^\|\s*`([^`]+)`", block, re.M)), {"_rx.at", "_rx.ip"})

    def test_store_default_outside_repo(self):
        src = (HERE.parent / "dashboard_api.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("EDITORTRACK_STORE") or "/srv/editortrack"', src)
        self.assertEqual(retention.DEFAULT_STORE, "/srv/editortrack")


class Retention(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="et-ret-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.today = datetime.date(2026, 10, 26)
        for age in (0, 1, 89, 90, 91, 400):
            day = self.today - datetime.timedelta(days=age)
            (self.d / f"{day.isoformat()}.jsonl").write_text(json.dumps({"host": "h", "user": "u"}) + "\n")
        for name in ("agent_control.json", "notes.jsonl", "2026-13-45.jsonl", "2020-01-01.jsonl.bak"):
            (self.d / name).write_text("{}")

    def names(self):
        return sorted(p.name for p in self.d.iterdir())

    def test_dry_run_deletes_nothing(self):
        before = self.names()
        gone = retention.run(self.d, 90, dry_run=True, today=self.today, echo=lambda *_: None)
        self.assertEqual(sorted(p.name for p in gone), ["2025-09-21.jsonl", "2026-07-27.jsonl"])
        self.assertEqual(self.names(), before)

    def test_deletes_only_older_than_n_days(self):
        retention.run(self.d, 90, today=self.today, echo=lambda *_: None)
        left = self.names()
        self.assertNotIn("2026-07-27.jsonl", left)                 # 91 days
        self.assertNotIn("2025-09-21.jsonl", left)                 # 400 days
        for keep in ("2026-10-26.jsonl", "2026-10-25.jsonl", "2026-07-29.jsonl", "2026-07-28.jsonl",
                     "agent_control.json", "notes.jsonl", "2026-13-45.jsonl", "2020-01-01.jsonl.bak"):
            self.assertIn(keep, left)

    def test_scrub_personal(self):
        retention.run(self.d, 90, dry_run=True, scrub=True, today=self.today, echo=lambda *_: None)
        self.assertIn('"user"', (self.d / "2026-10-25.jsonl").read_text())
        retention.run(self.d, 90, scrub=True, today=self.today, echo=lambda *_: None)
        self.assertNotIn('"user"', (self.d / "2026-10-25.jsonl").read_text())
        self.assertEqual(stat.S_IMODE((self.d / "2026-10-25.jsonl").stat().st_mode), 0o600)
        self.assertIn('"user"', (self.d / "2026-10-26.jsonl").read_text())       # today: server may be appending
        retention.run(self.d, 90, scrub=True, today=self.today, echo=lambda *_: None, include_today=True)
        self.assertNotIn('"user"', (self.d / "2026-10-26.jsonl").read_text())

    def test_days_from_env_and_validation(self):
        os.environ["EDITORTRACK_RETENTION_DAYS"] = "30"
        try:
            self.assertEqual(retention.retention_days(), 30)
        finally:
            del os.environ["EDITORTRACK_RETENTION_DAYS"]
        self.assertEqual(retention.retention_days(), 90)
        for bad in ("0", "-5", "x"):
            with self.assertRaises(SystemExit):
                retention.retention_days(bad)

    def test_cli(self):
        r = subprocess.run([sys.executable, str(HERE.parent / "retention.py"), "--store", str(self.d), "--days", "1",
                            "--dry-run"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would delete", r.stdout)
        self.assertEqual(len(list(self.d.glob("*.jsonl"))), 8)


if __name__ == "__main__":
    unittest.main()

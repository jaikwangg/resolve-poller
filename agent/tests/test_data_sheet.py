"""PRIVACY: the record an agent sends has exactly the fields listed in docs/DATA_SHEET.md.

Builds real records with runner.build_record (mock Resolve for conform/color, temp folders for
data/master/subtitle) in every state that changes the shape: ok, skipped, sampler failure,
screen locked. The union of the field paths must equal the data sheet's agent list, so a new
field cannot reach the server without the consent sheet being updated first. Also checks the
data-minimisation defaults (no login name, no app names, no shot codes, no LUT names).
marker: privacy
"""
import json, pathlib, re, sys, tempfile, shutil, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tests.util import agent_env, AGENT              # noqa: E402
from tests.mock_resolve import World, loader          # noqa: E402

MARKERS = {"privacy"}
SHEET = AGENT.parent / "docs" / "DATA_SHEET.md"
# values whose keys come from free text in Resolve markers or vary per collector: one entry each
OPEN = {"source.scope_hint", "source.job_hint", "source.detail", "progress.detail.parts"}
FULL_SAMPLE = {"idle_sec": 30.0, "frontmost_name": "Some Other App", "frontmost_bundle": "com.example.app",
               "resolve_frontmost": True, "platform": "linux"}


def sheet_fields(block):
    text = SHEET.read_text(encoding="utf-8")
    m = re.search(r"<!-- fields:%s:begin -->(.*?)<!-- fields:%s:end -->" % (block, block), text, re.S)
    assert m, "block %s missing in DATA_SHEET.md" % block
    return {f for f in re.findall(r"^\|\s*`([^`]+)`", m.group(1), re.M)}


def paths(d, prefix=""):
    out = set()
    for k, v in d.items():
        p = prefix + k
        if isinstance(v, dict) and v and p not in OPEN:
            out |= paths(v, p + ".")
        else:
            out.add(p)
    return out


def record(stage, sample=FULL_SAMPLE, world=None, **env):
    with agent_env(EDITORTRACK_STAGE=stage, **env) as (m, sd):
        m["resolve_poller"]._load_resolve = loader(world or World())
        m["runner"].sample = lambda: dict(sample)
        return m["runner"].build_record()


class DataSheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="et-sheet-"))
        cls.addClassCleanup(shutil.rmtree, tmp, True)
        (tmp / "out").mkdir()
        (tmp / "out" / "a.mov").write_bytes(b"x" * 10)
        (tmp / "subs").mkdir()
        (tmp / "subs" / "ep1.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n\n", encoding="utf-8")
        cls.recs = [
            record("color"),
            record("conform"),
            record("color", sample=dict(FULL_SAMPLE, idle_sec=0.0)),                 # user active -> skipped
            record("color", sample={"idle_sec": None, "frontmost_name": "", "resolve_frontmost": False,
                                    "unavailable": True, "error": "boom", "platform": "linux"}),
            record("conform", sample=dict(FULL_SAMPLE, frontmost_name="loginwindow", resolve_frontmost=False)),
            record("data", EDITORTRACK_WATCH_DIR=str(tmp / "out")),
            record("master", EDITORTRACK_WATCH_DIR=str(tmp / "nope")),                # not ok, dict detail
            record("subtitle", EDITORTRACK_SUBTITLE_PATH=str(tmp / "subs")),
        ]

    def test_record_keys_equal_data_sheet(self):
        got = set().union(*(paths(r) for r in self.recs))
        want = sheet_fields("agent")
        self.assertEqual(sorted(got - want), [], "sent but not on the data sheet")
        self.assertEqual(sorted(want - got), [], "on the data sheet but never produced (stale sheet or test gap)")

    def test_no_personal_fields(self):
        for r in self.recs:
            self.assertNotIn("user", r)
            for k in ("frontmost_name", "frontmost_bundle"):
                self.assertNotIn(k, r["activity"])
        self.assertTrue(self.recs[4]["activity"].get("locked"))
        self.assertNotIn("locked", self.recs[0]["activity"])

    def test_opt_in_fields_off_by_default(self):
        blob = json.dumps(self.recs[0]) + json.dumps(self.recs[1])
        for k in ('"shots"', '"clips_detail"', '"luts"', "SH010"):   # SH010 = shot code in the mock markers
            self.assertNotIn(k, blob)
        with agent_env() as (m, sd):
            self.assertFalse(m["resolve_poller"].EXTRACT_SHOTS)
            self.assertFalse(m["resolve_poller"].EXTRACT_CLIP_META)

    def test_opt_in_fields_listed(self):
        opt = sheet_fields("optin")
        for k in ("source.shots", "source.clips_detail", "source.clips_detail_count"):
            self.assertIn(k, opt)
        with_shots = record("color", EDITORTRACK_EXTRACT_SHOTS="1")
        self.assertIn("shots", with_shots["source"])
        self.assertEqual(paths(with_shots) - sheet_fields("agent"), {"source.shots"})

    def test_lut_names_never_collected(self):
        with agent_env(EDITORTRACK_EXTRACT_CLIP_META="1") as (m, sd):
            w = World()
            m["resolve_poller"]._load_resolve = loader(w)
            m["runner"].sample = lambda: dict(FULL_SAMPLE)
            r = m["runner"].build_record()
        blob = json.dumps(r)
        self.assertIn("clips_detail", blob)
        self.assertNotIn('"luts"', blob)


if __name__ == "__main__":
    unittest.main()

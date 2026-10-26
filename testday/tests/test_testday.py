"""Tests for the phase-3 test-day kit (what can be tested without a workstation, Resolve or ffmpeg)."""
import ast
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

MARKERS = {"testday"}   # ./run_tests.sh testday

KIT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import agent_cost  # noqa: E402
import common  # noqa: E402
import imgdiff  # noqa: E402
import make_dummy_project as mdp  # noqa: E402
import playback_trials  # noqa: E402
import prefs_snapshot  # noqa: E402
import render_timer  # noqa: E402
import screen_watch  # noqa: E402
import turn_stats  # noqa: E402
import drill  # noqa: E402


def solid(w, h, rgb=(40, 40, 40)):
    return imgdiff.Frame(w, h, 3, [bytes(rgb) * w for _ in range(h)])


def paint(frame, x0, y0, x1, y1, rgb):
    rows = list(frame.rows)
    for y in range(y0, y1):
        r = bytearray(rows[y])
        r[x0 * 3:x1 * 3] = bytes(rgb) * (x1 - x0)
        rows[y] = bytes(r)
    return imgdiff.Frame(frame.w, frame.h, 3, rows)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="et-testday-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------- image I/O + diff
class ImageIO(Tmp):
    def test_png_roundtrip(self):
        f = paint(solid(37, 11), 3, 2, 9, 7, (200, 10, 99))
        g = imgdiff.read_png(imgdiff.write_png(f))
        self.assertEqual((g.w, g.h, g.bpp), (37, 11, 3))
        self.assertEqual(g.rows, f.rows)

    def test_png_all_filters(self):
        w, h, bpp = 9, 5, 3
        rows = [bytes((x * 7 + y * 31 + c * 50) % 256 for x in range(w) for c in range(bpp)) for y in range(h)]

        def enc(ft, cur, prev):
            out = bytearray()
            for i in range(len(cur)):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pred = {0: 0, 1: a, 2: b, 3: (a + b) >> 1, 4: imgdiff._paeth(a, b, c)}[ft]
                out.append((cur[i] - pred) & 255)
            return bytes([ft]) + bytes(out)

        raw, prev = b"", bytes(w * bpp)
        for y, r in enumerate(rows):
            raw += enc(y % 5, r, prev)
            prev = r

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        self.assertEqual(imgdiff.read_png(png).rows, rows)

    def test_bmp_roundtrip_keeps_rgb_order(self):
        f = paint(solid(10, 6), 1, 1, 4, 3, (255, 0, 0))
        b = imgdiff.write_bmp(f)
        g = imgdiff.read_bmp(b)
        self.assertEqual(g.rows, f.rows)
        # on disk BMP is BGR: the red pixel is stored as 00 00 FF
        self.assertIn(b"\x00\x00\xff", b)

    def test_read_image_dispatch(self):
        p = os.path.join(self.tmp, "a.png")
        with open(p, "wb") as fh:
            fh.write(imgdiff.write_png(solid(4, 4)))
        self.assertEqual(imgdiff.read_image(p).w, 4)


class Diff(unittest.TestCase):
    def test_identical(self):
        a = solid(64, 36)
        self.assertFalse(imgdiff.diff(a, solid(64, 36))["changed"])

    def test_one_pixel_bbox(self):
        a = solid(64, 36)
        b = paint(a, 10, 20, 11, 21, (41, 40, 40))     # one channel off by one
        d = imgdiff.diff(a, b)
        self.assertTrue(d["changed"])
        self.assertEqual(d["bbox"], (10, 20, 11, 21))

    def test_block_bbox(self):
        a = solid(64, 36)
        b = paint(a, 5, 6, 30, 9, (255, 255, 255))
        self.assertEqual(imgdiff.diff(a, b)["bbox"], (5, 6, 30, 9))

    def test_mask_ignores_clock(self):
        a = solid(100, 50)
        b = paint(a, 92, 0, 99, 2, (250, 250, 250))       # "clock" top right
        self.assertTrue(imgdiff.diff(a, b)["changed"])
        self.assertFalse(imgdiff.diff(a, b, masks=[(0.9, 0, 0.1, 0.05)])["changed"])
        c = paint(b, 10, 10, 12, 12, (0, 0, 0))            # plus a real change elsewhere
        self.assertEqual(imgdiff.diff(a, c, masks=[(0.9, 0, 0.1, 0.05)])["bbox"], (10, 10, 12, 12))

    def test_tolerance(self):
        a = solid(20, 10)
        b = paint(a, 0, 0, 20, 10, (42, 40, 40))
        self.assertTrue(imgdiff.diff(a, b)["changed"])
        self.assertFalse(imgdiff.diff(a, b, tol=3)["changed"])

    def test_resize_is_change(self):
        self.assertTrue(imgdiff.diff(solid(10, 10), solid(12, 10))["changed"])

    def test_bbox_inside(self):
        self.assertTrue(imgdiff.bbox_inside((92, 0, 99, 2), (0.9, 0, 0.1, 0.05), 100, 50))
        self.assertFalse(imgdiff.bbox_inside((80, 0, 99, 2), (0.9, 0, 0.1, 0.05), 100, 50))

    def test_bad_rect(self):
        with self.assertRaises(ValueError):
            imgdiff.parse_rect("1,2,3")


# ---------------------------------------------------------------- screen_watch classification
def cap_rows(spec):
    """spec: list of (t, idle, changed, bbox)"""
    return [{"epoch": str(t), "local": str(t), "idle_sec": "" if i is None else str(i), "changed": str(int(c)),
             "bbox": b or "", "w": "100", "h": "50", "file": ""} for t, i, c, b in spec]


class Classify(unittest.TestCase):
    turns = [{"start": 1000.0, "end": 1001.5, "source": "record", "reason": "ok"}]

    def test_unexplained_at_turn_fails(self):
        rows = cap_rows([(996, 50, 0, None), (998, 52, 0, None), (1000.8, 55, 1, "10,10,20,20")])
        ev, st = screen_watch.classify(rows, self.turns)
        self.assertEqual(st["fail"], 1)
        self.assertEqual(ev[0]["verdict"], "FAIL")

    def test_user_change_at_turn_ok(self):
        rows = cap_rows([(998, 50, 0, None), (1000.8, 0.4, 1, "10,10,20,20")])
        _, st = screen_watch.classify(rows, self.turns)
        self.assertEqual((st["fail"], st["user"]), (0, 1))

    def test_unexplained_away_from_turn_needs_explaining(self):
        rows = cap_rows([(1100, 50, 0, None), (1102, 52, 1, "1,1,2,2")])
        _, st = screen_watch.classify(rows, self.turns)
        self.assertEqual((st["fail"], st["explain"]), (0, 1))

    def test_window_edges(self):
        # change detected at 1004 with the previous frame at 1002: interval (1002,1004] vs window [998, 1003.5]
        rows = cap_rows([(1002, 50, 0, None), (1004, 52, 1, "1,1,2,2")])
        self.assertEqual(screen_watch.classify(rows, self.turns)[1]["fail"], 1)
        rows = cap_rows([(1004, 50, 0, None), (1006, 52, 1, "1,1,2,2")])
        self.assertEqual(screen_watch.classify(rows, self.turns)[1]["fail"], 0)

    def test_no_operator_ignores_idle(self):
        rows = cap_rows([(998, 0.1, 0, None), (1000.8, 0.1, 1, "10,10,20,20")])
        self.assertEqual(screen_watch.classify(rows, self.turns)[1]["fail"], 0)
        self.assertEqual(screen_watch.classify(rows, self.turns, no_operator=True)[1]["fail"], 1)

    def test_unknown_idle_at_turn_is_check(self):
        rows = cap_rows([(998, None, 0, None), (1000.8, None, 1, "10,10,20,20")])
        self.assertEqual(screen_watch.classify(rows, self.turns)[1]["check"], 1)

    def test_ignore_region(self):
        rows = cap_rows([(998, 50, 0, None), (1000.8, 55, 1, "92,0,99,2")])
        st = screen_watch.classify(rows, self.turns, ignore=[(0.9, 0, 0.1, 0.05)])[1]
        self.assertEqual(st["changes"], 0)

    def test_grab_errors_skipped(self):
        rows = cap_rows([(998, 50, 0, None)]) + [{"epoch": "999", "note": "grab_error:OSError"}]
        self.assertEqual(screen_watch.classify(rows, self.turns)[1]["frames"], 1)


class ScreenReplayEndToEnd(Tmp):
    """Sample images through the real CLI: replay -> analyze, with an agent.log, as on the test day."""

    def _frames(self, page_jump_at):
        ui = solid(160, 90, (30, 30, 30))
        ui = paint(ui, 0, 80, 160, 90, (60, 60, 70))        # page bar
        ui = paint(ui, 10, 82, 30, 88, (200, 120, 0))       # current page = "Edit" tab highlighted
        jumped = paint(paint(ui, 10, 82, 30, 88, (60, 60, 70)), 130, 82, 150, 88, (200, 120, 0))  # -> "Deliver"
        listing, base = [], 1761530400.0                    # 2025-10-27 02:00:00 UTC (any fixed epoch)
        for k in range(12):
            t = base + 2 * k
            f = jumped if t >= page_jump_at else ui
            if k % 5 == 4:                                   # clock in the top-right corner ticks
                f = paint(f, 150, 0, 159, 3, (255, 255, 255))
            p = os.path.join(self.tmp, "f%02d.png" % k)
            with open(p, "wb") as fh:
                fh.write(imgdiff.write_png(f))
            listing.append((t, 120 + 2 * k, p))
        lst = os.path.join(self.tmp, "frames.csv")
        with open(lst, "w") as fh:
            fh.write("epoch,idle_sec,path\n" + "".join("%s,%s,%s\n" % x for x in listing))
        return base, lst

    def _agent_log(self, turn_epoch):
        import time as _t
        p = os.path.join(self.tmp, "agent.log")
        with open(p, "w") as fh:
            fh.write("--- %s poll ---\n200\n" % _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(turn_epoch)))
        return p

    def _run(self, *args):
        return subprocess.run([sys.executable, str(KIT / "screen_watch.py")] + list(args),
                              capture_output=True, text=True, timeout=120)

    def test_page_jump_at_agent_turn_is_detected(self):
        base, lst = self._frames(page_jump_at=1761530400.0 + 12)
        run = os.path.join(self.tmp, "run")
        r = self._run("replay", "--list", lst, "--out", run, "--mask", "0.9,0,0.1,0.1")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self._run("analyze", "--run", run, "--agent-log", self._agent_log(base + 11), "--no-operator")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL", r.stdout)
        res = json.load(open(os.path.join(run, "analysis.json")))
        self.assertEqual(res["stats"]["fail"], 1)
        self.assertEqual(res["stats"]["changes"], 1)          # the masked clock never counted

    def test_static_screen_passes(self):
        base, lst = self._frames(page_jump_at=10 ** 12)      # never
        run = os.path.join(self.tmp, "run")
        self._run("replay", "--list", lst, "--out", run, "--mask", "0.9,0,0.1,0.1")
        r = self._run("analyze", "--run", run, "--agent-log", self._agent_log(base + 11), "--no-operator")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("PASS", r.stdout)

    def test_no_turns_is_not_a_pass(self):
        base, lst = self._frames(page_jump_at=10 ** 12)
        run = os.path.join(self.tmp, "run")
        self._run("replay", "--list", lst, "--out", run)
        r = self._run("analyze", "--run", run, "--no-operator")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no agent turns", r.stdout)


# ---------------------------------------------------------------- dummy project generator
class Generator(Tmp):
    def _gen(self, *extra):
        out = os.path.join(self.tmp, "dummy")
        rc = mdp.main(["--out", out, "--no-media"] + list(extra))
        self.assertEqual(rc, 0)
        return out

    def test_timelines_parse_and_counts(self):
        out = self._gen()
        man = json.load(open(os.path.join(out, "MANIFEST.json")))
        counts = {t["name"]: t["clips"] for t in man["timelines"]}
        self.assertEqual(counts["T0030_v1"], 30)
        self.assertEqual(counts["T0300_v1"], 300)
        self.assertEqual(counts["T1500_v1"], 1500)
        self.assertEqual(counts["T3000_v1"], 3000)
        self.assertEqual(counts["T0006_WC01"], 6)
        self.assertEqual(counts["T0006_WC01_plus2"], 8)
        frames = {m["path"]: m["frames"] for m in man["media"]}
        for t in man["timelines"]:
            root = ET.parse(os.path.join(out, "timelines", t["name"] + ".xml")).getroot()
            self.assertEqual(root.tag, "xmeml")
            items = root.findall(".//clipitem")
            self.assertEqual(len(items), t["clips"])
            files = {}
            for f in root.iter("file"):
                if f.find("pathurl") is not None:
                    files[f.get("id")] = f.find("pathurl").text
            pos = 0
            tracks = root.findall(".//track")
            for ci in tracks[0].findall("clipitem"):
                s, e = int(ci.find("start").text), int(ci.find("end").text)
                i, o = int(ci.find("in").text), int(ci.find("out").text)
                self.assertEqual(s, pos)                          # V1 is gap-free and in order
                self.assertEqual(e - s, o - i)
                fid = ci.find("file").get("id")
                url = files[fid]
                self.assertTrue(url.startswith("file://localhost/"))
                rel = os.path.relpath(url[len("file://localhost"):], out)
                self.assertGreater(frames[rel], 0)
                self.assertLessEqual(o, frames[rel], "clip out beyond media length")
                self.assertGreaterEqual(i, 0)
                pos = e

    def test_wc01_matches_the_real_replay(self):
        out = self._gen()
        root = ET.parse(os.path.join(out, "timelines", "T0006_WC01.xml")).getroot()
        self.assertEqual(int(root.find("sequence/duration").text), int(round(161.56 * 24)))
        r2 = ET.parse(os.path.join(out, "timelines", "T0006_WC01_plus2.xml")).getroot()
        self.assertEqual(len(r2.findall(".//track")), 2)
        self.assertEqual(int(r2.find("sequence/duration").text), int(round(161.56 * 24)))  # duration unchanged

    def test_reconform_is_a_different_cut(self):
        out = self._gen()
        a = ET.parse(os.path.join(out, "timelines", "T0300_v1.xml")).getroot()
        b = ET.parse(os.path.join(out, "timelines", "T0300_v2_reconform.xml")).getroot()
        self.assertNotEqual(len(a.findall(".//clipitem")), len(b.findall(".//clipitem")))
        self.assertNotEqual(a.find("sequence/name").text, b.find("sequence/name").text)

    def test_edl(self):
        out = self._gen()
        txt = open(os.path.join(out, "timelines", "T0300_v1.edl")).read()
        self.assertTrue(txt.startswith("TITLE: T0300_v1"))
        self.assertEqual(txt.count("* FROM CLIP NAME:"), 300)

    def test_deterministic(self):
        a = open(os.path.join(self._gen(), "timelines", "T3000_v1.edl")).read()
        shutil.rmtree(os.path.join(self.tmp, "dummy"))
        b = open(os.path.join(self._gen(), "timelines", "T3000_v1.edl")).read()
        self.assertEqual(a, b)

    def test_revision_steps_written(self):
        out = self._gen()
        txt = open(os.path.join(out, "REVISION_STEPS.txt")).read()
        for k in ("R2-add (WC02)", "R3-replace (WC07)", "R4-regrade", "R5-cut", "R7-reconform", "R8-WC01"):
            self.assertIn(k, txt)

    def test_refuses_foreign_nonempty_dir(self):
        d = os.path.join(self.tmp, "client")
        os.makedirs(d)
        open(os.path.join(d, "project.drp"), "w").close()
        with self.assertRaises(SystemExit):
            mdp.main(["--out", d, "--no-media"])
        self.assertEqual(os.listdir(d), ["project.drp"])

    def test_refuses_home(self):
        with self.assertRaises(SystemExit):
            mdp.main(["--out", os.path.expanduser("~"), "--dry-run"])

    def test_dry_run_writes_nothing(self):
        out = os.path.join(self.tmp, "dry")
        self.assertEqual(mdp.main(["--out", out, "--dry-run", "--uhd"]), 0)
        self.assertFalse(os.path.exists(out))

    def test_rerun_into_own_dir_ok(self):
        self._gen()
        self._gen()

    def test_ffmpeg_command(self):
        class A(object):
            codec, profile, uhd_profile, font = "prores", 0, 2, "/f/My:Font.ttf"
        args = A()
        plan = {"real_fps": 24.0, "ffrate": "24"}
        m = {"w": 1920, "h": 1080, "frames": 48, "pattern": "testsrc2", "hue": 47, "label": "ET DUMMY SRC_001",
             "rel": "media/src/SRC_001.mov"}
        cmd = mdp.ffmpeg_cmd("ffmpeg", m, plan, args, "/o", True)
        self.assertIn("testsrc2=size=1920x1080:rate=24:duration=2.000000", cmd)
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("hue=h=47", vf)
        self.assertIn("fontfile=/f/My\\:Font.ttf", vf)
        self.assertEqual(cmd[cmd.index("-profile:v") + 1], "0")
        self.assertEqual(cmd[-1], "/o/media/src/SRC_001.mov")
        m["w"], m["h"] = 7680, 4320
        cmd = mdp.ffmpeg_cmd("ffmpeg", m, plan, args, "/o", False)
        self.assertEqual(cmd[cmd.index("-profile:v") + 1], "2")
        self.assertNotIn("drawtext", " ".join(cmd))

    def test_pathurl_quotes_spaces(self):
        self.assertEqual(mdp.pathurl("/Volumes/My Disk/a.mov"), "file://localhost/Volumes/My%20Disk/a.mov")


# ---------------------------------------------------------------- records based measurements
def rec(t, reason="scan", calls=12, ms=5, turn_ms=300, rss=28.0, clips=300, extra=None):
    import datetime
    ts = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
    src = {"ok": True, "timeline": {"scan_ms": ms, "video_clips": clips}} if reason == "scan" else \
        {"ok": False, "reason": reason, "skipped": True}
    r = {"ts": ts, "host": "grade-01", "source": src,
         "agent": {"turn_ms": turn_ms, "rss_mb": rss, "guard": {"calls": calls, "ms": ms}}}
    r.update(extra or {})
    return r


def write_jsonl(path, recs):
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return path


class Records(Tmp):
    def test_turn_stats_p2(self):
        recs = [rec(1000 + 60 * k, ms=100 + k) for k in range(60)] + [rec(5000, "user_active", calls=8)]
        p = write_jsonl(os.path.join(self.tmp, "d.jsonl"), recs + [dict(rec(9000), host="other")])
        loaded = common.load_records([p], "grade-01")
        self.assertEqual(len(loaded), 61)
        s = turn_stats.stats(loaded, 1500)
        self.assertEqual(s["scans"], 60)
        self.assertTrue(all(s["checks"].values()), s["checks"])
        self.assertAlmostEqual(s["guard_ms_p95"], 156.05, places=2)
        s2 = turn_stats.stats(loaded, 150)
        self.assertFalse(s2["checks"]["P2_p95_guard_ms_within_budget"])

    def test_turn_stats_flags_forbidden(self):
        p = write_jsonl(os.path.join(self.tmp, "d.jsonl"), [rec(1000), rec(1060, "forbidden_call")])
        s = turn_stats.stats(common.load_records([p], "grade-01"))
        self.assertEqual(s["bad_records"], 1)
        self.assertEqual(turn_stats.main(["--records", p, "--host", "grade-01"]), 1)

    def test_load_records_window_and_garbage(self):
        p = os.path.join(self.tmp, "d.jsonl")
        with open(p, "w") as f:
            f.write("not json\n\n" + json.dumps(rec(1000)) + "\n" + json.dumps({"host": "grade-01"}) + "\n")
        self.assertEqual(len(common.load_records([p], "grade-01")), 1)
        self.assertEqual(len(common.load_records([p], "grade-01", t0=2000)), 0)


class Playback(unittest.TestCase):
    def trials(self, on_drops=0, off_drops=0):
        return [{"trial": "1", "timeline": "PB_4K", "agent": "off", "start": "1000", "end": "1120", "dropped": str(off_drops)},
                {"trial": "2", "timeline": "PB_4K", "agent": "on", "start": "2000", "end": "2120", "dropped": str(on_drops)}]

    def recs(self, *rs):
        out = []
        for r in rs:
            r["_t"] = common.parse_iso(r["ts"])
            out.append(r)
        return out

    def test_pass(self):
        res = playback_trials.evaluate(self.trials(), self.recs(rec(2060, "playback", calls=12)))
        self.assertEqual(res["PB_4K"]["verdict"], "PASS")

    def test_scan_during_playback_fails(self):
        res = playback_trials.evaluate(self.trials(), self.recs(rec(2060, "scan")))
        self.assertEqual(res["PB_4K"]["verdict"], "FAIL")

    def test_too_many_calls_fails(self):
        res = playback_trials.evaluate(self.trials(), self.recs(rec(2060, "playback", calls=13)))
        self.assertEqual(res["PB_4K"]["verdict"], "FAIL")

    def test_drop_with_agent_fails(self):
        res = playback_trials.evaluate(self.trials(on_drops=2), self.recs(rec(2060, "playback")))
        self.assertEqual(res["PB_4K"]["verdict"], "FAIL")

    def test_baseline_not_realtime_is_invalid(self):
        res = playback_trials.evaluate(self.trials(off_drops=3), self.recs(rec(2060, "playback")))
        self.assertTrue(res["PB_4K"]["verdict"].startswith("INVALID"))

    def test_no_turn_in_on_window_is_not_pass(self):
        res = playback_trials.evaluate(self.trials(), self.recs(rec(3000, "playback")))
        self.assertEqual(res["PB_4K"]["verdict"], "FAIL")

    def test_record_in_off_window_fails(self):
        res = playback_trials.evaluate(self.trials(), self.recs(rec(1060, "playback"), rec(2060, "playback")))
        self.assertFalse(res["PB_4K"]["checks"]["no_records_inside_off_trials"])

    def test_schedule_alternates(self):
        s = playback_trials.schedule(["A", "B"], 2)
        self.assertEqual([x[2] for x in s], ["off", "on", "off", "on"] * 2)
        self.assertEqual(len(s), 8)


class Render(unittest.TestCase):
    def rows(self, off, on):
        out, t = [], 0.0
        for state, xs in (("off", off), ("on", on)):
            for x in xs:
                out.append({"agent": state, "seconds": str(x), "first_seen": str(t), "last_write": str(t + x)})
                t += 10000
        return out

    def test_pass(self):
        res = render_timer.evaluate(self.rows([600, 602, 598], [601, 603, 599]))
        self.assertTrue(res["pass"], res)

    def test_slower_fails(self):
        res = render_timer.evaluate(self.rows([600, 602, 598], [612, 613, 611]))
        self.assertFalse(res["pass"])
        self.assertFalse(res["checks"]["mean_delta_<=1%"])

    def test_too_few_runs(self):
        self.assertFalse(render_timer.evaluate(self.rows([600, 601], [600, 601]))["pass"])

    def test_scan_during_render_fails(self):
        rows = self.rows([600, 602, 598], [601, 603, 599])
        r = rec(30100, "scan")
        r["_t"] = common.parse_iso(r["ts"])
        self.assertFalse(render_timer.evaluate(rows, [r])["checks"]["agent_skips_during_on_renders"])

    def test_watch_dir_times_new_file(self):
        clock = [0.0]
        states = []

        def snap(_d):
            t = clock[0]
            if t < 3:
                return {"/x/old.mov": (1, 1.0)}
            size = min(int(t), 20)
            return {"/x/old.mov": (1, 1.0), "/x/new.mov": (size, 100.0 + min(t, 20))}

        def sleep(s):
            clock[0] += s
            states.append(clock[0])
        r = render_timer.watch_dir("/x", settle=5, poll=1, clock=lambda: clock[0], sleep=sleep, snap=snap)
        self.assertEqual(r["files"], 1)
        self.assertEqual(r["first_seen"], 3.0)
        self.assertEqual(r["last_write"], 120.0)

    def test_hms(self):
        self.assertEqual(render_timer._hms("00:10:12"), 612)
        self.assertEqual(render_timer._hms("10:12"), 612)
        self.assertIsNone(render_timer._hms("abc"))


class Cost(unittest.TestCase):
    def test_parse_consumed_real_format(self):
        txt = ("editortrack-agent.service: Consumed 312ms CPU time.\n"
               "editortrack-agent.service: Consumed 1.938s CPU time over 2.1s wall clock time, 28.8M memory peak.\n"
               "editortrack-agent.service: Consumed 1min 2.5s CPU time over 34min 17.359s wall clock time.\n"
               "Started editortrack-agent.service\n")
        self.assertEqual([round(x, 3) for x in agent_cost.parse_consumed(txt)], [0.312, 1.938, 62.5])

    def test_ps_time(self):
        self.assertEqual(agent_cost.parse_ps_time("0:00.12"), 0.12)
        self.assertEqual(agent_cost.parse_ps_time("01:02:03"), 3723)
        self.assertEqual(agent_cost.parse_ps_time("1-00:00:01"), 86401)

    def test_summarize(self):
        rows = [{"epoch": "0", "cpu_total_s": "0", "last_turn_cpu_s": "0.2", "state_bytes": "1000"},
                {"epoch": "3600", "cpu_total_s": "12.0", "last_turn_cpu_s": "0.3", "state_bytes": "2000"}]
        s = agent_cost.summarize(rows, 60)
        self.assertAlmostEqual(s["avg_cpu_pct"], 0.3333, places=3)
        self.assertTrue(all(s["checks"].values()))
        rows[1]["cpu_total_s"] = "30"
        self.assertFalse(agent_cost.summarize(rows, 60)["checks"]["avg_cpu_below_0.5%_of_one_core"])


# ---------------------------------------------------------------- prefs snapshot / drill
class Prefs(Tmp):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        self.prefs = os.path.join(self.home, ".local/share/DaVinciResolve/configs")
        os.makedirs(self.prefs)
        os.makedirs(os.path.join(self.home, ".local/share/DaVinciResolve/logs"))
        os.makedirs(os.path.join(self.home, ".config/systemd/user"))
        with open(os.path.join(self.prefs, "config.dat"), "wb") as f:
            f.write(b"\x00prefs v1")
        with open(os.path.join(self.home, ".local/share/DaVinciResolve/logs/rollinglog.txt"), "w") as f:
            f.write("log")
        self._orig = prefs_snapshot.scheduler_lists
        prefs_snapshot.scheduler_lists = lambda platform=None: {"systemd_user_units": ["a.service"],
                                                                "crontab_lines": []}

    def tearDown(self):
        prefs_snapshot.scheduler_lists = self._orig
        super().tearDown()

    def snap(self):
        return json.loads(json.dumps(prefs_snapshot.snapshot(prefs_snapshot.default_roots(self.home, "linux"),
                                                             "linux")))

    def test_identical_passes_and_logs_ignored(self):
        a = self.snap()
        with open(os.path.join(self.home, ".local/share/DaVinciResolve/logs/rollinglog.txt"), "a") as f:
            f.write("more")
        self.assertTrue(prefs_snapshot.compare(a, self.snap())["ok"])

    def test_one_byte_change_fails(self):
        a = self.snap()
        with open(os.path.join(self.prefs, "config.dat"), "wb") as f:
            f.write(b"\x00prefs v2")
        res = prefs_snapshot.compare(a, self.snap())
        self.assertFalse(res["ok"])
        self.assertEqual(res["strict_differences"], [os.path.join(self.prefs, "config.dat")])

    def test_mtime_only_is_touched_not_failed(self):
        a = self.snap()
        os.utime(os.path.join(self.prefs, "config.dat"), (1, 1))
        res = prefs_snapshot.compare(a, self.snap())
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["groups"]["resolve_prefs"]["touched"]), 1)

    def test_added_pref_file_fails_unless_control_explains(self):
        a = self.snap()
        p = os.path.join(self.prefs, "new.xml")
        open(p, "w").close()
        self.assertFalse(prefs_snapshot.compare(a, self.snap())["ok"])
        res = prefs_snapshot.compare(a, self.snap(), allow=[p])
        self.assertTrue(res["ok"])
        self.assertEqual(res["control_explained"], [p])

    def test_leftover_unit_and_state_dir_fail(self):
        a = self.snap()
        open(os.path.join(self.home, ".config/systemd/user/editortrack-agent.timer"), "w").close()
        os.makedirs(os.path.join(self.home, ".editortrack"))
        res = prefs_snapshot.compare(a, self.snap())
        self.assertFalse(res["ok"])
        txt = " ".join(res["fail"])
        self.assertIn("editortrack-agent.timer", txt)
        self.assertIn(".editortrack", txt)

    def test_new_unit_name_fails(self):
        a = self.snap()
        prefs_snapshot.scheduler_lists = lambda platform=None: {"systemd_user_units": ["a.service",
                                                                                      "editortrack-agent.timer"]}
        self.assertFalse(prefs_snapshot.compare(a, self.snap())["ok"])

    def test_expected_code_folder(self):
        a = self.snap()
        os.makedirs(os.path.join(self.home, "editortrack"))
        self.assertFalse(prefs_snapshot.compare(a, self.snap())["ok"])
        self.assertTrue(prefs_snapshot.compare(a, self.snap(), expected=["editortrack"])["ok"])

    def test_missing_roots_recorded(self):
        s = self.snap()
        self.assertIn("/opt/resolve/configs", s["missing"])


class AgentLog(Tmp):
    def test_turns_and_results(self):
        p = os.path.join(self.tmp, "agent.log")
        with open(p, "w") as f:
            f.write("--- 2026-11-02 10:00:00 poll ---\n200\n--- 2026-11-02 10:01:00 poll ---\nNone\nnoise\n")
        t = common.agent_log_turns(p, tz_offset_h=7)
        self.assertEqual(len(t), 2)
        self.assertEqual(t[1]["start"] - t[0]["start"], 60)
        self.assertEqual(t[0]["start"], common.parse_iso("2026-11-02T10:00:00+07:00"))
        self.assertTrue(drill.is_silent(t[1]["result"]))
        self.assertFalse(drill.is_silent(t[0]["result"]))


# ---------------------------------------------------------------- the kit itself stays harmless
class KitSafety(unittest.TestCase):
    FORBIDDEN_IMPORTS = {"DaVinciResolveScript", "fusionscript", "socket", "urllib.request", "http", "http.client",
                         "requests", "ftplib", "smtplib"}

    def modules(self):
        return [p for p in KIT.glob("*.py")]

    def test_no_resolve_scripting_or_network(self):
        for p in self.modules():
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for n in names:
                    self.assertNotIn(n, self.FORBIDDEN_IMPORTS, "%s imports %s" % (p.name, n))
            src = p.read_text(encoding="utf-8")
            for word in ("scriptapp", "GetResolve", "bmd.scriptapp"):
                self.assertNotIn(word, src, "%s mentions %s" % (p.name, word))

    def test_never_runs_sudo_itself(self):
        for p in self.modules():
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) \
                        and node.elts[0].value == "sudo":
                    self.fail("%s builds a sudo command" % p.name)

    def test_python38_compatible_syntax(self):
        for p in self.modules():
            src = p.read_text(encoding="utf-8")
            ast.parse(src, feature_version=(3, 8))
            self.assertNotIn("match ", "".join(l for l in src.splitlines() if l.strip().startswith("match ")))


if __name__ == "__main__":
    unittest.main()

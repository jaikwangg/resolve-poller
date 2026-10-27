"""Stored XSS in the HTML pages (ported from audit-fixes 66176b8 + 780709d, test_bugs.Escape).

Values in the pages come from agent records (stage, page, pct, host, project names): every
data-derived interpolation goes through esc(), and esc() also escapes quotes because values are
placed in attributes (data-rows, data-th, style). Runtime checks need node (skipped without it).

run:  cd server && ~/.editortrack-venv/bin/python -m unittest tests.test_html_escape -v
"""
import json
import pathlib
import re
import shutil
import subprocess
import unittest

SERVER = pathlib.Path(__file__).parents[1]
PAGES = ["dashboard.html", "inbox.html", "report.html", "projects.html"]
EVIL = """<img src=x onerror="alert('x')">&"""
WANT = "&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;"


def esc_src(page):
    line = next(ln for ln in (SERVER / page).read_text(encoding="utf-8").splitlines() if ln.startswith("const esc = "))
    return line[:line.rindex(";") + 1] if "//" in line else line


class Escape(unittest.TestCase):
    def test_esc_escapes_quotes_static(self):
        for p in PAGES:
            src = esc_src(p)
            self.assertIn('"', re.search(r"\[(.*?)\]/g", src).group(1), p)
            self.assertIn("'", re.search(r"\[(.*?)\]/g", src).group(1), p)

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node")
    def test_esc_runtime(self):

        for p in PAGES:
            code = esc_src(p) + f"\nprocess.stdout.write(esc({json.dumps(EVIL)}) + '|' + esc(null) + '|' + esc(0));"
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=20)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout, WANT + "||0", p)

    def test_dashboard_tooltip_labels_escaped(self):
        src = (SERVER / "dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn("${PHASE_TH[ph]||ph}", src)          # page จาก agent ต้องผ่าน esc
        self.assertNotIn('<div class="th">${r.dataset.th}', src)
        self.assertIn('data-rows="${esc(rows)}"', src)


    def test_previously_raw_interpolations_now_escaped(self):
        """stored XSS: stage/pct จาก agent ไปลง innerHTML ดิบ (dashboard ~441, 448, 450) + ตัวอื่นที่ตรวจทุกหน้า"""
        raw = {"dashboard.html": ["${label}", "${val}", "${ov}", '">${n}</div>', "${ps.series[nm][i]}%",
                                  "<span class='v'>${val}%", "<span class='v'>${mins(v)}", "<span class='v'>${a}ชม",
                                  'style="${style}"'],
               "inbox.html": ["${r.record.progress.pct}%", "${tm(r.record.ts)}", "${ago(h.age_sec)}", "${h.today}"],
               "report.html": ["${s.records}", "${s.gap_min}", "${p.at}", "${pct(p.from)}", "${g.n}", "${g.minutes}"],
               "projects.html": ['+ h.delta_graded}', "${gp ?? 0}", "${dur(c.duration_sec)}", "${mins(p.active_min)}"]}
        for page, pats in raw.items():
            src = (SERVER / page).read_text(encoding="utf-8")
            for pat in pats:
                self.assertNotIn(pat, src, (page, pat))

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node")
    def test_dashboard_track_bar_runtime(self):
        """รัน trackBar() จริงด้วย stage/pct/worker/page ที่เป็น HTML — ต้องไม่มี tag หลุดออกมา"""

        src = (SERVER / "dashboard.html").read_text(encoding="utf-8")
        lines = src.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("function trackBar("))
        end = next(i for i in range(start, len(lines)) if lines[i] == "}")
        consts = [ln for ln in lines if ln.startswith(("const STAGE_LABEL", "const STAGE_COLOR"))]
        code = "\n".join([esc_src("dashboard.html"), *consts, *lines[start:end + 1]]) + f"""
const ev = {json.dumps(EVIL)};
process.stdout.write(JSON.stringify([
  trackBar(ev, null),
  trackBar(ev, {{pct: ev, worker: ev, page: ev, active: false}}),
  trackBar("color", {{pct: 40, worker: "grade-03", page: "color", active: true}}),
]));"""
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        empty, evil, ok = json.loads(out.stdout)
        for html in (empty, evil):
            self.assertNotIn("<img", html)
            self.assertIn(WANT, html)
        self.assertIn("width:2%;", evil)                      # pct ไม่ใช่ตัวเลข → ขั้นต่ำ 2% (ไม่ลง style ดิบ)
        self.assertIn("40%", ok)
        self.assertIn('class="live"', ok)




if __name__ == "__main__":
    unittest.main()

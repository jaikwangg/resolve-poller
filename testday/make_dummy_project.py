#!/usr/bin/env python3
"""Build the DUMMY material for the phase-3 test day: synthetic media + importable timelines.

    python3 testday/make_dummy_project.py --out ~/ET_DUMMY_TESTDAY --dry-run     # plan + disk estimate, writes nothing
    python3 testday/make_dummy_project.py --out ~/ET_DUMMY_TESTDAY               # media (ffmpeg) + timelines
    python3 testday/make_dummy_project.py --out ~/ET_DUMMY_TESTDAY --uhd         # + 4K/8K playback media (P1)
    python3 testday/make_dummy_project.py --out DIR --no-media                   # timelines only (media made elsewhere)

What it makes (see docs/testday/DUMMY_PROJECT.md):
  media/src/SRC_###.mov      K synthetic sources (colour bars / test patterns from ffmpeg lavfi, never client media)
  media/vfx_v1, vfx_v2       the same VFX file names with different content (replace = relink, grade kept: WC07)
  media/vfx_late             VFX shots that "arrive late" (scope added: WC02)
  media/wc01                 the 6+2 clips of the exact 2026-10-14 replay (WC01)
  media/uhd                  4K/8K sources for the playback test (only with --uhd)
  timelines/*.xml            FCP7 XML (xmeml v5), Resolve: File > Import > Timeline (primary)
  timelines/*.edl            CMX3600 EDL fallback (needs the media already in the media pool)
  MANIFEST.json, REVISION_STEPS.txt

It never touches DaVinci Resolve (no scripting): the operator imports the XML in the Resolve UI
into a DUMMY project in a separate project library. It only writes inside --out, and refuses a
non-empty --out that it did not create.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import urllib.parse
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

KIT_MARK = "editortrack-testday-dummy"

FPS = {  # name -> (timebase, ntsc, ffmpeg rate, real fps)
    "23.976": (24, True, "24000/1001", 24000 / 1001.0),
    "24": (24, False, "24", 24.0),
    "25": (25, False, "25", 25.0),
    "29.97": (30, True, "30000/1001", 30000 / 1001.0),
    "30": (30, False, "30", 30.0),
}
PATTERNS = ["testsrc2", "smptehdbars", "testsrc", "smptebars", "rgbtestsrc", "yuvtestsrc", "mandelbrot"]
# approx. Mbit/s at 1920x1080, 24 fps (Apple ProRes white paper scaled to 24p; DNxHR LB; x264 crf 18 on test patterns)
MBPS_1080P24 = {("prores", 0): 36, ("prores", 1): 82, ("prores", 2): 117, ("prores", 3): 176,
                ("dnxhr", 0): 36, ("h264", 0): 12}

N_VFX = 12          # VFX shots in the 300-clip timeline (replace scenario)
N_LATE = 10         # VFX that arrive late (add scenario)
WC01_DUR = 161.56   # seconds, the 2026-10-14 TEST timeline duration


# ---------------------------------------------------------------- plan
def build_plan(args):
    rnd = random.Random(args.seed)
    tb, ntsc, ffrate, real = FPS[args.fps]
    src_frames = int(round(args.source_sec * tb))
    media = []   # dicts: id, path (relative to out), pattern, hue, frames, w, h, label

    def add_media(rel, pattern, hue, frames, w, h, label):
        m = {"id": "file-%d" % (len(media) + 1), "rel": rel, "name": os.path.basename(rel),
             "pattern": pattern, "hue": hue, "frames": frames, "w": w, "h": h, "label": label}
        media.append(m)
        return m

    srcs = [add_media("media/src/SRC_%03d.mov" % i, PATTERNS[(i - 1) % len(PATTERNS)],
                      ((i - 1) // len(PATTERNS)) * 47 % 360, src_frames, args.width, args.height,
                      "ET DUMMY SRC_%03d" % i) for i in range(1, args.sources + 1)]
    vfx1 = [add_media("media/vfx_v1/VFX_%03d.mov" % i, "testsrc2", 200, 4 * tb, args.width, args.height,
                      "ET DUMMY VFX_%03d v1" % i) for i in range(1, N_VFX + 1)]
    vfx2 = [add_media("media/vfx_v2/VFX_%03d.mov" % i, "testsrc2", 20, 4 * tb, args.width, args.height,
                      "ET DUMMY VFX_%03d v2" % i) for i in range(1, N_VFX + 1)]
    late = [add_media("media/vfx_late/LATE_%03d.mov" % i, "smptehdbars", 120, 4 * tb, args.width, args.height,
                      "ET DUMMY LATE_%03d" % i) for i in range(1, N_LATE + 1)]
    wc01_frames = int(round(WC01_DUR * tb))
    per = wc01_frames // 6
    wc01 = [add_media("media/wc01/WC01_%d.mov" % i, "testsrc", 30 * i, per + tb, args.width, args.height,
                      "ET DUMMY WC01_%d" % i) for i in range(1, 9)]
    uhd = []
    if args.uhd:
        uhd = [add_media("media/uhd/UHD_4K.mov", "testsrc2", 0, 60 * tb, 3840, 2160, "ET DUMMY 4K"),
               add_media("media/uhd/UHD_8K.mov", "testsrc2", 90, 60 * tb, 7680, 4320, "ET DUMMY 8K")]

    def clips_from_sources(n, start_index=1):
        out = []
        for k in range(n):
            s = srcs[rnd.randrange(len(srcs))]
            length = rnd.randint(tb, 4 * tb)                       # 1-4 s
            cin = rnd.randrange(0, max(1, s["frames"] - length))
            out.append({"media": s, "in": cin, "len": length, "n": start_index + k})
        return out

    timelines = []

    def tl(name, clips, purpose, v2=None):
        t = {"name": name, "clips": clips, "v2": v2 or [], "purpose": purpose}
        timelines.append(t)
        return t

    for size in args.sizes:
        if size == 300:
            continue
        tl("T%04d_v1" % size, clips_from_sources(size), "size %d (S/P tests)" % size)

    # 300-clip revision base: VFX shots at fixed positions, late-VFX gaps left as real gaps (no slug: WC02)
    base = clips_from_sources(300 - N_VFX)
    vfx_pos = [20 + 23 * i for i in range(N_VFX)]                   # 1-based positions in the final cut
    clips300 = []
    it = iter(base)
    for pos in range(1, 301):
        if pos in vfx_pos:
            m = vfx1[vfx_pos.index(pos)]
            clips300.append({"media": m, "in": 0, "len": m["frames"], "n": pos, "vfx": True})
        else:
            c = dict(next(it))
            c["n"] = pos
            clips300.append(c)
    tl("T0300_v1", clips300, "revision base (R0-R6) + size 300")

    # R7 reconform: a NEW timeline: 25 clips removed, 20 new, 2 sections swapped
    keep = [c for c in clips300 if c["n"] % 12 != 5][:280]
    new = clips_from_sources(20, start_index=301)
    v2 = keep[:100] + new[:10] + keep[180:280] + keep[100:180] + new[10:]
    tl("T0300_v2_reconform", [dict(c, n=i + 1) for i, c in enumerate(v2)], "R7 reconform (new timeline)")

    # WC01 exact replay: 6 clips on V1 = 161.56 s; 2 more clips later placed on V2 (duration unchanged)
    wcl = [{"media": wc01[i], "in": 0, "len": per if i < 5 else wc01_frames - 5 * per, "n": i + 1}
           for i in range(6)]
    wc_v2 = [{"media": wc01[6], "in": 0, "len": per, "n": 7, "at": per},
             {"media": wc01[7], "in": 0, "len": per, "n": 8, "at": 3 * per}]
    tl("T0006_WC01", wcl, "R8 exact 2026-10-14 replay (6 clips, then +2 on V2)")
    tl("T0006_WC01_plus2", wcl, "R8 reference only: the state after the 2 clips are added", v2=wc_v2)

    if uhd:
        for m in uhd:
            seg = 30 * tb
            cl = [{"media": m, "in": (k % 2) * seg, "len": seg, "n": k + 1} for k in range(4)]  # 2 min
            tl("PB_%s" % m["name"].split("_")[1].split(".")[0], cl, "P1 playback 2 min")
    return {"media": media, "timelines": timelines, "fps": args.fps, "tb": tb, "ntsc": ntsc,
            "ffrate": ffrate, "real_fps": real, "vfx_pos": vfx_pos, "srcs": srcs}


# ---------------------------------------------------------------- media
def ffmpeg_cmd(ffmpeg, m, plan, args, out_dir, drawtext):
    size = "%dx%d" % (m["w"], m["h"])
    dur = "%.6f" % (m["frames"] / plan["real_fps"])
    src = "%s=size=%s:rate=%s:duration=%s" % (m["pattern"], size, plan["ffrate"], dur)
    vf = ["hue=h=%d" % m["hue"]] if m["hue"] else []
    if drawtext:
        font = ("fontfile=%s:" % args.font.replace(":", "\\:")) if args.font else ""
        fs = max(24, m["h"] // 20)
        vf.append("drawtext=%stext='%s':x=40:y=40:fontsize=%d:fontcolor=white:box=1:boxcolor=black@0.6"
                  % (font, m["label"].replace(":", "\\:"), fs))
        vf.append("drawtext=%stimecode='00\\:00\\:00\\:00':rate=%s:x=40:y=%d:fontsize=%d:fontcolor=yellow:"
                  "box=1:boxcolor=black@0.6" % (font, plan["ffrate"], 60 + fs, fs))
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    codec = args.codec
    profile = args.uhd_profile if m["w"] > 1920 else args.profile
    if codec == "prores":
        cmd += ["-c:v", "prores_ks", "-profile:v", str(profile), "-pix_fmt", "yuv422p10le", "-vendor", "apl0"]
    elif codec == "dnxhr":
        cmd += ["-c:v", "dnxhd", "-profile:v", "dnxhr_lb" if m["w"] <= 1920 else "dnxhr_sq", "-pix_fmt", "yuv422p"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    cmd += ["-an", "-metadata", "comment=editortrack test-day DUMMY media (synthetic)",
            os.path.join(out_dir, m["rel"])]
    return cmd


def estimate_bytes(m, plan, args):
    profile = args.uhd_profile if m["w"] > 1920 else args.profile
    key = (args.codec, profile if args.codec == "prores" else 0)
    mbps = MBPS_1080P24[key] * (m["w"] * m["h"]) / (1920 * 1080.0) * (plan["real_fps"] / 24.0)
    return mbps * 1e6 / 8 * m["frames"] / plan["real_fps"]


def has_drawtext(ffmpeg):
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20)
        return " drawtext " in out.stdout
    except Exception:
        return False


# ---------------------------------------------------------------- timelines
def tc(frames, tb):
    f = frames % tb
    s = frames // tb
    return "%02d:%02d:%02d:%02d" % (s // 3600, (s // 60) % 60, s % 60, f)


def _rate(plan):
    return "<rate><timebase>%d</timebase><ntsc>%s</ntsc></rate>" % (plan["tb"], "TRUE" if plan["ntsc"] else "FALSE")


def pathurl(path):
    return "file://localhost" + urllib.parse.quote(os.path.abspath(path))


def xmeml(t, plan, media_root):
    tb = plan["tb"]
    rate = _rate(plan)
    start_tc = 3600 * tb   # 01:00:00:00
    seen = set()

    def file_el(m):
        if m["id"] in seen:
            return '<file id="%s"/>' % m["id"]
        seen.add(m["id"])
        return ('<file id="%s"><name>%s</name><pathurl>%s</pathurl>%s<duration>%d</duration>'
                '<media><video><samplecharacteristics>%s<width>%d</width><height>%d</height>'
                '</samplecharacteristics></video></media></file>'
                % (m["id"], escape(m["name"]), escape(pathurl(os.path.join(media_root, m["rel"]))), rate,
                   m["frames"], rate, m["w"], m["h"]))

    def track(clips, sequential):
        items, pos = [], 0
        for c in clips:
            st = c.get("at", pos) if not sequential else pos
            items.append('<clipitem id="clipitem-%d"><name>%s</name><enabled>TRUE</enabled><duration>%d</duration>%s'
                         '<start>%d</start><end>%d</end><in>%d</in><out>%d</out>%s</clipitem>'
                         % (c["n"], escape(os.path.splitext(c["media"]["name"])[0]), c["media"]["frames"], rate,
                            st, st + c["len"], c["in"], c["in"] + c["len"], file_el(c["media"])))
            pos = st + c["len"]
        return "<track>%s</track>" % "".join(items)

    total = sum(c["len"] for c in t["clips"])
    w = max((c["media"]["w"] for c in t["clips"]), default=1920)
    h = max((c["media"]["h"] for c in t["clips"]), default=1080)
    tracks = track(t["clips"], True) + (track(t["v2"], False) if t["v2"] else "")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">'
            '<sequence id="sequence-1"><name>%s</name><duration>%d</duration>%s'
            '<timecode>%s<string>%s</string><frame>%d</frame><displayformat>NDF</displayformat></timecode>'
            '<media><video><format><samplecharacteristics>%s<width>%d</width><height>%d</height>'
            '<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>%s</video></media>'
            '</sequence></xmeml>\n'
            % (escape(t["name"]), total, rate, rate, tc(start_tc, tb), start_tc, rate, w, h, tracks))


def edl(t, plan):
    tb = plan["tb"]
    lines = ["TITLE: %s" % t["name"], "FCM: NON-DROP FRAME", ""]
    rec = 3600 * tb
    for i, c in enumerate(t["clips"], 1):
        reel = os.path.splitext(c["media"]["name"])[0]
        lines.append("%03d  %-8s V     C        %s %s %s %s" % (
            i % 1000, reel, tc(c["in"], tb), tc(c["in"] + c["len"], tb), tc(rec, tb), tc(rec + c["len"], tb)))
        lines.append("* FROM CLIP NAME: %s" % c["media"]["name"])
        rec += c["len"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- revision steps
def revision_steps(plan):
    """Operator steps on the DUMMY project. Positions refer to T0300_v1 as imported; the cut (R5) comes after
    every step that names positions, so the numbers stay valid. Grading never uses scripting."""
    vfx = plan["vfx_pos"]
    return [
        ("R0-import", "Import timelines/T0300_v1.xml into the DUMMY project (tick: import source clips into the "
                      "media pool). Make it the current timeline. Expect on the server: provisional, 0 %."),
        ("R1-grade", "Color page: grade clips 1-120 (any visible change, e.g. one serial node + offset), in 3 "
                     "sessions of 40 with a 10 min pause. Expect: progress rises, provisional, never done/complete."),
        ("R2-add (WC02)", "Media pool: import the folder media/vfx_late (10 clips) and append them to the end of "
                          "T0300_v1 (Edit > Append to End of Timeline). Clips 300 -> 310. Expect: the % drops with "
                          "reason scope_change (not shown as lost work)."),
        ("R3-replace (WC07)", "Grade the 12 VFX clips (positions %s), then relink them to the folder media/vfx_v2 "
                              "(same file names, new content; grades kept). Expect: no jump to complete, no "
                              "unexplained drop." % ", ".join(map(str, vfx))),
        ("R4-regrade (WC09/WC10)", "Color page: Reset Grades and Nodes on clips 70-84; wait 3 agent turns; regrade "
                                   "them. Expect: a drop labelled rework (or regression), then back up."),
        ("R5-cut (WC05/WC06)", "Edit page: ripple-delete the graded clips 45-54 and the ungraded clips 210-219 "
                               "(20 clips; positions as imported). Expect: reason scope_shrink; the drop of graded "
                               "clips is labelled, never silent."),
        ("R6-look-all (WC12)", "Apply one LUT / shared node to ALL clips (select all on the Color page). Expect: NOT "
                               "100 %, NOT done (a look on every clip is not 'graded'; mass-grade guard)."),
        ("R7-reconform (WC08/WC15)", "Import timelines/T0300_v2_reconform.xml as a NEW timeline and make it current. "
                                     "Expect: a labelled change (scope_change / reference_timeline hold); no false 0 % or 100 %."),
        ("R8-WC01 replay", "Import timelines/T0006_WC01.xml (6 clips). Grade clip 1; next turn grade all 6; next turn "
                           "reset clip 6; then place WC01_7 and WC01_8 on V2 (positions as in T0006_WC01_plus2). "
                           "Today's server sent 16.7 -> 100 -> 83.3 -> 75 for this. Expect with progress v2: never "
                           "complete (at most 99, provisional), and the drops carry reasons."),
        ("R9-scale", "On T1500_v1 and T3000_v1: append 50 clips from media/src, wait 3 turns, ripple-delete 50. "
                     "Expect: same reasons as R2/R5; guard.ms within the P2 budget; no scan_budget storm."),
    ]


# ---------------------------------------------------------------- main
def check_out_dir(out):
    out = os.path.abspath(os.path.expanduser(out))
    home = os.path.abspath(os.path.expanduser("~"))
    if out in ("/", home) or len(out) < 5:
        common.die("refusing --out %s (pick a new dedicated folder, e.g. ~/ET_DUMMY_TESTDAY)" % out)
    if os.path.isdir(out) and os.listdir(out):
        man = os.path.join(out, "MANIFEST.json")
        try:
            with open(man, encoding="utf-8") as f:
                ok = json.load(f).get("kit") == KIT_MARK
        except Exception:
            ok = False
        if not ok:
            common.die("%s is not empty and was not made by this tool; refusing to write there" % out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--media-root", help="where the media will live on the workstation (default: --out)")
    ap.add_argument("--fps", default="24", choices=sorted(FPS))
    ap.add_argument("--sizes", default="30,300,1500,3000")
    ap.add_argument("--sources", type=int, default=24)
    ap.add_argument("--source-sec", type=float, default=30.0)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--codec", default="prores", choices=["prores", "dnxhr", "h264"])
    ap.add_argument("--profile", type=int, default=0, help="ProRes profile for HD (0 proxy .. 3 HQ)")
    ap.add_argument("--uhd", action="store_true", help="also make 4K + 8K playback media and PB_* timelines")
    ap.add_argument("--uhd-profile", type=int, default=2, help="ProRes profile for 4K/8K (default 2 = 422)")
    ap.add_argument("--ffmpeg", default=os.environ.get("FFMPEG", "ffmpeg"))
    ap.add_argument("--font", help="TTF/OTF for the burned-in labels (drawtext); default: fontconfig")
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--no-media", action="store_true", help="timelines + manifest only")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, ffmpeg commands and disk estimate only")
    ap.add_argument("--seed", type=int, default=26)
    args = ap.parse_args(argv)
    args.sizes = sorted({int(s) for s in args.sizes.split(",") if s.strip()} | {300})
    out = check_out_dir(args.out)
    media_root = os.path.abspath(os.path.expanduser(args.media_root or out))
    plan = build_plan(args)

    ffmpeg = shutil.which(args.ffmpeg) or (args.ffmpeg if os.path.isfile(args.ffmpeg) else None)
    drawtext = (not args.no_labels) and bool(ffmpeg) and has_drawtext(ffmpeg)
    est = sum(estimate_bytes(m, plan, args) for m in plan["media"])
    print("plan: %d media files, %d timelines, fps %s, est. %.1f GB (%s profile %d)"
          % (len(plan["media"]), len(plan["timelines"]), args.fps, est / 1e9, args.codec, args.profile))
    for t in plan["timelines"]:
        frames = sum(c["len"] for c in t["clips"])
        print("  %-22s %5d clips  %s  %s" % (t["name"], len(t["clips"]) + len(t["v2"]), tc(frames, plan["tb"]),
                                            t["purpose"]))
    if args.dry_run:
        fake = ffmpeg or "ffmpeg"
        print("\nffmpeg commands (first 3 of %d; labels %s):" % (len(plan["media"]), "on" if drawtext else "off"))
        for m in plan["media"][:3]:
            print("  " + " ".join(ffmpeg_cmd(fake, m, plan, args, out, drawtext)))
        print("\nrevision steps:")
        for k, s in revision_steps(plan):
            print("  %s: %s" % (k, s))
        return 0

    if not args.no_media and not ffmpeg:
        common.die("ffmpeg not found (use --ffmpeg /path/to/ffmpeg, or --no-media and make the media elsewhere)")
    os.makedirs(os.path.join(out, "timelines"), exist_ok=True)
    manifest = {"kit": KIT_MARK, "kit_version": common.KIT_VERSION, "created": common.iso_local(common.now_epoch()),
                "fps": args.fps, "codec": args.codec, "seed": args.seed, "media_root": media_root,
                "labels": drawtext, "media": [], "timelines": [], "vfx_positions_T0300": plan["vfx_pos"]}
    common.write_json(os.path.join(out, "MANIFEST.json"), manifest)   # mark the folder first
    if not args.no_media:
        for i, m in enumerate(plan["media"], 1):
            dst = os.path.join(out, m["rel"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                continue
            print("[%d/%d] %s" % (i, len(plan["media"]), m["rel"]), flush=True)
            r = subprocess.run(ffmpeg_cmd(ffmpeg, m, plan, args, out, drawtext))
            if r.returncode != 0:
                if os.path.exists(dst):
                    os.remove(dst)
                common.die("ffmpeg failed on %s (exit %d)" % (m["rel"], r.returncode))
    for t in plan["timelines"]:
        with open(os.path.join(out, "timelines", t["name"] + ".xml"), "w", encoding="utf-8") as f:
            f.write(xmeml(t, plan, media_root))
        if not t["v2"]:
            with open(os.path.join(out, "timelines", t["name"] + ".edl"), "w", encoding="utf-8") as f:
                f.write(edl(t, plan))
        manifest["timelines"].append({"name": t["name"], "clips": len(t["clips"]) + len(t["v2"]),
                                      "frames": sum(c["len"] for c in t["clips"]), "purpose": t["purpose"]})
    manifest["media"] = [{"path": m["rel"], "frames": m["frames"], "size": "%dx%d" % (m["w"], m["h"]),
                          "pattern": m["pattern"]} for m in plan["media"]]
    common.write_json(os.path.join(out, "MANIFEST.json"), manifest)
    with open(os.path.join(out, "REVISION_STEPS.txt"), "w", encoding="utf-8") as f:
        f.write("editortrack test day - revision scenarios on the DUMMY project (see docs/testday/DUMMY_PROJECT.md)\n\n")
        for k, s in revision_steps(plan):
            f.write("[ ] %s\n    %s\n\n" % (k, s))
    print("done: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

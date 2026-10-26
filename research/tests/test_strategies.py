import json
import pathlib
import sys

from sim import engine, library, strategies as S

LIB = pathlib.Path(S.__file__).parent / "scenarios" / "library"


def ob(t, v, g, **kw):
    o = {"t": t, "ts": f"2026-11-02T02:{t:02d}:00+00:00", "host": "h1", "stage": "color", "ok": True,
         "project": "J1_grade", "timeline": "T", "video_clips": v, "graded_clips": g,
         "booking": {"project_no": "J1", "project_name": "J1"}, "external": {"scope": {}, "pms_task": None}}
    o.update(kw)
    return o


def items(states, ver=None):
    return [{"id": f"u{i}", "kind": "video", "track": 1, "start": i * 4.0, "end": i * 4.0 + 4, "graded": g,
             "sig": f"s{i}" if g else None, "media": f"SH{i}_v001", "version": (ver or {}).get(i, 1),
             "online": True, "sid": f"SH{i}_v{(ver or {}).get(i, 1):03d}", "src_in": 0.0}
            for i, g in enumerate(states)]


def test_s0_reproduces_the_recorded_2026_10_14_values():
    sc = engine.load(LIB / "WC01_replay_2026_10_14_TEST.json")
    obs = engine.expand(sc)
    outs = S.run(S.S0Current, obs)
    for o, out in zip(obs, outs):
        assert abs(out["pct"] - o["recorded_pct"]) < 0.06, (o["t"], out["pct"], o["recorded_pct"])
    assert max(out["pct"] for out in outs) == 100.0


def test_s0_matches_agent_scope_compute_progress():
    sys.path.insert(0, str(pathlib.Path(S.__file__).resolve().parents[2] / "agent"))
    import scope
    for v, g in [(6, 1), (6, 6), (8, 6), (309, 120)]:
        snap = {"timeline": {"video_clips": v, "graded_clips": g, "duration_sec": 10.0}, "render": {}}
        want = scope.compute_progress(snap, {})["phases"]["color"]
        assert abs(S.s0_pct(ob(0, v, g)) - want) < 0.06
    for d, tgt in [(20.0, 40.0), (50.0, 40.0)]:
        snap = {"timeline": {"duration_sec": d, "video_clips": 3, "graded_clips": None}, "render": {}}
        want = scope.compute_progress(snap, {"target_duration_sec": tgt})["phases"]["edit"]
        assert abs(S.s0_pct(ob(0, 3, 0, stage="conform", duration_sec=d, offline_target_sec=tgt)) - want) < 0.06


def test_s1_keeps_the_highest_denominator():
    outs = S.run(S.S1HighWater, [ob(0, 8, 0), ob(1, 6, 6)])
    assert outs[1]["pct"] == 75.0 and "scope_shrink_ignored" in outs[1]["reasons"]


def test_c3_caps_unconfirmed_at_99_and_never_done():
    outs = S.run(S.C3Recommended, [ob(t, 3, 3, items=items([True] * 3)) for t in range(0, 120, 10)])
    assert all(o["pct"] == 99.0 and not o["done"] and o["provisional"] for o in outs)
    assert "scope_unconfirmed" in outs[-1]["reasons"]


def test_c3_done_needs_confirmed_scope_and_30_stable_minutes():
    ext = {"scope": {"color": {"shots": ["SH0", "SH1", "SH2"]}}, "pms_task": None}
    outs = S.run(S.C3Recommended, [ob(t, 3, 3, items=items([True] * 3), external=ext) for t in (0, 10, 29, 30, 31)])
    assert [o["done"] for o in outs] == [False, False, False, True, True]
    assert outs[0]["pct"] == 99.0 and "awaiting_stability" in outs[0]["reasons"]
    assert outs[3]["pct"] == 100.0 and outs[3]["total_confirmed"]


def test_c3_scope_growth_is_explained_not_regression():
    outs = S.run(S.C3Recommended, [ob(0, 2, 2, items=items([True, True])),
                                   ob(1, 3, 2, items=items([True, True, False]))])
    assert outs[1]["pct"] < outs[0]["pct"]
    assert "scope_change" in outs[1]["reasons"] and "regression" not in outs[1]["reasons"]


def test_c3_replaced_media_with_carried_grade_is_not_done():
    a = ob(0, 2, 2, items=items([True, True]))
    b = ob(1, 2, 2, items=items([True, True], ver={1: 2}))
    b["items"][1]["sig"] = "s1"            # Replace Clip keeps the grade
    outs = S.run(S.C3Recommended, [a, b])
    assert outs[1]["done_count"] == 1 and "replaced" in outs[1]["reasons"]


def test_c3_holds_on_job_mismatch_and_detector_pending():
    outs = S.run(S.C3Recommended, [ob(0, 2, 1, items=items([True, False])),
                                   ob(1, 9, 9, project="Other_Film", items=items([True] * 9)),
                                   ob(2, 2, 0, grade_pending=2, items=items([False, False]))])
    assert not outs[1]["send"] and "job_mismatch" in outs[1]["reasons"]
    assert not outs[2]["send"] and "detector_pending" in outs[2]["reasons"]
    assert outs[1]["pct"] == outs[2]["pct"] == 50.0


def test_c3_mass_apply_in_one_minute_is_not_counted_until_touched():
    base = items([False] * 6)
    allg = items([True] * 6)
    touched = items([True] * 6)
    touched[0]["sig"] = "s0b"
    outs = S.run(S.C3Recommended, [ob(0, 6, 0, items=base), ob(1, 6, 6, items=allg), ob(5, 6, 6, items=touched)])
    assert outs[1]["done_count"] == 0 and "mass_grade" in outs[1]["reasons"]
    assert outs[2]["done_count"] == 1


def test_c3_reopens_when_pms_task_goes_back_to_revision():
    ext = {"scope": {"color": {"shots": ["SH0"]}}, "pms_task": "DONE"}
    rev = {"scope": {"color": {"shots": ["SH0"]}}, "pms_task": "REVISION"}
    outs = S.run(S.C3Recommended, [ob(0, 1, 1, items=items([True]), external=ext),
                                   ob(40, 1, 1, items=items([True]), external=ext),
                                   ob(41, 1, 1, items=items([True]), external=rev)])
    assert outs[1]["done"] and not outs[2]["done"]
    assert {"reopened", "rework"} <= set(outs[2]["reasons"]) and outs[2]["pct"] == 99.0


def test_work_counter_is_monotonic_when_grades_are_reset():
    outs = S.run(S.C3Recommended, [ob(0, 2, 2, items=items([True, True])), ob(1, 2, 0, items=items([False, False]))])
    assert outs[1]["work_done"] >= outs[0]["work_done"] == 2


def test_late_records_do_not_move_the_current_value():
    outs = S.run(S.C3Recommended, [ob(10, 2, 2, items=items([True, True])), ob(5, 2, 0, items=items([False, False]))])
    assert not outs[1]["send"] and "late_record" in outs[1]["reasons"]
    s0 = S.run(S.S0Current, [ob(10, 2, 2), ob(5, 2, 0)])
    assert s0[1]["send"]                     # today's server pushes the backlog record


def test_every_output_has_the_pms_fields():
    sc = engine.load(LIB / "WC18_two_rooms_one_job.json")
    obs, res = library.evaluate(sc)
    keys = {"key", "pct", "done", "total", "done_count", "total_confirmed", "provisional", "reasons",
            "work_done", "send"}
    for name, (outs, _, _) in res.items():
        assert all(set(o) == keys for o in outs), name
    json.dumps(res["C3"][0])

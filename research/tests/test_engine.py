from sim import engine


def _sc(steps, **kw):
    return {"id": "t", "job": "J1", "steps": steps, "truth": {"J1": {"color": ["A", "B", "C"]}}, **kw}


def test_counts_grade_and_truth():
    obs = engine.expand(_sc([
        {"t": 0, "ops": [["open", {"project": "P", "timeline": "T"}], ["add", {"shots": ["A", "B", "C"]}]]},
        {"t": 1, "ops": [["grade", {"shots": ["A"]}]]},
        {"t": 2, "ops": [["lut_all", {}]]},
    ]))
    assert [(o["video_clips"], o["graded_clips"]) for o in obs] == [(3, 0), (3, 1), (3, 3)]
    assert [o["truth"]["pct"] for o in obs] == [0.0, 33.33, 33.33]     # a look on every clip is not grading
    assert obs[0]["booking"]["project_no"] == "J1"


def test_blade_adds_a_clip_but_not_scope():
    obs = engine.expand(_sc([
        {"t": 0, "ops": [["open", {"project": "P", "timeline": "T"}], ["add", {"shots": ["A", "B", "C"]}]]},
        {"t": 1, "ops": [["blade", {"shot": "B"}]]},
    ]))
    assert obs[1]["video_clips"] == 4 and obs[1]["truth"]["scope"] == 3
    b = [i for i in obs[1]["items"] if i["sid"] == "B_v001"]
    assert len(b) == 2 and b[0]["end"] == b[1]["start"]


def test_offline_target_uses_first_regex_match_like_the_poller():
    obs = engine.expand(_sc([
        {"t": 0, "ops": [["open", {"project": "P", "timeline": "R1_offline_v1"}], ["add", {"shots": ["A"], "dur": 10}],
                         ["new_timeline", {"name": "R1_offline_v2"}],
                         ["add", {"shots": ["A", "B"], "dur": 10, "timeline": "R1_offline_v2"}],
                         ["new_timeline", {"name": "conform", "open": True}]]},
    ], stage="conform"))
    assert obs[0]["offline_target_sec"] == 10.0
    assert obs[0]["offline_shots"] == ["A_v001"]


def test_down_and_no_booking():
    obs = engine.expand(_sc([
        {"t": 0, "ops": [["open", {"project": "P", "timeline": "T"}], ["add", {"shots": ["A"]}], ["booking", None]]},
        {"t": 1, "ops": [["down"]]},
    ]))
    assert obs[0]["booking"] is None
    assert obs[1]["ok"] is False and obs[1]["reason"] == "resolve_not_running"


def test_expand_is_deterministic():
    sc = engine.load(engine.__file__.replace("engine.py", "scenarios/library/WC18_two_rooms_one_job.json"))
    assert engine.expand(sc) == engine.expand(sc)

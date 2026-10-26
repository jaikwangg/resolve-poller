"""The shipped server path (server/progress_v2.py, strategies SV and SVL) on the whole test system:
scenario library, the exact 2026-10-14 replay, a scope lock, the fuzzer and hypothesis invariants.
SV = default owner decisions (D1 none: never done). SVL = D1 lock (a human-set shot count)."""
import os
import pathlib

import pytest

from sim import engine, fuzz, library, report, strategies as S

LIB = pathlib.Path(S.__file__).parent / "scenarios" / "library"
SCENARIOS = [(p, sc) for p, sc in library.load_all() if sc.get("status") != "new"]
PV = S._load_server_progress()


def _wc01():
    return engine.expand(engine.load(LIB / "WC01_replay_2026_10_14_TEST.json"))


def _sent(outs):
    seq, last = [], None
    for o in outs:
        if o["send"] and o["pct"] is not None and (o["pct"], o["done"]) != last:
            seq.append(o)
            last = (o["pct"], o["done"])
    return seq


@pytest.mark.parametrize("path,sc", SCENARIOS, ids=[p.stem for p, _ in SCENARIOS])
def test_shipped_path_on_every_library_scenario(path, sc):
    _, res = library.evaluate(sc, list(S.SHIPPED) + ["C3"])
    for name in S.SHIPPED:
        outs, m, inv = res[name]
        assert inv == [], (name, inv)
        assert m["false_completion"] is False, name
        assert m["wrong_job"] == 0, name
        assert m["fr_unexplained"] == 0, name
        # whatever the scenario asks of the recommended strategy on safety, the shipped path meets too
        for k, want in (sc.get("expect") or {}).get("C3", {}).items():
            if k in ("false_completion", "fr_unexplained", "wrong_job") or k == "invariants":
                got = m.get(k) if k != "invariants" else sorted({x["inv"] for x in inv} & set(want))
                assert got == (want if k != "invariants" else []), (name, k, got)
        for o in outs:
            if o["send"]:
                p = PV.payload(o, o["key"].split("|")[0], o["key"].split("|")[1], "2026-11-02T02:00:00+00:00", "h")
                assert PV.pms_check(p) is None, (name, p)


@pytest.mark.parametrize("path,sc", SCENARIOS, ids=[p.stem for p, _ in SCENARIOS])
def test_lock_mode_with_a_confirmed_scope_never_falsely_completes(path, sc):
    _, res = library.evaluate(report.with_confirmed_scope(sc), ["SVL", "SVIL"])
    for name in ("SVL", "SVIL"):
        outs, m, inv = res[name]
        assert m["false_completion"] is False and inv == [], name


# step 3 is a port of the study's reference ledger (C3): the shipped code must give the same answers.
# Expected differences, each stricter on the shipped side: with D1 none the scenarios that confirm a scope
# (confirm_scope op: WC02b, WC10, WC11, WC12) are never done; a hold restarts the stability window (WC37).
SVI_VS_C3 = {"WC02b_vfx_late_confirmed_list", "WC10_client_notes_rework", "WC11_revision_days_after_delivery",
             "WC12_look_lut_on_all_clips"}
SVIL_VS_C3_CONFIRMED = {"WC37_detector_warmup_reopen"}


class _NoGap(S.SVILShipped):
    name = "SVIL_nogap"
    config = {"scope_source": ("lock",), "stable_gap_min": 1e9}


@pytest.mark.parametrize("path,sc", SCENARIOS, ids=[p.stem for p, _ in SCENARIOS])
def test_identity_ledger_matches_the_reference_c3(path, sc):
    _, res = library.evaluate(sc, ["C3", "SVI"])
    if sc["id"] not in SVI_VS_C3:
        assert report.cell(res["SVI"][1]) == report.cell(res["C3"][1])
    else:
        assert not res["SVI"][1]["false_completion"]
    obs = engine.expand(report.with_confirmed_scope(sc))
    c3, ng = S.run(S.C3Recommended, obs), S.run(_NoGap, obs)
    from sim import metrics
    mc, mn = metrics.score(obs, c3), metrics.score(obs, ng)
    if sc["id"] not in SVIL_VS_C3_CONFIRMED:
        assert report.cell(mn) == report.cell(mc)        # same answers once polls are dense enough
    assert not mn["false_completion"]
    for a, b in zip(c3, ng):                             # never done where the reference is not
        assert not b["done"] or a["done"]


def test_the_2026_10_14_replay_never_emits_complete_or_100():
    obs = _wc01()
    for name in S.SHIPPED:
        outs = S.run(S.BY_NAME[name], obs)
        assert not any(o["done"] or (o["pct"] or 0) >= 100 for o in outs), name
        seq = _sent(outs)
        assert [o["pct"] for o in seq] == [16.67, 99.0, 83.33, 75.0], name
        assert all(o["provisional"] and not o["total_confirmed"] for o in seq)
        assert "regression" in seq[2]["reasons"] and "scope_change" in seq[3]["reasons"]
        assert [(o["done_count"], o["total"]) for o in seq] == [(1, 6), (6, 6), (5, 6), (6, 8)]
        works = [o["work_done"] for o in outs if o["work_done"] is not None]
        assert works == sorted(works) and works[-1] >= 6


def test_the_2026_10_14_replay_with_a_lock_of_6_reports_scope_grew_after_lock():
    obs = _wc01()
    for o in obs:
        o["external"] = {"scope": {"color": {"total": 6}}, "pms_task": None}
    outs = S.run(S.SVLShipped, obs)
    assert not any(o["done"] or (o["pct"] or 0) >= 100 for o in outs)     # 6/6 held 3 min only, and in one jump
    last = outs[-1]
    assert last["pct"] == 75.0 and last["provisional"] and not last["total_confirmed"] and last["total"] == 8
    assert {"scope_grew_after_lock", "scope_change"} <= set(last["reasons"])


def _ob(t, v, g, scope=None, **kw):
    o = {"t": t, "ts": engine._iso(t), "host": "grade-01", "stage": "color", "ok": True,
         "project": "KPP-2026-0101 BrandX", "timeline": "Master v3", "video_clips": v, "graded_clips": g,
         "duration_sec": 100.0, "offline_target_sec": None, "grade_pending": 0,
         "booking": {"project_no": "KPP-2026-0101", "project_name": "BrandX"},
         "external": {"scope": {"color": scope} if scope else {}, "pms_task": None}}
    o.update(kw)
    return o


def test_lock_complete_only_after_the_stability_window_then_scope_grew_after_lock():
    lock = {"total": 6}
    obs = [_ob(t, 6, min(6, t // 2), lock) for t in range(0, 12)]              # one clip every 2 min
    obs += [_ob(t, 6, 6, lock) for t in range(12, 50)]                          # holds 6/6
    obs += [_ob(t, 8, 6, lock) for t in range(50, 55)]                          # 2 clips added after the lock
    outs = S.run(S.SVLShipped, obs)
    done_t = [o_["t"] for o_, x in zip(obs, outs) if x["done"]]
    assert done_t and done_t[0] == 42                                           # 6/6 from t=12, + 30 min
    for o_, x in zip(obs, outs):
        if o_["t"] < 42:
            assert not x["done"] and x["pct"] <= 99.0
    first = outs[42]
    assert first["pct"] == 100.0 and first["total_confirmed"] and not first["provisional"]
    assert first["done_count"] == first["total"] == 6
    grew = outs[50]
    assert not grew["done"] and grew["pct"] == 75.0 and (grew["done_count"], grew["total"]) == (6, 8)
    assert {"scope_grew_after_lock", "scope_change"} <= set(grew["reasons"])
    assert grew["provisional"] and not grew["total_confirmed"]
    for x in outs:
        if x["send"]:
            assert PV.pms_check(PV.payload(x, "KPP-2026-0101", "color", "2026-11-02T02:00:00Z", "grade-01")) is None


def test_default_decisions_never_send_done_even_with_a_lock_present():
    obs = [_ob(t, 6, 6, {"total": 6}) for t in range(0, 90)]
    outs = S.run(S.SVShipped, obs)
    assert not any(o["done"] for o in outs) and max(o["pct"] for o in outs) == 99.0


@pytest.mark.parametrize("field,value,reason", [("grade_pending", 3, "detector_pending"),
                                                ("timeline", "BrandX_offline_ref", "reference_timeline"),
                                                ("project", "Other Client TVC", "job_mismatch")])
def test_holds_send_nothing_and_keep_the_previous_value(field, value, reason):
    outs = S.run(S.SVShipped, [_ob(0, 10, 2), _ob(1, 10, 9, **{field: value}), _ob(2, 10, 3)])
    assert outs[0]["send"] and outs[0]["pct"] == 20.0
    assert not outs[1]["send"] and outs[1]["pct"] == 20.0 and reason in outs[1]["reasons"]
    assert outs[2]["send"] and outs[2]["pct"] == 30.0


def test_a_late_backlog_record_does_not_move_the_value():
    a, b, late = _ob(10, 10, 8), _ob(11, 10, 9), _ob(5, 10, 1)
    outs = S.run(S.SVShipped, [a, b, late])
    assert not outs[2]["send"] and outs[2]["pct"] == 90.0 and "late_record" in outs[2]["reasons"]


def test_mass_grade_blocks_done_in_lock_mode_until_relocked():
    obs = [_ob(0, 10, 0, {"total": 10})] + [_ob(t, 10, 10, {"total": 10}) for t in range(1, 60)]
    outs = S.run(S.SVLShipped, obs)
    assert "mass_grade" in outs[1]["reasons"] and not any(o["done"] for o in outs)
    relock = [_ob(t, 10, 10, {"total": 10, "locked_t": 61}) for t in range(61, 100)]
    outs = S.run(S.SVLShipped, obs + relock)
    assert any(o["done"] for o in outs[61:]) and "mass_grade" not in outs[-1]["reasons"]


def test_config_from_env_defaults_are_the_study_defaults_and_typos_stay_strict():
    c = PV.Config.from_env({})
    assert c.describe() == {"scope_source": ["none"], "send_done": True, "cap": 99.0, "stable_min": 30.0,
                            "stable_gap_min": 10.0, "job_guard": True, "mass_guard": True,
                            "offline_match": PV.DEFAULT_OFFLINE_MATCH}
    c = PV.Config.from_env({"EDITORTRACK_PROGRESS_CAP": "100", "EDITORTRACK_SCOPE_SOURCE": "pms,lokc",
                            "EDITORTRACK_STABLE_MIN": "abc", "EDITORTRACK_PROGRESS_DONE": "nevr"})
    assert c.cap == 99.99 and c.scope_source == () and c.stable_min == 30.0 and c.send_done is False


# ---------------- generative: the same invariants as the recommended strategy ----------------
hyp = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402

N = int(os.environ.get("HYPOTHESIS_EXAMPLES", "60"))
SET = settings(max_examples=N, deadline=None, suppress_health_check=[HealthCheck.too_slow], database=None)
GENE = st.tuples(st.sampled_from(fuzz.OBSERVABLE_OPS), st.integers(0, 9), st.integers(1, 39))
GENE_ANY = st.tuples(st.sampled_from(fuzz.OPS), st.integers(0, 9), st.integers(1, 39))
# dense: short steps and a 40-minute tail of 1-minute polls, so the lock's stability window is reached
DENSE_OPS = ["grade"] * 6 + ["confirm", "wait", "add", "cut", "blade", "ungrade", "dup", "room2", "other_project"]
GENE_DENSE = st.tuples(st.sampled_from(DENSE_OPS), st.integers(0, 9), st.integers(1, 3))


@SET
@given(st.lists(GENE, max_size=20))
def test_shipped_keeps_all_invariants_on_observable_histories(genes):
    sc = fuzz.compose(genes)
    for name in S.SHIPPED:
        assert fuzz.violations(sc, name) == [], name


@SET
@given(st.lists(GENE_ANY, max_size=20))
def test_shipped_never_unconfirmed_done_even_with_invisible_edits(genes):
    sc = fuzz.compose(genes, observable_only=False)
    for name in S.SHIPPED:
        assert [x for x in fuzz.violations(sc, name) if x["inv"] != "INV7_done_is_true"] == [], name


@SET
@given(st.lists(st.tuples(st.sampled_from(DENSE_OPS + ["replace", "replace", "peek_old"]), st.integers(0, 9),
                          st.integers(1, 3)), min_size=3, max_size=16))
def test_identity_ledger_lock_done_is_true_on_dense_observable_histories_with_replace(genes):
    sc = fuzz.compose(genes, settle_tail=40)
    assert fuzz.violations(sc, "SVIL") == []


@SET
@given(st.lists(GENE_DENSE, min_size=3, max_size=16))
def test_lock_mode_done_is_true_on_dense_observable_histories_without_replace(genes):
    sc = fuzz.compose(genes, settle_tail=40)
    assert fuzz.violations(sc, "SVL") == []


def test_known_limit_lock_mode_counts_cannot_see_replace_clip_with_a_kept_grade():
    """Documented limit of D1 = lock without per-item data (step 3): Replace Clip that keeps the grade
    leaves every count unchanged, so a locked, fully graded timeline reads done although the new VFX
    version is not graded. The default (D1 none) never sends done; see the night report."""
    genes = [("confirm", 0, 1), ("grade", 0, 1), ("grade", 8, 2), ("replace", 0, 1), ("cut", 5, 1)]
    sc = fuzz.compose(genes, settle_tail=40)
    assert {x["inv"] for x in fuzz.violations(sc, "SVL")} == {"INV7_done_is_true"}
    assert fuzz.violations(sc, "SV") == []
    assert fuzz.violations(sc, "SVIL") == []          # the identity ledger (step 3) sees the new version

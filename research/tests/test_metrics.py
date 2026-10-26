from sim import metrics


def out(pct, done=False, reasons=(), key="J|color", **kw):
    o = {"key": key, "pct": pct, "done": done, "total": None, "done_count": None, "total_confirmed": False,
         "provisional": True, "reasons": list(reasons), "work_done": None, "send": True}
    o.update(kw)
    return o


def ob(t, truth, done=False):
    return {"t": t, "ts": f"2026-11-02T02:{t:02d}:00+00:00", "host": "h", "ok": True, "true_job": "J",
            "truth": {"pct": truth, "done": done}}


def test_false_completion_and_time_to_correct():
    m = metrics.score([ob(0, 50), ob(10, 60), ob(20, 70)], [out(100.0), out(100.0), out(70.0)])
    assert m["false_completion"] and m["fc_polls"] == 2 and m["ttc_min"] == 20 and not m["uncorrected"]


def test_false_regression_explained_or_not():
    o = [ob(0, 50), ob(1, 50), ob(2, 50)]
    m = metrics.score(o, [out(80.0), out(60.0), out(40.0, reasons=["scope_change"])])
    assert m["fr_unexplained"] == 1 and m["fr_explained"] == 1


def test_true_regression_is_not_false():
    m = metrics.score([ob(0, 80), ob(1, 50)], [out(80.0), out(50.0)])
    assert m["fr_unexplained"] == 0


def test_pms_shows_latest_observed_at():
    o = [ob(10, 50), ob(5, 50)]
    m = metrics.score(o, [out(50.0), out(20.0)])
    assert m["pms_stale"] == 1 and m["pms_max_err"] == 0.0


def test_invariants():
    o = [ob(0, 50), ob(1, 50), ob(2, 50)]
    v = metrics.check_invariants(o, [out(100.0, work_done=3), out(40.0, work_done=2), out(100.123)])
    kinds = {x["inv"] for x in v}
    assert {"INV1_bounded", "INV2_no_unconfirmed_done", "INV3_drop_explained", "INV4_work_monotonic"} <= kinds

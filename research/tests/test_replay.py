import pathlib

import pytest

from sim import replay

FIX = pathlib.Path(replay.REPO) / "server" / "tests" / "fixtures" / "replay"


def test_replay_of_the_committed_fixture_flags_the_false_100():
    res = replay.run([FIX])
    assert res["records"] >= 260
    assert res["by_strategy"]["S0"]["premature_100"] == 1
    prem = [a for a in res["anomalies"]["S0"] if a["flag"] == "premature_100"]
    assert prem[0]["key"] == "TEST|color" and prem[0]["ts"].startswith("2026-10-14T10:16")
    assert res["by_strategy"]["C3"]["premature_100"] == 0
    assert res["by_strategy"]["C3"]["unexplained_drop"] == 0
    assert "| S0 |" in replay.markdown(res)


@pytest.mark.slow
def test_replay_every_real_data_dir():
    dirs = replay.default_dirs()
    res = replay.run(dirs)
    assert res["by_strategy"]["C3"]["premature_100"] == 0

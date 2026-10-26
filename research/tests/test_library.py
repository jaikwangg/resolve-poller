import pytest

from sim import library, strategies

SCENARIOS = library.load_all()


def test_library_has_the_catalogue():
    ids = {sc["id"] for _, sc in SCENARIOS}
    assert len([i for i in ids if i.startswith("WC")]) >= 40
    assert "WC01_replay_2026_10_14_TEST" in ids


@pytest.mark.parametrize("path,sc", SCENARIOS, ids=[p.stem for p, _ in SCENARIOS])
def test_scenario(path, sc):
    assert library.validate(sc) == []
    if sc["status"] == "new":
        pytest.skip("untriaged fuzzer find: triage it (see docs/research/TESTING_SYSTEM.md)")
    _, res = library.evaluate(sc)
    assert library.check_expect(sc, res) == []
    assert res[strategies.RECOMMENDED][2] == [], "recommended strategy breaks an invariant"

"""Property-based tests: random edit histories must keep the invariants of the recommended strategy.
hypothesis shrinks a failing history to a minimal one; `python -m sim.fuzz --save` stores such
histories as scenarios. HYPOTHESIS_EXAMPLES raises the budget for the scheduled run."""
import json
import os

import pytest

from sim import fuzz, library, strategies

hyp = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402

N = int(os.environ.get("HYPOTHESIS_EXAMPLES", "60"))
GENE = st.tuples(st.sampled_from(fuzz.OBSERVABLE_OPS), st.integers(0, 9), st.integers(1, 39))
GENE_ANY = st.tuples(st.sampled_from(fuzz.OPS), st.integers(0, 9), st.integers(1, 39))
SET = settings(max_examples=N, deadline=None, suppress_health_check=[HealthCheck.too_slow], database=None)


@SET
@given(st.lists(GENE, max_size=20))
def test_recommended_keeps_all_invariants_on_observable_histories(genes):
    sc = fuzz.compose(genes)
    assert fuzz.violations(sc, strategies.RECOMMENDED) == []


@SET
@given(st.lists(GENE_ANY, max_size=20))
def test_recommended_never_unconfirmed_done_even_with_invisible_edits(genes):
    sc = fuzz.compose(genes, observable_only=False)
    v = [x for x in fuzz.violations(sc, strategies.RECOMMENDED) if x["inv"] != "INV7_done_is_true"]
    assert v == []


@settings(max_examples=max(10, N // 4), deadline=None, database=None)
@given(st.lists(GENE_ANY, max_size=15))
def test_same_input_same_output_for_every_strategy(genes):
    sc = fuzz.compose(genes, observable_only=False)
    _, a = library.evaluate(sc)
    _, b = library.evaluate(json.loads(json.dumps(sc)))
    for name in a:
        assert a[name][0] == b[name][0], name


def test_seeded_fuzz_smoke_and_shrinker():
    found, _ = fuzz.run(n=25, seed=1, strategy=strategies.RECOMMENDED)
    assert found == {}
    # the shrinker reduces a known S0 failure (100% with no confirmed scope) to its essence
    genes = [("wait", 0, 5), ("grade", 0, 2), ("cut", 4, 1), ("grade", 3, 2), ("add", 0, 1), ("wait", 0, 2)]

    def fails(g):
        return any(x["inv"] == "INV2_no_unconfirmed_done" for x in fuzz.violations(fuzz.compose(g), "S0"))
    assert fails(genes)
    small = fuzz.shrink(genes, fails)
    assert len(small) <= 2 and fails(small)


@pytest.mark.slow
def test_scheduled_fuzz_budget():
    found, _ = fuzz.run(n=int(os.environ.get("FUZZ_N", "400")), seed=int(os.environ.get("FUZZ_SEED", "0")),
                        strategy=strategies.RECOMMENDED)
    assert found == {}, list(found)


def test_found_failures_are_saved_as_valid_untriaged_scenarios(tmp_path, monkeypatch):
    monkeypatch.setattr(fuzz, "FOUND", tmp_path)
    found, paths = fuzz.run(n=20, seed=3, strategy="S0", do_save=True, max_saves=2)
    assert found and paths
    loaded = library.load_all([tmp_path])
    for _, sc in loaded:
        assert library.validate(sc) == [] and sc["status"] == "new"
        _, res = library.evaluate(sc)
        assert library.check_expect(sc, res)          # still failing: that is why it was saved

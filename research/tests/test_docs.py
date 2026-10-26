from sim import docs, library


def test_catalog_lists_every_scenario():
    text = (docs.DOCS / "WORST_CASES.md").read_text(encoding="utf-8")
    for _, sc in library.load_all():
        if sc["status"] != "new":
            assert f"## {sc['id']}" in text, f"{sc['id']} missing: run python -m sim.docs"


def test_comparison_has_generated_blocks():
    text = (docs.DOCS / "STRATEGY_COMPARISON.md").read_text(encoding="utf-8")
    for name in ("aggregate", "strategies", "matrix", "confirmed", "replay"):
        assert f"<!-- BEGIN GENERATED:{name} -->" in text

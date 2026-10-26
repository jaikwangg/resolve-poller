import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))                 # research/ -> import sim
REPO = HERE.parents[1]


def pytest_collection_modifyitems(config, items):
    import pytest
    for it in items:
        if "slow" not in it.keywords:
            it.add_marker(pytest.mark.sim)

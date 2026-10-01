"""Most tests look at individual small functions, so they run with grouping off.
test_grouping.py turns it on and tests it directly."""
import pytest

import chunker


@pytest.fixture(autouse=True)
def no_grouping_by_default(monkeypatch):
    monkeypatch.setattr(chunker, "GROUP_SMALL_CHUNKS", False)

"""Every server test has one owner; stale map patterns must be removed."""
from pathlib import Path

import pytest
from area_selection import AreaMap

pytestmark = pytest.mark.fast


def test_server_test_area_coverage_and_patterns():
    mapping = AreaMap(Path(__file__).resolve().parents[2])
    mapping.validate_patterns()
    assert set(mapping.owners) == set(mapping.files)

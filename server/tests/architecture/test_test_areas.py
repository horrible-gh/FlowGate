"""Every server test has one owner; stale map patterns must be removed."""
from pathlib import Path

import pytest
from area_selection import AreaMap

pytestmark = pytest.mark.fast


def test_server_test_area_coverage_and_patterns():
    mapping = AreaMap(Path(__file__).resolve().parents[2])
    mapping.validate_patterns()
    assert set(mapping.owners) == set(mapping.files)


def test_client_test_area_coverage_and_patterns():
    import json
    from fnmatch import fnmatchcase

    root = Path(__file__).resolve().parents[3] / "client_gui"
    areas = json.loads((root / "tests/areas.json").read_text(encoding="utf-8"))["area"]
    files = {path.relative_to(root).as_posix() for path in (root / "tests").rglob("*.spec.ts")}
    owners = {path: set() for path in files}
    for name, config in areas.items():
        assert config["tests"], name
        for pattern in config["tests"]:
            assert not any(char in pattern for char in "[]{}\\"), pattern
            matched = {path for path in files if fnmatchcase(path, pattern)}
            assert matched, f"Empty client test pattern: {name}: {pattern}"
            for path in matched:
                owners[path].add(name)
    invalid = {path: sorted(names) for path, names in owners.items() if len(names) != 1}
    assert files and not invalid, invalid

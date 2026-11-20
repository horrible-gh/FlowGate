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


GIT = "modules/flow_gate/services/git/"
GRAPH = "modules/flow_gate/graph_workflow/"


@pytest.mark.parametrize("changed, expected", [
    # A file owned by one sub-area: that sub-area plus the package catch-all, nothing else.
    (GIT + "branch_merge.py", {"git.core", "git.merge"}),
    (GIT + "worktree.py", {"git.core", "git.worktree"}),
    (GRAPH + "services/run_service.py", {"graph.definition", "graph.run"}),
    # A shared part: every sibling whose source imports it runs too (0053, review of seq 99).
    (GIT + "command.py", {"git.core", "git.merge", "git.recovery", "git.worktree"}),
    (GIT + "lock_manager.py", {"git.core", "git.merge", "git.recovery", "git.worktree", "git.remote"}),
    (GRAPH + "domain/graph_model.py", {"graph.definition", "graph.run", "graph.attempt"}),
    (GRAPH + "services/flow_guard.py",
     {"graph.definition", "graph.run", "graph.attempt", "graph.root", "graph.workflow"}),
    # Imported by no sibling: stays inside the catch-all sub-area.
    (GIT + "status.py", {"git.core"}),
    (GRAPH + "api/canvas_routes.py", {"graph.definition"}),
])
def test_shared_package_parts_select_the_sub_areas_that_import_them(changed, expected):
    """git.core / graph.definition take the whole package glob. If a shared part selected only
    them, changing command.py would skip the merge/worktree/recovery tests that run through it;
    if `also` tied the siblings together, every git source would run all of git again."""
    mapping = AreaMap(Path(__file__).resolve().parents[2])
    for tier in ("veryfast", "standard"):
        assert mapping.select(None, changed, tier) == expected

"""Selection must never silently omit changed paths or import excluded modules."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from area_selection import AreaMap

pytestmark = pytest.mark.fast


@pytest.fixture
def suite(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "areas.toml").write_text('''
[area."a.one"]
source = ["src/a.py"]
tests = ["tests/test_a.py"]
also = ["b"]
[area.b]
source = ["src/b.py"]
tests = ["tests/test_b.py"]
also = []
''', encoding="utf-8")
    (tests / "test_a.py").write_text("def test_a(): assert True\n", encoding="utf-8")
    (tests / "test_b.py").write_text("def test_b(): assert True\n", encoding="utf-8")
    return tmp_path


def test_parent_multi_area_and_changed_union(suite):
    mapping = AreaMap(suite)
    assert mapping.select("a", None, "fast") == {"a.one"}
    assert mapping.select("a.one,b", None, "fast") == {"a.one", "b"}
    assert mapping.select("a", "src/b.py", "fast") == {"a.one", "b"}
    assert mapping.select(None, "tests\\test_a.py", "fast") == {"a.one"}
    assert mapping.select(None, "./src/a.py", "fast") == {"a.one"}
    assert mapping.select("a", None, "standard") == {"a.one", "b"}


@pytest.mark.parametrize("area,changed", [
    ("missing", None), ("", None), ("a,", None),
    (None, "missing.py"), (None, "src/a.py,missing.py"),
    (None, "../src/a.py"), (None, "/src/a.py"), (None, "C:\\src\\a.py"),
    (None, ""),
])
def test_bad_selection_fails_closed(suite, area, changed):
    with pytest.raises(ValueError):
        AreaMap(suite).select(area, changed, "fast")


def test_unmapped_duplicate_and_literal_override(suite):
    path = suite / "tests/test_new.py"
    path.write_text("def test_new(): pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        AreaMap(suite)
    path.write_text('import pytest\npytestmark = [pytest.mark.area("b")]\n', encoding="utf-8")
    assert AreaMap(suite).owners["tests/test_new.py"] == "b"
    path.write_text('import pytest\npytestmark = pytest.mark.area("missing")\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown module area"):
        AreaMap(suite)
    path.unlink()
    config = suite / "tests/areas.toml"
    config.write_text(config.read_text(encoding="utf-8").replace(
        'tests = ["tests/test_b.py"]', 'tests = ["tests/test_*.py"]'), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        AreaMap(suite)
    # A file-level literal is the sole owner even when legacy globs overlap.
    (suite / "tests/test_a.py").write_text('import pytest\npytestmark = pytest.mark.area("a.one")\n', encoding="utf-8")
    assert AreaMap(suite).owners["tests/test_a.py"] == "a.one"


def test_stale_pattern_is_an_error(suite):
    with pytest.raises(ValueError, match="pattern matches no files"):
        AreaMap(suite).validate_patterns()


def run_pytest(suite, *args):
    plugin_dir = Path(__file__).resolve().parents[1]
    (suite / "conftest.py").write_text(
        "from pathlib import Path\nfrom area_selection import register\n"
        "def pytest_addoption(parser):\n"
        "    parser.addoption('--area', default=None)\n"
        "    parser.addoption('--changed', default=None)\n"
        "    parser.addoption('--tier', default='full')\n"
        "def pytest_configure(config):\n"
        "    register(config, Path(__file__).parent)\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(plugin_dir) + os.pathsep + env.get("PYTHONPATH", "")
    # This synthetic tree tests only selection; no product fixtures or slot reservations.
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "FLOWGATE_SELFCHECK_SLOT_DIR", "FLOWGATE_SELFCHECK_SLOTS"):
        env.pop(key, None)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", *args],
                          cwd=suite, env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("parallel", [False, True])
def test_excluded_module_is_not_imported_and_summary_survives_xdist(suite, parallel):
    (suite / "tests/test_b.py").write_text("raise RuntimeError('must not import')\n", encoding="utf-8")
    args = ["tests", "--area=a", "--tier=fast"]
    if parallel:
        args += ["-n", "2", "--dist", "loadfile"]
    result = run_pytest(suite, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "selected 1 files / excluded 1 files | collected 1 files" in result.stdout
    assert "not the full suite" in result.stdout


def test_explicit_excluded_path_is_not_imported(suite):
    (suite / "tests/test_b.py").write_text("raise RuntimeError('must not import')\n", encoding="utf-8")
    result = run_pytest(suite, "tests/test_a.py", "tests/test_b.py", "--area=a", "--tier=fast")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_no_area_preserves_legacy_collection_even_with_broken_map(suite):
    (suite / "tests/areas.toml").write_text("broken TOML", encoding="utf-8")
    result = run_pytest(suite, "tests")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    assert "area:" not in result.stdout


def test_unknown_changed_path_errors_before_test_import(suite):
    (suite / "tests/test_b.py").write_text("raise RuntimeError('must not import')\n", encoding="utf-8")
    result = run_pytest(suite, "tests", "--changed=unknown.py")
    assert result.returncode == 4, result.stdout + result.stderr
    assert "changed path has no area" in result.stderr
    assert "must not import" not in result.stdout + result.stderr

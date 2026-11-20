"""0053 A3: timing safety and controller report aggregation."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from timing_support import (duration_tier, fallback_tier, load_slow_hints, load_timings,
                            measured_tier, module_tier)

pytest_plugins = ["pytester"]


@pytest.mark.parametrize("seconds,tier", [
    (0, "veryfast"), (1, "veryfast"), (1.01, "fast"), (5, "fast"), (5.01, "standard"),
    (15, "standard"), (15.01, "heavy"), (30, "heavy"), (30.01, "slow"), (120, "slow"),
    (120.01, "install")])
def test_boundaries(seconds, tier):
    assert duration_tier(seconds) == tier


def test_baseline_requires_complete_unchanged_file(tmp_path):
    path = tmp_path / "tests/test_git.py"
    path.parent.mkdir()
    path.write_text("import subprocess", encoding="utf-8")
    record = {"seconds": 1, "eligible": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    records = {"tests/test_git.py": record}
    assert measured_tier(records, path, tmp_path) == "veryfast"
    record["eligible"] = False
    assert measured_tier(records, path, tmp_path) is None
    record["eligible"] = True
    path.write_text("changed", encoding="utf-8")
    assert measured_tier(records, path, tmp_path) is None
    assert load_timings(tmp_path / "absent.json") == {}


def test_unmeasured_file_falls_back_and_hints_only_move_up(tmp_path):
    tests = tmp_path / "tests"
    (tests / "e2e").mkdir(parents=True)
    plain, shell, e2e = tests / "test_plain.py", tests / "test_shell.py", tests / "e2e/test_flow.py"
    plain.write_text("def test_a(): pass\n", encoding="utf-8")
    shell.write_text("import subprocess\n", encoding="utf-8")
    e2e.write_text("def test_b(): pass\n", encoding="utf-8")
    assert [fallback_tier(p) for p in (plain, shell, e2e)] == ["standard", "heavy", "install"]
    assert module_tier({}, {}, plain, tmp_path) == ("standard", None, "fallback")
    # Seen running 90s while failing: no baseline, but it must not sit in standard.
    assert module_tier({}, {"tests/test_plain.py": 90}, plain, tmp_path) == ("slow", None, "hint")
    assert module_tier({}, {"tests/test_plain.py": 500}, plain, tmp_path) == ("install", None, "hint")
    assert module_tier({}, {"tests/test_shell.py": 2}, shell, tmp_path) == ("heavy", None, "fallback")
    baseline = tmp_path / "timings.json"
    baseline.write_text(json.dumps({"version": 1, "files": {}, "measurement": {
        "ineligible_files_over_60s": [{"path": "tests/test_plain.py", "seconds": 90},
                                      {"path": "tests/test_bad.py", "seconds": "x"}]}}), encoding="utf-8")
    assert load_slow_hints(baseline) == {"tests/test_plain.py": 90}
    assert load_slow_hints(tmp_path / "absent.json") == {}


@pytest.mark.parametrize("seconds", [-1, float("nan"), True])
def test_bad_baseline_rejected(tmp_path, seconds):
    path = tmp_path / "timings.json"
    path.write_text(json.dumps({"version": 1, "files": {"tests/test_a.py": {"seconds": seconds, "eligible": True}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_timings(path)


@pytest.fixture
def timing_project(pytester):
    helper = Path(__file__).resolve().parents[1] / "timing_support.py"
    pytester.makeconftest(
        "import sys\nfrom pathlib import Path\n"
        + f"sys.path.insert(0, {str(helper.parent)!r})\n"
        + "from timing_support import TimingRecorder\n"
        + "def pytest_addoption(parser):\n    parser.addoption('--tier', default='standard')\n"
        + "def pytest_configure(config):\n    config.pluginmanager.register(TimingRecorder(config, Path(__file__).parent, Path(__file__).parent / 'out.json'), 'timings')\n"
    )
    directory = pytester.path / "tests"
    directory.mkdir()
    (directory / "test_sample.py").write_text(
        "import pytest\n@pytest.mark.install\ndef test_one(): pass\ndef test_two(): pass\n", encoding="utf-8")
    return pytester


@pytest.mark.parametrize("args,eligible,passed", [([], True, 2), (["-k", "one"], False, 1), (["-n", "2", "--dist", "loadfile"], False, 2)])
def test_real_reports(timing_project, args, eligible, passed):
    result = timing_project.runpytest_subprocess("-q", *args)
    result.assert_outcomes(passed=passed)
    records = load_timings(timing_project.path / "out.json")
    record = records["tests/test_sample.py"]
    assert record["eligible"] is eligible
    assert record["seconds"] > 0
    assert sum(record["seconds_by_marker"].values()) == pytest.approx(record["seconds"])
    assert "install" in record["seconds_by_marker"]


def test_skipped_file_is_not_baseline(timing_project):
    path = timing_project.path / "tests/test_sample.py"
    path.write_text("import pytest\ndef test_one(): pytest.skip('host unavailable')\n", encoding="utf-8")
    timing_project.runpytest_subprocess("-q").assert_outcomes(skipped=1)
    assert load_timings(timing_project.path / "out.json")["tests/test_sample.py"]["eligible"] is False


def test_budget_uses_marker_overrides_and_reports_unknown(tmp_path):
    from types import SimpleNamespace
    source = Path(__file__).resolve().parents[3] / "tools/test_budget.py"
    spec = importlib.util.spec_from_file_location("fg_budget", source)
    budget = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(budget)
    path = tmp_path / "test_one.py"
    path.write_text("# file", encoding="utf-8")
    records = {"test_one.py": {"seconds": 70, "eligible": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "seconds_by_marker": {"fast": 2, "auto": 68}}}
    other = tmp_path / "test_two.py"
    other.write_text("# unmeasured", encoding="utf-8")
    mapping = SimpleNamespace(areas={"git": {}}, owners={"test_one.py": "git", "test_two.py": "git"},
                              files={"test_one.py": path, "test_two.py": other}, root=tmp_path)
    rows, slow = budget.budget_rows(mapping, records)
    row = rows["git"]
    # The explicit fast bucket counts from fast up; the rest follows the 70s file (slow).
    assert [row[t] for t in ("veryfast", "fast", "standard", "heavy", "slow")] == [0, 2, 2, 2, 70]
    assert row["files"]["slow"] == 1 and row["files"]["standard"] == 1
    assert row["unknown"] == 1
    assert row["unknown_at"] == {"veryfast": 0, "fast": 0, "standard": 1, "heavy": 1, "slow": 1, "install": 1}
    assert slow == [(70, "test_one.py")]
    assert budget.flags(row) == []
    row["standard"] = 130.0
    assert budget.flags(row) == ["standard 65s>60s"]


def test_conftest_measured_tier_and_marker_priority(pytestconfig, monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "conftest.py"
    conf = next(p for p in pytestconfig.pluginmanager.get_plugins()
                if getattr(p, "__file__", None) and Path(p.__file__).resolve() == source)
    path = tmp_path / "tests/test_git.py"
    path.parent.mkdir()
    path.write_text("import subprocess", encoding="utf-8")
    monkeypatch.setattr(conf, "_SERVER_DIR", tmp_path)
    monkeypatch.setattr(conf, "_measured_timings", {"tests/test_git.py": {
        "seconds": 1, "eligible": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}})
    assert conf._tier_for_module(path) == "veryfast"
    class Item:
        fspath = path
        marks = []
        def get_closest_marker(self, name):
            return object() if name == "slow" else None
        def add_marker(self, mark):
            self.marks.append(mark)
    class Config:
        def getoption(self, name, default=None):
            return "fast"
    monkeypatch.delenv("FLOWGATE_REQUIRE_FULL_SUITE", raising=False)
    item = Item()
    conf.pytest_collection_modifyitems(Config(), [item])
    assert item.marks  # explicit slow wins over measured veryfast
    monkeypatch.setattr(conf, "_measured_timings", {})
    monkeypatch.setattr(conf, "_tier_cache", {})
    assert conf._tier_for_module(path) == "heavy"  # unmeasured subprocess file
    monkeypatch.setattr(conf, "_slow_hints", {"tests/test_git.py": 400})
    monkeypatch.setattr(conf, "_tier_cache", {})
    assert conf._tier_for_module(path) == "install"  # seen far over the slow limit



@pytest.mark.parametrize("destination", ["file", "stdout"])
@pytest.mark.parametrize("args,eligible", [([], True), (["-n", "2", "--dist", "loadfile"], False)])
def test_real_conftest_option_writes_output(pytester, args, eligible, destination):
    source = Path(__file__).resolve().parents[1]
    directory = pytester.path / "tests"
    directory.mkdir()
    for name in ("conftest.py", "timing_support.py"):
        (directory / name).write_bytes((source / name).read_bytes())
    (directory / "test_sample.py").write_text("def test_one(): pass\n", encoding="utf-8")
    output = pytester.path / "timings.json"
    target = "-" if destination == "stdout" else str(output)
    result = pytester.runpytest_subprocess("tests/test_sample.py", "-q", f"--fg-timings-out={target}", *args)
    result.assert_outcomes(passed=1)
    if destination == "stdout":
        lines = [line for line in result.stdout.lines if line.startswith("FLOWGATE-TIMINGS ")]
        assert len(lines) == 1  # only the controller publishes, even with xdist
        records = json.loads(lines[0].removeprefix("FLOWGATE-TIMINGS "))["files"]
        assert not output.exists() and not (pytester.path / "-").exists()
    else:
        records = load_timings(output)
    assert records["tests/test_sample.py"]["eligible"] is eligible


def test_baseline_eligibility_is_independent_per_file(timing_project):
    directory = timing_project.path / "tests"
    (directory / "test_failed.py").write_text(
        "def test_failure(): assert False\n", encoding="utf-8")
    (directory / "test_skipped.py").write_text(
        "import pytest\ndef test_skip(): pytest.skip('unavailable')\n", encoding="utf-8")
    result = timing_project.runpytest_subprocess("-q")
    result.assert_outcomes(passed=2, failed=1, skipped=1)
    records = load_timings(timing_project.path / "out.json")
    assert records["tests/test_sample.py"]["eligible"] is True
    assert records["tests/test_failed.py"]["eligible"] is False
    assert records["tests/test_skipped.py"]["eligible"] is False


def test_reports_survive_platform_override_until_teardown(timing_project):
    path = timing_project.path / "tests/test_sample.py"
    path.write_text(
        "import os\ndef test_platform(monkeypatch):\n"
        "    monkeypatch.setattr(os, 'name', 'posix' if os.name == 'nt' else 'nt')\n",
        encoding="utf-8",
    )
    timing_project.runpytest_subprocess("-q").assert_outcomes(passed=1)
    record = load_timings(timing_project.path / "out.json")["tests/test_sample.py"]
    assert record["eligible"] is True
    assert record["seconds"] > 0

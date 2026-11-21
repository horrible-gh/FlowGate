"""Selection boundaries and actual process termination for the bounded runner."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest

SPEC = importlib.util.spec_from_file_location("area_runner", Path(__file__).resolve().parents[3] / "tools/test_run.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def mapping():
    return SimpleNamespace(areas={"git": {}, "git.child": {}},
        owners={"tests/test_a.py": "git", "tests/test_b.py": "git.child"},
        select=lambda *args: {"git", "git.child"})


def test_work_has_one_shared_minute_for_all_changed_areas():
    runs = runner.plan(mapping(), "work", changed="source.py")
    assert len(runs) == 1 and runs[0]["seconds"] == 60
    assert runs[0]["files"] == ["tests/test_a.py", "tests/test_b.py"]
    assert "--tier=standard" in runs[0]["command"]
    assert "--install-check" not in runs[0]["command"]


def test_verification_has_independent_five_minutes_without_duplicate_children():
    runs = runner.plan(mapping(), "verify", all_areas=True)
    assert [(r["name"], r["files"], r["seconds"]) for r in runs] == [
        ("git", ["tests/test_a.py"], 300), ("git.child", ["tests/test_b.py"], 300)]
    assert all("--install-check" in r["command"] for r in runs)
    assert all(not any(a.startswith("--area") for a in r["command"]) for r in runs)


@pytest.mark.parametrize("options", [{}, {"all_areas": True}, {"all_areas": True, "area": "git"}])
def test_work_cannot_fall_back_to_unbounded_full_suite(options):
    with pytest.raises(ValueError):
        runner.plan(mapping(), "work", **options)


def test_timeout_stops_real_subprocess_and_reports_failure(tmp_path):
    marker = tmp_path / "late"
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(2)\nfrom pathlib import Path\n"
                      + f"Path({str(marker)!r}).write_text('should not run')\n", encoding="utf-8")
    assert runner.run_command([sys.executable, str(script)], tmp_path, 0.1) == 124
    time.sleep(2.1)
    assert not marker.exists()


def test_real_subprocess_failure_is_preserved(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text("raise SystemExit(3)\n", encoding="utf-8")
    assert runner.run_command([sys.executable, str(script)], tmp_path, 5) == 3

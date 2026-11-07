"""flowgate.v02.0146 T#2 — the per-test hang guard (tests/hang_guard.py) fails a hanging test, by name, long before
the Self-check ceiling, and never turns the hang into a pass.

The guard is exercised in a child pytest (``-p hang_guard``) over a throw-away test file, so the real suite is never
made to hang. The child's own limit is 3 s; the parent allows 60 s for it to end.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
LIMIT = 3

HANGS = textwrap.dedent("""
    import time

    def test_before_the_hang():
        assert True

    def test_this_one_hangs():
        time.sleep(300)          # stands for a Git call / Event.wait() nobody ever ends

    def test_after_the_hang():
        assert True
""")


def _child(tmp_path, *pytest_args, env_limit=str(LIMIT), source=HANGS, name="test_child_hang.py", extra_path=None):
    (tmp_path / name).write_text(source, encoding="utf-8")
    path = os.pathsep.join([str(TESTS_DIR), *([str(extra_path)] if extra_path else []),
                            *([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])])
    env = {**os.environ, "PYTHONPATH": path, ENV: env_limit}
    env.pop("PYTEST_TIMEOUT", None)
    for leaked in ("FLOWGATE_SELFCHECK_SLOT_DIR", "FLOWGATE_SELFCHECK_SLOTS"):
        env.pop(leaked, None)
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "hang_guard", "-p", "no:cacheprovider",
                           "-p", "no:randomly", *pytest_args, str(tmp_path / name)],
                          cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60)
    return proc, time.monotonic() - started


ENV = "FLOWGATE_TEST_TIMEOUT"


def test_a_hanging_test_fails_the_run_by_name_long_before_the_self_check_ceiling(tmp_path):
    proc, elapsed = _child(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 70, out                    # a failure status, never 0
    assert elapsed < 40, elapsed                         # ~3 s limit + interpreter start, not 300 s
    assert "FLOWGATE HANG GUARD" in out and "test_this_one_hangs" in out and f"{LIMIT}s test limit" in out, out
    assert "test_before_the_hang" not in out.split("FLOWGATE HANG GUARD")[1]    # only the hanging test is named
    assert "Thread" in out or "thread" in out            # the stacks of the threads that were stuck


def test_a_hanging_worker_is_a_failed_test_under_xdist_and_the_others_still_run(tmp_path):
    pytest.importorskip("xdist")
    (tmp_path / "test_child_fine.py").write_text("def test_fine_1():\n    pass\n\ndef test_fine_2():\n    pass\n",
                                                 encoding="utf-8")
    proc, elapsed = _child(tmp_path, "-n", "2", "--dist", "loadfile", str(tmp_path / "test_child_fine.py"))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert elapsed < 50, elapsed
    assert "crashed while running" in out and "test_this_one_hangs" in out, out
    assert "passed" in out and "failed" in out, out      # test_fine_* ran and passed on the other worker


def test_the_marker_beats_the_environment_and_zero_turns_the_guard_off(tmp_path):
    marked = textwrap.dedent("""
        import time, pytest

        @pytest.mark.timeout(2)
        def test_marked_to_two_seconds():
            time.sleep(120)
    """)
    proc, elapsed = _child(tmp_path, env_limit="600", source=marked, name="test_child_marked.py")
    assert proc.returncode == 70 and "2s test limit" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    assert elapsed < 40, elapsed

    quick = "import time\n\ndef test_longer_than_the_would_be_limit():\n    time.sleep(4)\n"
    off, _ = _child(tmp_path, env_limit="0", source=quick, name="test_child_off.py")
    assert off.returncode == 0, off.stdout + off.stderr   # guard off: no limit, the 4 s test just passes


def test_a_test_inside_the_limit_is_untouched(tmp_path):
    fine = "import time\n\ndef test_inside():\n    time.sleep(1)\n"
    proc, _ = _child(tmp_path, source=fine, name="test_child_inside.py")
    assert proc.returncode == 0 and "1 passed" in proc.stdout, proc.stdout + proc.stderr


def test_the_limit_resolution_prefers_marker_then_environment_then_default(monkeypatch):
    import hang_guard

    class Item:
        def __init__(self, marker=None):
            self._marker = marker

        def get_closest_marker(self, name):
            return self._marker

    class Marker:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    monkeypatch.delenv(ENV, raising=False)
    assert hang_guard.limit_for(Item()) == hang_guard.DEFAULT_SECONDS == 900
    monkeypatch.setenv(ENV, "120")
    assert hang_guard.limit_for(Item()) == 120
    assert hang_guard.limit_for(Item(Marker(30))) == 30 and hang_guard.limit_for(Item(Marker(timeout=45))) == 45
    monkeypatch.setenv(ENV, "0")
    assert hang_guard.limit_for(Item()) == 0
    monkeypatch.setenv(ENV, "not-a-number")
    assert hang_guard.limit_for(Item()) == hang_guard.DEFAULT_SECONDS


# A plugin registered under the name ``timeout`` is how pytest-timeout looks to the guard. This stub loads under that
# exact name and, like pytest-timeout without a configured limit, never stops a test: it only accepts the marker.
STUB = textwrap.dedent("""
    def pytest_configure(config):
        config.addinivalue_line("markers", "timeout(seconds): stub of the pytest-timeout marker")
""")


def _stub_plugin_dir(tmp_path):
    stub_dir = tmp_path / "stubplugin"
    stub_dir.mkdir()
    (stub_dir / "timeout.py").write_text(STUB, encoding="utf-8")
    return stub_dir


def test_a_loaded_timeout_plugin_does_not_switch_the_guard_off(tmp_path):
    """The reproduced defect: with a plugin named ``timeout`` loaded and no plugin limit, a 1 s sleep under a
    0.1 s FLOWGATE_TEST_TIMEOUT passed (exit 0). The environment limit must still fail it."""
    stub_dir = _stub_plugin_dir(tmp_path)
    sleeper = "import time\n\ndef test_unmarked_sleeper():\n    time.sleep(30)\n"
    proc, elapsed = _child(tmp_path, "-p", "timeout", env_limit="0.5", source=sleeper, name="test_child_sleep.py",
                           extra_path=stub_dir)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 70, out
    assert elapsed < 40, elapsed
    assert "FLOWGATE HANG GUARD" in out and "test_unmarked_sleeper" in out and "0.5s test limit" in out, out

    off, _ = _child(tmp_path, "-p", "timeout", env_limit="0",
                    source="import time\n\ndef test_ok():\n    time.sleep(2)\n", name="test_child_off2.py",
                    extra_path=stub_dir)
    assert off.returncode == 0, off.stdout + off.stderr           # 0 still turns the guard off


def test_the_default_limit_is_applied_when_a_timeout_plugin_is_loaded(tmp_path):
    stub_dir = _stub_plugin_dir(tmp_path)
    probe = textwrap.dedent("""
        import hang_guard

        def test_default(request):
            assert hang_guard.limit_for(request.node) == hang_guard.DEFAULT_SECONDS
            assert request.config.pluginmanager.has_plugin("flowgate-hang-guard")
            assert request.config.pluginmanager.hasplugin("timeout")
    """)
    env_clear = {"env_limit": ""}                         # empty value -> unparsable -> the 900 s default
    proc, _ = _child(tmp_path, "-p", "timeout", source=probe, name="test_child_default.py", extra_path=stub_dir,
                     **env_clear)
    assert proc.returncode == 0 and "1 passed" in proc.stdout, proc.stdout + proc.stderr


def test_the_real_pytest_timeout_plugin_does_not_switch_the_guard_off(tmp_path):
    """Same check with the real plugin, when it is importable (not installed on the Self-check host)."""
    pytest.importorskip("pytest_timeout")
    sleeper = "import time\n\ndef test_unmarked_sleeper():\n    time.sleep(30)\n"
    proc, elapsed = _child(tmp_path, env_limit="0.5", source=sleeper,
                           name="test_child_real.py")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0 and elapsed < 40, out
    assert "FLOWGATE HANG GUARD" in out, out

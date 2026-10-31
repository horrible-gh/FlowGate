"""flowgate.v02.0114 T#4: the per-run worker slot pool of ``server/tests`` pytest (D 0006 3.5 (3),
9.2-7 (e), the pool half of (f)).

Real pytest sessions run in a throwaway repository holding a copy of the real
``server/tests/conftest.py`` (as ``test_repo_scratch_guard_0382`` does), with the pool Self-check
would name (``tr_self_check_policy.slot_pool_env``) in their environment. What they report is read
from the real result block (``selfcheck_evidence``) through the server parser; which processes
ran tests is recorded by the tests themselves. On Windows the last case runs the whole tree
through ``executor.spawn(cpu_limit=2)`` and checks every process's processors through the OS.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.services import tr_self_check_result as result_block

_TESTS_DIR = Path(__file__).resolve().parents[1]
_CONFTEST = _TESTS_DIR / "conftest.py"
pytest.importorskip("xdist")

_RECORDING_TEST = textwrap.dedent('''
    import json, os, time
    from pathlib import Path


    def _affinity():
        try:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                k = ctypes.WinDLL("kernel32")
                k.GetCurrentProcess.restype = wintypes.HANDLE
                k.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                                     ctypes.POINTER(ctypes.c_size_t)]
                mine, system = ctypes.c_size_t(), ctypes.c_size_t()
                k.GetProcessAffinityMask(k.GetCurrentProcess(), ctypes.byref(mine), ctypes.byref(system))
                return bin(mine.value).count("1")
            return len(os.sched_getaffinity(0))
        except Exception:
            return None


    def test_record():
        out = Path(os.environ["SLOT_TEST_OUT"])
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{os.getpid()}-{__name__}.json").write_text(json.dumps({
            "pid": os.getpid(), "worker": os.environ.get("PYTEST_XDIST_WORKER"), "cpus": _affinity(),
            "tree": os.environ.get("SLOT_TEST_TREE", "outer")}))
        time.sleep(float(os.environ.get("SLOT_TEST_SLEEP", "0.3")))
''')

_NESTED_TEST = textwrap.dedent('''
    import json, os, subprocess, sys
    from pathlib import Path


    def test_nested_pytest_runs():
        env = dict(os.environ, SLOT_TEST_TREE="nested")
        for key in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT"):   # the outer worker's own marks
            env.pop(key, None)
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "xdist.plugin",
                               "-n", "4", "--dist", "loadfile", "tests"], cwd=os.environ["SLOT_INNER_SERVER"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                              timeout=240)
        Path(os.environ["SLOT_TEST_OUT"], "nested.json").write_text(json.dumps(
            {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr[-3000:]}))
        assert proc.returncode == 0, proc.stdout[-2000:]
''')


def _repo(root: Path, files: dict) -> Path:
    """A throwaway repository: ``server/tests/conftest.py`` is the real one. Returns ``server``."""
    tests = root / "server" / "tests"
    tests.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(".test-tmp-*/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (tests / "conftest.py").write_text(_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    for name, text in files.items():
        (tests / name).write_text(text, encoding="utf-8")
    return root / "server"


def _env(out: Path, pool: dict | None = None, **extra) -> dict:
    env = os.environ.copy()
    for key in (*policy.SLOT_ENV_KEYS, "FLOWGATE_REQUIRE_FULL_SUITE", "PYTEST_XDIST_WORKER",
                "PYTEST_XDIST_WORKER_COUNT", "PYTEST_ADDOPTS"):
        env.pop(key, None)                    # this session's own pool never leaks into the child
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               SLOT_TEST_OUT=str(out),
               PYTHONPATH=os.pathsep.join([str(_TESTS_DIR), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep))
    env.update(pool or {})
    env.update(extra)
    return env


def _pytest_argv(*args: str) -> list[str]:
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "xdist.plugin", *args, "tests"]


def _summarize(stdout: str, returncode: int) -> tuple[dict, dict]:
    summary, environment = result_block.summarize(stdout, returncode)
    assert summary["status"] == result_block.COMPLETE, (summary, stdout[-2500:])
    return summary, environment


def _records(out: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in out.glob("*-*.json")]


def _lockable(pool_dir: Path, index: int) -> bool:
    handle = _live_conftest()._lock_slot(pool_dir / f"slot-{index}")
    if handle is None:
        return False
    handle.close()
    return True


def _live_conftest():
    """The module pytest loaded from server/tests/conftest.py in this session."""
    target = _CONFTEST.resolve()
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and Path(path).resolve() == target:
            return module
    raise LookupError("server/tests/conftest.py is not loaded")


FILES = {f"test_part_{i}.py": _RECORDING_TEST for i in range(4)}


@pytest.mark.parametrize("workers", ["8", "auto"])
def test_7e_a_share_two_run_applies_at_most_two_workers_whatever_it_asks(tmp_path, workers):
    server = _repo(tmp_path / "repo", FILES)
    pool = policy.slot_pool_env(tmp_path / "runtime", 2)
    out = tmp_path / "out"
    proc = subprocess.run(_pytest_argv("-n", workers, "--dist", "loadfile"), cwd=str(server), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=_env(out, pool), timeout=300)
    assert proc.returncode == 0, proc.stdout[-3000:]
    summary, environment = _summarize(proc.stdout, proc.returncode)
    assert environment["workers"]["mode"] == "xdist" and environment["workers"]["applied"] == 2
    parallel = summary["parallel"]
    assert (parallel["applied"], parallel["slot_source"], parallel["pool_size"]) == (2, "pool", 2)
    assert parallel["requested"] == 8 if workers == "8" else parallel["requested"] >= 2
    assert environment["workers"]["cpu_limit"] == parallel["cpu_limit"] and parallel["cpu_limit"] >= 1
    pids = {r["pid"] for r in _records(out)}
    assert len(_records(out)) == 4 and 1 <= len(pids) <= 2
    pool_dir = Path(pool[policy.SLOT_DIR_ENV])
    assert _lockable(pool_dir, 0) and _lockable(pool_dir, 1)               # released at the end


def test_7e_a_nested_pytest_finds_no_free_slot_and_runs_serially_without_waiting(tmp_path):
    inner = _repo(tmp_path / "inner", {"test_inner_a.py": _RECORDING_TEST, "test_inner_b.py": _RECORDING_TEST})
    outer = _repo(tmp_path / "outer", {"test_outer_record.py": _RECORDING_TEST, "test_outer_nested.py": _NESTED_TEST})
    pool = policy.slot_pool_env(tmp_path / "runtime", 2)
    out = tmp_path / "out"
    started = time.monotonic()
    proc = subprocess.run(_pytest_argv("-n", "2", "--dist", "loadfile"), cwd=str(outer), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=_env(out, pool, SLOT_INNER_SERVER=str(inner)), timeout=300)
    assert proc.returncode == 0, proc.stdout[-3000:]
    assert time.monotonic() - started < 240
    summary, environment = _summarize(proc.stdout, proc.returncode)
    assert summary["parallel"]["applied"] == 2 and environment["workers"]["applied"] == 2
    nested = json.loads((out / "nested.json").read_text())
    nested_summary, nested_env = _summarize(nested["stdout"], nested["returncode"])
    assert nested_env["workers"] == {"mode": "serial", "applied": 0, "dist": "no",
                                     "cpu_limit": nested_summary["parallel"]["cpu_limit"]}
    assert nested_summary["parallel"]["slot_source"] == "none" and nested_summary["parallel"]["requested"] == 4
    records = _records(out)
    xdist_workers = {r["pid"] for r in records if r["worker"]}
    assert len(xdist_workers) <= 2                                          # the whole run: never above 2
    assert {r["tree"] for r in records} == {"outer", "nested"}
    assert all(r["worker"] is None for r in records if r["tree"] == "nested")


def test_7e_two_pytest_runs_started_together_share_the_two_slots(tmp_path):
    pool = policy.slot_pool_env(tmp_path / "runtime", 2)
    runs = []
    for name in ("first", "second"):
        server = _repo(tmp_path / name, FILES)
        runs.append((server, tmp_path / f"out_{name}"))
    procs = [subprocess.Popen(_pytest_argv("-n", "2", "--dist", "loadfile"), cwd=str(server), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                              env=_env(out, pool)) for server, out in runs]
    outputs = [p.communicate(timeout=300) for p in procs]
    applied = []
    for proc, (stdout, _stderr) in zip(procs, outputs):
        assert proc.returncode == 0, stdout[-3000:]
        summary, environment = _summarize(stdout, proc.returncode)
        applied.append(environment["workers"]["applied"])
        assert summary["parallel"]["applied"] == environment["workers"]["applied"]
    assert sum(applied) <= 2 and max(applied) >= 1, applied


_LEAK_TEST = ("from pathlib import Path\n"
              "def test_leaks_ignored_scratch():\n"
              "    root = Path(__file__).resolve().parents[2]\n"
              "    leak = root / '.test-tmp-9999'\n"
              "    leak.mkdir()\n"
              "    (leak / 'left.txt').write_text('x', encoding='utf-8')\n")


@pytest.mark.parametrize("size,held,source", [(1, 1, "none"), (2, 1, "pool")])
def test_7e_the_0382_nested_session_neither_waits_nor_fails_for_a_slot(tmp_path, size, held, source):
    """The shape of test_repo_scratch_guard_0382::test_pollution_guard_fails_a_session_that_leaves_ignored_scratch
    inside a run whose outer session already holds a slot: serial Self-check (share 1, no slot left)
    and share 2 (one left). The nested session still ends with the guard's failure, unchanged."""
    server = _repo(tmp_path / "repo", {"test_leak.py": _LEAK_TEST})
    pool = policy.slot_pool_env(tmp_path / "runtime", size)
    pool_dir = Path(pool[policy.SLOT_DIR_ENV])
    holders = [_live_conftest()._lock_slot(pool_dir / f"slot-{i}") for i in range(held)]   # the outer session
    try:
        assert all(holders)
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-m", "pytest", "test_leak.py", "-q"], cwd=str(server / "tests"),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=_env(tmp_path / "out", pool), timeout=120)
        assert time.monotonic() - started < 120
    finally:
        for handle in holders:
            if handle:
                handle.close()
    output = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0
    assert "테스트가 저장소 안에 파일을 남겼습니다" in output and ".test-tmp-9999" in output
    summary, _environment = result_block.summarize(proc.stdout, proc.returncode)
    assert summary.get("parallel", {}) and summary["parallel"]["slot_source"] == source, summary


def test_7e_a_killed_slot_holder_frees_its_slot(tmp_path):
    server = _repo(tmp_path / "repo", {"test_hold.py": _RECORDING_TEST})
    pool = policy.slot_pool_env(tmp_path / "runtime", 1)
    pool_dir, out = Path(pool[policy.SLOT_DIR_ENV]), tmp_path / "out"
    proc = subprocess.Popen(_pytest_argv(), cwd=str(server), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=_env(out, pool, SLOT_TEST_SLEEP="120"))
    try:
        deadline = time.monotonic() + 60
        while not _records(out):
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        assert not _lockable(pool_dir, 0)                                   # the session holds it
    finally:
        proc.kill()
        proc.wait(timeout=30)
    assert _lockable(pool_dir, 0)                                           # the OS let it go


def test_7f_without_a_pool_a_local_run_is_unchanged(tmp_path):
    server = _repo(tmp_path / "repo", FILES)
    proc = subprocess.run(_pytest_argv("-n", "2", "--dist", "loadfile"), cwd=str(server), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=_env(tmp_path / "out"), timeout=300)
    assert proc.returncode == 0, proc.stdout[-3000:]
    summary, environment = _summarize(proc.stdout, proc.returncode)
    assert environment["workers"] == {"mode": "xdist", "applied": 2, "dist": "loadfile", "cpu_limit": None}
    assert summary["parallel"] is None


def test_7e_a_worker_that_loses_its_handed_slot_fails_instead_of_running(tmp_path, monkeypatch):
    conftest = _live_conftest()
    pool = policy.slot_pool_env(tmp_path / "runtime", 2)
    pool_dir = Path(pool[policy.SLOT_DIR_ENV])
    for key, value in pool.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(conftest, "_slot_handles", [])
    monkeypatch.setattr(conftest, "_slot_handoff", [])
    monkeypatch.setattr(conftest, "_slot_state", {})
    thief = conftest._lock_slot(pool_dir / "slot-0")
    try:
        # controller: -n 8 with one free slot -> one worker, the counted slot kept for it
        option = SimpleNamespace(dist="loadfile", tx=["popen"] * 8, numprocesses=8)
        conftest._apply_slot_pool(SimpleNamespace(option=option))
        assert (option.numprocesses, option.tx) == (1, ["popen"])
        assert conftest._slot_state["applied"] == 1 and len(conftest._slot_handoff) == 1
        node = SimpleNamespace(workerinput={})
        conftest.pytest_configure_node(node)
        assert node.workerinput["flowgate_slot"] == 1 and not conftest._slot_handoff
        # a worker whose slot was taken meanwhile and finds none free fails, it does not run
        other = conftest._lock_slot(pool_dir / "slot-1")
        try:
            with pytest.raises(pytest.UsageError, match="no free slot"):
                conftest._apply_slot_pool(SimpleNamespace(workerinput={"flowgate_slot": 1}, option=option))
        finally:
            other.close()
        conftest._apply_slot_pool(SimpleNamespace(workerinput={"flowgate_slot": 1}, option=option))
        assert len(conftest._slot_handles) == 1                              # took its slot
    finally:
        thief.close()
        while conftest._slot_handles:
            conftest._slot_handles.pop().close()


@pytest.mark.skipif(os.name != "nt", reason="the OS query of every member uses the Windows Job")
def test_7e_7f_every_process_of_a_limited_run_stays_on_its_two_processors(tmp_path):
    """Outer pytest -n 2 holding both slots, a nested pytest -n 4 started by a test: the whole tree
    spawned by the Self-check executor with R=2 and the pool of size 2."""
    inner = _repo(tmp_path / "inner", {"test_inner_a.py": _RECORDING_TEST, "test_inner_b.py": _RECORDING_TEST})
    outer = _repo(tmp_path / "outer", {"test_outer_record.py": _RECORDING_TEST, "test_outer_nested.py": _NESTED_TEST})
    runtime, out = tmp_path / "runtime", tmp_path / "out"
    env = _env(out, policy.slot_pool_env(runtime, 2), SLOT_INNER_SERVER=str(inner))
    control, ownership = executor.spawn(_pytest_argv("-n", "2", "--dist", "loadfile"), outer, env, 300,
                                        "scr_0114_slot_tree", cpu_limit=2)
    try:
        result = executor.wait(control, 300, threading.Event())
    finally:
        control.close()
    assert result.exit_code == 0, result.stdout_tail[-3000:]
    summary, environment = _summarize(result.stdout_tail, result.exit_code)
    assert environment["workers"]["applied"] == 2 and environment["workers"]["cpu_limit"] == 2
    records = _records(out)
    assert {r["tree"] for r in records} == {"outer", "nested"}
    assert all(r["cpus"] == 2 for r in records), records                    # every test process: 2 processors
    assert len({r["pid"] for r in records if r["worker"]}) <= 2


_GUARD_0382 = ("tests/architecture/test_repo_scratch_guard_0382.py"
               "::test_pollution_guard_fails_a_session_that_leaves_ignored_scratch")


@pytest.mark.skipif(os.name != "nt", reason="the run tree is the Self-check executor's Windows Job")
@pytest.mark.parametrize("size", [1, 2])
def test_7e_the_original_0382_case_passes_inside_a_run_of_share_one_and_two(tmp_path, size):
    """D 9.2-7 (e): the original nodeid itself -- not a copy, no expectation changed -- in a run tree
    spawned by the Self-check executor with R processors and the real pool of R slots. Its session
    takes one slot; the pytest it nests finds none (R=1: serial, no slot) or the second (R=2), and
    must neither wait nor fail for it. The harness prepares only the encoding: UTF-8 mode, so the
    child's Korean guard message reaches the test's text pipe intact on a non-UTF-8 host locale."""
    server = _TESTS_DIR.parent
    env = os.environ.copy()
    for key in (*policy.SLOT_ENV_KEYS, "FLOWGATE_REQUIRE_FULL_SUITE", "PYTEST_XDIST_WORKER",
                "PYTEST_XDIST_WORKER_COUNT", "PYTEST_ADDOPTS"):
        env.pop(key, None)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    pool = policy.slot_pool_env(tmp_path / "runtime", size)
    env.update(pool)
    started = time.monotonic()
    control, _ownership = executor.spawn([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rA",
                                          _GUARD_0382], server, env, 300, f"scr_0114_guard_{size}", cpu_limit=size)
    try:
        result = executor.wait(control, 300, threading.Event())
    finally:
        control.close()
    assert result.timed_out is False and time.monotonic() - started < 240     # nobody waited for a slot
    assert result.exit_code == 0, (result.stdout_tail[-3000:], result.stderr_tail[-2000:])
    assert "1 passed" in result.stdout_tail and "PASSED " + _GUARD_0382 in result.stdout_tail
    summary, environment = _summarize(result.stdout_tail, result.exit_code)
    assert (summary["parallel"]["slot_source"], summary["parallel"]["pool_size"]) == ("pool", size)
    assert environment["workers"]["mode"] == "serial" and environment["workers"]["cpu_limit"] == size
    pool_dir = Path(pool[policy.SLOT_DIR_ENV])
    assert all(_lockable(pool_dir, index) for index in range(size))         # every slot let go

"""flowgate.v02.0114 T#4: the run tree's processor limit (D 0006 3.5 (2), 8-12, 9.2-7 (c)(f)).

Windows (the FlowGate server host): ``executor.spawn(cpu_limit=R)`` gives the run's named Job an
affinity of R processors before the suspended target is assigned and resumed. Checked through
the operating system -- the Job's limit information, every member's affinity, the Job's CPU
accounting -- not through anything the tool reports. POSIX: the supervisor's own processor set
(narrowed before the target exists) and its widening check are exercised with the OS calls
replaced, since this host has no ``sched_setaffinity``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from modules.flow_gate.services import process_runner
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_supervisor as supervisor

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object limit")
_JOB_BASIC_ACCOUNTING = 1
_JOB_BASIC_LIMIT = 2


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD)]
    k.QueryInformationJobObject.restype = wintypes.BOOL
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                         ctypes.POINTER(ctypes.c_size_t)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return ctypes, wintypes, k


def _job_limit(handle) -> tuple[int, int]:
    """``(LimitFlags, Affinity)`` of a Job, read from the OS."""
    ctypes, wintypes, k = _kernel32()
    _c, _w, info_type = process_runner._JobBasicLimitInformation.types()
    info = info_type()
    assert k.QueryInformationJobObject(handle, _JOB_BASIC_LIMIT, ctypes.byref(info.BasicLimitInformation),
                                       ctypes.sizeof(info.BasicLimitInformation), None)
    return info.BasicLimitInformation.LimitFlags, info.BasicLimitInformation.Affinity


def _job_cpu_seconds(handle) -> float:
    ctypes, wintypes, k = _kernel32()

    class ACCOUNTING(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                    ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    info = ACCOUNTING()
    assert k.QueryInformationJobObject(handle, _JOB_BASIC_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None)
    return (info.TotalUserTime + info.TotalKernelTime) / 1e7


def _process_affinity(pid: int):
    ctypes, wintypes, k = _kernel32()
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        mine, system = ctypes.c_size_t(), ctypes.c_size_t()
        if not k.GetProcessAffinityMask(handle, ctypes.byref(mine), ctypes.byref(system)):
            return None
        return mine.value
    finally:
        k.CloseHandle(handle)


_BUSY_TREE = textwrap.dedent("""
    import ctypes, json, subprocess, sys, time
    out = sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[2] == "child":
        if "widen" in sys.argv:                      # one child tries to widen its own processor set
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.GetCurrentProcess.restype = ctypes.c_void_p
            mine, system = ctypes.c_size_t(), ctypes.c_size_t()
            k.GetProcessAffinityMask(ctypes.c_void_p(k.GetCurrentProcess()), ctypes.byref(mine), ctypes.byref(system))
            ok = k.SetProcessAffinityMask(ctypes.c_void_p(k.GetCurrentProcess()), ctypes.c_size_t(system.value))
            after = ctypes.c_size_t()
            k.GetProcessAffinityMask(ctypes.c_void_p(k.GetCurrentProcess()), ctypes.byref(after), ctypes.byref(system))
            open(out + ".widen", "w").write(json.dumps({"ok": bool(ok), "after": after.value, "system": system.value}))
        end = time.monotonic() + float(sys.argv[-1])
        while time.monotonic() < end:
            pass
        sys.exit(0)
    seconds = sys.argv[2]
    children = [subprocess.Popen([sys.executable, __file__, out, "child", seconds])
                for _ in range(7)]
    children.append(subprocess.Popen([sys.executable, __file__, out, "child", "widen", seconds]))
    open(out + ".ready", "w").write(",".join(str(c.pid) for c in children))
    sys.exit(max(c.wait() for c in children))
""")


@windows_only
def test_7f_an_unknown_tool_with_eight_busy_children_runs_on_its_two_processors_only(tmp_path):
    script, marker = tmp_path / "busy_tree.py", tmp_path / "busy"
    script.write_text(_BUSY_TREE, encoding="utf-8")
    started = time.monotonic()
    control, ownership = executor.spawn([sys.executable, str(script), str(marker), "3"], tmp_path,
                                        dict(os.environ), 60, "scr_0114_cpu_f", cpu_limit=2)
    try:
        assert ownership["process_owner_kind"] == "windows_job"
        flags, mask = _job_limit(control.owner.handle)
        assert flags & process_runner._JOB_OBJECT_LIMIT_AFFINITY and bin(mask).count("1") == 2
        assert mask == control.owner.affinity_mask == process_runner.job_affinity_mask(2)
        deadline = time.monotonic() + 20
        while not (tmp_path / "busy.ready").exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        time.sleep(0.5)
        members = control.owner.process_ids()
        assert members and len(members) >= 9                               # launcher + 8 children
        seen = {pid: _process_affinity(pid) for pid in members}
        assert all(value is None or (value & ~mask) == 0 for value in seen.values()), seen
        assert sum(value is not None for value in seen.values()) >= 8
        result = executor.wait(control, 60, threading.Event())
        wall = time.monotonic() - started
        cpu = _job_cpu_seconds(control.owner.handle)
    finally:
        control.close()
    assert (result.exit_code, result.timed_out, result.cancelled) == (0, False, False)   # not a failure
    ratio = cpu / wall
    print(f"cpu-limit 2: cpu={cpu:.2f}s wall={wall:.2f}s ratio={ratio:.2f}")
    assert ratio <= 2.0 + 0.25, (cpu, wall)                                # 8 busy loops, at most 2 at once
    widen = json.loads((tmp_path / "busy.widen").read_text())
    assert (widen["after"] & ~mask) == 0, widen                            # the Job outranks the process


@windows_only
def test_7f_a_limit_that_cannot_be_set_starts_nothing(tmp_path, monkeypatch):
    def broken(_limit):
        raise OSError("injected affinity failure")

    monkeypatch.setattr(process_runner, "job_affinity_mask", broken)
    with pytest.raises(executor.OwnershipError, match="selfcheck_process_owner_unavailable") as caught:
        executor.spawn([sys.executable, "-c", "print(1)"], tmp_path, dict(os.environ), 5, "scr_0114_cpu_x", cpu_limit=1)
    assert caught.value.created is False                                    # no tree without its limit
    monkeypatch.undo()
    with pytest.raises(executor.OwnershipError) as caught:
        executor.spawn([sys.executable, "-c", "print(1)"], tmp_path, dict(os.environ), 5, "scr_0114_cpu_y",
                       cpu_limit=len(policy.processor_ids()) + 1)
    assert caught.value.created is False


@windows_only
def test_7f_an_ai_run_job_and_a_run_without_a_limit_are_unchanged(tmp_path):
    owner = process_runner.WindowsProcessOwner("ai_0114")
    try:
        assert owner.active and owner.cpu_limit is None and owner.affinity_mask is None
        flags, mask = _job_limit(owner.handle)
        assert flags == process_runner._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and mask == 0
    finally:
        owner.close()
    control, _ownership = executor.spawn([sys.executable, "-c", "print('ready')"], tmp_path, dict(os.environ), 10,
                                         "scr_0114_cpu_none")
    try:
        assert _job_limit(control.owner.handle) == (process_runner._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, 0)
        assert executor.wait(control, 10, threading.Event()).exit_code == 0
    finally:
        control.close()


def _handle_count() -> int:
    ctypes, wintypes, k = _kernel32()
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    count = wintypes.DWORD()
    assert k.GetProcessHandleCount(k.GetCurrentProcess(), ctypes.byref(count))
    return count.value


@windows_only
def test_7f_a_limit_that_cannot_be_computed_leaves_no_job_handle_or_named_job(monkeypatch):
    """The mask is decided before the Job exists: a failing limit closes nothing because it
    opened nothing -- no handle of this process grows, and the same Job name is free again."""
    name = proof.job_name("scr_0114_cpu_leak")

    def broken(_limit):
        raise OSError("injected affinity failure")

    monkeypatch.setattr(process_runner, "job_affinity_mask", broken)
    for _ in range(3):                                                     # logging and ctypes warm up
        process_runner.WindowsProcessOwner("scr_0114_cpu_leak", job_name=name, cpu_limit=1).close()
    before = _handle_count()
    for _ in range(30):
        owner = process_runner.WindowsProcessOwner("scr_0114_cpu_leak", job_name=name, cpu_limit=1)
        assert not owner.active and owner.handle is None and owner.affinity_mask is None
        owner.close()
    grown = _handle_count() - before
    assert grown <= 3, grown                                               # a leak would be one per attempt
    monkeypatch.undo()
    again = process_runner.WindowsProcessOwner("scr_0114_cpu_leak", job_name=name, cpu_limit=1)
    try:
        assert again.active, "a Job of this name was left behind"          # ALREADY_EXISTS would refuse it
        assert _job_limit(again.handle)[1] == process_runner.job_affinity_mask(1)
    finally:
        again.close()


_QUERY_BY_NAME = textwrap.dedent("""
    import ctypes, sys
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenJobObjectW.restype = wintypes.HANDLE
    class BASIC(ctypes.Structure):
        _fields_ = [("a", ctypes.c_longlong), ("b", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("f", wintypes.DWORD), ("g", wintypes.DWORD)]
    handle = k.OpenJobObjectW(0x0004, False, sys.argv[1])
    info = BASIC()
    ok = handle and k.QueryInformationJobObject(handle, 2, ctypes.byref(info), ctypes.sizeof(info), None)
    print("%d %d" % (1 if ok else 0, bin(info.Affinity).count("1")))
""")


@windows_only
def test_7c_the_limit_belongs_to_the_named_job_so_another_server_process_reads_it(tmp_path):
    run_id = "scr_0114_cpu_restart"
    control, _ownership = executor.spawn([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path,
                                         dict(os.environ), 60, run_id, job_name=proof.job_name(run_id), cpu_limit=3)
    try:
        script = tmp_path / "query_job.py"
        script.write_text(_QUERY_BY_NAME, encoding="utf-8")
        done = subprocess.run([sys.executable, str(script), proof.job_name(run_id)], capture_output=True, text=True,
                              timeout=30)
        assert done.stdout.split() == ["1", "3"], (done.stdout, done.stderr)    # a fresh process sees R=3
        control.cancel()
        executor.wait(control, 10, threading.Event())
    finally:
        control.close()


# ── POSIX supervisor (OS calls replaced) ─────────────────────────────────────

class _Affinity:
    def __init__(self, cpus, others=None):
        self.cpus = set(cpus)
        self.others = dict(others or {})
        self.set_calls = []

    def get(self, pid):
        if pid == 0:
            return set(self.cpus)
        value = self.others.get(pid)
        if value is None:
            raise ProcessLookupError(pid)
        return set(value)

    def set(self, pid, cpus):
        self.set_calls.append((pid, set(cpus)))
        self.cpus = set(cpus)


def test_posix_supervisor_narrows_itself_before_the_target_or_fails(monkeypatch):
    fake = _Affinity(range(8))
    monkeypatch.setattr(os, "sched_getaffinity", fake.get, raising=False)
    monkeypatch.setattr(os, "sched_setaffinity", fake.set, raising=False)
    assert supervisor._limit(None) is None and fake.set_calls == []
    assert supervisor._limit(2) == frozenset({0, 1})
    assert fake.set_calls == [(0, {0, 1})]
    for bad in (0, 3 + 8, True, "2"):
        with pytest.raises(OSError):
            supervisor._limit(bad)
    stubborn = _Affinity(range(4))
    monkeypatch.setattr(os, "sched_getaffinity", stubborn.get, raising=False)
    monkeypatch.setattr(os, "sched_setaffinity", lambda pid, cpus: None, raising=False)
    with pytest.raises(OSError, match="not applied"):
        supervisor._limit(2)


def test_posix_supervisor_detects_a_widened_member_of_the_group_or_the_marker(monkeypatch):
    allowed = frozenset({0, 1})
    fake = _Affinity(allowed, {10: {0, 1}, 11: {1}, 12: {0, 1}})
    monkeypatch.setattr(os, "sched_getaffinity", fake.get, raising=False)
    monkeypatch.setattr(proof, "posix_group_members", lambda pgid, start: (proof.MATCH, (10, 11, 99), True))
    monkeypatch.setattr(proof, "marker_scan", lambda run_id: (proof.MATCH, (12,)))
    target = type("P", (), {"pid": 10})()
    assert supervisor._widened(target, "1", allowed, "scr_x") is False      # 99 vanished: skipped
    fake.others[12] = {0, 1, 2}                                             # a marker carrier widened
    assert supervisor._widened(target, "1", allowed, "scr_x") is True
    fake.others[12] = {0}
    fake.others[11] = {0, 1, 5}                                             # a group member widened
    assert supervisor._widened(target, "1", allowed, None) is True

def test_posix_supervisor_checks_every_thread_of_a_process_not_only_its_main_one(monkeypatch):
    """Linux keeps the processor set per thread (sched_setaffinity(2), NOTES): a process whose main
    thread stays on the allowed processors while another thread widened its own set is a violation."""
    allowed = frozenset({0, 1})
    fake = _Affinity(allowed, {100: {0, 1}, 101: {0, 1, 2, 3}, 102: {0}, 200: {1}})
    tasks = {100: ["100", "101", "102"]}                                   # 200 has no task listing
    real_listdir = os.listdir

    def listdir(path):
        text = str(path).replace("\\", "/")
        if text.startswith("/proc/") and text.endswith("/task"):
            pid = int(text.split("/")[2])
            if pid not in tasks:
                raise FileNotFoundError(text)
            return list(tasks[pid])
        return real_listdir(path)

    monkeypatch.setattr(os, "sched_getaffinity", fake.get, raising=False)
    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(proof, "posix_group_members", lambda pgid, start: (proof.MATCH, (100,), True))
    monkeypatch.setattr(proof, "marker_scan", lambda run_id: (proof.MATCH, (200,)))
    target = type("P", (), {"pid": 100})()
    assert supervisor._tasks(100) == [100, 101, 102] and supervisor._tasks(200) == [200]
    assert fake.get(100) <= allowed                                        # the main thread alone looks fine
    assert supervisor._widened(target, "1", allowed, "scr_x") is True      # thread 101 is not
    fake.others[101] = {1}
    assert supervisor._widened(target, "1", allowed, "scr_x") is False
    fake.others[200] = {0, 5}                                              # no listing: the PID itself
    assert supervisor._widened(target, "1", allowed, "scr_x") is True


_WIDEN_A_THREAD = textwrap.dedent("""
    import json, os, sys, threading, time
    wide = {int(c) for c in sys.argv[2].split(",")}
    def widen():
        os.sched_setaffinity(0, wide)                  # this thread only (Linux: per-thread set)
        time.sleep(30)
    thread = threading.Thread(target=widen, daemon=True)
    thread.start()
    time.sleep(0.5)
    open(sys.argv[1], "w").write(json.dumps({"main": sorted(os.sched_getaffinity(0)),
                                             "thread": sorted(os.sched_getaffinity(thread.native_id))}))
    time.sleep(30)
""")


@pytest.mark.skipif(not sys.platform.startswith("linux") or not hasattr(os, "sched_getaffinity")
                    or len(os.sched_getaffinity(0)) < 2, reason="real Linux per-thread affinity, 2+ processors")
def test_linux_a_helper_thread_that_widens_its_set_fails_the_run_while_the_main_thread_stays(tmp_path):
    """Real supervisor, real threads: only a helper thread widens its own processor set; the
    main thread keeps the run's one processor. The supervisor ends the tree as a violation."""
    script, report = tmp_path / "widen_thread.py", tmp_path / "sets.json"
    script.write_text(_WIDEN_A_THREAD, encoding="utf-8")
    wide = ",".join(str(c) for c in sorted(os.sched_getaffinity(0)))
    started = time.monotonic()
    control, ownership = executor.spawn([sys.executable, str(script), str(report), wide], tmp_path,
                                        dict(os.environ), 60, "scr_0114_cpu_thread", cpu_limit=1)
    try:
        result = executor.wait(control, 60, threading.Event())
    finally:
        control.close()
    assert ownership["process_owner_kind"] == "posix_supervisor"
    assert result.cpu_limit_violated is True and result.timed_out is False, result
    assert time.monotonic() - started < 25                                 # ended by the check, not the sleep
    if report.exists():                                                    # written before the check fired
        sets = json.loads(report.read_text())
        assert len(sets["main"]) == 1 and len(sets["thread"]) > 1, sets



def test_posix_spawn_hands_the_limit_to_the_supervisor_and_a_violation_reaches_the_result(monkeypatch, tmp_path):
    sent = {}

    class FakeProc:
        pid = 4242

        def __init__(self, *a, **k):
            self.stdin = self
            self.stdout = self
            self.returncode = None
            self.lines = [b'{"event": "started", "pid": 4243, "start_identity": "7"}\n',
                          b'{"event": "result", "exit_code": -9, "timed_out": false, "cancelled": false,'
                          b' "cpu_limit_violated": true, "stdout_tail": "", "stderr_tail": ""}\n']

        def write(self, data):
            sent.update(json.loads(data.decode("utf-8")))

        def flush(self):
            pass

        def readline(self):
            return self.lines.pop(0)

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

    class _PosixOs:                                    # only the executor's view of the platform
        name = "posix"

        def __getattr__(self, item):
            return getattr(os, item)

    monkeypatch.setattr(executor, "os", _PosixOs())
    monkeypatch.setattr(executor.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(executor, "start_identity", lambda pid: "1")
    control, ownership = executor.spawn(["pytest"], tmp_path, {"PATH": "x"}, 30, "scr_posix", cpu_limit=2)
    assert sent["cpu_limit"] == 2 and ownership["process_owner_kind"] == "posix_supervisor"
    result = executor.wait(control, 30, threading.Event())
    assert result.cpu_limit_violated is True and result.timed_out is False

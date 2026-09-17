"""Windows subprocess ownership regression coverage for group 0573."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from modules.flow_gate.services import process_runner


class _Owner:
    def __init__(self, active=True, terminated=True):
        self.active = active
        self.terminated = terminated
        self.calls = 0
        self.run_id = "run-a"

    def terminate(self):
        self.calls += 1
        return self.terminated


class _ExitedProc:
    pid = 123
    returncode = 0

    def poll(self):
        return self.returncode


def test_owned_cleanup_is_used_even_after_launcher_exited(monkeypatch):
    """The old early poll return is precisely the orphan-descendant bug."""
    proc = _ExitedProc()
    owner = _Owner()
    proc._flowgate_process_owner = owner
    monkeypatch.setattr(process_runner.os, "name", "nt")

    process_runner.kill_process_tree(proc)

    assert owner.calls == 1


def test_job_failure_falls_back_to_tree_kill(monkeypatch, caplog):
    proc = _ExitedProc()
    proc.returncode = None
    owner = _Owner(terminated=False)
    proc._flowgate_process_owner = owner
    monkeypatch.setattr(process_runner.os, "name", "nt")
    calls = []
    monkeypatch.setattr(process_runner.subprocess, "run",
                        lambda *a, **kw: calls.append(a[0]))

    process_runner.kill_process_tree(proc)

    assert calls == [["taskkill", "/F", "/T", "/PID", "123"]]
    assert "fallback_tree_kill" in caplog.text


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job Objects")
def test_real_windows_orphan_descendant_is_killed(tmp_path):
    """Parent exits; inherited pipe stays open in its child; Job cleanup converges."""
    pid_file = tmp_path / "descendant.pid"
    child_code = (
        "import os,time,pathlib;"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys;"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}])"
    )
    owner = process_runner.WindowsProcessOwner("windows-real-orphan")
    assert owner.active
    kwargs = process_runner.popen_kwargs(tmp_path, None)
    kwargs["creationflags"] = owner.creationflags(kwargs.get("creationflags", 0))
    proc = subprocess.Popen(
        subprocess.list2cmdline([sys.executable, "-c", parent_code]), **kwargs
    )
    assert owner.attach(proc)

    started = time.monotonic()
    process_runner.communicate_with_cleanup(
        proc, owner, timeout=15, drain_grace=0.2
    )
    owner.close()

    assert time.monotonic() - started < 10
    assert pid_file.is_file()
    descendant_pid = int(pid_file.read_text())
    time.sleep(0.2)
    import ctypes
    handle = ctypes.WinDLL("kernel32", use_last_error=True).OpenProcess(
        0x00100000, False, descendant_pid
    )
    if handle:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
    assert not handle


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job Objects")
def test_two_windows_jobs_are_isolated(tmp_path):
    owner_a = process_runner.WindowsProcessOwner("run-a")
    owner_b = process_runner.WindowsProcessOwner("run-b")
    kwargs_a = process_runner.popen_kwargs(tmp_path, None, include_stdio=False)
    kwargs_b = process_runner.popen_kwargs(tmp_path, None, include_stdio=False)
    kwargs_a["creationflags"] = owner_a.creationflags(kwargs_a.get("creationflags", 0))
    kwargs_b["creationflags"] = owner_b.creationflags(kwargs_b.get("creationflags", 0))
    command = f'"{sys.executable}" -c "import time;time.sleep(30)"'
    proc_a = subprocess.Popen(command, **kwargs_a)
    proc_b = subprocess.Popen(command, **kwargs_b)
    assert owner_a.attach(proc_a)
    assert owner_b.attach(proc_b)
    try:
        assert owner_a.terminate()
        proc_a.wait(timeout=5)
        assert proc_b.poll() is None
    finally:
        owner_a.close()
        owner_b.terminate()
        owner_b.close()
        proc_b.wait(timeout=5)

# Rejection rework: failure boundaries and actual Windows processes, without
# scratch files. These tests also run through unittest for a remote-source harness.
import ctypes
import logging
import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock


class TestOwnershipFailureBoundaries(unittest.TestCase):
    def fake_owner(self):
        with mock.patch.object(process_runner.WindowsProcessOwner, "_create"):
            owner = process_runner.WindowsProcessOwner("failure-boundary")
        owner.handle = 123
        owner._kernel32 = mock.Mock()
        return owner

    def test_close_failure_retains_handle_for_retry(self):
        owner = self.fake_owner()
        owner._kernel32.CloseHandle.side_effect = [0, 1]
        with self.assertLogs(process_runner.logger, level="WARNING") as logs:
            owner.close()
        self.assertTrue(owner.active)
        self.assertIn("job_close_failed", "\n".join(logs.output))
        owner.close()
        self.assertFalse(owner.active)

    def test_resume_failure_is_not_silently_left_suspended(self):
        owner = self.fake_owner()
        owner.creationflags()
        with mock.patch.object(owner, "_resume", side_effect=OSError("resume denied")):
            with self.assertRaisesRegex(RuntimeError, "unable to resume"):
                owner.attach(SimpleNamespace(_handle=456))
        self.assertFalse(owner._suspended)
        owner.close()

    def test_assignment_failure_resumes_fallback_and_logs(self):
        owner = self.fake_owner()
        owner._kernel32.AssignProcessToJobObject.return_value = 0
        owner.creationflags()
        with mock.patch.object(owner, "_resume") as resume:
            with self.assertLogs(process_runner.logger, level="WARNING") as logs:
                self.assertFalse(owner.attach(SimpleNamespace(_handle=456)))
        resume.assert_called_once()
        self.assertFalse(owner.active)
        self.assertIn("job_assign_failed", "\n".join(logs.output))
        self.assertIn("fallback_tree_kill", "\n".join(logs.output))

    def test_terminate_and_close_serialize_handle_use(self):
        owner = self.fake_owner()
        entered, proceed = threading.Event(), threading.Event()
        def terminate(*args):
            entered.set()
            self.assertTrue(proceed.wait(2))
            return 1
        owner._kernel32.TerminateJobObject.side_effect = terminate
        killer = threading.Thread(target=owner.terminate)
        closer = threading.Thread(target=owner.close)
        killer.start()
        try:
            self.assertTrue(entered.wait(2))
            closer.start()
            self.assertFalse(owner._kernel32.CloseHandle.called)
        finally:
            proceed.set()
            killer.join(3)
            if closer.ident is not None:
                closer.join(3)
        self.assertFalse(killer.is_alive())
        self.assertFalse(closer.is_alive())
        owner._kernel32.CloseHandle.assert_called_once_with(123)

    def test_timeout_recovery_reuses_single_communicator(self):
        release = threading.Event()
        proc = SimpleNamespace(args="fake", poll=lambda: None)
        proc.communicate = mock.Mock(side_effect=lambda **kw: (
            release.wait(3) and b"output", b""))
        owner = _Owner()
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                process_runner.communicate_with_cleanup(proc, owner, input=b"prompt", timeout=0.05)
            release.set()
            self.assertEqual(process_runner.communicate_with_cleanup(
                proc, owner, timeout=2), (b"output", b""))
            proc.communicate.assert_called_once_with(input=b"prompt")
        finally:
            release.set()
            proc._flowgate_drain_state[2].join(3)

    @unittest.skipUnless(os.name == "nt", "Windows fallback drain")
    def test_no_job_still_bounds_parent_exit_pipe_wait(self):
        release = threading.Event()
        proc = SimpleNamespace(args="fake", poll=lambda: 0)
        proc.communicate = mock.Mock(side_effect=lambda **kw: (
            release.wait(3) and b"tail", b""))
        try:
            with mock.patch.object(process_runner, "kill_process_tree",
                                   side_effect=lambda p: release.set()) as kill:
                out = process_runner.communicate_with_cleanup(
                    proc, _Owner(active=False), timeout=2, drain_grace=0.01)
            self.assertEqual(out, (b"tail", b""))
            kill.assert_called_once_with(proc)
        finally:
            release.set()
            proc._flowgate_drain_state[2].join(3)

    def test_posix_communication_contract(self):
        proc = mock.Mock()
        proc.communicate.return_value = (b"normal", b"")
        with mock.patch.object(process_runner.os, "name", "posix"):
            self.assertEqual(process_runner.communicate_with_cleanup(
                proc, _Owner(active=False), input=b"prompt", timeout=7), (b"normal", b""))
        proc.communicate.assert_called_once_with(input=b"prompt", timeout=7)

    def test_cli_setup_failures_always_close_owner(self):
        from modules.flow_gate.services.ai_invoke import provider_cli as cli
        for failure in ("kwargs", "spawn", "attach", "watchdog", "remaining", "stop"):
            with self.subTest(failure=failure), ExitStack() as stack:
                owner = mock.Mock(active=True)
                proc = mock.Mock()
                proc.poll.return_value = None
                proc.returncode = 0
                run = dict(scratch_dir=str(Path.cwd()), run_id="failure-boundary",
                           raw_token="test", cancel_event=threading.Event())
                svc = SimpleNamespace(FAST_FAIL_WINDOW_SEC=10)
                stack.enter_context(mock.patch.object(cli, "_svc", return_value=svc))
                stack.enter_context(mock.patch.object(cli, "_canonicalize_cli_prompt", return_value=("p", "")))
                stack.enter_context(mock.patch.object(cli.ai_settings_service, "normalize_cli_command", return_value="fake"))
                stack.enter_context(mock.patch.object(cli, "_resolve_cli_launch", return_value=(
                    dict(effective_command="fake", agent_cwd=str(Path.cwd()), spawn_cwd=str(Path.cwd())), "valid")))
                stack.enter_context(mock.patch.object(cli, "_audit_cli_launch"))
                stack.enter_context(mock.patch.object(process_runner, "WindowsProcessOwner", return_value=owner))
                kwargs = stack.enter_context(mock.patch.object(process_runner, "popen_kwargs", return_value={}))
                spawn = stack.enter_context(mock.patch.object(subprocess, "Popen", return_value=proc))
                watchdog = stack.enter_context(mock.patch.object(cli, "_start_progress_watchdog", return_value=(None, None)))
                remaining = stack.enter_context(mock.patch.object(cli, "_absolute_remaining_sec", return_value=10))
                stop = stack.enter_context(mock.patch.object(cli, "_stop_progress_watchdog"))
                stack.enter_context(mock.patch.object(process_runner, "communicate_with_cleanup", return_value=(b"", b"")))
                kill = stack.enter_context(mock.patch.object(process_runner, "kill_process_tree"))
                target = dict(kwargs=kwargs, spawn=spawn, attach=owner.attach,
                              watchdog=watchdog, remaining=remaining, stop=stop)[failure]
                target.side_effect = RuntimeError("injected")
                if failure == "spawn":
                    self.assertEqual(cli._cli_execute(dict(cli_command="fake"), "p", run)[0], "spawn_failed")
                else:
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        cli._cli_execute(dict(cli_command="fake"), "p", run)
                owner.close.assert_called_once()
                self.assertIsNone(run.get("proc"))
                if failure not in ("kwargs", "spawn"):
                    kill.assert_called_once_with(proc)
                    proc.wait.assert_called_once_with(timeout=5)


@unittest.skipUnless(os.name == "nt", "requires actual Windows Job Objects")
class TestRealWindowsOwnershipRework(unittest.TestCase):
    def launch(self, code, run_id):
        owner = process_runner.WindowsProcessOwner(run_id)
        self.assertTrue(owner.active)
        proc = None
        try:
            proc = subprocess.Popen(
                [sys.executable, "-B", "-c", code],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=owner.creationflags(subprocess.CREATE_NEW_PROCESS_GROUP))
            self.assertTrue(owner.attach(proc))
        except BaseException:
            owner.terminate()
            owner.close()
            if proc is not None:
                proc.wait(timeout=5)
            raise
        self.addCleanup(self.reap, proc, owner)
        return proc, owner

    def reap(self, proc, owner):
        process_runner.kill_process_tree(proc)
        owner.close()
        proc.wait(timeout=5)
        state = getattr(proc, "_flowgate_drain_state", None)
        if state:
            state[2].join(5)
            self.assertFalse(state[2].is_alive())
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def assert_pid_exited(self, pid):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
            finally:
                kernel.CloseHandle(handle)
        else:
            self.assertEqual(ctypes.get_last_error(), 87)

    def test_orphan_pipe_cleanup_and_output_preservation(self):
        child = "import os,time;print(os.getpid(),flush=True);time.sleep(30)"
        parent = "import subprocess,sys;subprocess.Popen([sys.executable,'-B','-c'," + repr(child) + "])"
        proc, owner = self.launch(parent, "real-orphan")
        started = time.monotonic()
        stdout, stderr = process_runner.communicate_with_cleanup(
            proc, owner, timeout=10, drain_grace=0.2)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(stderr, b"")
        self.assert_pid_exited(int(stdout.strip()))

    def test_timeout_recovery_real_process(self):
        proc, owner = self.launch("import time;print('ready',flush=True);time.sleep(30)", "real-timeout")
        with self.assertRaises(subprocess.TimeoutExpired):
            process_runner.communicate_with_cleanup(proc, owner, timeout=0.5)
        process_runner.kill_process_tree(proc)
        stdout, stderr = process_runner.communicate_with_cleanup(proc, owner, timeout=5)
        self.assertIn(b"ready", stdout)
        self.assertFalse(proc._flowgate_drain_state[2].is_alive())

    def test_normal_completion_and_independent_run_isolation(self):
        other, other_owner = self.launch("import time;time.sleep(30)", "real-other")
        proc, owner = self.launch("print('finished')", "real-normal")
        self.assertEqual(process_runner.communicate_with_cleanup(
            proc, owner, timeout=5)[0].strip(), b"finished")
        process_runner.kill_process_tree(proc)
        owner.close()
        self.assertIsNone(other.poll())

    def test_cancel_kills_live_descendant(self):
        child = "import os,time;print(os.getpid(),flush=True);time.sleep(30)"
        parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-B','-c'," + repr(child) + "]);time.sleep(30)"
        proc, owner = self.launch(parent, "real-cancel")
        # Start the single communicator and wait a bounded startup window.
        with self.assertRaises(subprocess.TimeoutExpired):
            process_runner.communicate_with_cleanup(proc, owner, timeout=0.5)
        process_runner.kill_process_tree(proc)
        stdout, _ = process_runner.communicate_with_cleanup(proc, owner, timeout=5)
        self.assert_pid_exited(int(stdout.strip()))

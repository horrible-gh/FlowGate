"""flowgate.default.0579 T0004 -- subprocess liveness observation.

NR0003 found that the no-progress watchdog (0446 T0014) only ever compares document
max-seq and `git status` paths, so a worker running a long `pytest`/build/lint subprocess
with neither signal moving looks dead in the UI for the whole run even though child
processes are actively being created and reaped underneath it. This adds a THIRD, weaker
signal read from the SAME ownership primitives the process already uses to kill its tree
(`WindowsProcessOwner`'s Job Object on Windows, the `start_new_session` process group on
Linux) and keeps it out of the stall-timeout decision entirely (T0004 contract items 1-3
and 7): it only ever writes `last_activity_*`, never `last_progress_*` /
`stall_anchor_mono`.

Covers, in the order T0004 §10.1/§2/§3/§10.4 ask for them:
  * `process_tree_snapshot` dispatch -- owner present delegates to it; owner absent
    dispatches to the Linux `/proc` reader only on Linux; anything else is None (§4).
  * The Linux `/proc` + `os.getpgid()` reader in isolation: matching/non-matching pgid,
    non-numeric `/proc` entries, a PID that disappears mid-enumeration (race, §3), and
    `/proc` itself being unreadable.
  * `WindowsProcessOwner.process_ids()`: empty/one/many PID lists, the variable-length
    buffer resize-and-retry path, a query failure, an inactive owner, and the same
    `_handle_lock` serialization discipline `terminate()`/`close()` already use (§2).
  * Two real-process integration cases, one per platform, built the same way: a real
    launcher with a real descendant under it, then the descendant ALONE is killed and
    reaped while the launcher keeps running. "descendant exits -> snapshot loses it"
    (§10.3 / §10.4 item 4) has to be provable without tearing the ownership boundary
    down, so the group/Job teardown is a separate assertion that runs afterwards. The
    Linux case additionally pins an unrelated process outside the group and checks the
    snapshot's PGID against the same `os.killpg(proc.pid, ...)` boundary
    `kill_process_tree` uses.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import process_runner  # noqa: E402

PY = sys.executable


# ── process_tree_snapshot dispatch (§1/§4) ──────────────────────────────────────

class _FakeOwner:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def process_ids(self):
        self.calls += 1
        return self.result


class TestProcessTreeSnapshotDispatch:
    def test_an_attached_owner_is_asked_directly(self):
        proc = SimpleNamespace(pid=999)
        owner = _FakeOwner(frozenset({1, 2}))
        proc._flowgate_process_owner = owner

        assert process_runner.process_tree_snapshot(proc) == frozenset({1, 2})
        assert owner.calls == 1

    def test_no_owner_and_not_linux_is_unavailable(self, monkeypatch):
        # Windows runs whose Job Object never attached, and every other POSIX (macOS/BSD),
        # have no ownership primitive to read at all -- T0004 §4 forbids adding one
        # (WMI/tasklist/psutil) rather than reporting the gap honestly.
        monkeypatch.setattr(process_runner.sys, "platform", "darwin")
        proc = SimpleNamespace(pid=999)

        assert process_runner.process_tree_snapshot(proc) is None

    def test_no_owner_on_linux_reads_the_proc_group(self, monkeypatch):
        monkeypatch.setattr(process_runner.sys, "platform", "linux")
        seen = {}

        def fake(pgid):
            seen["pgid"] = pgid
            return frozenset({111})
        monkeypatch.setattr(process_runner, "_linux_process_group_snapshot", fake)
        proc = SimpleNamespace(pid=4242)

        assert process_runner.process_tree_snapshot(proc) == frozenset({111})
        assert seen["pgid"] == 4242


# ── Linux /proc + os.getpgid() reader in isolation (§3) ──────────────────────────

class TestLinuxProcessGroupSnapshot:
    def test_only_matching_pgid_members_are_collected(self, monkeypatch):
        monkeypatch.setattr(process_runner.os, "listdir", lambda p: ["10", "11", "notpid", "12"])
        pgids = {10: 500, 11: 999, 12: 500}
        monkeypatch.setattr(process_runner.os, "getpgid", lambda pid: pgids[pid], raising=False)

        assert process_runner._linux_process_group_snapshot(500) == frozenset({10, 12})

    def test_non_numeric_proc_entries_are_never_queried(self, monkeypatch):
        monkeypatch.setattr(process_runner.os, "listdir", lambda p: ["self", "cpuinfo", "7"])
        queried = []

        def getpgid(pid):
            queried.append(pid)
            return 7
        monkeypatch.setattr(process_runner.os, "getpgid", getpgid, raising=False)

        assert process_runner._linux_process_group_snapshot(7) == frozenset({7})
        assert queried == [7]

    def test_a_pid_that_exits_mid_enumeration_is_skipped_not_fatal(self, monkeypatch):
        # §3 race handling: ProcessLookupError/PermissionError/OSError on ONE pid must not
        # abort the whole snapshot -- the remaining PIDs still get collected.
        monkeypatch.setattr(process_runner.os, "listdir", lambda p: ["10", "11", "12"])

        def getpgid(pid):
            if pid == 10:
                raise ProcessLookupError()
            if pid == 11:
                raise PermissionError()
            return 5
        monkeypatch.setattr(process_runner.os, "getpgid", getpgid, raising=False)

        assert process_runner._linux_process_group_snapshot(5) == frozenset({12})

    def test_unreadable_proc_is_unavailable_not_empty(self, monkeypatch):
        def boom(p):
            raise OSError("no /proc on this platform")
        monkeypatch.setattr(process_runner.os, "listdir", boom)

        assert process_runner._linux_process_group_snapshot(1) is None


# ── WindowsProcessOwner.process_ids() (§2) ───────────────────────────────────────

def _fake_owner():
    with mock.patch.object(process_runner.WindowsProcessOwner, "_create"):
        owner = process_runner.WindowsProcessOwner("liveness-test")
    owner.handle = 123
    owner._kernel32 = mock.Mock()
    return owner


class TestWindowsProcessIdsUnit:
    def test_inactive_owner_returns_none(self):
        with mock.patch.object(process_runner.WindowsProcessOwner, "_create"):
            owner = process_runner.WindowsProcessOwner("liveness-inactive")
        assert owner.handle is None
        assert owner.process_ids() is None

    @pytest.mark.skipif(os.name != "nt", reason="ctypes.wintypes only exists on Windows")
    def test_empty_pid_list(self):
        owner = _fake_owner()

        def query(handle, info_class, buf_ref, size, ret_len):
            struct = buf_ref._obj if hasattr(buf_ref, "_obj") else buf_ref
            struct.NumberOfAssignedProcesses = 0
            struct.NumberOfProcessIdsInList = 0
            return 1
        owner._kernel32.QueryInformationJobObject.side_effect = query

        assert owner.process_ids() == frozenset()

    @pytest.mark.skipif(os.name != "nt", reason="ctypes.wintypes only exists on Windows")
    def test_several_pids_within_the_first_buffer(self):
        owner = _fake_owner()
        pids = [111, 222, 333]

        def query(handle, info_class, buf_ref, size, ret_len):
            struct = buf_ref._obj if hasattr(buf_ref, "_obj") else buf_ref
            struct.NumberOfAssignedProcesses = len(pids)
            struct.NumberOfProcessIdsInList = len(pids)
            for i, pid in enumerate(pids):
                struct.ProcessIdList[i] = pid
            return 1
        owner._kernel32.QueryInformationJobObject.side_effect = query

        assert owner.process_ids() == frozenset(pids)

    @pytest.mark.skipif(os.name != "nt", reason="ctypes.wintypes only exists on Windows")
    def test_buffer_too_small_returns_false_with_more_data_and_is_retried(self):
        # T0004 §2: the real Win32 shape for a too-small variable-length buffer is
        # QueryInformationJobObject returning FALSE with GetLastError() == ERROR_MORE_DATA
        # (not a truthy return with a silently truncated list) -- NumberOfAssignedProcesses
        # is still filled in with the true count, and the call must be retried with a
        # buffer that size rather than raised as a hard failure.
        import ctypes

        owner = _fake_owner()
        real_pids = list(range(1000, 1080))          # 80 > the initial guess of 64
        attempts = []

        def query(handle, info_class, buf_ref, size, ret_len):
            struct = buf_ref._obj if hasattr(buf_ref, "_obj") else buf_ref
            capacity = len(struct.ProcessIdList)
            attempts.append(capacity)
            struct.NumberOfAssignedProcesses = len(real_pids)
            if capacity < len(real_pids):
                struct.NumberOfProcessIdsInList = 0
                ctypes.set_last_error(234)  # ERROR_MORE_DATA
                return 0
            struct.NumberOfProcessIdsInList = len(real_pids)
            for i, pid in enumerate(real_pids):
                struct.ProcessIdList[i] = pid
            return 1
        owner._kernel32.QueryInformationJobObject.side_effect = query

        assert owner.process_ids() == frozenset(real_pids)
        assert len(attempts) == 2
        assert attempts[0] == 64
        assert attempts[1] == len(real_pids)

    @pytest.mark.skipif(os.name != "nt", reason="ctypes.wintypes only exists on Windows")
    def test_query_failure_is_none_not_an_exception(self, caplog):
        import ctypes

        owner = _fake_owner()

        def query(*a):
            ctypes.set_last_error(6)  # ERROR_INVALID_HANDLE -- not a buffer problem
            return 0
        owner._kernel32.QueryInformationJobObject.side_effect = query

        assert owner.process_ids() is None
        assert "job_query_pid_list_failed" in caplog.text

    @pytest.mark.skipif(os.name != "nt", reason="ctypes.wintypes only exists on Windows")
    def test_more_data_without_a_larger_assigned_count_is_not_retried_forever(self):
        # Defends against a buggy/lying driver that reports ERROR_MORE_DATA without ever
        # growing NumberOfAssignedProcesses past the current buffer -- must fail closed
        # (None) rather than loop or raise past process_ids()'s own exception handling.
        import ctypes

        owner = _fake_owner()

        def query(handle, info_class, buf_ref, size, ret_len):
            struct = buf_ref._obj if hasattr(buf_ref, "_obj") else buf_ref
            struct.NumberOfAssignedProcesses = 1
            struct.NumberOfProcessIdsInList = 0
            ctypes.set_last_error(234)  # ERROR_MORE_DATA
            return 0
        owner._kernel32.QueryInformationJobObject.side_effect = query

        assert owner.process_ids() is None

    @pytest.mark.skipif(os.name != "nt", reason="ctypes.wintypes only exists on Windows")
    def test_query_and_close_serialize_on_the_handle_lock(self):
        owner = _fake_owner()
        entered, proceed = threading.Event(), threading.Event()

        def query(handle, info_class, buf_ref, size, ret_len):
            entered.set()
            assert proceed.wait(2)
            struct = buf_ref._obj if hasattr(buf_ref, "_obj") else buf_ref
            struct.NumberOfAssignedProcesses = 0
            struct.NumberOfProcessIdsInList = 0
            return 1
        owner._kernel32.QueryInformationJobObject.side_effect = query
        reader = threading.Thread(target=owner.process_ids)
        closer = threading.Thread(target=owner.close)
        reader.start()
        try:
            assert entered.wait(2)
            closer.start()
            assert not owner._kernel32.CloseHandle.called
        finally:
            proceed.set()
            reader.join(3)
            closer.join(3)
        assert not reader.is_alive()
        assert not closer.is_alive()
        owner._kernel32.CloseHandle.assert_called_once_with(123)

    @pytest.mark.skipif(os.name != "nt", reason="ctypes.wintypes only exists on Windows")
    def test_query_and_terminate_serialize_on_the_handle_lock(self):
        owner = _fake_owner()
        entered, proceed = threading.Event(), threading.Event()

        def query(handle, info_class, buf_ref, size, ret_len):
            entered.set()
            assert proceed.wait(2)
            struct = buf_ref._obj if hasattr(buf_ref, "_obj") else buf_ref
            struct.NumberOfAssignedProcesses = 0
            struct.NumberOfProcessIdsInList = 0
            return 1
        owner._kernel32.QueryInformationJobObject.side_effect = query
        reader = threading.Thread(target=owner.process_ids)
        terminator = threading.Thread(target=owner.terminate)
        reader.start()
        try:
            assert entered.wait(2)
            terminator.start()
            assert not owner._kernel32.TerminateJobObject.called
        finally:
            proceed.set()
            reader.join(3)
            terminator.join(3)
        assert not reader.is_alive()
        assert not terminator.is_alive()
        owner._kernel32.TerminateJobObject.assert_called_once_with(123, 1)


# ── Real Windows Job Object integration (§10.3) ──────────────────────────────────

@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job Objects")
class TestRealWindowsProcessIdsIntegration:
    def test_descendant_pid_is_visible_then_disappears_on_exit(self, tmp_path):
        pid_file = tmp_path / "grandchild.pid"
        grandchild = (
            "import os,time,pathlib;"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
            "time.sleep(20)"
        )
        parent = (
            "import subprocess,sys,time;"
            f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}]);"
            # wait() reaps the grandchild and `del` drops the last handle to it, so the
            # Job Object's PID list loses it the moment IT exits -- the launcher itself
            # keeps running for the rest of the sleep.
            "p.wait();"
            "del p;"
            "time.sleep(20)"
        )
        owner = process_runner.WindowsProcessOwner("liveness-real")
        assert owner.active
        proc = None
        try:
            kwargs = process_runner.popen_kwargs(tmp_path, None)
            kwargs["creationflags"] = owner.creationflags(kwargs.get("creationflags", 0))
            proc = subprocess.Popen(
                subprocess.list2cmdline([sys.executable, "-c", parent]), **kwargs
            )
            assert owner.attach(proc)

            deadline = time.monotonic() + 10
            grandchild_pid = None
            while time.monotonic() < deadline:
                if pid_file.is_file():
                    grandchild_pid = int(pid_file.read_text())
                    break
                snapshot = owner.process_ids()
                assert snapshot is not None
                time.sleep(0.2)
            assert grandchild_pid is not None

            deadline = time.monotonic() + 10
            seen_grandchild = False
            while time.monotonic() < deadline:
                snapshot = owner.process_ids()
                assert snapshot is not None
                assert proc.pid in snapshot
                if grandchild_pid in snapshot:
                    seen_grandchild = True
                    break
                time.sleep(0.2)
            assert seen_grandchild

            # T0004 10.3 "descendant exits -> removed from snapshot", proved on its own:
            # kill ONLY the grandchild and leave the launcher running, so the removal is
            # caused by that process's own exit and not by the Job Object being torn down.
            os.kill(grandchild_pid, signal.SIGTERM)
            deadline = time.monotonic() + 15
            grandchild_gone = False
            while time.monotonic() < deadline:
                snapshot = owner.process_ids()
                assert snapshot is not None
                if grandchild_pid not in snapshot:
                    grandchild_gone = True
                    break
                time.sleep(0.2)
            assert grandchild_gone
            # ... and the launcher is untouched: still running, still in the snapshot.
            assert proc.poll() is None
            survivors = owner.process_ids()
            assert survivors is not None
            assert proc.pid in survivors
            assert grandchild_pid not in survivors

            # Only now tear the whole Job Object down, as a separate assertion.
            process_runner.kill_process_tree(proc)
            proc.wait(timeout=10)
            deadline = time.monotonic() + 10
            torn_down = False
            while time.monotonic() < deadline:
                final = owner.process_ids()
                if final is None or not ({proc.pid, grandchild_pid} & final):
                    torn_down = True
                    break
                time.sleep(0.2)
            assert torn_down
        finally:
            if proc is not None:
                process_runner.kill_process_tree(proc)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
            owner.close()


# ── Real Linux process-group integration (§10.4 / completion criterion 12) ──────

@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux process groups")
class TestRealLinuxProcessGroupIntegration:
    def test_launcher_child_unrelated_and_exit_against_the_killpg_boundary(self, tmp_path):
        pid_file = tmp_path / "child.pid"
        child_script = tmp_path / "child.py"
        launcher_script = tmp_path / "launcher.py"
        child_script.write_text(
            "import os, pathlib, sys, time\n"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
            "time.sleep(20)\n"
        )
        launcher_script.write_text(
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
            # wait() reaps the child the moment it dies. Without it the killed child
            # would linger as a zombie, which still has a /proc/<pid> entry and still
            # answers getpgid() -- the snapshot would never lose it and item 4 could
            # not be proved.
            "p.wait()\n"
            "time.sleep(20)\n"
        )
        launcher_cmd = " ".join(
            shlex.quote(part)
            for part in (PY, str(launcher_script), str(child_script), str(pid_file))
        )

        unrelated = subprocess.Popen(
            [PY, "-c", "import time; time.sleep(20)"], start_new_session=True,
        )
        proc = None
        try:
            kwargs = process_runner.popen_kwargs(tmp_path, None, include_stdio=False)
            proc = subprocess.Popen(launcher_cmd, **kwargs)

            # launcher PID is immediately visible, and is its own PGID (start_new_session).
            deadline = time.monotonic() + 10
            snapshot = None
            while time.monotonic() < deadline:
                snapshot = process_runner.process_tree_snapshot(proc)
                if snapshot is not None and proc.pid in snapshot:
                    break
                time.sleep(0.2)
            assert snapshot is not None
            assert proc.pid in snapshot
            assert os.getpgid(proc.pid) == proc.pid

            # the child (spawned by the launcher, no session change) shares that PGID.
            deadline = time.monotonic() + 10
            child_pid = None
            while time.monotonic() < deadline:
                if pid_file.is_file():
                    child_pid = int(pid_file.read_text())
                    break
                time.sleep(0.2)
            assert child_pid is not None
            assert os.getpgid(child_pid) == proc.pid

            deadline = time.monotonic() + 10
            seen_child = False
            while time.monotonic() < deadline:
                snapshot = process_runner.process_tree_snapshot(proc)
                assert snapshot is not None
                assert proc.pid in snapshot
                assert unrelated.pid not in snapshot
                if child_pid in snapshot:
                    seen_child = True
                    break
                time.sleep(0.2)
            assert seen_child

            # the same boundary read directly, independent of process_tree_snapshot dispatch.
            direct = process_runner._linux_process_group_snapshot(proc.pid)
            assert direct is not None
            assert {proc.pid, child_pid}.issubset(direct)
            assert unrelated.pid not in direct

            # T0004 10.4 item 4 "child exits -> removed from snapshot", proved on its own:
            # kill ONLY the child. The launcher keeps running and reaps it, so the snapshot
            # loses exactly that one PID while the launcher stays in it. The group teardown
            # below is a separate assertion and must not be what proves this one.
            os.kill(child_pid, signal.SIGKILL)
            deadline = time.monotonic() + 15
            child_gone = False
            while time.monotonic() < deadline:
                mid = process_runner._linux_process_group_snapshot(proc.pid)
                if mid is not None and child_pid not in mid:
                    child_gone = True
                    break
                time.sleep(0.2)
            assert child_gone
            assert proc.poll() is None
            survivors = process_runner.process_tree_snapshot(proc)
            assert survivors is not None
            assert proc.pid in survivors
            assert child_pid not in survivors
            assert unrelated.pid not in survivors

            # Only now the group teardown: kill_process_tree uses os.killpg(proc.pid, ...),
            # the exact boundary read above, and stops there -- unrelated survives.
            process_runner.kill_process_tree(proc)
            proc.wait(timeout=10)
            deadline = time.monotonic() + 10
            group_gone = False
            while time.monotonic() < deadline:
                final = process_runner._linux_process_group_snapshot(proc.pid)
                if final is not None and proc.pid not in final:
                    group_gone = True
                    break
                time.sleep(0.2)
            assert group_gone
            assert unrelated.poll() is None
        finally:
            if proc is not None:
                process_runner.kill_process_tree(proc)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            unrelated.kill()
            try:
                unrelated.wait(timeout=5)
            except Exception:
                pass

"""Process ownership for advisory TR self-checks."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from modules.flow_gate.services.process_runner import WindowsProcessOwner

_log = logging.getLogger(__name__)
_JOB_TERMINATE_ATTEMPTS = 3


class OwnershipError(RuntimeError):
    """A failed spawn. ``ownership`` is what was learnt about the run's processes before the
    failure (spawn's keys). The failure proves no process absent -- a supervisor or target
    may already exist -- so the caller proves the tree from this and the run's stored
    ``owner_marker`` before it ends the run or frees anything (0080 T#1).

    ``created=False`` only when spawn knows no process was created (the owner could not be
    set up, the pre-spawn record failed, or Popen itself raised OSError)."""

    def __init__(self, *args, ownership: dict | None = None, created: bool = True):
        super().__init__(*args)
        self.ownership = dict(ownership or {})
        self.created = created


@dataclass
class ExecutionResult:
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    stdout_tail: str
    stderr_tail: str


# Four-way process judgement (flowgate.v02.0080 T#1, NR0007 §5-0 ①). start_identity's None
# mixes "the OS says there is no such process" with "the lookup failed"; releasing a run on
# the second is exactly the bug, so the Self-check proof path judges with identity_state.
MATCH = "match"         # the recorded process is alive (same PID, same start marker)
ABSENT = "absent"       # the OS reports the process gone
MISMATCH = "mismatch"   # the PID now names another process: the recorded one is gone
UNKNOWN = "unknown"     # lookup failed, access denied, no marker to compare: proves nothing

_PRESENT, _EXITED, _GONE, _UNREADABLE = "present", "exited", "absent", "unknown"
_ERROR_INVALID_PARAMETER = 87   # OpenProcess on a PID no process holds
_STILL_ACTIVE = 259


def _proc_usable() -> bool:
    try:
        return Path("/proc/self/stat").is_file()
    except OSError:
        return False


def probe_start_identity(pid: int) -> tuple[str, str | None]:
    """``(status, start marker)``; status is present / exited / absent / unknown.

    POSIX: only a missing ``/proc/<pid>`` on a working ``/proc`` is absent; permission and
    other read errors and an unparsable stat are unknown. Windows: only OpenProcess failing
    with ERROR_INVALID_PARAMETER is absent; access denied, other errors, a GetProcessTimes
    failure and any exception are unknown. ``exited`` is a Windows process object that is
    still open somewhere but has finished (it runs no code any more).
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return _UNREADABLE, None
    if pid <= 0:
        return _UNREADABLE, None
    if os.name != "nt":
        if not _proc_usable():
            return _UNREADABLE, None
        try:
            # Linux stat field 22; comm may contain parentheses and spaces.
            stat = Path(f"/proc/{pid}/stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            return _GONE, None
        except OSError:
            return _UNREADABLE, None
        try:
            return _PRESENT, stat.rsplit(")", 1)[1].split()[19]
        except IndexError:
            return _UNREADABLE, None
    try:
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER:
                return _GONE, None
            return _UNREADABLE, None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return _UNREADABLE, None
            identity = f"{created.dwHighDateTime:08x}{created.dwLowDateTime:08x}"
            code = wintypes.DWORD()
            # A failed exit-code read stays "present": it never turns a live process into gone.
            if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
                return _EXITED, identity
            return _PRESENT, identity
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return _UNREADABLE, None


def start_identity(pid: int) -> str | None:
    """Return an OS start marker so PID reuse never grants kill authority.

    None means absent OR unreadable; callers that release anything use identity_state.
    """
    return probe_start_identity(pid)[1]


def identity_state(pid: int | None, expected: str | None) -> str:
    """MATCH / ABSENT / MISMATCH / UNKNOWN for a recorded ``(pid, start marker)``."""
    if not pid:
        return UNKNOWN
    status, current = probe_start_identity(pid)
    if status == _GONE:
        return ABSENT
    if status == _UNREADABLE or current is None or not expected:
        return UNKNOWN
    if current != expected:
        return MISMATCH
    return ABSENT if status == _EXITED else MATCH


def _read_tail(pipe, target: bytearray) -> None:
    while True:
        data = pipe.read(8192)
        if not data:
            break
        target.extend(data)
        if len(target) > 65536:
            del target[:-65536]


class ProcessControl:
    def __init__(self, proc: subprocess.Popen, owner: WindowsProcessOwner | None = None):
        self.proc = proc
        self.owner = owner
        self._lock = threading.Lock()

    def cancel(self) -> bool:
        """Deliver the stop. False = it could not be delivered (0080 T#2: reported to the
        canceller); the worker's tree proof still decides whether the run may end."""
        with self._lock:
            if self.owner:
                # The Job, not the direct child: a target that already exited may have left
                # descendants in it (0080 T#1).
                return self._terminate_job()
            if self.proc.poll() is not None:
                # The supervisor is gone; it already cleared the group (its finally) or the
                # run's tree proof catches what it left.
                return True
            if self.proc.stdin:
                try:
                    self.proc.stdin.write(b"cancel\n")
                    self.proc.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    return self.proc.poll() is not None
                return True
            return False

    def _terminate_job(self) -> bool:
        """TerminateJobObject, retried (NR0007 §5 item 3-c). If it keeps failing, the direct
        child is killed through its own handle -- still open, so its PID cannot have been
        reused -- and False reports the failure: Job members other than the direct child
        may survive, and the run's tree proof then keeps the run held."""
        if not self.owner.active:
            return self.proc.poll() is not None     # closed: the worker is past its wait
        for attempt in range(_JOB_TERMINATE_ATTEMPTS):
            if self.owner.terminate():
                return True
            if attempt + 1 < _JOB_TERMINATE_ATTEMPTS:
                time.sleep(0.2)
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                _log.warning("selfcheck %s: direct child kill failed", self.owner.run_id, exc_info=True)
        _log.error("selfcheck %s: job terminate failed %s times; direct child killed, the rest of the Job unconfirmed",
                   self.owner.run_id, _JOB_TERMINATE_ATTEMPTS)
        return False

    def close(self) -> None:
        if self.owner:
            self.owner.close()
        if self.proc.stdin:
            self.proc.stdin.close()


def spawn(argv: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int,
          run_id: str, *, job_name: str | None = None,
          record: Callable[[str, dict], None] | None = None) -> tuple[ProcessControl, dict]:
    """``job_name`` (Windows): create the run's Job under that name so a later server can open
    it and prove it empty (Self-check, 0080 T#1). A name already in use fails the spawn.

    ``record(stage, ownership)`` (Self-check, 0080 T#3) persists what is known before it is
    needed: ``"spawning"`` right before the first Popen, ``"spawned"`` right after each Popen
    -- on Windows before the Job assignment. A failing record fails the spawn.

    On failure the OwnershipError carries the ownership learnt so far (process records are
    taken right after each Popen, before anything can reap the process)."""
    if os.name == "nt":
        owner = WindowsProcessOwner(run_id, job_name=job_name)
        if not owner.active:
            raise OwnershipError("selfcheck_process_owner_unavailable", created=False)
        proc = None
        ownership = {"process_owner_kind": "windows_job"}
        try:
            _record(record, "spawning", ownership)
        except Exception as exc:
            owner.close()
            raise OwnershipError("selfcheck_process_owner_unavailable", created=False) from exc
        try:
            try:
                proc = subprocess.Popen(argv, cwd=cwd, env=env, shell=False, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        creationflags=owner.creationflags())
            except OSError as exc:
                owner.close()
                raise OwnershipError("selfcheck_process_owner_unavailable", created=False) from exc
            ownership.update(target_pid=proc.pid, target_start_identity=start_identity(proc.pid))
            # Suspended and not yet in the Job: an empty Job proves nothing for it.
            _record(record, "spawned", ownership)
            if not owner._kernel32.AssignProcessToJobObject(owner.handle, proc._handle):
                raise OwnershipError("selfcheck_process_owner_unavailable")
            proc._flowgate_process_owner = owner
            owner._resume(proc)
            owner._suspended = False
            return ProcessControl(proc, owner), ownership
        except Exception as exc:
            if isinstance(exc, OwnershipError) and not exc.created:
                raise                     # Popen raised: no process, the owner is closed
            _windows_unwind(proc, owner)
            raise OwnershipError("selfcheck_process_owner_unavailable", ownership=ownership) from exc
    module = "modules.flow_gate.services.tr_self_check_supervisor"
    ownership = {"process_owner_kind": "posix_supervisor"}
    try:
        _record(record, "spawning", ownership)
    except Exception as exc:
        raise OwnershipError("selfcheck_process_owner_unavailable", created=False) from exc
    try:
        proc = subprocess.Popen([sys.executable, "-m", module], shell=False,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                cwd=Path(__file__).resolve().parents[3], env={**env, "PYTHONPATH": str(Path(__file__).resolve().parents[3])})
    except OSError as exc:
        raise OwnershipError("selfcheck_process_owner_unavailable", created=False) from exc
    ownership.update(supervisor_pid=proc.pid, supervisor_start_identity=start_identity(proc.pid))
    try:
        _record(record, "spawned", ownership)
        proc.stdin.write((json.dumps({"argv": argv, "cwd": str(cwd), "env": env,
                                      "timeout_seconds": timeout_seconds}) + "\n").encode("utf-8"))
        proc.stdin.flush()
        started = json.loads(proc.stdout.readline())
        if started.get("event") != "started":
            raise OwnershipError("selfcheck_process_owner_unavailable")
        # The supervisor read the target's identity before it could reap it; a read here
        # could meet a reused PID.
        ownership.update(target_pid=started["pid"], target_start_identity=started.get("start_identity"))
        _record(record, "spawned", ownership)
        return ProcessControl(proc), ownership
    except Exception as exc:
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            pass   # the supervisor still runs: the caller's tree proof sees it and holds the run
        raise OwnershipError("selfcheck_process_owner_unavailable", ownership=ownership) from exc


def _record(record: Callable[[str, dict], None] | None, stage: str, ownership: dict) -> None:
    if record is not None:
        record(stage, dict(ownership))


def _windows_unwind(proc, owner: WindowsProcessOwner) -> None:
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                owner.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass   # still running: the caller's tree proof sees it and holds the run
    owner.close()


def wait(control: ProcessControl, timeout_seconds: int, cancelled: threading.Event) -> ExecutionResult:
    proc = control.proc
    if control.owner:
        out, err = bytearray(), bytearray()
        threads = [threading.Thread(target=_read_tail, args=(pipe, dest), daemon=True)
                   for pipe, dest in ((proc.stdout, out), (proc.stderr, err))]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            control.cancel()
            proc.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
        return ExecutionResult(proc.returncode, timed_out, cancelled.is_set(),
                               out.decode("utf-8", "ignore"), err.decode("utf-8", "ignore"))
    try:
        result = json.loads(proc.stdout.readline())
        proc.wait(timeout=timeout_seconds + 10)
        if result.get("event") != "result":
            raise OwnershipError("selfcheck_process_owner_unavailable")
        return ExecutionResult(result.get("exit_code"), bool(result.get("timed_out")),
                               bool(result.get("cancelled")) or cancelled.is_set(),
                               result.get("stdout_tail", ""), result.get("stderr_tail", ""))
    finally:
        if proc.poll() is None:
            control.cancel()
            proc.wait(timeout=5)

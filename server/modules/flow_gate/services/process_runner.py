"""Subprocess execution primitives for the AI-invoke engine
(flowgate.default.0187 L0006 §2.5).

Collects the pieces that are easy to get subtly wrong when spawning and
supervising a child process: process-group creation (the precondition for tree
kill), full process-tree termination, timeout-then-kill with partial-output
recovery, and output tail capture.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Windows Job Object constants.  Kept here (rather than in provider orchestration)
# so every lifecycle action uses the same ownership primitive.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_CREATE_SUSPENDED = 0x00000004
_PROCESS_DRAIN_GRACE_SEC = 3.0


class _JobBasicLimitInformation:
    """Factory namespace for the ctypes declarations, imported only on Windows."""

    @staticmethod
    def types():
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]
        return ctypes, wintypes, JOBOBJECT_EXTENDED_LIMIT_INFORMATION


class WindowsProcessOwner:
    """Own one Windows subprocess attempt and all descendants via a Job Object.

    The child is created suspended, assigned before it can spawn descendants, then
    resumed.  If Job setup is unavailable the process is launched normally and the
    existing taskkill fallback remains available.
    """

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id
        self._handle_lock = threading.RLock()
        self.handle = None
        self._kernel32 = None
        self._suspended = False
        if os.name == "nt":
            self._create()

    @property
    def active(self) -> bool:
        return self.handle is not None

    def _log_failure(self, event: str) -> None:
        logger.warning("ai-invoke %s: %s", self.run_id, event, exc_info=True)

    def _create(self) -> None:
        try:
            ctypes, wintypes, info_type = _JobBasicLimitInformation.types()
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
            ]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            info = info_type()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info)
            ):
                error = ctypes.WinError(ctypes.get_last_error())
                if not kernel32.CloseHandle(handle):
                    self._log_failure("job_close_failed")
                raise error
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
            kernel32.ResumeThread.restype = wintypes.DWORD
            self._kernel32 = kernel32
            self.handle = handle
        except Exception:
            self.handle = None
            self._kernel32 = None
            self._log_failure("job_create_failed")
            logger.warning("ai-invoke %s: fallback_tree_kill", self.run_id)

    def creationflags(self, existing: int = 0) -> int:
        self._suspended = self.active
        return existing | (_CREATE_SUSPENDED if self._suspended else 0)

    def _resume(self, proc: subprocess.Popen) -> None:
        """Resume CREATE_SUSPENDED without relying on private Popen thread handles."""
        import ctypes
        from ctypes import wintypes

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        self._kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self._kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self._kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self._kernel32.Thread32First.restype = wintypes.BOOL
        self._kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self._kernel32.Thread32Next.restype = wintypes.BOOL
        self._kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenThread.restype = wintypes.HANDLE
        snapshot = self._kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            found = self._kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == proc.pid:
                    thread = self._kernel32.OpenThread(0x0002, False, entry.th32ThreadID)
                    if thread:
                        try:
                            if self._kernel32.ResumeThread(thread) == 0xFFFFFFFF:
                                raise ctypes.WinError(ctypes.get_last_error())
                        finally:
                            self._kernel32.CloseHandle(thread)
                        return
                found = self._kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError("suspended process thread not found")
        finally:
            self._kernel32.CloseHandle(snapshot)

    def attach(self, proc: subprocess.Popen) -> bool:
        if not self.active:
            return False
        try:
            import ctypes
            if not self._kernel32.AssignProcessToJobObject(self.handle, proc._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            proc._flowgate_process_owner = self
            return True
        except Exception:
            self._log_failure("job_assign_failed")
            logger.warning("ai-invoke %s: fallback_tree_kill", self.run_id)
            self.close()
            return False
        finally:
            if self._suspended:
                try:
                    self._resume(proc)
                except Exception:
                    logger.warning("ai-invoke %s: process_resume_failed", self.run_id,
                                   exc_info=True)
                    # Never leave a CREATE_SUSPENDED child waiting for the watchdog.
                    # The caller's finally owns termination and handle cleanup.
                    raise RuntimeError("unable to resume CLI process") from None
                finally:
                    self._suspended = False

    def terminate(self) -> bool:
        with self._handle_lock:
            if not self.active:
                return False
            try:
                if not self._kernel32.TerminateJobObject(self.handle, 1):
                    import ctypes
                    raise ctypes.WinError(ctypes.get_last_error())
                return True
            except Exception:
                self._log_failure("job_terminate_failed")
                return False

    def close(self) -> None:
        with self._handle_lock:
            if not self.active:
                return
            handle = self.handle
            try:
                if not self._kernel32.CloseHandle(handle):
                    import ctypes
                    raise ctypes.WinError(ctypes.get_last_error())
            except Exception:
                self._log_failure("job_close_failed")
            else:
                self.handle = None


def communicate_with_cleanup(
    proc: subprocess.Popen,
    owner: WindowsProcessOwner,
    *,
    input=None,
    timeout: Optional[float] = None,
    drain_grace: float = _PROCESS_DRAIN_GRACE_SEC,
):
    """communicate(), but bound inherited-pipe EOF after the tracked process exits."""
    if os.name != "nt" and not owner.active:
        return proc.communicate(input=input, timeout=timeout)

    # One communicator per Popen, including timeout/error recovery. Calling
    # communicate again while its first invocation still owns stdin/readers races
    # pipe closure and loses output. Reuse the same completion state instead.
    state = getattr(proc, "_flowgate_drain_state", None)
    if state is None:
        result: dict = {}
        done = threading.Event()

        def _communicate() -> None:
            try:
                result["value"] = proc.communicate(input=input)
            except BaseException as exc:
                result["error"] = exc
            finally:
                done.set()

        thread = threading.Thread(target=_communicate, name="flowgate-process-drain", daemon=True)
        state = (result, done, thread)
        proc._flowgate_drain_state = state
        try:
            thread.start()
        except BaseException:
            del proc._flowgate_drain_state
            raise
    result, done, thread = state
    started = time.monotonic()
    exited_at = None
    while not done.wait(0.05):
        now = time.monotonic()
        if timeout is not None and now - started >= timeout:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        if proc.poll() is not None:
            exited_at = exited_at or now
            if now - exited_at >= drain_grace:
                logger.warning(
                    "ai-invoke %s: pipe drain exceeded %ss after launcher exit; "
                    "terminating owned descendants", owner.run_id, drain_grace,
                )
                kill_process_tree(proc)
                if not done.wait(5):
                    raise subprocess.TimeoutExpired(proc.args, timeout)
                break
        else:
            exited_at = None
    if "error" in result:
        raise result["error"]
    return result.get("value", (None, None))


def popen_kwargs(
    root: Path,
    env: Optional[dict[str, str]],
    *,
    include_stdio: bool = True,
) -> dict:
    """Popen kwargs that guarantee the child owns a fresh process group.

    CREATE_NEW_PROCESS_GROUP (nt) / start_new_session (posix) is what makes
    kill_process_tree able to reap grandchildren (node/agent workers) instead
    of only the shell.
    """
    kwargs = {
        "cwd": str(root),
        "shell": True,
    }
    if include_stdio:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update(env)
        kwargs["env"] = merged_env
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Force-terminate proc and every descendant using its ownership boundary."""
    owner = getattr(proc, "_flowgate_process_owner", None)
    if owner is not None and owner.active:
        if owner.terminate():
            return
        logger.warning("ai-invoke %s: fallback_tree_kill", owner.run_id)
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        except Exception:
            logger.warning("taskkill failed for process %s", proc.pid, exc_info=True)
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                logger.warning("process kill failed for %s", proc.pid, exc_info=True)
        return

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        logger.warning("process group kill failed for %s", proc.pid, exc_info=True)
        try:
            proc.kill()
        except Exception:
            logger.warning("process kill failed for %s", proc.pid, exc_info=True)


def _decode_candidates() -> tuple[str, ...]:
    """Encodings to try, in order, for child-process output on this host.

    On Windows the console codepage comes FIRST (0277 B0001 -> NR0003 §5 S1). cmd.exe and
    most native tools emit the ANSI/OEM codepage (cp949, cp932, cp1252 ...), and a short
    run of those bytes is often accidentally valid UTF-8 — trying UTF-8 first therefore
    succeeds with mojibake rather than failing over. os.device_encoding(1) returns None
    when stdout is redirected (which it is, whenever the server runs as a service), so
    'mbcs' — the process ANSI codepage — is the reliable fallback.
    """
    if os.name == "nt":
        return (os.device_encoding(1) or "mbcs", "utf-8")
    return ("utf-8", os.device_encoding(1) or "mbcs")


def safe_decode(data) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    for enc in _decode_candidates():
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def read_tail(path: Path, chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-chars:]


def unc_safe_shell(cmd: str, root: Path) -> tuple[str, Optional[str]]:
    r"""Make (cmd, cwd) safe to hand to a ``shell=True`` child on Windows.

    cmd.exe refuses a UNC path (``\\host\share\...``) as its current directory:
    it prints a localized "UNC paths are not supported" message and
    silently resets CWD to ``C:\Windows``, breaking every relative path the
    command relies on (0285 B0001 -> NR0004). When *root* is a UNC path we
    therefore do NOT pass it to cmd.exe as ``cwd``; instead we prefix ``pushd``,
    which maps the share to a temporary drive letter and cd's into it. The temp
    mapping is released automatically when the ``cmd /c`` session exits, so no
    trailing ``popd`` is needed — and because ``&&`` leaves the command last on
    the line its exit code is preserved. Returns ``(effective_cmd, effective_cwd)``;
    ``effective_cwd`` is None on the UNC path so Popen inherits the server's own
    (local) working directory before pushd relocates.

    POSIX shells and local / mapped-drive roots are returned unchanged.
    """
    root_str = str(root)
    if os.name == "nt" and root_str.startswith("\\\\"):
        return f'pushd "{root_str}" && {cmd}', None
    return cmd, root_str


def run_command(
    cmd: str,
    root: Path,
    timeout: int,
    env: Optional[dict[str, str]],
) -> tuple[bool, Optional[int], str]:
    """Run cmd to completion with a hard timeout.

    Returns (timed_out, exit_code, combined_output). On timeout the whole
    process tree is killed and any partial output is recovered — the exact
    mechanics test_run_service._run_shell_command has always used.
    """
    eff_cmd, eff_cwd = unc_safe_shell(cmd, root)
    kwargs = {
        "shell": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    kwargs.update(popen_kwargs(root, env, include_stdio=False))
    kwargs["cwd"] = eff_cwd

    proc = subprocess.Popen(eff_cmd, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout = getattr(exc, "output", None)
            stderr = getattr(exc, "stderr", None)
        output = safe_decode(stdout) + safe_decode(stderr)
        return True, None, output

    output = safe_decode(stdout) + safe_decode(stderr)
    return False, proc.returncode, output

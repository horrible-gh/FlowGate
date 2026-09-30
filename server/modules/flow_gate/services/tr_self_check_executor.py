"""Process ownership for advisory TR self-checks."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from modules.flow_gate.services.process_runner import WindowsProcessOwner


class OwnershipError(RuntimeError):
    pass


@dataclass
class ExecutionResult:
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    stdout_tail: str
    stderr_tail: str


def start_identity(pid: int) -> str | None:
    """Return an OS start marker so PID reuse never grants kill authority."""
    if os.name != "nt":
        try:
            # Linux stat field 22; comm may contain parentheses and spaces.
            stat = Path(f"/proc/{pid}/stat").read_text()
            return stat.rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            return None
    try:
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            return f"{created.dwHighDateTime:08x}{created.dwLowDateTime:08x}"
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return None


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

    def cancel(self) -> None:
        with self._lock:
            if self.proc.poll() is not None:
                return
            if self.owner:
                self.owner.terminate()
            elif self.proc.stdin:
                try:
                    self.proc.stdin.write(b"cancel\n")
                    self.proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass

    def close(self) -> None:
        if self.owner:
            self.owner.close()
        if self.proc.stdin:
            self.proc.stdin.close()


def spawn(argv: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int,
          run_id: str) -> tuple[ProcessControl, dict]:
    if os.name == "nt":
        owner = WindowsProcessOwner(run_id)
        if not owner.active:
            raise OwnershipError("selfcheck_process_owner_unavailable")
        proc = None
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, shell=False, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=owner.creationflags())
            if not owner._kernel32.AssignProcessToJobObject(owner.handle, proc._handle):
                raise OwnershipError("selfcheck_process_owner_unavailable")
            proc._flowgate_process_owner = owner
            owner._resume(proc)
            owner._suspended = False
            return ProcessControl(proc, owner), {"process_owner_kind": "windows_job",
                "target_pid": proc.pid, "target_start_identity": start_identity(proc.pid)}
        except Exception as exc:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    owner.terminate()
                    proc.wait(timeout=5)
            owner.close()
            raise OwnershipError("selfcheck_process_owner_unavailable") from exc
    module = "modules.flow_gate.services.tr_self_check_supervisor"
    proc = subprocess.Popen([sys.executable, "-m", module], shell=False,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd=Path(__file__).resolve().parents[3], env={**env, "PYTHONPATH": str(Path(__file__).resolve().parents[3])})
    try:
        proc.stdin.write((json.dumps({"argv": argv, "cwd": str(cwd), "env": env,
                                      "timeout_seconds": timeout_seconds}) + "\n").encode("utf-8"))
        proc.stdin.flush()
        started = json.loads(proc.stdout.readline())
        if started.get("event") != "started":
            raise OwnershipError("selfcheck_process_owner_unavailable")
        return ProcessControl(proc), {"process_owner_kind": "posix_supervisor",
            "target_pid": started["pid"], "target_start_identity": start_identity(started["pid"]),
            "supervisor_pid": proc.pid, "supervisor_start_identity": start_identity(proc.pid)}
    except Exception as exc:
        proc.stdin.close()
        proc.wait(timeout=5)
        raise OwnershipError("selfcheck_process_owner_unavailable") from exc


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
            control.owner.terminate()
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

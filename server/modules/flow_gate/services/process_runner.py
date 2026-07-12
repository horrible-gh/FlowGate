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
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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
    """Force-terminate proc and every descendant (Windows taskkill /F /T,
    POSIX killpg SIGKILL). Safe to call on an already-exited process."""
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


def safe_decode(data) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    for enc in ("utf-8", os.device_encoding(1) or "mbcs"):
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
    kwargs = {
        "cwd": str(root),
        "shell": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    kwargs.update(popen_kwargs(root, env, include_stdio=False))

    proc = subprocess.Popen(cmd, **kwargs)
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

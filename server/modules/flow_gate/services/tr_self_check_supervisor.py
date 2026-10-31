"""POSIX self-check process owner.  Stdin EOF means the server disappeared."""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time

from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_proof as proof

LIMIT = 65536
EMPTY, OWNED, UNPROVEN = "empty", "owned", "unproven"


def _tail(pipe, output: bytearray) -> None:
    while True:
        chunk = pipe.read(8192)
        if not chunk:
            return
        output.extend(chunk)
        if len(output) > LIMIT:
            del output[:-LIMIT]


def _leader_exited(proc: subprocess.Popen) -> bool:
    """Has the target (the group leader) exited? Asked WITHOUT reaping it (0080 T#1): while
    the leader is unreaped its PID -- and so the group's number -- cannot be reused, so a
    group found under that number is still the one the target's setsid made."""
    if proc.returncode is not None:
        return True
    if hasattr(os, "waitid"):
        try:
            return os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            pass
    return proc.poll() is not None   # reaps: _group() treats the group as unproven from here


def _group(proc: subprocess.Popen, leader_start: str | None) -> str:
    """EMPTY: no live member is left. OWNED: every member is proved this run's by the shared
    rule (proof.posix_group_members: every /proc entry read, session = pgid, started no
    earlier than the recorded leader, the leader PID still the leader). UNPROVEN: anything
    else -- including a reaped leader, whose PID may already name another process's group."""
    if proc.returncode is not None:
        return UNPROVEN
    state, members, owned = proof.posix_group_members(proc.pid, leader_start)
    if state not in (proof.MATCH, proof.ABSENT) or not owned:
        return UNPROVEN
    exited = _leader_exited(proc)
    if proc.returncode is not None:
        return UNPROVEN
    live = [pid for pid in members if pid != proc.pid or not exited]
    return OWNED if live else EMPTY


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def _signal_leader(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)   # unreaped (checked by the caller): the PID is still the target's
    except OSError:
        pass


def _terminate(proc: subprocess.Popen, leader_start: str | None) -> None:
    """Stop the target's whole process group (pgid = target PID, start_new_session), then
    reap the target.

    The direct child exiting first is not the end: its descendants keep the group (and
    possibly the worktree) alive, so the group decides, never poll() alone. Every signal is
    preceded by the ownership check (:func:`_group`); a group that is not proved this run's
    gets no group signal -- only the unreaped target itself is signalled -- and what is left
    keeps the service's tree proof MATCH/UNKNOWN, so the run stays protected. A target
    already reaped gets nothing at all: its number may name someone else's group now.
    """
    if proc.returncode is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        state = _group(proc, leader_start)
        if state == OWNED:
            _signal_group(proc.pid, sig)
            done = lambda: _group(proc, leader_start) != OWNED  # noqa: E731
        elif state == UNPROVEN and proc.returncode is None and not _leader_exited(proc):
            _signal_leader(proc.pid, sig)
            done = lambda: _leader_exited(proc)  # noqa: E731
        else:
            break
        deadline = time.monotonic() + 3
        while not done() and time.monotonic() < deadline:
            time.sleep(.05)
    proc.wait()


def _limit(cpu_limit) -> frozenset | None:
    """flowgate.v02.0114 T#4 (D 0006 3.5 (2)): narrow this supervisor to the lowest
    ``cpu_limit`` processors it may use before the target exists; the target and its
    descendants inherit the set. None = no limit. Raises when it cannot be set exactly."""
    if cpu_limit is None:
        return None
    current = sorted(os.sched_getaffinity(0))
    if not isinstance(cpu_limit, int) or isinstance(cpu_limit, bool) or not 1 <= cpu_limit <= len(current):
        raise OSError(f"cpu limit {cpu_limit!r} cannot be set on {len(current)} processors")
    allowed = frozenset(current[:cpu_limit])
    os.sched_setaffinity(0, allowed)
    if frozenset(os.sched_getaffinity(0)) != allowed:
        raise OSError("cpu limit not applied")
    return allowed


def _tasks(pid: int) -> list[int]:
    """Every thread of ``pid``. Linux keeps the processor set per thread (sched_setaffinity(2),
    NOTES), and ``sched_getaffinity(pid)`` answers for the main thread only, so each task under
    /proc/<pid>/task is checked. Without that listing (process gone, no /proc) the PID alone."""
    try:
        tids = [int(name) for name in os.listdir(f"/proc/{pid}/task") if name.isdigit()]
    except OSError:
        tids = []
    return tids or [pid]


def _widened(proc: subprocess.Popen, leader_start: str | None, allowed: frozenset, run_id: str | None) -> bool:
    """A thread of a process of the run -- the target's group or a carrier of the run marker --
    runs on a processor outside ``allowed`` (POSIX lets a thread widen its own set). A thread
    gone or unreadable meanwhile is skipped: the residual limit of the POSIX ownership."""
    _state, members, _owned = proof.posix_group_members(proc.pid, leader_start)
    pids = set(members)
    if run_id:
        pids.update(proof.marker_scan(run_id)[1])
    for pid in pids:
        for tid in _tasks(pid):
            try:
                if not frozenset(os.sched_getaffinity(tid)) <= allowed:
                    return True
            except OSError:
                continue
    return False


def main() -> None:
    payload = json.loads(sys.stdin.buffer.readline())
    try:
        allowed = _limit(payload.get("cpu_limit"))
    except Exception:
        # No tree without its limit: the server reads anything but "started" as an owner failure.
        sys.stdout.write(json.dumps({"event": "limit_failed"}) + "\n")
        sys.stdout.flush()
        return
    run_id = (payload.get("env") or {}).get(proof.MARKER_ENV)
    proc = subprocess.Popen(payload["argv"], shell=False, cwd=payload["cwd"], env=payload["env"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    # Read before anything can reap the target: the identity every later signal is checked
    # against, and the one the server records (its own read could meet a reused PID).
    leader_start = executor.start_identity(proc.pid)
    out, err = bytearray(), bytearray()
    threads = [threading.Thread(target=_tail, args=(pipe, dest), daemon=True)
               for pipe, dest in ((proc.stdout, out), (proc.stderr, err))]
    for thread in threads:
        thread.start()
    sys.stdout.write(json.dumps({"event": "started", "pid": proc.pid, "start_identity": leader_start}) + "\n")
    sys.stdout.flush()
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.buffer, selectors.EVENT_READ)
    deadline = time.monotonic() + payload["timeout_seconds"]
    reason = None
    try:
        while not _leader_exited(proc):
            if time.monotonic() >= deadline:
                reason = "timeout"
                break
            if allowed is not None and _widened(proc, leader_start, allowed, run_id):
                reason = "cpu_limit"
                break
            events = selector.select(min(.2, max(0, deadline - time.monotonic())))
            if events:
                command = sys.stdin.buffer.readline()
                reason = "cancel" if command.strip() == b"cancel" else "server_lost"
                break
    finally:
        selector.close()
        # Whatever ended the run (exit, cancel, timeout, server loss, an error here), the
        # group's leftovers go before the result: they may hold the pipes and the worktree.
        # The target is reaped only inside _terminate, after the group is handled.
        try:
            _terminate(proc, leader_start)
        except Exception:
            pass
        for thread in threads:
            thread.join(timeout=5)
        result = {"event": "result", "exit_code": proc.returncode,
                  "timed_out": reason == "timeout", "cancelled": reason == "cancel",
                  "cpu_limit_violated": reason == "cpu_limit",
                  "stdout_tail": bytes(out).decode("utf-8", "ignore"),
                  "stderr_tail": bytes(err).decode("utf-8", "ignore")}
        try:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            pass


if __name__ == "__main__":
    main()

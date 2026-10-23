"""Release proofs for a Self-check run (flowgate.v02.0080 T#1, NR0007 §5-0).

Ending a run (failed/cancelled), clearing its ``active_key`` or deleting its Group's G is
allowed only when BOTH hold, in this order:

1. the server that owns the run is proved dead (:func:`owner_server_state`), so it can
   spawn nothing more, and then
2. the run's whole process tree -- target, supervisor and everything in the target's
   process group (POSIX) or the run's named Job (Windows) -- is proved empty
   (:func:`tree_state`, :func:`terminate_tree`).

Every judgement is four-way (``MATCH``/``ABSENT``/``MISMATCH``/``UNKNOWN``, see
``tr_self_check_executor.identity_state``). ``UNKNOWN`` -- a failed lookup, missing
ownership records, another node, a namespace we cannot see -- never releases anything.
An instance's DEAD state, ``os_process_alive() == False``, ``status='dead'`` and heartbeat
expiry are hints only: nothing here reads them.

Shared by ``recover()`` (T#1), the G reclaim gate and cancel (T#2) and the reaper and
manual re-verification (T#3), so the rule exists once: :func:`release_verdict`.

Run marker scan (Linux): adopted. Each run's environment carries ``MARKER_ENV=<run_id>``
and the tree proof also requires that no process carries it, which covers a run that died
between spawn and ``mark_running`` (no PID recorded yet) and descendants that left the
group with ``setsid``/``setpgid``. Residual limit: a descendant that also drops or rewrites
its environment, or that runs as another user (its environ is unreadable and skipped), is
invisible to the scan; on non-Linux POSIX the scan is UNKNOWN, so such runs stay protected.
"""
from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from modules.flow_gate.services import tr_self_check_executor as executor

MATCH, ABSENT, MISMATCH, UNKNOWN = executor.MATCH, executor.ABSENT, executor.MISMATCH, executor.UNKNOWN
GONE = frozenset((ABSENT, MISMATCH))

# Reason codes, written to tr_self_check_runs.recovery_reason when a run stays protected.
OWNERSHIP_UNKNOWN = "ownership_unknown"          # no owning server recorded for the run
OWNER_SERVER_UNPROVEN = "owner_server_unproven"  # owner recorded, its death not proved
TREE_UNPROVEN = "tree_unproven"                  # owner dead, process tree not proved empty
OWNER_ALIVE = "owner_alive"

RELEASE, KEEP, PROTECT = "release", "keep", "protect"

MARKER_ENV = "FG_SELFCHECK_RUN_ID"   # not FLOWGATE_*: child_env strips that prefix on inherit
_JOB_PREFIX = "Local\\flowgate-selfcheck-"
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_TERMINATE = 0x0008
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
_ERROR_FILE_NOT_FOUND = 2
_ERROR_MORE_DATA = 234


@dataclass(frozen=True)
class Proof:
    """One judgement. ``state`` is MATCH/ABSENT/MISMATCH/UNKNOWN; for a tree MATCH means
    "a member remains" and ABSENT "proved empty". ``reason`` is set when it proves nothing."""
    state: str
    reason: Optional[str] = None
    detail: dict = field(default_factory=dict)

    @property
    def gone(self) -> bool:
        return self.state in GONE


@dataclass(frozen=True)
class Verdict:
    """RELEASE: end the run and free its G. KEEP: touch nothing (owner alive or unproven).
    PROTECT: owner dead but the tree is not proved empty -- protect G, run incomplete."""
    decision: str
    reason: Optional[str]
    server: Proof
    tree: Optional[Proof] = None


# ── run-level ownership records (written before spawn) ───────────────────────

def job_name(run_id: str) -> str:
    return _JOB_PREFIX + run_id


def windows_session_id() -> Optional[int]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.ProcessIdToSessionId.restype = wintypes.BOOL
        session = wintypes.DWORD()
        if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            return None
        return int(session.value)
    except Exception:
        return None


def owner_marker(run_id: str) -> str:
    """The run-unit ownership record stored in ``tr_self_check_runs.owner_marker``.

    Windows: the run's Job name and this process's session (``Local\\`` is per session; a
    server in another session could not see the Job and must not read that as empty).
    POSIX: the environment marker every process of the run inherits.
    """
    if os.name == "nt":
        return json.dumps({"v": 1, "kind": "windows_job", "job": job_name(run_id),
                           "session": windows_session_id()})
    return json.dumps({"v": 1, "kind": "posix_marker", "env": MARKER_ENV})


def _marker(row: dict) -> dict:
    try:
        value = json.loads(row.get("owner_marker") or "null")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def owner_instance_of(row: dict) -> Optional[str]:
    """The server instance that runs this run's worker.

    Rows written before migration 158 carry no owner; their own G row names the instance
    that took it in ``_start`` -- but only a G row that predates the run, because recovery's
    ``selfcheck_ensure_protected`` inserts one under the *recovering* instance.
    """
    if row.get("owner_instance_id"):
        return row["owner_instance_id"]
    try:
        from modules.flow_gate.db import git_concurrency as gdb
        from modules.flow_gate.services.git import lock_manager
        lock = gdb.get_resource_lock(lock_manager.lock_key("G", row["project_id"], row["group_id"]))
        if not lock or lock.get("holder_ctx_id") != lock_manager.selfcheck_ctx_id(row["self_check_run_id"]):
            return None
        if datetime.fromisoformat(lock["acquired_at"]) > datetime.fromisoformat(row["created_at"]):
            return None
        return lock.get("instance_id") or None
    except Exception:
        return None


# ── ③ owning server ──────────────────────────────────────────────────────────

def owner_server_state(instance_id: Optional[str]) -> Proof:
    """Four-way judgement of the server instance that owns a run.

    MATCH = alive (this process, or its PID still carries its start marker): leave its
    runs alone. ABSENT/MISMATCH on this node = proved dead. UNKNOWN = no owner recorded,
    lookup failure, no instance row, another node (no OS view of it), unreadable PID.
    The row's ``status`` (alive/stopped/dead) is not evidence and is not read.
    """
    if not instance_id:
        return Proof(UNKNOWN, OWNERSHIP_UNKNOWN)
    try:
        from modules.flow_gate.db import git_concurrency as gdb
        from modules.flow_gate.services.git import instance_registry as registry
        if instance_id == registry.current_instance_id():
            return Proof(MATCH, None, {"instance_id": instance_id, "self": True})
        row = gdb.get_instance(instance_id)
        node = registry.node_key()
    except Exception:
        return Proof(UNKNOWN, OWNER_SERVER_UNPROVEN, {"instance_id": instance_id, "cause": "lookup_failed"})
    if row is None:
        return Proof(UNKNOWN, OWNER_SERVER_UNPROVEN, {"instance_id": instance_id, "cause": "instance_row_missing"})
    if row.get("node_key") != node:
        return Proof(UNKNOWN, OWNER_SERVER_UNPROVEN, {"instance_id": instance_id, "cause": "other_node"})
    state = executor.identity_state(row.get("pid"), row.get("process_started_at"))
    return Proof(state, None if state != UNKNOWN else OWNER_SERVER_UNPROVEN,
                 {"instance_id": instance_id, "pid": row.get("pid")})


# ── ② process tree: OS probes ────────────────────────────────────────────────

def posix_group_state(pgid: Optional[int]) -> str:
    """``killpg(pgid, 0)``: ESRCH = ABSENT, success or EPERM = MATCH (a member exists,
    perhaps another user's), anything else = UNKNOWN. Not ``_linux_process_group_snapshot``:
    that one skips unreadable PIDs, which turns "cannot see" into "not there"."""
    if os.name == "nt" or not pgid or int(pgid) <= 1:
        return UNKNOWN
    try:
        os.killpg(int(pgid), 0)
    except ProcessLookupError:
        return ABSENT
    except PermissionError:
        return MATCH
    except OSError:
        return UNKNOWN
    return MATCH


def posix_group_members(pgid: int, leader_start: Optional[str]) -> tuple[str, tuple, bool]:
    """``(state, member PIDs, owned)`` from ``/proc/*/stat``.

    ``owned`` is True only when every process could be read and every member belongs to
    this run's session (sid = pgid) and started no earlier than the recorded leader -- and
    no member is a *different* process holding the leader's PID (a reused PID that made
    its own group). Only an owned group may be signalled. The one rule for every signaller:
    :func:`terminate_tree` here and the POSIX supervisor's own termination.
    """
    try:
        leader = int(leader_start) if leader_start is not None else None
    except (TypeError, ValueError):
        return UNKNOWN, (), False
    table = _proc_stat_table()
    if table is None:
        return UNKNOWN, (), False
    rows, complete = table
    members, owned = [], complete and leader is not None
    for pid, pgrp, session, start in rows:
        if pgrp != int(pgid):
            continue
        members.append(pid)
        if (session != int(pgid) or leader is None or start < leader
                or (pid == int(pgid) and start != leader)):
            owned = False
    return (MATCH if members else ABSENT), tuple(members), owned


def _proc_stat_table() -> Optional[tuple[list, bool]]:
    """``([(pid, pgrp, session, start)], complete)`` from ``/proc/*/stat``; None without a
    usable ``/proc``. A process gone while reading is skipped; any other unreadable entry
    makes ``complete`` False (it might be a member)."""
    if os.name == "nt" or not executor._proc_usable():
        return None
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    rows, complete = [], True
    for name in entries:
        if not name.isdigit():
            continue
        try:
            fields = Path(f"/proc/{name}/stat").read_text().rsplit(")", 1)[1].split()
            rows.append((int(name), int(fields[2]), int(fields[3]), int(fields[19])))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, IndexError, ValueError):
            complete = False
    return rows, complete


def marker_scan(run_id: str) -> tuple[str, tuple]:
    """Linux: ``(state, PIDs)`` of processes whose environment carries this run's marker.

    An environ we may not read counts as UNKNOWN when the process is ours (same UID) and is
    skipped otherwise (a run's processes inherit the server's UID; a non-dumpable or other
    user's process is the documented residual limit). Gone-while-reading is skipped.
    """
    if os.name == "nt" or not run_id or not executor._proc_usable():
        return UNKNOWN, ()
    needle = f"{MARKER_ENV}={run_id}".encode("utf-8")
    me, uid = os.getpid(), os.getuid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return UNKNOWN, ()
    found = []
    for name in entries:
        if not name.isdigit() or int(name) == me:
            continue
        try:
            data = Path(f"/proc/{name}/environ").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            try:
                if os.stat(f"/proc/{name}").st_uid == uid:
                    return UNKNOWN, ()
            except (FileNotFoundError, ProcessLookupError):
                pass
            except OSError:
                return UNKNOWN, ()
            continue
        except OSError:
            return UNKNOWN, ()
        if needle in data.split(b"\0"):
            found.append(int(name))
    return (MATCH if found else ABSENT), tuple(found)


def windows_job_state(name: Optional[str], *, terminate: bool = False, wait: float = 0.0) -> str:
    """Open the run's named Job: not found = ABSENT (every handle closed, KILL_ON_JOB_CLOSE
    took the members), open with no assigned process = ABSENT, any member = MATCH, any
    other failure = UNKNOWN. ``terminate`` sends TerminateJobObject first and waits up to
    ``wait`` seconds for the Job to empty."""
    if os.name != "nt" or not name:
        return UNKNOWN
    try:
        import ctypes
        from ctypes import wintypes
        from modules.flow_gate.services.process_runner import _JobBasicProcessIdList
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenJobObjectW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        access = _JOB_OBJECT_QUERY | (_JOB_OBJECT_TERMINATE if terminate else 0)
        handle = kernel.OpenJobObjectW(access, False, name)
        if not handle:
            return ABSENT if ctypes.get_last_error() == _ERROR_FILE_NOT_FOUND else UNKNOWN
        try:
            def assigned() -> str:
                _, _, list_type = _JobBasicProcessIdList.types(1)
                buf = list_type()
                if kernel.QueryInformationJobObject(handle, _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                                                    ctypes.byref(buf), ctypes.sizeof(buf), None):
                    return MATCH if buf.NumberOfAssignedProcesses else ABSENT
                return MATCH if ctypes.get_last_error() == _ERROR_MORE_DATA else UNKNOWN

            if terminate:
                kernel.TerminateJobObject(handle, 1)   # judged by the query below, not by this
            state = assigned()
            deadline = time.monotonic() + wait
            while state == MATCH and time.monotonic() < deadline:
                time.sleep(.1)
                state = assigned()
            return state
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return UNKNOWN


# ── ② process tree: judgement ────────────────────────────────────────────────

def _combine(parts: dict) -> Proof:
    states = set(parts.values())
    if MATCH in states:
        return Proof(MATCH, TREE_UNPROVEN, dict(parts))
    if UNKNOWN in states or not parts:
        return Proof(UNKNOWN, TREE_UNPROVEN, dict(parts))
    return Proof(ABSENT, None, dict(parts))


def tree_state(row: dict) -> Proof:
    """Is the run's whole process tree empty? Read-only.

    Windows: the run's named Job (from ``owner_marker``) plus the recorded target; a run
    whose spawn began (``process_owner_kind`` stamped) without a recorded target is UNKNOWN.
    A run without a named Job (before 158) cannot be proved: UNKNOWN. POSIX: target and
    supervisor identities, the target's process group (pgid = target PID) and, when the
    run carries the marker, the Linux marker scan. With neither a target PID nor a marker
    nothing is known about the tree: UNKNOWN (``ownership_unknown``).
    """
    marker = _marker(row)
    parts: dict = {}
    target_pid, target_id = row.get("target_pid"), row.get("target_start_identity")
    if os.name == "nt":
        name = marker.get("job") if marker.get("kind") == "windows_job" else None
        if not name:
            return Proof(UNKNOWN, TREE_UNPROVEN, {"cause": "no_named_job"})
        job = windows_job_state(name)
        if job == ABSENT and name.startswith("Local\\") and (
                marker.get("session") is None or marker.get("session") != windows_session_id()):
            job = UNKNOWN   # another session's Local\ namespace: "not found" proves nothing
        parts["job"] = job
        if target_pid:
            parts["target"] = executor.identity_state(target_pid, target_id)
        elif row.get("process_owner_kind"):
            # The spawn began (its stamp lands before Popen, 0080 T#3) and no target is on
            # record: one may exist outside the Job (Popen -> AssignProcessToJobObject), so
            # an empty or vanished Job proves nothing for it.
            parts["target"] = UNKNOWN
        return _combine(parts)
    if marker and marker.get("kind") != "posix_marker":
        return Proof(UNKNOWN, TREE_UNPROVEN, {"cause": "foreign_marker"})
    if not target_pid and not marker:
        return Proof(UNKNOWN, OWNERSHIP_UNKNOWN, {"cause": "no_process_record"})
    if target_pid:
        parts["target"] = executor.identity_state(target_pid, target_id)
        parts["group"] = posix_group_state(target_pid)
    if row.get("supervisor_pid"):
        parts["supervisor"] = executor.identity_state(row["supervisor_pid"], row.get("supervisor_start_identity"))
    if marker:
        parts["marker"] = marker_scan(row.get("self_check_run_id"))[0]
    return _combine(parts)


def terminate_tree(row: dict, *, wait: float = 3.0) -> Proof:
    """:func:`tree_state`, after a verified attempt to empty a tree that still has members.

    Only what is proved to be this run's is signalled: the named Job (Windows), or the
    target's group when every member is owned (:func:`posix_group_members`). Marker-only
    processes outside the group and a live supervisor are not signalled; they keep the
    tree MATCH (the supervisor is waited for, it exits on its own once its server is gone).
    Call it only once the owning server is proved dead, or from the owning server itself.
    """
    tree = tree_state(row)
    if tree.state != MATCH:
        return tree
    if os.name == "nt":
        marker = _marker(row)
        windows_job_state(marker.get("job"), terminate=True, wait=wait)
    else:
        pgid, leader = row.get("target_pid"), row.get("target_start_identity")
        if pgid and MATCH in (tree.detail.get("group"), tree.detail.get("target")):
            state, _members, owned = posix_group_members(int(pgid), leader)
            if state == MATCH and owned:
                _signal_group(int(pgid), signal.SIGTERM)
                if not _wait_group_gone(int(pgid), wait):
                    # Checked again before each signal: the group may have emptied and its
                    # number been reused in between.
                    state, _members, owned = posix_group_members(int(pgid), leader)
                    if state == MATCH and owned:
                        _signal_group(int(pgid), signal.SIGKILL)
                        _wait_group_gone(int(pgid), 2.0)
    deadline = time.monotonic() + wait
    tree = tree_state(row)
    while tree.state == MATCH and time.monotonic() < deadline:
        time.sleep(.1)
        tree = tree_state(row)
    return tree


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def _wait_group_gone(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while posix_group_state(pgid) != ABSENT:
        if time.monotonic() >= deadline:
            return False
        time.sleep(.05)
    return True


# ── ④ the release rule ───────────────────────────────────────────────────────

def release_verdict(row: dict, *, terminate: bool = False, wait: float = 3.0) -> Verdict:
    """The single release rule: owner server proved dead, THEN the tree proved empty, then
    both re-checked right before the caller writes anything.

    ``terminate=True`` tries a verified kill of a tree that still has members (only after
    the server is proved dead). The caller does the writes, under its own CAS
    (``mark_recovering`` for recovery), and on PROTECT keeps G protected and the run
    ``recovery_incomplete`` with ``verdict.reason``.
    """
    owner = owner_instance_of(row)
    server = owner_server_state(owner)
    if server.state == MATCH:
        return Verdict(KEEP, OWNER_ALIVE, server)
    if not server.gone:
        return Verdict(KEEP, server.reason or OWNER_SERVER_UNPROVEN, server)
    tree = terminate_tree(row, wait=wait) if terminate else tree_state(row)
    if tree.state != ABSENT:
        return Verdict(PROTECT, TREE_UNPROVEN, server, tree)
    server = owner_server_state(owner)
    if not server.gone:
        return Verdict(PROTECT, OWNER_SERVER_UNPROVEN, server, tree)
    tree = tree_state(row)
    if tree.state != ABSENT:
        return Verdict(PROTECT, TREE_UNPROVEN, server, tree)
    return Verdict(RELEASE, None, server, tree)

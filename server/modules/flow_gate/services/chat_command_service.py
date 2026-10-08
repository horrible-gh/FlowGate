"""Chat (CH) command execution (flowgate.default.0670 T0004, R0001 / NR0003 §6-§7, §13).

One contract, two transports:

* API providers call the ``run_command`` tool inside the worker's tool loop
  (:func:`run_tool`), and the tool result goes back into the SAME conversation of the
  SAME ``ai_run_id`` so the model keeps working (NR0003 §7.2).
* CLI providers call the token-bound ``/api/v1/chat-commands`` endpoints, which reach
  the very same :func:`create_request` / :func:`wait_terminal` / :func:`ai_result`.

The approval state lives in a durable ``chat_command_requests`` row, never in the
browser. The user's policy decides the first transition:

    always_approve -> approved -> running -> succeeded | failed | timed_out | cancelled
    user_approval  -> pending_approval -> (user) approved ... | rejected | cancelled
    reject         -> rejected                       (no process is ever created)

Deliberately NOT done here (T0004 / NR0003 §3.2, §5.2, §13):

* no ``user_chat_source_access`` claim/commit/rollback -- a command is a sub-action of
  an already admitted CH run and never consumes ``1회 편집`` again;
* no Self-check service/DB/policy and no project-wide Git lock -- neither a pending
  approval nor a running test holds any repository lock. Only the process ownership
  layer (``tr_self_check_executor.spawn/wait``: Job Object / supervised process group,
  timeout, tails, cancel) is reused.
"""
from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from modules.flow_gate.db import chat_command_requests as db
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import chat_command_policy as policy
from modules.flow_gate.services import chat_settings_service
from modules.flow_gate.services import git_service
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services.tr_self_check_service import _redact

logger = logging.getLogger(__name__)

TOOL_NAME = "run_command"
POLICY_DOMAIN = chat_settings_service.COMMAND_POLICY_DOMAIN
POLICY_DEFAULT = chat_settings_service.COMMAND_POLICY_DEFAULT
# How long one tool call may sit in pending_approval inside an API run before the
# request is withdrawn (the model is told; it can ask again). Bounded further by the
# run's own remaining budget.
APPROVAL_WAIT_MAX_SEC = 900
# Per-call long-poll cap for the CLI transport.
CLI_WAIT_DEFAULT_SEC = 30
CLI_WAIT_MAX_SEC = 120
# What one result hands back to the model per stream. The durable row keeps 64 KiB.
RESULT_STREAM_CHARS = 12000
_RUN_SAFETY_SEC = 5.0

TOOL_DESCRIPTION = (
    "Run one local command (tests, build/lint/typecheck, git status/diff/log/add/commit, ...) in "
    "this conversation's group worktree and get its exit_code/stdout/stderr back. Give the "
    "program and its arguments separately: no shell, no pipes/redirection/&&, no inline "
    "`python -c`/`node -e`. `cwd` is relative to the worktree root (default '.'). The user's "
    "command policy decides whether it runs immediately, waits for their approval, or is "
    "refused; a refusal is returned as a result (rejected=true) so you can continue. Use this "
    "instead of saying you cannot run tests or git commands."
)
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "program": {"type": "string", "description": "Executable name only, e.g. pytest, git, npm"},
        "args": {"type": "array", "items": {"type": "string"}, "maxItems": 256},
        "cwd": {"type": "string", "description": "Worktree-relative directory, default '.'"},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": policy.TIMEOUT_MAX_SEC},
    },
    "required": ["program"],
    "additionalProperties": False,
}

_events: dict[str, threading.Event] = {}
_controls: dict[str, tuple[executor.ProcessControl, threading.Event]] = {}
_cancel_requested: set[str] = set()
# request_id -> set once its executor thread has finished, process and runtime dir
# cleaned up. Registered before the thread starts, so a cancel can always wait on it --
# including the window where the row is already ``running`` but no process exists yet.
_executions: dict[str, threading.Event] = {}
# Runs whose commands are being (or have been) closed by a run cancel or finalization.
_closed_runs: dict[str, None] = {}
_CLOSED_RUNS_MAX = 4096
# run_id -> launch gate. An executor thread holds its run's gate from the final live-run
# check until the spawned process's control is installed; closing the run (run cancel,
# finalization) and a user cancel of a running request take the same gate. So a launch
# and a cancellation are strictly ordered: either the cancel comes first and the launch
# sees it and spawns nothing, or the process already exists with a control the cancel
# kills. ``_lock`` is not held across the spawn, so other requests' reads never wait.
_launch_gates: dict[str, threading.Lock] = {}
_CANCEL_POLL_SEC = 1.0
_lock = threading.RLock()


class ChatCommandError(Exception):
    def __init__(self, status: int, code: str, message: Optional[str] = None):
        self.status, self.code, self.message = status, code, message or code
        super().__init__(self.message)


# ── policy setting ──────────────────────────────────────────────────────────

def resolve_policy(user_id: Optional[str]) -> str:
    """Read at request time, so a policy switched mid-run applies to the next command."""
    return chat_settings_service.resolve_command_policy(user_id)


# ── helpers ─────────────────────────────────────────────────────────────────

def _svc():
    from modules.flow_gate.services import ai_invoke_service
    return ai_invoke_service


def _live_run(run_id: Optional[str]) -> Optional[dict]:
    if not run_id:
        return None
    try:
        return _svc().get_run_record(run_id)
    except Exception:
        return None


def _note_progress(run: Optional[dict]) -> None:
    """A command request/decision/result is progress of the run that asked for it.

    Without this a CLI run waiting on a human approval would be ended by the
    no-progress watchdog exactly while it is doing what it was told to do.
    """
    if not run:
        return
    try:
        now = _svc()._now_mono()
        run["stall_anchor_mono"] = now
        run["last_progress_mono"] = now
        run["last_progress_at"] = now_iso()
        run["last_progress_signal"] = "command"
        run["progress_observations"] = int(run.get("progress_observations") or 0) + 1
    except Exception:
        logger.debug("chat command progress note failed", exc_info=True)


def _provider_name(run: dict) -> Optional[str]:
    provider = run.get("provider")
    if isinstance(provider, dict):
        provider = provider.get("name") or provider.get("id")
    return str(provider)[:200] if provider else None


def _event(request_id: str) -> threading.Event:
    with _lock:
        return _events.setdefault(request_id, threading.Event())


def _notify(row: Optional[dict]) -> None:
    if not row:
        return
    _event(row["request_id"]).set()
    if row["status"] in db.TERMINAL_STATUSES:
        # Waiters already hold the object they block on; a later waiter re-reads the row.
        with _lock:
            _events.pop(row["request_id"], None)
    git_service._emit("chat_command_updated", row["project_id"], row["group_id"], {
        "project_id": row["project_id"], "group_id": row["group_id"], "doc_id": row["doc_id"],
        "ai_run_id": row["ai_run_id"], "request_id": row["request_id"], "status": row["status"],
    })


def _authorize_run(run: Optional[dict]) -> dict:
    if not run or run.get("action_scope") != "chat" or not run.get("doc_ref"):
        raise ChatCommandError(403, "chat_command_capability_required",
                               "command execution is only available inside a live chat AI run")
    if run.get("status") not in (None, "running") or run.get("finished_at"):
        raise ChatCommandError(409, "chat_command_run_finished", "this AI run has already finished")
    if run.get("cancel_event") is not None and run["cancel_event"].is_set():
        raise ChatCommandError(409, "chat_command_run_cancelled", "this AI run is being cancelled")
    if _run_closed(run.get("run_id")):
        raise ChatCommandError(409, "chat_command_run_finished", "this AI run is finishing")
    return run


def _run_closed(run_id: Optional[str]) -> bool:
    with _lock:
        return bool(run_id) and run_id in _closed_runs


def _launch_gate(run_id: Optional[str]):
    if not run_id:
        return contextlib.nullcontext()
    with _lock:
        return _launch_gates.setdefault(run_id, threading.Lock())


def _close_run(run_id: str, on_closed: Optional[Callable[[], None]] = None) -> None:
    """Waits out a launch of this run that is between its live check and its spawn.

    ``on_closed`` runs while the gate is still held, right after the run is closed: what
    it announces (the run is cancelling) is never visible to a launch that could still spawn.
    """
    with _launch_gate(run_id):
        with _lock:
            _closed_runs[run_id] = None
            while len(_closed_runs) > _CLOSED_RUNS_MAX:
                _closed_runs.pop(next(iter(_closed_runs)))
        if on_closed is not None:
            on_closed()


def _live_refusal(run_id: Optional[str]) -> Optional[ChatCommandError]:
    """Why the run that asked for a command may no longer have it executed, if it may not.

    The same checks a new request gets (_authorize_run), re-applied at decision time and
    again right before a process is spawned: a finished, stopping, cancelled or finalizing
    run never gets a process.
    """
    run = _live_run(run_id)
    if run is None:
        return ChatCommandError(409, "chat_command_run_finished", "this AI run has already finished")
    try:
        _authorize_run(run)
    except ChatCommandError as exc:
        return exc
    return None


def authorize_token(token: dict, run: Optional[dict]) -> dict:
    """CLI transport: the bearer must be the current token of that same live chat run."""
    if not token.get("ai_run_id") or not run:
        raise ChatCommandError(403, "chat_command_forbidden", "a live AI chat run token is required")
    axes = (
        (token.get("project") or token.get("project_id"), run.get("project_id")),
        (token.get("group_id"), run.get("group_id")),
        (token.get("ai_run_id"), run.get("run_id")),
    )
    if any(not left or str(left) != str(right or "") for left, right in axes):
        raise ChatCommandError(403, "chat_command_forbidden", "AI run/token scope does not match")
    current = str(run.get("current_token_id") or run.get("token_id") or "")
    if current and str(token.get("token_id") or "") != current:
        raise ChatCommandError(403, "chat_command_forbidden", "AI token is not current for this run")
    return _authorize_run(run)


def worktree_root(project_id: str, group_id: str, expected: Optional[str] = None) -> Path:
    """The group's managed worktree, strictly -- never the base checkout (R0001 §10).

    ``expected`` is the root the run was admitted with; a mismatch (the group was
    re-provisioned, the run fell back to another checkout) refuses instead of running
    the command somewhere the conversation is not working.
    """
    try:
        root, reason = git_service.effective_src_root_ex(project_id, group_id)
    except Exception as exc:
        raise ChatCommandError(409, "chat_command_worktree_unavailable", str(exc)[:200]) from exc
    if reason != "worktree" or not root:
        raise ChatCommandError(409, "chat_command_worktree_unavailable",
                               "this group has no managed worktree to run commands in")
    try:
        path = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ChatCommandError(409, "chat_command_worktree_unavailable", str(exc)[:200]) from exc
    if expected:
        try:
            bound = Path(expected).resolve(strict=False)
        except OSError:
            bound = Path(expected)
        if bound != path:
            raise ChatCommandError(409, "chat_command_worktree_mismatch",
                                   "the run's source root is not this group's worktree")
    return path


def public(row: dict) -> dict:
    """The browser/CLI view of one request."""
    args = list(row.get("args") or [])
    _category, high_impact = policy.classify(row["program"], args)
    return {
        "request_id": row["request_id"], "ai_run_id": row["ai_run_id"], "doc_id": row["doc_id"],
        "project_id": row["project_id"], "group_id": row["group_id"],
        "program": row["program"], "args": args, "command": policy.display(row["program"], args),
        "cwd": row["cwd_relative"], "timeout_seconds": row["timeout_seconds"],
        "category": row["category"], "high_impact": high_impact, "policy": row["policy"],
        "provider_name": row.get("provider_name"), "status": row["status"],
        "decision_source": row.get("decision_source"), "decided_by": row.get("decided_by"),
        "decided_at": row.get("decided_at"), "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"), "duration_ms": row.get("duration_ms"),
        "exit_code": row.get("exit_code"), "timed_out": bool(row.get("timed_out")),
        "stdout_tail": _redact(row.get("stdout_tail")), "stderr_tail": _redact(row.get("stderr_tail")),
        "error_code": row.get("error_code"), "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        # 0675 T0004: where the row sits in the conversation, decided by the server.
        "anchor_seq": row.get("anchor_seq"), "anchor_position": row.get("anchor_position"),
        "anchor_state": row.get("anchor_state"),
    }


def ai_result(row: dict) -> dict:
    """What the model gets back (R0001 §6): enough to judge success, failure or refusal."""
    args = list(row.get("args") or [])
    status = row["status"]
    rejected = status == "rejected"
    rejected_by = None
    if rejected:
        rejected_by = "policy" if row.get("decision_source") == "policy" else "user"
    stdout = _redact(row.get("stdout_tail")) or ""
    stderr = _redact(row.get("stderr_tail")) or ""
    messages = {
        "succeeded": "The command ran and exited with code 0.",
        "failed": "The command ran and failed." if row.get("exit_code") is not None
                  else "The command could not be started.",
        "timed_out": "The command was killed after reaching its timeout.",
        "cancelled": "The command was cancelled before it completed.",
        "rejected": ("The user's command policy refuses command execution; nothing was run."
                     if rejected_by == "policy" else "The user rejected this command; nothing was run."),
        "pending_approval": "Waiting for the user's approval.",
        "approved": "Approved; starting.",
        "running": "Running.",
    }
    return {
        "ok": True,
        "request_id": row["request_id"],
        "command": policy.display(row["program"], args),
        "program": row["program"], "args": args, "cwd": row["cwd_relative"],
        "status": status, "exit_code": row.get("exit_code"),
        "timed_out": bool(row.get("timed_out")), "rejected": rejected, "rejected_by": rejected_by,
        "cancelled": status == "cancelled", "duration_ms": row.get("duration_ms"),
        "stdout": stdout[-RESULT_STREAM_CHARS:], "stdout_truncated": len(stdout) > RESULT_STREAM_CHARS,
        "stderr": stderr[-RESULT_STREAM_CHARS:], "stderr_truncated": len(stderr) > RESULT_STREAM_CHARS,
        "error_code": row.get("error_code"),
        "message": messages.get(status, status),
    }


# ── request / decision ──────────────────────────────────────────────────────

def create_request(run: dict, payload: object, *, token_id: Optional[str] = None) -> dict:
    """Validate, bind to the worktree, apply the user's policy. Returns the stored row."""
    _authorize_run(run)
    try:
        command = policy.validate_request(payload)
    except policy.ChatCommandPolicyError as exc:
        raise ChatCommandError(422, exc.code, exc.message) from exc
    root = worktree_root(run["project_id"], run["group_id"], run.get("source_root"))
    try:
        policy.resolve(command, root)
    except policy.ChatCommandPolicyError as exc:
        raise ChatCommandError(422, exc.code, exc.message) from exc
    user_policy = resolve_policy(run.get("issued_to"))
    status = {"always_approve": "approved", "reject": "rejected"}.get(user_policy, "pending_approval")
    row = db.create(
        request_id=f"ccr_{uuid.uuid4().hex[:24]}", ai_run_id=run["run_id"], doc_id=run["doc_ref"],
        project_id=run["project_id"], group_id=run["group_id"],
        token_id=token_id or run.get("current_token_id") or run.get("token_id"),
        issued_to=run.get("issued_to"), provider_name=_provider_name(run),
        program=command.program, args=list(command.args), cwd_relative=command.cwd,
        timeout_seconds=command.timeout_seconds, category=command.category, policy=user_policy,
        status=status, decision_source=None if status == "pending_approval" else "policy",
        run_start_seq=run.get("chat_start_seq"),
    )
    if _run_closed(run["run_id"]):
        # cancel_for_run listed the run's rows before this insert became visible to it.
        closed = db.transition(row["request_id"], ("pending_approval", "approved"), "cancelled",
                               error_code="chat_command_run_finished")
        _notify(closed)
        raise ChatCommandError(409, "chat_command_run_finished", "this AI run is finishing")
    _note_progress(run)
    _notify(row)
    if status == "approved":
        _start(row)
    return row


def decide(request_id: str, user: dict, decision: str) -> dict:
    """The user's [실행] / [거부] (and cancel of a pending or running request)."""
    row = db.get(request_id)
    if row is None:
        raise ChatCommandError(404, "chat_command_not_found")
    if decision not in ("approve", "reject", "cancel"):
        raise ChatCommandError(422, "chat_command_invalid_decision",
                               "decision must be approve, reject or cancel")
    user_id = (user or {}).get("user_id")
    if not (user or {}).get("is_admin") and (not user_id or user_id != row.get("issued_to")):
        raise ChatCommandError(403, "chat_command_decision_forbidden",
                               "only the user who started this AI run can decide its commands")
    fields = {"decision_source": "user", "decided_by": user_id, "decided_at": now_iso()}
    if decision == "approve":
        # The same live-run checks as a new request: a run that finished, stopped, is being
        # cancelled or is finalizing never gets an approval (and so never a process).
        refusal = _live_refusal(row["ai_run_id"])
        if refusal is not None:
            updated = db.transition(request_id, ("pending_approval",), "cancelled",
                                    error_code=refusal.code, **fields)
            _notify(updated)
            raise refusal
        updated = db.transition(request_id, ("pending_approval",), "approved", **fields)
        if updated is None:
            raise ChatCommandError(409, "chat_command_already_decided", f"request is {db.get(request_id)['status']}")
        _note_progress(_live_run(row["ai_run_id"]))
        _notify(updated)
        _start(updated)
        return updated
    if decision == "reject":
        updated = db.transition(request_id, ("pending_approval",), "rejected", **fields)
        if updated is None:
            raise ChatCommandError(409, "chat_command_already_decided", f"request is {db.get(request_id)['status']}")
        _note_progress(_live_run(row["ai_run_id"]))
        _notify(updated)
        return updated
    updated = _cancel(request_id, "chat_command_cancelled_by_user", fields)
    if updated is None:
        raise ChatCommandError(409, "chat_command_already_finished", f"request is {db.get(request_id)['status']}")
    return updated


def _cancel(request_id: str, error_code: str, fields: Optional[dict] = None) -> Optional[dict]:
    fields = dict(fields or {})
    updated = db.transition(request_id, ("pending_approval", "approved"), "cancelled",
                            error_code=error_code, **fields)
    if updated is not None:
        _notify(updated)
        return updated
    # Under the launch gate: a launch that has not passed its live check yet sees the
    # cancel and spawns nothing; one that has already holds a control by the time we get in.
    with _launch_gate((db.get(request_id) or {}).get("ai_run_id")):
        with _lock:
            execution = _executions.get(request_id)
            if execution is not None:
                _cancel_requested.add(request_id)
    if execution is None:
        # No executor thread of this process owns the row (or it already finished):
        # nothing can still spawn or write for it.
        updated = db.transition(request_id, ("running",), "cancelled", error_code=error_code, **fields)
        _notify(updated)
    else:
        _await_execution(request_id, execution)
    row = db.get(request_id)
    return row if row and row["status"] == "cancelled" else None


def _await_execution(request_id: str, execution: threading.Event) -> None:
    """Kill the request's process and wait until its executor thread has cleaned up.

    No time cap: the executor's kill is bounded (Job Object / process group), and
    returning while the row is still ``running`` would let a command outlive its run and
    write after the run's end snapshot. The pre-spawn window is covered too -- the thread
    checks ``_cancel_requested`` under the run's launch gate before it spawns.
    """
    waited = 0.0
    while True:
        with _lock:
            control = _controls.get(request_id)
        if control is not None:
            control[1].set()
            try:
                control[0].cancel()
            except Exception:
                logger.debug("chat command cancel signal failed for %s", request_id, exc_info=True)
        if execution.wait(_CANCEL_POLL_SEC):
            return
        waited += _CANCEL_POLL_SEC
        if waited % 30 < _CANCEL_POLL_SEC:
            logger.warning("still waiting for chat command %s to stop (%.0fs)", request_id, waited)


def cancel_for_run(run_id: str, error_code: str = "chat_command_run_finished") -> None:
    """Finalize hook: nothing of a finished run may stay pending or running.

    Returns only when every command of the run is terminal and its process is gone, so
    the end snapshot taken right after this includes everything the commands wrote.
    The run is marked closed first, under its launch gate: a decision, a new request or
    an executor thread that has not passed its last live check all see it and start nothing.
    """
    _close_run(run_id)
    for row in db.list_open_for_run(run_id):
        try:
            _cancel(row["request_id"], error_code)
        except Exception:
            logger.warning("chat command cancel failed for %s", row["request_id"], exc_info=True)
    with _lock:
        # Every execution of the run has finished; a late launch makes a fresh gate and
        # still finds the run in _closed_runs.
        _launch_gates.pop(run_id, None)


def stop_run(run_id: str, on_closed: Optional[Callable[[], None]] = None) -> None:
    """Run cancel hook (``chain.cancel_run``), called BEFORE the run is announced cancelling.

    Closing the run takes its launch gate, so the cancel is ordered against every launch:
    one already past its live check has spawned and installed its control by the time
    the gate is ours (it is killed here), and every later one sees the run closed and
    spawns nothing. ``on_closed`` (the cancelling announcement) runs under that gate, after
    the close: the cancellation begins only once no launch can spawn any more. Does not
    wait for the processes to exit -- finalization does that (cancel_for_run) before the
    end snapshot.
    """
    _close_run(run_id, on_closed)
    for row in db.list_open_for_run(run_id):
        request_id = row["request_id"]
        with _lock:
            if request_id not in _executions:
                continue
            _cancel_requested.add(request_id)
            control = _controls.get(request_id)
        if control is not None:
            control[1].set()
            try:
                control[0].cancel()
            except Exception:
                logger.debug("chat command cancel signal failed for %s", request_id, exc_info=True)


def recover() -> int:
    """Startup: rows left open by a dead server process are closed, never resumed."""
    closed = 0
    for row in db.list_open():
        updated = db.transition(row["request_id"], db.OPEN_STATUSES, "cancelled",
                                error_code="chat_command_server_restarted")
        if updated:
            closed += 1
    return closed


# ── execution ───────────────────────────────────────────────────────────────

def _start(row: dict) -> None:
    run = _live_run(row["ai_run_id"]) or {}
    request_id = row["request_id"]
    execution = threading.Event()
    with _lock:
        _executions[request_id] = execution
    thread = threading.Thread(
        target=_execute, args=(request_id, run.get("source_root"), execution),
        name=f"chat-command-{request_id}", daemon=True,
    )
    try:
        thread.start()
    except Exception:
        _finish_execution(request_id, execution)
        raise


def _finish_execution(request_id: str, execution: threading.Event) -> None:
    with _lock:
        if _executions.get(request_id) is execution:
            _executions.pop(request_id, None)
        _cancel_requested.discard(request_id)
    execution.set()


def _execute(request_id: str, expected_root: Optional[str],
             execution: Optional[threading.Event] = None) -> None:
    try:
        _execute_owned(request_id, expected_root)
    finally:
        # Only now -- process killed, Job Object closed, row terminal -- may a cancel return.
        if execution is not None:
            _finish_execution(request_id, execution)


def _execute_owned(request_id: str, expected_root: Optional[str]) -> None:
    row = db.transition(request_id, ("approved",), "running", started_at=now_iso())
    if row is None:
        return
    _notify(row)
    started = time.monotonic()
    control = None
    runtime: Optional[Path] = None
    cancelled = threading.Event()
    final: Optional[dict] = None
    try:
        # Re-verified right before the process exists: the approval may have waited long.
        root = worktree_root(row["project_id"], row["group_id"], expected_root)
        command = policy.ChatCommand(row["program"], tuple(row["args"]), row["cwd_relative"],
                                     int(row["timeout_seconds"]), row["category"], False)
        resolved, cwd, path_value = policy.resolve(command, root)
        runtime = Path(tempfile.mkdtemp(prefix=f"flowgate-{request_id}-"))
        env = policy.environment(path_value, runtime)
        # Last gate before a process exists: the run may have been stopped, cancelled or
        # finalized while this request waited for approval or for this thread to start.
        # The check, the spawn and the control install happen under the run's launch gate,
        # so no cancellation can begin between the check and the spawn.
        with _launch_gate(row["ai_run_id"]):
            refusal = _live_refusal(row["ai_run_id"])
            with _lock:
                if refusal is None and request_id in _cancel_requested:
                    refusal = ChatCommandError(409, "chat_command_cancelled")
            if refusal is None:
                control, _ownership = executor.spawn(
                    [resolved.executable, *resolved.argv_prefix, *command.args], cwd, env,
                    command.timeout_seconds, request_id,
                )
                with _lock:
                    _controls[request_id] = (control, cancelled)
        if refusal is not None:
            final = db.transition(request_id, ("running",), "cancelled", error_code=refusal.code,
                                  duration_ms=int((time.monotonic() - started) * 1000))
            return
        # Defensive: a run stop that bypassed the gate (cancel_event set directly).
        run = _live_run(row["ai_run_id"]) or {}
        with _lock:
            early_cancel = (request_id in _cancel_requested or _run_closed(row["ai_run_id"])
                            or (run.get("cancel_event") is not None and run["cancel_event"].is_set()))
        if early_cancel:
            cancelled.set()
            control.cancel()
        result = executor.wait(control, command.timeout_seconds, cancelled)
        with _lock:
            was_cancelled = result.cancelled or early_cancel or request_id in _cancel_requested
        if was_cancelled:
            status = "cancelled"
        elif result.timed_out:
            status = "timed_out"
        elif result.exit_code == 0:
            status = "succeeded"
        else:
            status = "failed"
        final = db.transition(
            request_id, ("running",), status, exit_code=result.exit_code, timed_out=result.timed_out,
            stdout_tail=_redact(result.stdout_tail), stderr_tail=_redact(result.stderr_tail),
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code="chat_command_cancelled" if status == "cancelled" else None,
        )
    except ChatCommandError as exc:
        final = db.transition(request_id, ("running",), "failed", error_code=exc.code,
                              stderr_tail=exc.message, duration_ms=int((time.monotonic() - started) * 1000))
    except policy.ChatCommandPolicyError as exc:
        final = db.transition(request_id, ("running",), "failed", error_code=exc.code,
                              stderr_tail=exc.message, duration_ms=int((time.monotonic() - started) * 1000))
    except executor.OwnershipError:
        final = db.transition(request_id, ("running",), "failed",
                              error_code="chat_command_process_owner_unavailable",
                              duration_ms=int((time.monotonic() - started) * 1000))
    except Exception as exc:
        logger.exception("chat command %s failed", request_id)
        final = db.transition(request_id, ("running",), "failed", error_code="chat_command_internal_error",
                              stderr_tail=str(exc)[:500], duration_ms=int((time.monotonic() - started) * 1000))
    finally:
        if control is not None:
            try:
                control.close()
            except Exception:
                pass
        with _lock:
            _controls.pop(request_id, None)
        if runtime is not None:
            shutil.rmtree(runtime, ignore_errors=True)
        _note_progress(_live_run((final or row)["ai_run_id"]))
        _notify(final or db.get(request_id))


# ── waiting / transports ────────────────────────────────────────────────────

def wait_terminal(request_id: str, timeout: float, cancel_event: Optional[threading.Event] = None,
                  run: Optional[dict] = None) -> Optional[dict]:
    """Block until the row is terminal, the timeout passes or the run is cancelled."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    event = _event(request_id)
    row = db.get(request_id)
    while row is not None and row["status"] not in db.TERMINAL_STATUSES:
        left = deadline - time.monotonic()
        if left <= 0 or (cancel_event is not None and cancel_event.is_set()):
            break
        event.wait(min(1.0, left))
        event.clear()
        if run is not None and row["status"] == "pending_approval":
            _note_progress(run)
        row = db.get(request_id)
    return row


def run_tool(run: dict, tool_input: object, remaining_sec: float) -> tuple[int, dict]:
    """API-provider tool entry: create, wait in this same run, return the result."""
    try:
        row = create_request(run, tool_input)
    except ChatCommandError as exc:
        return exc.status, {"ok": False, "op": TOOL_NAME,
                            "error": {"code": exc.code, "message": exc.message}}
    budget = max(0.0, float(remaining_sec) - _RUN_SAFETY_SEC)
    if row["status"] == "pending_approval":
        row = wait_terminal(row["request_id"], min(budget, APPROVAL_WAIT_MAX_SEC),
                            run.get("cancel_event"), run)
        if row and row["status"] == "pending_approval":
            withdrawn = db.transition(row["request_id"], ("pending_approval",), "cancelled",
                                      error_code="chat_command_approval_wait_expired")
            _notify(withdrawn)
            row = withdrawn or db.get(row["request_id"])
    if row and row["status"] not in db.TERMINAL_STATUSES:
        left = max(0.0, float(remaining_sec) - _RUN_SAFETY_SEC)
        row = wait_terminal(row["request_id"], min(left, row["timeout_seconds"] + 15),
                            run.get("cancel_event"))
        if row and row["status"] not in db.TERMINAL_STATUSES:
            row = _cancel(row["request_id"], "chat_command_run_budget_exhausted") or db.get(row["request_id"])
    return 200, ai_result(row)


def cli_payload(row: dict) -> dict:
    terminal = row["status"] in db.TERMINAL_STATUSES
    return {"ok": True, "request": public(row), "terminal": terminal,
            "result": ai_result(row) if terminal else None}


def cli_create(run: dict, token: dict, body: dict) -> dict:
    payload = dict(body or {})
    wait = payload.pop("wait_seconds", CLI_WAIT_DEFAULT_SEC)
    row = create_request(run, payload, token_id=token.get("token_id"))
    return cli_payload(wait_terminal(row["request_id"], _cli_wait(wait), run.get("cancel_event"), run) or row)


def cli_read(run: dict, request_id: str, wait: object) -> dict:
    row = db.get(request_id)
    if row is None or row["ai_run_id"] != run.get("run_id"):
        raise ChatCommandError(404, "chat_command_not_found")
    return cli_payload(wait_terminal(request_id, _cli_wait(wait), run.get("cancel_event"), run) or row)


def _cli_wait(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(CLI_WAIT_DEFAULT_SEC)
    return float(min(max(0, value), CLI_WAIT_MAX_SEC))


def list_for_doc(doc_id: str, *, from_seq: Optional[int] = None, to_seq: Optional[int] = None,
                 include_unplaced: bool = True) -> list[dict]:
    return [public(row) for row in db.list_for_doc(
        doc_id, from_seq=from_seq, to_seq=to_seq, include_unplaced=include_unplaced)]

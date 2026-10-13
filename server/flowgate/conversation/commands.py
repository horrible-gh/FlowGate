"""Chat command requests: state machine, decision, waiting, execution, AI result (D0006 §3.1-§3.7).

One contract, two transports:

* API providers call the ``run_command`` tool inside the worker's tool loop
  (:func:`run_tool`); the result returns to the SAME model conversation of the SAME run.
* CLI providers call the run-token ``/chat-commands`` endpoints (:func:`cli_create`,
  :func:`cli_read`), which reach the very same request, wait and result builder.

The approval state lives in the durable ``chat_command_requests`` row, never in the
browser or a provider prompt. The user's command policy decides the first transition::

    always_approve  approved -> running -> succeeded | failed | timed_out | cancelled
    user_approval   pending_approval -> approved ... | rejected | cancelled
    reject          rejected                      (no process is ever created)

Time: waiting for approval and for a running command is bounded by the run's hard
ceiling only -- :func:`chat_run_budget_sec` -- never by the hop's ``_remaining_sec``,
which turns negative for a run that keeps working past ``timeout_sec`` (D0006 §3.5.1).
Every wait step records run progress so the no-progress watchdog never ends a run that
is waiting for its user (D0006 §3.6).

No repository lock is taken for a pending approval or a running command (main's git
concurrency layer is not part of v0.2); only the self-check process ownership layer
(Job Object / supervised process group, timeout, tails, cancel) is reused.
"""
from __future__ import annotations

import contextlib
import json
import logging
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from flowgate.conversation import activity
from flowgate.conversation import command_policy as policy
from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter
from flowgate.conversation import repository as repo

logger = logging.getLogger(__name__)

TOOL_NAME = "run_command"
TOOL_DESCRIPTION = (
    "Run one local command (tests, build/lint/typecheck, git status/diff/log/add/commit, ...) in "
    "this conversation's group worktree and get its exit_code/stdout/stderr back. Give the "
    "program and its arguments separately: no shell, no pipes/redirection/&&, no inline "
    "`python -c`/`node -e`. `cwd` is relative to the worktree root (default '.'). The user's "
    "command policy decides whether it runs immediately, waits for their approval, or is "
    "refused; a refusal or an expired approval comes back as a result so you can continue. "
    "Use this instead of saying you cannot run tests or git commands."
)
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "program": {"type": "string", "description": "Executable name only, e.g. pytest, git, npm"},
        "args": {"type": "array", "items": {"type": "string"}, "maxItems": policy.ARGS_MAX},
        "cwd": {"type": "string", "description": "Worktree-relative directory, default '.'"},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": policy.TIMEOUT_MAX_SEC},
    },
    "required": ["program"],
    "additionalProperties": False,
}
TOOL_PROMPT_HINT = (
    "When the work needs a test, build or git command, call `run_command`; its result (or the "
    "user's refusal) comes back here and you continue from it."
)

# The executor keeps the last 64 KiB of each stream; a tail that long was cut there.
_EXECUTOR_TAIL_BYTES = 64 * 1024
_TRUNCATION_NOTE = "Only the end of the output is included; the full output is shown to the user."
_RESULT_BUILD_ATTEMPTS = 4

_events: dict[str, threading.Event] = {}
_controls: dict[str, tuple[object, threading.Event]] = {}
# request_id -> reason code of a cancel asked for while its executor thread owns it.
_cancel_requested: dict[str, str] = {}
# request_id -> set once its executor thread finished (process gone, row terminal).
# Registered before the thread starts, so a cancel can always wait on it.
_executions: dict[str, threading.Event] = {}
# run_id -> reason code; runs whose commands are being (or have been) closed.
_closed_runs: dict[str, str] = {}
_CLOSED_RUNS_MAX = 4096
# run_id -> launch gate. An executor thread holds it from its last live-run check until
# the spawned process's control is installed; closing the run takes the same gate, so a
# launch and a cancellation are strictly ordered.
_launch_gates: dict[str, threading.Lock] = {}
_CANCEL_POLL_SEC = 1.0
_lock = threading.RLock()


# ── run helpers ──────────────────────────────────────────────────────────────

def chat_run_budget_sec(run: Optional[dict]) -> float:
    """Seconds a command of ``run`` may still wait or run: hard-ceiling remainder minus safety.

    ``= _absolute_remaining_sec(run) - RUN_SAFETY_SEC`` (D0006 §3.5.1). Recomputed at every
    check by its callers. A run whose clock cannot be read has no budget.
    """
    if not run:
        return 0.0
    try:
        return legacy_adapter.absolute_remaining_sec(run) - c.RUN_SAFETY_SEC
    except Exception:
        logger.warning("chat run budget unreadable for %s", (run or {}).get("run_id"), exc_info=True)
        return 0.0


def note_progress(run: Optional[dict]) -> None:
    """Record command activity as progress of ``run`` (D0006 §3.6). Never raises."""
    if not run:
        return
    try:
        now = legacy_adapter.now_mono()
        run["stall_anchor_mono"] = now
        run["last_progress_mono"] = now
        run["last_progress_at"] = _now_iso()
        run["last_progress_signal"] = "command"
        run["progress_observations"] = int(run.get("progress_observations") or 0) + 1
    except Exception:
        logger.debug("chat command progress note failed", exc_info=True)


def _now_iso() -> str:
    from modules.flow_gate.db.connection import now_iso

    return now_iso()


def _live_run(run_id: Optional[str]) -> Optional[dict]:
    return legacy_adapter.get_run_record(run_id)


def _provider_name(run: dict) -> Optional[str]:
    provider = run.get("provider")
    if isinstance(provider, dict):
        provider = provider.get("name") or provider.get("id")
    return str(provider)[:200] if provider else None


def _run_closed(run_id: Optional[str]) -> Optional[str]:
    """The reason the run's commands were closed, or None while it is open."""
    if not run_id:
        return None
    with _lock:
        return _closed_runs.get(run_id)


def _launch_gate(run_id: Optional[str]):
    if not run_id:
        return contextlib.nullcontext()
    with _lock:
        return _launch_gates.setdefault(run_id, threading.Lock())


def _close_run(run_id: str, reason: str, on_closed: Optional[Callable[[], None]] = None) -> None:
    """Mark the run closed under its launch gate; ``on_closed`` runs while it is still held."""
    with _launch_gate(run_id):
        with _lock:
            _closed_runs.setdefault(run_id, reason)
            while len(_closed_runs) > _CLOSED_RUNS_MAX:
                _closed_runs.pop(next(iter(_closed_runs)))
        if on_closed is not None:
            on_closed()


def _authorize_run(run: Optional[dict]) -> dict:
    """The checks every new request gets, re-applied at approval and right before spawn."""
    if run:
        from modules.flow_gate.db import tokens as db_tokens
        from modules.flow_gate.services import graph_source_hooks
        token_id = run.get("current_token_id") or run.get("token_id")
        try:
            graph_source_hooks.source_root(
                db_tokens.get_by_id(token_id) if token_id else None,
                project_id=run.get("project_id"), group_id=run.get("group_id"))
        except graph_source_hooks.GraphSourceError as exc:
            raise c.ConversationError(409, exc.code, exc.message) from exc
    if not run or run.get("action_scope") != "chat" or not run.get("doc_ref") \
            or not run.get("chat_command_capability"):
        raise c.ConversationError(403, c.ERR_CAPABILITY_REQUIRED,
                                  "command execution is only available inside a live chat AI run")
    cancel_event = run.get("cancel_event")
    if run.get("status") == "cancelling" or (cancel_event is not None and cancel_event.is_set()):
        raise c.ConversationError(409, c.ERR_RUN_CANCELLED, "this AI run is being cancelled")
    if run.get("status") not in (None, "running") or run.get("finished_at"):
        raise c.ConversationError(409, c.ERR_RUN_FINISHED, "this AI run has already finished")
    closed = _run_closed(run.get("run_id"))
    if closed:
        code = c.ERR_RUN_CANCELLED if closed == c.REASON_RUN_CANCELLED else c.ERR_RUN_FINISHED
        raise c.ConversationError(409, code, "this AI run is finishing")
    return run


def _live_refusal(run_id: Optional[str]) -> Optional[c.ConversationError]:
    """Why the run that asked may no longer have the command executed, if it may not."""
    run = _live_run(run_id)
    if run is None:
        return c.ConversationError(409, c.ERR_RUN_FINISHED, "this AI run has already finished")
    try:
        _authorize_run(run)
    except c.ConversationError as exc:
        return exc
    return None


def authorize_token(token: dict, run: Optional[dict]) -> dict:
    """CLI transport: the bearer must be the current token of that same live chat run."""
    if not token.get("ai_run_id") or not run:
        raise c.ConversationError(403, c.ERR_FORBIDDEN, "a live AI chat run token is required")
    axes = (
        (token.get("project") or token.get("project_id"), run.get("project_id")),
        (token.get("group_id"), run.get("group_id")),
        (token.get("ai_run_id"), run.get("run_id")),
    )
    if any(not left or str(left) != str(right or "") for left, right in axes):
        raise c.ConversationError(403, c.ERR_FORBIDDEN, "AI run/token scope does not match")
    current = str(run.get("current_token_id") or run.get("token_id") or "")
    if current and str(token.get("token_id") or "") != current:
        raise c.ConversationError(403, c.ERR_FORBIDDEN, "AI token is not current for this run")
    return _authorize_run(run)


def worktree_root(project_id: str, group_id: str, expected: Optional[str] = None,
                  *, token_id: Optional[str] = None) -> Path:
    """The group's managed worktree, strictly -- never the base checkout.

    ``expected`` is the root the run was admitted with; a mismatch (re-provisioned group,
    a run that fell back to another checkout) refuses instead of running elsewhere.
    """
    from modules.flow_gate.db import tokens as db_tokens
    from modules.flow_gate.services import graph_source_hooks
    token = db_tokens.get_by_id(token_id) if token_id else None
    if token_id and not token:
        raise c.ConversationError(409, c.ERR_WORKTREE_UNAVAILABLE, "source token is missing")
    try:
        root = graph_source_hooks.source_root(token, project_id=project_id, group_id=group_id)
    except graph_source_hooks.GraphSourceError as exc:
        raise c.ConversationError(409, exc.code, exc.message) from exc
    try:
        root, reason = ((root, legacy_adapter.WORKTREE_REASON) if root is not None
                        else legacy_adapter.effective_src_root_ex(project_id, group_id))
    except Exception as exc:
        raise c.ConversationError(409, c.ERR_WORKTREE_UNAVAILABLE, str(exc)[:200]) from exc
    if reason != legacy_adapter.WORKTREE_REASON or not root:
        raise c.ConversationError(409, c.ERR_WORKTREE_UNAVAILABLE,
                                  "this group has no managed worktree to run commands in")
    try:
        path = Path(root).resolve(strict=True)
    except OSError as exc:
        raise c.ConversationError(409, c.ERR_WORKTREE_UNAVAILABLE, str(exc)[:200]) from exc
    if expected:
        try:
            bound = Path(expected).resolve(strict=False)
        except OSError:
            bound = Path(expected)
        if bound != path:
            raise c.ConversationError(409, c.ERR_WORKTREE_MISMATCH,
                                      "the run's source root is not this group's worktree")
    return path


# ── notification ─────────────────────────────────────────────────────────────

def _event(request_id: str) -> threading.Event:
    with _lock:
        return _events.setdefault(request_id, threading.Event())


def _notify(row: Optional[dict]) -> None:
    """Wake waiters of the row and emit the SSE invalidation signal."""
    if not row:
        return
    _event(row["request_id"]).set()
    if row["status"] in c.TERMINAL_STATUSES:
        # Waiters already hold the object they block on; later waiters re-read the row.
        with _lock:
            _events.pop(row["request_id"], None)
    legacy_adapter.emit_event(c.EVENT_COMMAND_UPDATED, row["project_id"], row["group_id"], {
        "project_id": row["project_id"], "group_id": row["group_id"], "doc_id": row["doc_id"],
        "ai_run_id": row["ai_run_id"], "request_id": row["request_id"], "status": row["status"],
    })


# ── views ────────────────────────────────────────────────────────────────────

def _rejected_by(row: dict) -> Optional[str]:
    if row.get("status") != c.REJECTED:
        return None
    return c.DECIDED_BY_POLICY if row.get("decision_source") == c.DECIDED_BY_POLICY else c.DECIDED_BY_USER


def public(row: dict) -> dict:
    """The browser / CLI view of one request (the full stored tails, redacted)."""
    args = list(row.get("args") or [])
    _category, high_impact = policy.classify(row["program"], args)
    return {
        "request_id": row["request_id"], "ai_run_id": row["ai_run_id"], "doc_id": row["doc_id"],
        "project_id": row["project_id"], "group_id": row["group_id"],
        "program": row["program"], "args": args, "command": policy.display(row["program"], args),
        "cwd": row["cwd_relative"], "timeout_seconds": row["timeout_seconds"],
        "category": row["category"], "high_impact": high_impact, "policy": row["policy"],
        "provider_name": row.get("provider_name"), "status": row["status"],
        "decision_source": row.get("decision_source"), "rejected_by": _rejected_by(row),
        "decided_by": row.get("decided_by"), "decided_at": row.get("decided_at"),
        "started_at": row.get("started_at"), "finished_at": row.get("finished_at"),
        "duration_ms": row.get("duration_ms"), "exit_code": row.get("exit_code"),
        "timed_out": bool(row.get("timed_out")),
        "stdout_tail": legacy_adapter.redact(row.get("stdout_tail")),
        "stderr_tail": legacy_adapter.redact(row.get("stderr_tail")),
        "stdout_truncated": bool(row.get("stdout_truncated")),
        "stderr_truncated": bool(row.get("stderr_truncated")),
        "error_code": row.get("error_code"),
        "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        "anchor_seq": row.get("anchor_seq"), "anchor_position": row.get("anchor_position"),
        "anchor_state": row.get("anchor_state"),
    }


_STATUS_MESSAGES = {
    c.SUCCEEDED: "The command ran and exited with code 0.",
    c.TIMED_OUT: "The command was killed after reaching its timeout.",
    c.PENDING_APPROVAL: "Waiting for the user's approval.",
    c.APPROVED: "Approved; starting.",
    c.RUNNING: "Running.",
}
_CANCEL_MESSAGES = {
    c.REASON_CANCELLED_BY_USER: "The user cancelled this command before it completed.",
    c.REASON_APPROVAL_WAIT_EXPIRED: (
        "The user did not decide within the approval wait limit, so the request was withdrawn and "
        "nothing was run. You may request it again."
    ),
    c.REASON_RUN_BUDGET_EXHAUSTED: (
        "This AI run's remaining time was not enough to wait for or finish the command, so it was "
        "cancelled."
    ),
    c.REASON_RUN_FINISHED: "The AI run finished before the command completed, so it was cancelled.",
    c.REASON_RUN_CANCELLED: "The AI run was cancelled, so the command was cancelled.",
    c.REASON_SERVER_RESTARTED: "The server restarted before the command completed, so it was cancelled.",
}


def _message(row: dict) -> str:
    status = row["status"]
    if status == c.REJECTED:
        if _rejected_by(row) == c.DECIDED_BY_POLICY:
            return "The user's command policy refuses command execution; nothing was run."
        return "The user rejected this command; nothing was run."
    if status == c.CANCELLED:
        return _CANCEL_MESSAGES.get(row.get("error_code"), "The command was cancelled before it completed.")
    if status == c.FAILED:
        if row.get("exit_code") is not None:
            return f"The command ran and failed with exit code {row['exit_code']}."
        return "The command could not be started."
    return _STATUS_MESSAGES.get(status, status)


def _json_len(value) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _clip_text(text: str, limit: int) -> tuple[str, bool]:
    """Longest prefix whose JSON-escaped form (without quotes) fits ``limit``."""
    if _json_len(text) - 2 <= limit:
        return text, False
    low, high = 0, min(len(text), limit)
    while low < high:
        mid = (low + high + 1) // 2
        if _json_len(text[:mid]) - 2 <= limit:
            low = mid
        else:
            high = mid - 1
    return text[:low], True


def _clip_args(args: list[str], limit: int) -> tuple[list[str], bool]:
    """Leading args whose serialized list fits ``limit`` (the last one may be cut)."""
    if _json_len(args) <= limit:
        return list(args), False
    kept: list[str] = []
    for arg in args:
        if _json_len([*kept, arg]) <= limit:
            kept.append(arg)
            continue
        room = limit - _json_len([*kept, ""])
        if room > 0:
            part, _ = _clip_text(str(arg), room)
            if part:
                kept.append(part)
        break
    return kept, True


def _allocate_streams(out: str, err: str, budget: int) -> tuple[str, str]:
    """Tails of both streams within one shared budget (D0006 §3.5.3 rule 2)."""
    budget = max(0, int(budget))
    half = budget // 2

    def tail(text: str, size: int) -> str:
        return text[-size:] if size > 0 else ""

    if len(out) <= half and len(err) <= half:
        return out, err
    if len(out) <= half:
        return out, tail(err, budget - len(out))
    if len(err) <= half:
        return tail(out, budget - len(err)), err
    return tail(out, half), tail(err, budget - half)


def ai_result(row: dict) -> dict:
    """What the model gets back (D0006 §5.2), built to fit ``AI_RESULT_MAX_CHARS`` serialized.

    Item order is the serialization order; ``stdout``/``stderr`` come last. The whole
    ``json.dumps(result, ensure_ascii=False)`` stays within the API worker's tool-result
    cap so its safety-net cut never breaks this JSON (D0006 §3.5.3).
    """
    args = [str(a) for a in (row.get("args") or [])]
    limit = c.RESULT_META_FIELD_CHARS
    program, cut_program = _clip_text(str(row.get("program") or ""), limit)
    command, cut_command = _clip_text(policy.display(str(row.get("program") or ""), args), limit)
    shown_args, cut_args = _clip_args(args, limit)
    cwd, cut_cwd = _clip_text(str(row.get("cwd_relative") or ""), limit)
    full_out = legacy_adapter.redact(row.get("stdout_tail")) or ""
    full_err = legacy_adapter.redact(row.get("stderr_tail")) or ""
    status = row["status"]
    rejected_by = _rejected_by(row)

    def build(stream_budget: int) -> dict:
        out, err = _allocate_streams(full_out, full_err, stream_budget)
        out_cut = len(out) < len(full_out) or bool(row.get("stdout_truncated"))
        err_cut = len(err) < len(full_err) or bool(row.get("stderr_truncated"))
        message = _message(row)
        if out_cut or err_cut:
            message = f"{message} {_TRUNCATION_NOTE}"
        message, cut_message = _clip_text(message, limit)
        result: dict = {
            "ok": True,
            "request_id": row["request_id"],
            "status": status,
            "error_code": row.get("error_code"),
            "rejected": status == c.REJECTED,
        }
        if rejected_by is not None:
            result["rejected_by"] = rejected_by
        result.update({
            "cancelled": status == c.CANCELLED,
            "exit_code": row.get("exit_code"),
            "timed_out": bool(row.get("timed_out")),
            "duration_ms": row.get("duration_ms"),
            "message": message,
            "stdout_truncated": out_cut,
            "stderr_truncated": err_cut,
            "meta_truncated": bool(cut_program or cut_command or cut_args or cut_cwd or cut_message),
            "program": program,
            "command": command,
            "args": shown_args,
            "cwd": cwd,
            "stdout": out,
            "stderr": err,
        })
        return result

    budget = c.RESULT_STREAMS_TOTAL_CHARS
    result = build(budget)
    for _ in range(_RESULT_BUILD_ATTEMPTS):
        over = _json_len(result) - c.AI_RESULT_MAX_CHARS
        if over <= 0:
            return result
        budget = max(0, budget - over)
        result = build(budget)
    if _json_len(result) > c.AI_RESULT_MAX_CHARS:
        result = build(0)
    return result


def error_result(code: str, message: str) -> dict:
    """Error-shaped tool result (D0006 §3.3), held to the same caps as ``ai_result``.

    Input errors may echo what the model sent (an unknown field name, a program), so the
    code and message are clipped like meta fields and the whole serialized result stays
    within ``AI_RESULT_MAX_CHARS``; the worker's safety-net cut never breaks this JSON.
    """
    limit = c.RESULT_META_FIELD_CHARS
    code_text, cut_code = _clip_text(str(code or ""), limit)
    text, cut_message = _clip_text(str(message or ""), limit)
    result = {"ok": False, "op": TOOL_NAME, "error": {"code": code_text, "message": text},
              "meta_truncated": bool(cut_code or cut_message)}
    if _json_len(result) > c.AI_RESULT_MAX_CHARS:
        result["error"]["message"] = ""
    return result


# ── request / decision ───────────────────────────────────────────────────────

def resolve_policy(user_id: Optional[str]) -> str:
    """Read per request, so a policy switched mid-run applies to the next command."""
    try:
        value = legacy_adapter.resolve_command_policy(user_id)
    except Exception:
        logger.warning("chat command policy unavailable; using default", exc_info=True)
        return c.POLICY_USER_APPROVAL
    return value if value in c.COMMAND_POLICIES else c.POLICY_USER_APPROVAL


def create_request(run: dict, payload: object, *, token_id: Optional[str] = None) -> dict:
    """Validate, bind to the worktree, apply the user's policy and store. Returns the row."""
    _authorize_run(run)
    try:
        command = policy.validate_request(payload)
    except policy.CommandPolicyError as exc:
        raise c.ConversationError(422, exc.code, exc.message) from exc
    root = worktree_root(run["project_id"], run["group_id"], run.get("source_root"),
                         token_id=token_id or run.get("current_token_id") or run.get("token_id"))
    try:
        policy.resolve(command, root)
    except policy.CommandPolicyError as exc:
        raise c.ConversationError(422, exc.code, exc.message) from exc
    user_policy = resolve_policy(run.get("issued_to"))
    error_code = None
    decision_source: Optional[str] = c.DECIDED_BY_POLICY
    if user_policy == c.POLICY_REJECT:
        status = c.REJECTED
    elif chat_run_budget_sec(run) <= 0:
        # No time left to wait or run: closed at once, never pending (D0006 §3.5.2 rule 3).
        status, error_code, decision_source = c.CANCELLED, c.REASON_RUN_BUDGET_EXHAUSTED, None
    elif user_policy == c.POLICY_ALWAYS_APPROVE:
        status = c.APPROVED
    else:
        status, decision_source = c.PENDING_APPROVAL, None
    row = repo.insert_command(
        request_id=f"ccr_{uuid.uuid4().hex[:24]}", ai_run_id=run["run_id"], doc_id=run["doc_ref"],
        project_id=run["project_id"], group_id=run["group_id"],
        token_id=token_id or run.get("current_token_id") or run.get("token_id"),
        issued_to=run.get("issued_to"), provider_name=_provider_name(run),
        program=command.program, args=list(command.args), cwd_relative=command.cwd,
        timeout_seconds=command.timeout_seconds, category=command.category, policy=user_policy,
        status=status, decision_source=decision_source, error_code=error_code,
        run_start_seq=run.get("chat_start_seq"),
    )
    try:
        activity.resolve_run(row["doc_id"], row["ai_run_id"])
        row = repo.get_command(row["request_id"]) or row
    except Exception:
        logger.warning("chat command anchor resolve failed for %s", row["request_id"], exc_info=True)
    # Stored while the run was ending: re-check right after the insert (D0006 §3.7).
    try:
        _authorize_run(run)
    except c.ConversationError as exc:
        closed = repo.transition_command(row["request_id"], (c.PENDING_APPROVAL, c.APPROVED), c.CANCELLED,
                                         error_code=exc.code)
        _notify(closed)
        raise
    note_progress(run)
    _notify(row)
    if status == c.APPROVED:
        _start(row)
    return row


def decide(request_id: str, user: dict, decision: object) -> dict:
    """The user's approve / reject (and cancel of a pending, approved or running request)."""
    row = repo.get_command(request_id)
    if row is None:
        raise c.ConversationError(404, c.ERR_NOT_FOUND, "command request not found")
    if decision not in c.DECISIONS:
        raise c.ConversationError(422, c.ERR_INVALID_DECISION, "decision must be approve, reject or cancel")
    user_id = (user or {}).get("user_id")
    if not (user or {}).get("is_admin") and (not user_id or user_id != row.get("issued_to")):
        raise c.ConversationError(403, c.ERR_DECISION_FORBIDDEN,
                                  "only the user who started this AI run (or an admin) can decide its commands")
    fields = {"decision_source": c.DECIDED_BY_USER, "decided_by": user_id, "decided_at": _now_iso()}
    if decision in (c.DECISION_APPROVE, c.DECISION_REJECT) and row["status"] != c.PENDING_APPROVAL:
        raise c.ConversationError(409, c.ERR_ALREADY_DECIDED, f"request is {row['status']}")
    if decision == c.DECISION_APPROVE:
        # A run that finished, is being cancelled or is finalizing never gets an approval
        # (and so never a process): the request is closed instead.
        refusal = _live_refusal(row["ai_run_id"])
        if refusal is not None:
            closed = repo.transition_command(request_id, (c.PENDING_APPROVAL,), c.CANCELLED,
                                             error_code=refusal.code, **fields)
            _notify(closed)
            if closed is None:
                raise c.ConversationError(409, c.ERR_ALREADY_DECIDED,
                                          f"request is {(repo.get_command(request_id) or {}).get('status')}")
            raise refusal
        updated = repo.transition_command(request_id, (c.PENDING_APPROVAL,), c.APPROVED, **fields)
        if updated is None:
            raise c.ConversationError(409, c.ERR_ALREADY_DECIDED,
                                      f"request is {(repo.get_command(request_id) or {}).get('status')}")
        note_progress(_live_run(row["ai_run_id"]))
        _notify(updated)
        _start(updated)
        return updated
    if decision == c.DECISION_REJECT:
        updated = repo.transition_command(request_id, (c.PENDING_APPROVAL,), c.REJECTED, **fields)
        if updated is None:
            raise c.ConversationError(409, c.ERR_ALREADY_DECIDED,
                                      f"request is {(repo.get_command(request_id) or {}).get('status')}")
        note_progress(_live_run(row["ai_run_id"]))
        _notify(updated)
        return updated
    updated = _cancel(request_id, c.REASON_CANCELLED_BY_USER, fields)
    if updated is None:
        raise c.ConversationError(409, c.ERR_ALREADY_FINISHED,
                                  f"request is {(repo.get_command(request_id) or {}).get('status')}")
    note_progress(_live_run(row["ai_run_id"]))
    return updated


def _cancel(request_id: str, reason: str, fields: Optional[dict] = None) -> Optional[dict]:
    """Close an open request with ``reason``; a running one is killed and waited out.

    Returns the cancelled row, or None when the request was already terminal.
    """
    fields = dict(fields or {})
    updated = repo.transition_command(request_id, (c.PENDING_APPROVAL, c.APPROVED), c.CANCELLED,
                                      error_code=reason, **fields)
    if updated is not None:
        _notify(updated)
        return updated
    # Under the launch gate: a launch that has not passed its live check yet sees the
    # cancel and spawns nothing; one that has already holds a control by now.
    with _launch_gate((repo.get_command(request_id) or {}).get("ai_run_id")):
        with _lock:
            execution = _executions.get(request_id)
            if execution is not None:
                _cancel_requested.setdefault(request_id, reason)
    if execution is None:
        # No executor thread of this process owns the row: nothing can still write for it.
        updated = repo.transition_command(request_id, (c.RUNNING,), c.CANCELLED, error_code=reason, **fields)
        _notify(updated)
    else:
        if fields:
            with contextlib.suppress(Exception):
                repo.transition_command(request_id, (c.RUNNING,), c.RUNNING, **fields)
        _await_execution(request_id, execution)
    row = repo.get_command(request_id)
    return row if row and row["status"] == c.CANCELLED else None


def _await_execution(request_id: str, execution: threading.Event) -> None:
    """Kill the request's process and wait until its executor thread has cleaned up.

    No time cap: the executor's kill is bounded (Job Object / process group), and
    returning while the row is still ``running`` would let a command outlive its run and
    write after the run's end snapshot.
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


def cancel_for_run(run_id: str, reason: str = c.REASON_RUN_FINISHED) -> int:
    """Finalize: nothing of a finished run may stay open (D0006 §3.7).

    Returns once every command of the run is terminal and its process is gone, so the
    end snapshot taken right after includes everything the commands wrote.
    """
    _close_run(run_id, reason)
    closed = 0
    for row in repo.list_open_commands_for_run(run_id):
        try:
            if _cancel(row["request_id"], reason) is not None:
                closed += 1
        except Exception:
            logger.warning("chat command cancel failed for %s", row["request_id"], exc_info=True)
    with _lock:
        # Every execution of the run has finished; a late launch makes a fresh gate and
        # still finds the run closed.
        _launch_gates.pop(run_id, None)
    return closed


def stop_run(run_id: str, on_closed: Optional[Callable[[], None]] = None) -> None:
    """Run cancel, called BEFORE the run is announced cancelling.

    Closing the run takes its launch gate: a launch already past its live check has
    spawned and installed its control by then (it is killed here), every later one sees
    the run closed. ``on_closed`` (the announcement) runs under that gate. Pending rows
    are closed; finalization (``cancel_for_run``) waits for processes before the end snapshot.
    """
    _close_run(run_id, c.REASON_RUN_CANCELLED, on_closed)
    for row in repo.list_open_commands_for_run(run_id):
        request_id = row["request_id"]
        if row["status"] in (c.PENDING_APPROVAL, c.APPROVED):
            closed = repo.transition_command(request_id, (c.PENDING_APPROVAL, c.APPROVED), c.CANCELLED,
                                             error_code=c.REASON_RUN_CANCELLED)
            if closed is not None:
                _notify(closed)
                continue
        with _lock:
            if request_id not in _executions:
                continue
            _cancel_requested.setdefault(request_id, c.REASON_RUN_CANCELLED)
            control = _controls.get(request_id)
        if control is not None:
            control[1].set()
            try:
                control[0].cancel()
            except Exception:
                logger.debug("chat command cancel signal failed for %s", request_id, exc_info=True)


def recover() -> int:
    """Startup: rows a dead server process left open are closed, never resumed."""
    closed = 0
    for row in repo.list_open_commands():
        updated = repo.transition_command(row["request_id"], c.OPEN_STATUSES, c.CANCELLED,
                                          error_code=c.REASON_SERVER_RESTARTED)
        if updated:
            closed += 1
    return closed


# ── execution ────────────────────────────────────────────────────────────────

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
        _cancel_requested.pop(request_id, None)
    execution.set()


def _execute(request_id: str, expected_root: Optional[str],
             execution: Optional[threading.Event] = None) -> None:
    try:
        _execute_owned(request_id, expected_root)
    finally:
        # Only now -- process killed, Job Object closed, row terminal -- may a cancel return.
        if execution is not None:
            _finish_execution(request_id, execution)


def _cancel_reason(request_id: str, ai_run_id: str) -> Optional[str]:
    with _lock:
        reason = _cancel_requested.get(request_id)
    if reason:
        return reason
    closed = _run_closed(ai_run_id)
    if closed:
        return closed
    run = _live_run(ai_run_id) or {}
    cancel_event = run.get("cancel_event")
    if run.get("status") == "cancelling" or (cancel_event is not None and cancel_event.is_set()):
        return c.REASON_RUN_CANCELLED
    return None


def _stream_was_cut(text: Optional[str]) -> bool:
    # The executor keeps the last 64 KiB; a decode may drop up to 3 bytes of a split char.
    return bool(text) and len(text.encode("utf-8")) >= _EXECUTOR_TAIL_BYTES - 3


def _execute_owned(request_id: str, expected_root: Optional[str]) -> None:
    row = repo.transition_command(request_id, (c.APPROVED,), c.RUNNING, started_at=_now_iso())
    if row is None:
        return
    note_progress(_live_run(row["ai_run_id"]))
    _notify(row)
    started = time.monotonic()
    control = None
    runtime: Optional[Path] = None
    cancelled = threading.Event()
    final: Optional[dict] = None
    executor = legacy_adapter.executor()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        # Re-verified right before the process exists: the approval may have waited long.
        root = worktree_root(row["project_id"], row["group_id"], expected_root, token_id=row.get("token_id"))
        command = policy.ChatCommand(row["program"], tuple(row["args"]), row["cwd_relative"],
                                     int(row["timeout_seconds"]), row["category"], False)
        resolved, cwd, path_value = policy.resolve(command, root)
        runtime = Path(tempfile.mkdtemp(prefix=f"flowgate-{request_id}-"))
        env = policy.environment(path_value, runtime)
        # Last gate before a process exists. The check, the spawn and the control install
        # happen under the run's launch gate, so no cancellation begins in between.
        with _launch_gate(row["ai_run_id"]):
            refusal_code = None
            refusal = _live_refusal(row["ai_run_id"])
            if refusal is not None:
                refusal_code = refusal.code
            with _lock:
                if refusal_code is None and request_id in _cancel_requested:
                    refusal_code = _cancel_requested[request_id]
            if refusal_code is None:
                control, _ownership = executor.spawn(
                    [resolved.executable, *resolved.argv_prefix, *command.args], cwd, env,
                    command.timeout_seconds, request_id,
                )
                with _lock:
                    _controls[request_id] = (control, cancelled)
        if refusal_code is not None:
            final = repo.transition_command(request_id, (c.RUNNING,), c.CANCELLED,
                                            error_code=refusal_code, duration_ms=elapsed_ms())
            return
        # Defensive: a run stop that bypassed the gate (cancel_event set directly).
        early_reason = _cancel_reason(request_id, row["ai_run_id"])
        if early_reason:
            cancelled.set()
            control.cancel()
        result = executor.wait(control, command.timeout_seconds, cancelled)
        reason = _cancel_reason(request_id, row["ai_run_id"]) or early_reason
        if result.cancelled or reason:
            status = c.CANCELLED
        elif result.timed_out:
            status = c.TIMED_OUT
        elif result.exit_code == 0:
            status = c.SUCCEEDED
        else:
            status = c.FAILED
        stdout = legacy_adapter.redact(result.stdout_tail)
        stderr = legacy_adapter.redact(result.stderr_tail)
        final = repo.transition_command(
            request_id, (c.RUNNING,), status, exit_code=result.exit_code, timed_out=result.timed_out,
            stdout_tail=stdout, stderr_tail=stderr,
            stdout_truncated=_stream_was_cut(result.stdout_tail),
            stderr_truncated=_stream_was_cut(result.stderr_tail),
            duration_ms=elapsed_ms(),
            error_code=(reason or c.REASON_RUN_CANCELLED) if status == c.CANCELLED else None,
        )
    except c.ConversationError as exc:
        final = repo.transition_command(request_id, (c.RUNNING,), c.FAILED, error_code=exc.code,
                                        stderr_tail=exc.message, duration_ms=elapsed_ms())
    except policy.CommandPolicyError as exc:
        final = repo.transition_command(request_id, (c.RUNNING,), c.FAILED, error_code=exc.code,
                                        stderr_tail=exc.message, duration_ms=elapsed_ms())
    except Exception as exc:
        if isinstance(exc, getattr(executor, "OwnershipError", ())):
            final = repo.transition_command(request_id, (c.RUNNING,), c.FAILED,
                                            error_code=c.REASON_PROCESS_OWNER_UNAVAILABLE,
                                            duration_ms=elapsed_ms())
        else:
            logger.exception("chat command %s failed", request_id)
            final = repo.transition_command(request_id, (c.RUNNING,), c.FAILED,
                                            error_code=c.REASON_INTERNAL_ERROR,
                                            stderr_tail=str(exc)[:500], duration_ms=elapsed_ms())
    finally:
        if control is not None:
            with contextlib.suppress(Exception):
                control.close()
        with _lock:
            _controls.pop(request_id, None)
        if runtime is not None:
            shutil.rmtree(runtime, ignore_errors=True)
        note_progress(_live_run((final or row)["ai_run_id"]))
        _notify(final or repo.get_command(request_id))


# ── waiting / transports ─────────────────────────────────────────────────────

WAIT_TERMINAL = "terminal"
WAIT_DEADLINE = "deadline"
WAIT_BUDGET = "budget"
WAIT_RUN_CANCELLED = "run_cancelled"


def wait_for(request_id: str, max_wait: float, run: Optional[dict] = None) -> tuple[Optional[dict], str]:
    """Block until the row is terminal, ``max_wait`` passes, the run's budget is used up
    or the run is cancelled. Returns (row, why).

    The budget is recomputed at every check (never once at the start), and while the
    row is pending or running each check notes run progress (D0006 §3.5.1, §3.6).
    """
    started = time.monotonic()
    event = _event(request_id)
    cancel_event = (run or {}).get("cancel_event")
    row = repo.get_command(request_id)
    while row is not None and row["status"] not in c.TERMINAL_STATUSES:
        if run is not None and row["status"] in (c.PENDING_APPROVAL, c.RUNNING):
            note_progress(run)
        left = float(max_wait) - (time.monotonic() - started)
        if run is not None:
            budget = chat_run_budget_sec(run)
            if budget <= 0:
                return row, WAIT_BUDGET
            left = min(left, budget)
        if left <= 0:
            return row, WAIT_DEADLINE
        if cancel_event is not None and cancel_event.is_set():
            return row, WAIT_RUN_CANCELLED
        event.wait(min(c.WAIT_POLL_SEC, left))
        event.clear()
        row = repo.get_command(request_id)
    return row, WAIT_TERMINAL


def run_tool(run: dict, tool_input: object) -> tuple[int, dict]:
    """API-provider tool entry: create, wait in this same run, return the result.

    Input / policy / worktree errors come back as an error-shaped tool result and never
    fail the run (D0006 §3.3). Rejections, expiries and cancels are normal results.
    """
    try:
        row = create_request(run, tool_input)
    except c.ConversationError as exc:
        return exc.status, error_result(exc.code, exc.message)
    request_id = row["request_id"]
    if row["status"] == c.PENDING_APPROVAL:
        # Decided once, right after the creation's progress note (D0006 §3.5.2 rule 2).
        approval_window = min(c.APPROVAL_WAIT_MAX_SEC, chat_run_budget_sec(run))
        row, why = wait_for(request_id, approval_window, run)
        if row is not None and row["status"] == c.PENDING_APPROVAL:
            reason = {
                WAIT_BUDGET: c.REASON_RUN_BUDGET_EXHAUSTED,
                WAIT_RUN_CANCELLED: c.REASON_RUN_CANCELLED,
            }.get(why, c.REASON_APPROVAL_WAIT_EXPIRED)
            withdrawn = repo.transition_command(request_id, (c.PENDING_APPROVAL,), c.CANCELLED,
                                                error_code=reason)
            _notify(withdrawn)
            row = withdrawn or repo.get_command(request_id)
    if row is not None and row["status"] not in c.TERMINAL_STATUSES:
        row, why = wait_for(request_id, int(row["timeout_seconds"]) + c.RUNNING_GRACE_SEC, run)
        if row is not None and row["status"] not in c.TERMINAL_STATUSES:
            reason = c.REASON_RUN_CANCELLED if why == WAIT_RUN_CANCELLED else c.REASON_RUN_BUDGET_EXHAUSTED
            row = _cancel(request_id, reason) or repo.get_command(request_id)
    return 200, ai_result(row)


def cli_wait_seconds(value: object) -> float:
    """CLI long-poll request value: default when missing / negative / not a number, capped."""
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return float(c.CLI_WAIT_DEFAULT_SEC)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return float(c.CLI_WAIT_DEFAULT_SEC)
    return float(min(value, c.CLI_WAIT_MAX_SEC))


def cli_payload(row: dict) -> dict:
    """CLI response: request view, terminal flag and (when terminal) the AI result."""
    terminal = row["status"] in c.TERMINAL_STATUSES
    return {"ok": True, "request": public(row), "terminal": terminal,
            "result": ai_result(row) if terminal else None}


def _cli_wait(row: dict, run: dict, requested: object) -> dict:
    # One long-poll: min(request, CLI cap, run budget); answered at once when <= 0.
    wait = min(cli_wait_seconds(requested), chat_run_budget_sec(run))
    if wait <= 0:
        return repo.get_command(row["request_id"]) or row
    waited, _why = wait_for(row["request_id"], wait, run)
    return waited or row


def cli_create(run: dict, token: dict, body: dict) -> dict:
    payload = dict(body or {})
    requested = payload.pop("wait_seconds", c.CLI_WAIT_DEFAULT_SEC)
    row = create_request(run, payload, token_id=token.get("token_id"))
    return cli_payload(_cli_wait(row, run, requested))


def cli_read(run: dict, request_id: str, wait: object) -> dict:
    row = repo.get_command(request_id)
    if row is None or row["ai_run_id"] != run.get("run_id"):
        raise c.ConversationError(404, c.ERR_NOT_FOUND, "command request not found")
    return cli_payload(_cli_wait(row, run, wait))


def cli_mention_section(api_base: str, raw_token: str) -> str:
    """How a CLI chat worker runs commands through FlowGate (added to the chat mention)."""
    lines = [
        "## Command execution",
        "---",
        "Run tests, builds, linters and git commands for this conversation through FlowGate so",
        "they execute in this group's worktree under the user's command policy (run at once,",
        "wait for the user's approval, or refuse). API tool-calling runs use the `run_command`",
        "tool instead of this endpoint.",
        "",
        f"POST {api_base}/chat-commands",
        f"Authorization: Bearer {raw_token}",
        "",
        '{"program": "pytest", "args": ["-q", "server/tests/test_x.py"], "cwd": ".",',
        ' "timeout_seconds": 300, "wait_seconds": 60}',
        "",
        "program is the executable name only and args are separate strings: no shell, no",
        "pipes/redirection/&&, no inline `python -c` / `node -e`. cwd is relative to the worktree.",
        "When the response has terminal=false (waiting for approval, or still running), keep",
        f"calling GET {api_base}/chat-commands/<request_id>?wait=60 with the same Authorization until",
        "terminal=true. result carries status, error_code, exit_code, stdout, stderr, timed_out,",
        "rejected and rejected_by. A rejected command was not run: do not resend it unchanged;",
        "continue with what you have and say so in your reply. Running commands does not consume",
        "this token, so finish the commands you need before you submit your turn.",
    ]
    return "\n".join(lines)


def tool_descriptor() -> dict:
    """The ``run_command`` capability as a chat tool (``chat_run_hooks.PROVIDE_CHAT_TOOLS``)."""
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "schema": TOOL_SCHEMA,
        "handler": run_tool,
        "cli_mention": cli_mention_section,
        "prompt_hint": TOOL_PROMPT_HINT,
    }

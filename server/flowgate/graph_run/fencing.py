"""0061 T#7: late results are fenced — a Stop blocks them from the moment ``stopping`` commits.

A write or result an attempt token brings (remote source write / remove, a chat command, a document registration and
the result binder, the result stage, a review / rework hop) is taken only while

(a) its run is ``running`` or ``paused`` (``WRITABLE``) — ``stopping``, ``idle`` and any other status refuse — with
    the two idle exceptions the 0017-TR question 439 settled (``IDLE_EXCEPTED``: running, paused or idle): a person's
    registration with the node's own token (no AI run on it; L0009 E-23, ``human_registration``) — its inbox
    storage and its result binding alike — and the hop of a person's explicit review request
    (``graph_execution_hooks.explicit_request``), which then takes the run back to running. ``stopping`` and an
    invalidated attempt refuse them as any other;
(b) its attempt is active — and, for a step that comes back from a job or an AI run of one attempt (an instruction's
    record or approval, the result stage, an AI failure's record), that attempt is still the node's active one;
(c) for an execution's own report (the ai_invoke finalize hand-off, a review verdict) the execution is ``running``,
    its lease token is the current one, and the run is ``running`` or ``paused``. A report that cannot show its live
    execution is refused.

The engine's own start of a new execution (``request_write`` when a person's approval opens the next node of an idle
run) is no attempt token's write: it refuses only a run that is not running, paused or idle (``OPENING``); the
execution itself then needs ``execution_store.eligible`` (running / paused) to acquire.

The Stop gate: the side effects of an attempt token (a source write, a document registration or verdict and its
commit, a chat command's creation or spawn) run under the shared hold of the Flow's gate (``hold``), judged again
once the hold is taken; the Stop's ``stopping`` transaction takes the gate exclusively (``exclusive``). So a side
effect either completes before ``stopping`` commits or is judged after it and refused — nothing of an old attempt
starts once the run is stopping. The same exclusive hold serializes the runtime's own invalidation of an attempt (a
failed worktree, ``workspace._failed``): no side effect of the attempt's token runs across that commit either. The
run-end stage of an execution (the instruction's automatic approval and the result stage, the result's automatic
approval) runs under the shared hold too, judged for the attempt the execution was made for (``node_hold``). Lock
order: the gate, then a Git lock, then the Flow guard; the gate is never awaited inside a Flow guard, and the Stop
never takes a Git lock while it holds the gate.

A refusal is 409 ``graph_attempt_inactive`` (``GraphAttemptInactive``) or, on the internal paths that answer nobody, a
skipped step. Either way it leaves a run event (``cause`` ``attempt_inactive``, the run status unchanged) and never
moves a node or the run candidates. A refusal judged under a lock is recorded after the lock is released; one judged
inside a Flow guard (``check``, ``refuse``) is recorded after that guard rolled back (``guarded``, ``recorded``).
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Optional

from modules.flow_gate.db.connection import in_transaction
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.errors import FlowLockTimeout
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard, FLOW_LOCK_WAIT_SEC
from modules.flow_gate.services import graph_source_hooks as port

log = logging.getLogger(__name__)

CAUSE = "attempt_inactive"
WRITABLE = ("running", "paused")
REPORTING = ("running", "paused")
OPENING = ("running", "paused", "idle")
IDLE_EXCEPTED = ("running", "paused", "idle")          # Q439: a person's own-token registration, an explicit review hop
HUMAN_REGISTRATIONS = ("inbox_new", "inbox_edit", "result_inbox_registered")
_STORED = {"stopped": "paused", "completed": "idle"}      # rows written before migration 151


def _status(value) -> str:
    return _STORED.get(value, value)


# ── the Stop gate ─────────────────────────────────────────────────────────────

class _Gate:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.readers = 0
        self.closing = False


_gates: dict[str, _Gate] = {}
_gates_lock = threading.Lock()
_local = threading.local()


def _gate(flow_id: str) -> _Gate:
    with _gates_lock:
        gate = _gates.get(flow_id)
        if gate is None:
            gate = _gates[flow_id] = _Gate()
        return gate


def _held() -> dict:
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = {}
    return held


@contextlib.contextmanager
def shared(flow_id: str):
    """The shared hold of the Flow's gate: no ``stopping`` commits while it is held. Re-entrant in a thread."""
    held = _held()
    if held.get(flow_id):
        held[flow_id] += 1
        try:
            yield
        finally:
            held[flow_id] -= 1
        return
    gate, deadline = _gate(flow_id), time.monotonic() + FLOW_LOCK_WAIT_SEC
    with gate.cond:
        while gate.closing:
            left = deadline - time.monotonic()
            if left <= 0:
                raise FlowLockTimeout(flow_id)
            gate.cond.wait(left)
        gate.readers += 1
    held[flow_id] = 1
    try:
        yield
    finally:
        held.pop(flow_id, None)
        with gate.cond:
            gate.readers -= 1
            gate.cond.notify_all()


@contextlib.contextmanager
def exclusive(flow_id: str, *, own_ok: bool = False):
    """The Stop's hold: waits out every side effect in flight and lets no new one start until released. ``own_ok``
    (the runtime's invalidation of an attempt, which may run under a side effect of this thread — an approval that
    opened the next node): this thread's own shared holds are not waited for; another closing hold is not waited
    for either (it would wait for this thread) — ``FlowLockTimeout`` at once."""
    own = _held().get(flow_id, 0)
    if own and not own_ok:
        raise RuntimeError("the Stop gate is held shared by this thread")
    gate, deadline = _gate(flow_id), time.monotonic() + FLOW_LOCK_WAIT_SEC
    with gate.cond:
        while gate.closing:
            left = deadline - time.monotonic()
            if left <= 0 or own:
                raise FlowLockTimeout(flow_id)
            gate.cond.wait(left)
        gate.closing = True
        try:
            # readers counts threads, while own counts this thread's nesting depth.
            while gate.readers > (1 if own else 0):
                left = deadline - time.monotonic()
                if left <= 0:
                    raise FlowLockTimeout(flow_id)
                gate.cond.wait(left)
        except BaseException:
            gate.closing = False
            gate.cond.notify_all()
            raise
    try:
        yield
    finally:
        with gate.cond:
            gate.closing = False
            gate.cond.notify_all()


# ── the judgement ─────────────────────────────────────────────────────────────

def attempt_row(attempt_id) -> Optional[dict]:
    return run_store._one(
        "SELECT a.attempt_id, a.run_id, a.node_key, a.status AS attempt_status, r.group_id, r.status AS run_status "
        "FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id WHERE a.attempt_id = ?", [attempt_id])


def reason(row: Optional[dict], statuses=WRITABLE) -> Optional[str]:
    """Why the attempt may not write now, or None."""
    if not row:
        return "attempt_not_found"
    if _status(row["run_status"]) not in statuses:
        return "run_" + str(row["run_status"])
    if row["attempt_status"] != "active":
        return "attempt_" + str(row["attempt_status"])
    return None


def record(row: dict, operation: str, why: str, **extra) -> None:
    """The refusal's run event: the run status unchanged, no node event, no SSE. Inside the caller's guard when there
    is one (the refusal then commits with it), else in its own. A failed record never turns a refusal into an
    acceptance."""
    from modules.flow_gate.graph_workflow.services import run_events

    def write() -> None:
        run = run_store.run_by_id(row["run_id"])
        if run is None:
            return
        seq = int(run["last_event_seq"]) + 1
        payload = {"flow_id": run["group_id"], "run_id": run["run_id"], "revision_no": run.get("revision_no"),
                   "event_seq": seq, "from": run["status"], "to": run["status"], "cause": CAUSE, "reason": why,
                   "operation": operation, "attempt_id": row.get("attempt_id"), "node_key": row.get("node_key"),
                   **extra}
        run_store.append_event(run["run_id"], seq, run_events.RUN_STATUS, payload, node_key=None,
                               from_status=run["status"], to_status=run["status"], cause=CAUSE, result_doc_id=None,
                               canvas_state=None, actor_user_id=None, reason=why)

    try:
        if in_transaction():
            write()
        else:
            with_flow_guard(row["group_id"], write)
    except Exception:  # noqa: BLE001
        log.exception("Graph fence event not recorded run=%s operation=%s", row.get("run_id"), operation)


def refusal(attempt_id, operation: str, *, statuses=WRITABLE, **extra) -> Optional[str]:
    """(a)(b) for one attempt; a refusal is recorded."""
    row = attempt_row(attempt_id)
    why = reason(row, statuses)
    if why and row:
        record(row, operation, why, **extra)
    return why


def _execution_reason(ai_run_id: str, attempt_id: str) -> Optional[str]:
    """(c) for a report made with an AI run's token: that run is live in this process, its current Graph execution
    runs on this attempt and holds the current lease token. Anything it cannot show refuses."""
    from modules.flow_gate.services import ai_invoke_service
    from flowgate.graph_run import execution_store as ledger
    live = ai_invoke_service.get_run_record(ai_run_id)
    if not live:
        return "execution_not_live"
    exec_id = live.get("graph_exec_id")
    if not exec_id:
        return "execution_missing"
    row = ledger.get(exec_id)
    if not row or row["attempt_id"] != attempt_id:
        return "execution_mismatch"
    if row["status"] != "running":
        return "execution_" + str(row["status"])
    if not live.get("graph_lease_token") or live["graph_lease_token"] != row["lease_token"]:
        return "lease_token_stale"
    return None


def human_registration(token: Optional[dict], operation: str) -> bool:
    """Q439 / L0009 E-23: a registration (inbox new / edit, its result binding) a person makes with the node's own
    token — one no AI run was issued with (the node's dispatch token). It is taken on an idle run too."""
    return operation in HUMAN_REGISTRATIONS and bool(token) and not str(token.get("ai_run_id") or "").strip()


def _token_reason(token: dict, attempt_id: str, report: bool, operation: str = "") -> tuple[Optional[str], dict]:
    statuses = REPORTING if report else (IDLE_EXCEPTED if human_registration(token, operation) else WRITABLE)
    why = reason(attempt_row(attempt_id), statuses)
    if why is None:
        # T#8: the result reservation outlives a failed Git/DB attempt. Once frozen,
        # token writes may not change the result being recovered on a retry.
        result = run_store._one("SELECT result_commit, integration_status FROM graph_node_attempts "
                                "WHERE attempt_id = ?", [attempt_id]) or {}
        if result.get("result_commit") or result.get("integration_status") in (
                "pending", "integrating", "integrated", "conflict", "failed"):
            return "attempt_result_frozen", {}
    if why is None and report and token.get("ai_run_id"):
        why = _execution_reason(str(token["ai_run_id"]), attempt_id)
        return why, {"ai_run_id": token.get("ai_run_id")}
    return why, {}


def require_token(token: dict, operation: str, *, report: bool = False, keep=None) -> None:
    """``graph_source_hooks.require_writable`` handler: an attempt token at an HTTP boundary (remote write, chat
    command, inbox registration; ``report``: a review verdict, the execution's own report). The judgement is made
    under the shared hold of the Flow's gate; ``keep`` (the caller's ``ExitStack``, ``graph_source_hooks
    .write_session``) keeps that hold until the caller's side effects have committed. A refusal releases the hold,
    is recorded and raises."""
    attempt_id = port.binding(token)
    if not attempt_id:
        return
    locale = token.get("continuation_locale") or "ko"
    row = attempt_row(attempt_id)
    if not row:
        raise port.GraphAttemptInactive(locale=locale, reason="attempt_not_found", attempt_id=attempt_id,
                                        operation=operation)
    with contextlib.ExitStack() as stack:
        stack.enter_context(shared(row["group_id"]))
        why, extra = _token_reason(token, attempt_id, report, operation)
        if why is None:
            if keep is not None:
                keep.enter_context(_transfer(stack.pop_all()))
            return
    record(attempt_row(attempt_id) or row, operation, why, **extra)
    raise port.GraphAttemptInactive(locale=locale, reason=why, attempt_id=attempt_id, operation=operation)


@contextlib.contextmanager
def _transfer(stack: contextlib.ExitStack):
    with stack:
        yield


@contextlib.contextmanager
def hold(attempt_id, operation: str, *, statuses=WRITABLE, before=None):
    """The shared hold for an attempt's side effect, judged (a)(b) under it. ``before`` (a context manager factory)
    is entered after the gate — a Git lock. A refusal leaves both, is recorded and raises; else the body runs
    holding both."""
    row = attempt_row(attempt_id)
    if not row:
        raise port.GraphAttemptInactive(reason="attempt_not_found", attempt_id=attempt_id, operation=operation)
    refused: Optional[str] = None
    with shared(row["group_id"]):
        with (before() if before else contextlib.nullcontext()):
            refused = reason(attempt_row(attempt_id), statuses)
            if refused is None:
                yield
    if refused is not None:
        record(attempt_row(attempt_id) or row, operation, refused)
        raise port.GraphAttemptInactive(reason=refused, attempt_id=attempt_id, operation=operation)


def require(attempt_id, operation: str, *, statuses=WRITABLE, **extra) -> None:
    """(a)(b), else ``GraphAttemptInactive`` (also an ``ExecutionLeaseLost``). Outside a guard: the refusal is
    recorded in its own guard before the caller's step could roll it back."""
    why = refusal(attempt_id, operation, statuses=statuses, **extra)
    if why:
        raise port.GraphAttemptInactive(reason=why, attempt_id=attempt_id, operation=operation)


def refuse(attempt_id, operation: str, why: str, *, row: Optional[dict] = None, **extra) -> Exception:
    """The ``GraphAttemptInactive`` of a refusal judged on a step's way (inside its guard or not). Outside a guard it
    is recorded now; inside one, recording would roll back with the step, so the exception carries it and the caller
    records it once the guard is left (``guarded``, ``recorded``)."""
    exc = port.GraphAttemptInactive(reason=why, attempt_id=attempt_id, operation=operation)
    row = row or (attempt_row(attempt_id) if attempt_id else None)
    if row:
        if in_transaction():
            exc.graph_unrecorded = (dict(row), operation, why, dict(extra))
        else:
            record(row, operation, why, **extra)
    return exc


def recorded(exc: BaseException) -> None:
    """After the guard a refusal was judged in has rolled back: record it (once). Anything else: nothing."""
    pending = getattr(exc, "graph_unrecorded", None)
    if not pending or in_transaction():
        return
    exc.graph_unrecorded = None
    row, operation, why, extra = pending
    record(row, operation, why, **extra)


def guarded(flow_id: str, body):
    """``with_flow_guard`` for a step that judges the fence inside: a refusal it raised is recorded after the
    rollback, then raised on."""
    try:
        return with_flow_guard(flow_id, body)
    except port.GraphAttemptInactive as exc:
        recorded(exc)
        raise


def check(attempt_id, operation: str, *, statuses=WRITABLE) -> None:
    """(a)(b) again inside the step's own guard, after ``require``: raises; the refusal is recorded once the guard is
    left (``guarded`` / ``recorded``) — or at once, outside a guard."""
    row = attempt_row(attempt_id)
    why = reason(row, statuses)
    if why:
        raise refuse(attempt_id, operation, why, row=row)


def _node_judgement(run_id: str, node_key: str, attempt_id: Optional[str],
                    statuses=WRITABLE) -> Optional[tuple[Optional[dict], str, dict]]:
    """None when the node step may write, else (the row its refusal is recorded on, why, extra)."""
    active = run_store.active_attempt_id(run_id, node_key)
    if attempt_id and attempt_id != active:
        row = attempt_row(attempt_id)
        why = (reason(row, statuses) or "attempt_superseded") if row else "attempt_not_found"
        return row, why, {"active_attempt_id": active}
    if active:
        row = attempt_row(active)
        why = reason(row, statuses)
        return (row, why, {}) if why else None
    run = run_store.run_by_id(run_id)
    if run is None or _status(run["status"]) in statuses:
        return None
    return ({"run_id": run_id, "group_id": run["group_id"], "attempt_id": None, "node_key": node_key},
            "run_" + str(run["status"]), {})


def node_refused(run_id: str, node_key: str, operation: str, attempt_id: Optional[str] = None) -> bool:
    """Before a node step of the engine writes (the result stage, an instruction's record or approval, an AI
    failure's record), inside its guard or not: ``attempt_id`` — the attempt the job or AI run that asks was made
    for — must still be the node's active attempt (an old attempt's late callback never moves a node opened again),
    and that attempt may write (a)(b). A node with no active attempt: its run must be one that may. A node opened
    before migration 151 with no attempt keeps the engine's own checks."""
    found = _node_judgement(run_id, node_key, attempt_id)
    if found is None:
        return False
    row, why, extra = found
    if row:
        record(row, operation, why, **extra)
    return True


@contextlib.contextmanager
def node_hold(run_id: str, node_key: str, attempt_id: Optional[str], operation: str):
    """The run-end stage's side effect for the attempt ``attempt_id`` an execution was made for (the instruction's
    automatic approval and the result stage, the result's automatic approval): judged as ``node_refused`` under the
    shared hold of the Flow's gate, kept until the body (its approval and commits) is over — so no ``stopping``
    commits between the judgement and the approval. Yields True to go on, False when refused (the refusal is
    recorded after the hold is released). Outside every Flow guard."""
    run = run_store.run_by_id(run_id)
    if run is None:
        yield False
        return
    found = None
    with shared(run["group_id"]):
        found = _node_judgement(run_id, node_key, attempt_id)
        if found is None:
            yield True
            return
    row, why, extra = found
    if row:
        record(row, operation, why, **extra)
    yield False


def execution_attempt(exec_id: Optional[str]) -> Optional[str]:
    """The attempt an execution (``graph_exec_id`` of an AI run) was made for."""
    if not exec_id:
        return None
    from flowgate.graph_run import execution_store as ledger
    return (ledger.get(exec_id) or {}).get("attempt_id")


def token_refused(token_id: Optional[str], operation: str) -> bool:
    """Inside the result binder's guard: a registration made with a node token whose attempt or run may not take it.
    A person's registration with the node's own token is taken on an idle run too (``human_registration``)."""
    if not token_id:
        return False
    row = run_store._one("SELECT attempt_id FROM graph_run_token_map WHERE token_id = ?", [token_id])
    if not row or not row.get("attempt_id"):
        return False
    token = run_store._one("SELECT token_id, ai_run_id FROM tokens WHERE token_id = ?", [token_id])
    statuses = IDLE_EXCEPTED if human_registration(token, operation) else WRITABLE
    return refusal(row["attempt_id"], operation, statuses=statuses, token_id=token_id) is not None


def run_refused(run: dict, operation: str, **extra) -> bool:
    """Inside a guard: a run that takes no result now (stopping). A person's decision on an idle run (its approval
    wait) is no attempt token's write and goes on. Recorded."""
    if _status(run.get("status")) in OPENING:
        return False
    record({"run_id": run["run_id"], "group_id": run["group_id"], "attempt_id": None, "node_key": None},
           operation, "run_" + str(run.get("status")), **extra)
    return True


def report_refused(run: dict) -> Optional[str]:
    """(c) for the AI run ``run`` finalizing, before its run-end hand-off: why its report may not reach the Graph."""
    exec_id = run.get("graph_exec_id")
    if not exec_id:
        return None
    from flowgate.graph_run import execution_store as ledger
    row = ledger.get(exec_id)
    if not row:
        return "execution_missing"
    attempt = attempt_row(row["attempt_id"])
    if row["status"] != "running":
        why = "execution_" + str(row["status"])
    elif not run.get("graph_lease_token") or run["graph_lease_token"] != row["lease_token"]:
        why = "lease_token_stale"
    else:
        why = reason(attempt, REPORTING)
    if why and attempt:
        record(attempt, "finalize", why, exec_id=exec_id, ai_run_id=run.get("run_id"))
    return why

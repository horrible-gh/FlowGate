"""The Graph Time Machine operation journal (0061 T#13) — the run event log, as T#12's contract fixed it.

Each phase of one operation is a ``graph_run_status_changed`` event of the run (status unchanged, cause
``graph_time_machine``) whose payload carries ``operation_id`` and ``operation_status``:

    started         in the deciding Flow guard, with the ``plan`` (the targets: node keys, attempts, documents and
                    the integration commits to revert). From this commit on the Flow refuses step / full commands,
                    edits and other Time Machine requests (``repository.incomplete_operations``).
    git_applied     the reverts are on the group branch (``reverts``: per integration commit, its revert or empty).
    completed       the DB rewind committed, in the rewind's own guard.
    blocked         nothing of the operation is left: Git refused, or every revert it made was compensated.
    partial_failed  Git and the DB disagree (the DB rewind failed after Git, or a compensation failed). Commands and
                    Time Machine requests stay refused until the server start finishes the operation (``operation``).

``tr_history_recovery`` (0648) is the same idea — a phase per Git/DB boundary, the pre-Git HEAD, the Git commit, a
resolution of ledger_completed / compensated — but cannot hold a Graph operation: its ``operation`` CHECK admits
``reapply`` only, one row is one document with one ``source_ledger_row_id`` (NOT NULL), and every unresolved row is
taken by the legacy ``recover_tr_history``, which would mark a Graph row ``recovery_required`` and block the legacy
cancel / reapply of the group. ``revert_pending`` is a ledger reading of reopened TR2 documents, not a journal. The run
event log already is the durable, ordered, Flow-scoped record T#12 reads, so no table or migration is added; the
phase vocabulary and the recovery rule (a recorded Git result -> finish the DB step, none -> from the start, an
unprovable state stays blocked) follow tr_history_recovery.
"""
from __future__ import annotations

import json
import secrets
from typing import Optional

from modules.flow_gate.graph_workflow.services import run_events
from modules.flow_gate.graph_workflow.store import run_store

from .repository import OPERATION_CAUSE, incomplete_operations

STARTED, GIT_APPLIED, COMPLETED = "started", "git_applied", "completed"
BLOCKED, PARTIAL_FAILED = "blocked", "partial_failed"
OPERATION_PREFIX = "gtm_"


def new_operation_id() -> str:
    return OPERATION_PREFIX + secrets.token_hex(12)


def emit(ctx, operation_id: str, status: str, **extra) -> int:
    """Inside a Flow guard: one journal entry, published after the commit as the run's status event (the screen
    reads the run again)."""
    run = ctx.run
    seq = int(run["last_event_seq"]) + 1
    payload = run_events.run_status_payload(run, seq, run["status"], run["status"], OPERATION_CAUSE, ctx.actor,
                                            status)
    payload.update(extra)
    payload.update(kind=OPERATION_CAUSE, operation_id=operation_id, operation_status=status)
    run_store.append_event(ctx.run_id, seq, run_events.RUN_STATUS, payload, node_key=None,
                           from_status=run["status"], to_status=run["status"], cause=OPERATION_CAUSE,
                           result_doc_id=None, canvas_state=None, actor_user_id=ctx.actor, reason=status)
    ctx.run["last_event_seq"] = seq
    run_events.queue(ctx.project_id, ctx.flow_id, run_events.RUN_STATUS, payload)
    return seq


def entries(run_id: str, operation_id: str) -> list[dict]:
    """The operation's journal entries (payloads), oldest first."""
    out = []
    for row in run_store._all("SELECT payload_json FROM graph_run_events WHERE run_id = ? AND event_type = ? "
                              "AND cause = ? ORDER BY event_seq", [run_id, run_events.RUN_STATUS, OPERATION_CAUSE]):
        try:
            data = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("operation_id") == operation_id:
            out.append(data)
    return out


def first(items: list[dict], status: str) -> Optional[dict]:
    return next((e for e in items if e.get("operation_status") == status), None)


def last(items: list[dict], status: str) -> Optional[dict]:
    return next((e for e in reversed(items) if e.get("operation_status") == status), None)


def unfinished() -> list[tuple[str, dict]]:
    """Every Flow's unfinished operations ``(flow_id, {operation_id, run_id, status})`` (server start)."""
    if not run_store.available():
        return []
    flows = [r["group_id"] for r in run_store._all(
        "SELECT DISTINCT r.group_id FROM graph_run_events e JOIN graph_runs r ON r.run_id = e.run_id "
        "WHERE e.event_type = ? AND e.cause = ? ORDER BY r.group_id", [run_events.RUN_STATUS, OPERATION_CAUSE])]
    return [(flow_id, op) for flow_id in flows for op in incomplete_operations(flow_id)]

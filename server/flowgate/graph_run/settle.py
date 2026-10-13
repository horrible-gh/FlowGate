"""Whether a Graph run goes on (flowgate.v02.0061 T#2, 0002-CH turn 29 / 31 / 33).

After a step (``run_state_ports.settle``) a running run stays running while any work goes on — an open node handed
out (an AI or a person writes), a node in review whose AI run or card review still works, or whose card stage is
about to follow its AI run (``card_settings_ports.ai_working``) — otherwise it opens the candidates its command may
still open, and with none it goes idle with a reason (``idle_reason``):

    completed         the completion policy (0061 T#1) judges the run complete
    no_targets        no run target, or nothing left for the command (a step whose targets ended)
    waiting_approval  a card awaiting a person's approval, or an open gate
    error             an open node whose hand-out or AI start failed
    blocked_rejected  a rejected root / gate

A person's approval wait is an idle reason only when nothing else works (turn 29). A paused run is never judged.

An idle run keeps its Flow. A node event while idle changes no state, only the reason (its propagation waits for the
next command). An idle run whose existing work goes on again — a card's review started on a result after the run
went idle — is running again (cause ``work_resumed``): only the state follows the work; nothing is drained or
opened by it, and a candidate behind a propagation that waits (a result decided while the run was idle) keeps
waiting for the next command, which drains it. A paused run never goes on by itself.
"""
from __future__ import annotations

from typing import Optional

from flowgate.graph_run import legacy_adapter as engine

IDLE_COMPLETED, IDLE_NO_TARGETS = "completed", "no_targets"
IDLE_WAITING_APPROVAL, IDLE_BLOCKED_REJECTED, IDLE_ERROR = "waiting_approval", "blocked_rejected", "error"
IDLE_REASONS = (IDLE_COMPLETED, IDLE_NO_TARGETS, IDLE_WAITING_APPROVAL, IDLE_BLOCKED_REJECTED, IDLE_ERROR)
CAUSE_COMPLETED, CAUSE_IDLE, CAUSE_WORK_RESUMED = "completed", "run_idle", "work_resumed"
# A review card's refusal while its work still goes on (node_approval).
_WORKING_REFUSALS = (engine.AI_RUNNING, engine.NO_RESULT)


def _node_type(ctx, key: str) -> Optional[str]:
    return (ctx.node_rows.get(key) or {}).get("node_type")


def _waits_on_person(ctx, row: dict) -> bool:
    """A gate open for its decision, or a card in review awaiting a person's approval."""
    node_type = _node_type(ctx, row["node_key"])
    if row["status"] == "open":
        return node_type == engine.GATE_NODE_TYPE
    return row["status"] == "review" and engine.awaiting_approval(ctx.flow_id, row, node_type)


def _failed(ctx, row: dict) -> bool:
    """An open node (not a gate) whose hand-out or AI start failed, or that nothing was handed out for."""
    return row["status"] == "open" and _node_type(ctx, row["node_key"]) != engine.GATE_NODE_TYPE and (
        row.get("dispatch_token_issued") is None or bool(row.get("dispatch_error_code")))


def working(ctx, rows: list[dict]) -> bool:
    """Work still goes on in the run: an open node handed out (its AI or a person writes) or a review card whose AI
    run or card review still works (or its card stage is about to follow its AI run)."""
    for row in rows:
        if row["status"] == "open" and not _waits_on_person(ctx, row) and not _failed(ctx, row):
            return True
        if row["status"] == "review" and engine.refusal(
                ctx.flow_id, row, _node_type(ctx, row["node_key"])) in _WORKING_REFUSALS:
            return True
    return False


def _completion(ctx, state) -> tuple[bool, Optional[str]]:
    """The run's completion policy judgement ``(complete, reason)``; not complete when it cannot judge."""
    try:
        policy = engine.completion_policy(ctx.run_id)
        evaluate = getattr(policy, "evaluate", None)
        if evaluate is None:
            return bool(policy.is_complete(ctx.revision, state)), None
        judged = evaluate(ctx.revision, state)
        return bool(judged.complete), judged.reason
    except (engine.PolicyUndecided, engine.PolicySnapshotUnavailable):
        return False, None


def idle_reason(ctx, rows: list[dict], state) -> str:
    """Why a run with no work going on and nothing its command may open is idle."""
    complete, reason = _completion(ctx, state)
    if complete:
        return IDLE_COMPLETED
    if reason == IDLE_NO_TARGETS:
        return IDLE_NO_TARGETS
    if any(_waits_on_person(ctx, r) for r in rows):
        return IDLE_WAITING_APPROVAL
    if any(_failed(ctx, r) for r in rows):
        return IDLE_ERROR
    if any(r["status"] == "rejected" for r in rows):
        return IDLE_BLOCKED_REJECTED
    return IDLE_NO_TARGETS


def _candidates(ctx, state) -> list[str]:
    """The run candidates the command may open now. One behind a propagation that waits keeps waiting with it: only a
    command drains what waits (``frontier.drain_pending``)."""
    found = engine.command_candidates(ctx.revision, state)
    waiting = {p.node_key for p in state.pending}
    if not waiting or not found:
        return found
    preds: dict[str, set[str]] = {}
    for e in ctx.revision.live_edges():
        preds.setdefault(e.target, set()).add(e.source)
    return [key for key in found if not (preds.get(key, set()) & waiting)]


def settle_plan(ctx) -> Optional[tuple[str, object]]:
    """What ``settle_run`` would do now (nothing written): ``("open", keys)``, ``("idle", reason)``, ``("running",
    None)`` for an idle run whose existing work goes on again, ``("reason", reason)`` for an idle run whose reason
    changed, or None."""
    status = ctx.run.get("status")
    if status not in (engine.RUNNING, engine.IDLE):
        return None                                   # paused: nothing opens and it stays paused
    rows = engine.node_runs(ctx.run_id)
    if working(ctx, rows):
        return ("running", None) if status == engine.IDLE else None
    state = engine.read_run_state(ctx)
    if status == engine.RUNNING:
        more = _candidates(ctx, state)
        if more:
            return ("open", more)
        return ("idle", idle_reason(ctx, rows, state))
    reason = idle_reason(ctx, rows, state)
    return ("reason", reason) if reason != ctx.run.get("idle_reason") else None


def _assert_one(affected: int, what: str) -> None:
    if affected != 1:
        raise AssertionError(f"graph run write missed its row: {what}")


def settle_run(ctx) -> None:
    """``run_state_ports.settle``, inside the step's Flow guard after its decision."""
    plan = settle_plan(ctx)
    if plan is None:
        return
    action, value = plan
    if action == "open":
        engine.apply_decision(ctx, engine.Decision(open=list(value)))
    elif action == "idle":
        _assert_one(engine.set_run_status(ctx.run_id, engine.RUNNING, engine.IDLE, str(value)), "idle")
        ctx.run["idle_reason"] = str(value)
        engine.emit_run_status(ctx, engine.RUNNING, engine.IDLE,
                               CAUSE_COMPLETED if value == IDLE_COMPLETED else CAUSE_IDLE)
    elif action == "running":
        _assert_one(engine.set_run_status(ctx.run_id, engine.IDLE, engine.RUNNING), "work resumed")
        ctx.run["idle_reason"] = None
        engine.emit_run_status(ctx, engine.IDLE, engine.RUNNING, CAUSE_WORK_RESUMED)
    elif engine.set_idle_reason(ctx.run_id, str(value)) == 1:
        ctx.run["idle_reason"] = str(value)

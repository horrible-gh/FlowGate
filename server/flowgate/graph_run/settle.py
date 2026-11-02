"""0061 settlement and capacity refill (T#2/T#6).

Running runs launch old requested executions first, then open candidates while
running + requested is below the Flow limit. A step can refill only its saved
command targets. Scheduling order is defined in scheduling.py. Paused runs may
launch existing requests but never open nodes. Idle/stopping never launch work;
an explicit review request may promote idle to running before its admission.

Only requested/running executions keep a run working; an active attempt awaiting
a person does not occupy a slot. With no work or command candidate left, the run
becomes idle with completed/no_targets/waiting_approval/error/blocked_rejected/step_done.
Deferred propagation remains deferred until the next explicit command.

T#7: a Stop leaves the run idle with the reason ``stopped`` (``stop``); it stays so until the next command — a run read
does not judge a stopped run's reason again. A stopping run settles nothing (``stop`` finishes it). An execution's
run-end hand-off in flight keeps a running run working (``working``), so the instruction -> result and result ->
review / approval hand-offs of its last execution find it running, not idle.

T#9: an approved result whose merge is pending or in progress keeps a running / paused run working. An idle run's
integration wakes it first (``result_merge._wake``: running under the integration hold ``idle``), and a step / full /
resume command whose waiting integrations go first holds too (``result_merge.hold_command``): while a running run
holds, it opens, refills and dispatches nothing — the hold's end decides (an idle run goes idle again, a command goes
on from the state of that moment).

T#11: the idle reason ``hold`` — no execution or integration going on, no run candidate, and a run target not done is
held and pending (``hold.blocks``: it, and every target below it, waits for its release; a held node is never a run
candidate). "No run candidate" is the whole run candidates (``frontier.run_candidates``), not the current command's: a
step that ended with a candidate outside its targets waits for the next command and is not ``hold`` (nor is a
candidate opened by it — the step's boundary stays). ``idle_reason`` judges in this order, the first that holds wins: ``completed`` (completion policy),
``no_targets`` (an empty run scope), ``waiting_approval``, ``gate_publish``, ``merge_conflict``, ``error``,
``blocked_rejected``, ``hold``, ``step_done`` (a step command that opened targets ended at its boundary), else
``no_targets``. A reason a
person must act on comes before the Hold, which a person chose; a run with a held target left is never ``completed``
(the completion policy needs every target done). ``no_targets`` means nothing was there to run: an empty run scope, a
step that opened no target, or a full command with no run candidate left.

0083 WP-2 (0061 T#2 개정, idle reason only): ``waiting_approval`` also covers an open card a person writes (its token
issued, no dispatch failure, not an AI card) — it was ``no_targets`` before. No new idle reason value is added.

flowgate.v02.0111 T#2 (T-A1, idle reason only): an AI card whose writer ended asking (``question_pending``) waits for its
answers and is not ``error``. No new idle reason value is added either: the run's reason is the one it would have
without that card (as an instruction stage that ended on its own and waits for its answers), and the card tells its wait
by its question mark (``run_service.question_nodes``).

flowgate.v02.0113 T#3 rework (0084, idle reason only, no new value): that question wait hid the card's stop as
``no_targets`` ("실행할 카드 없음") once its responder failed. An open card whose own AI asked a question nobody has answered
yet (``_asks``) now waits for a person (``waiting_approval``), and its card shows the question mark; a card of a
pre-feature attempt whose written instruction no AI judges any more is a person's wait too (``stage_view.waits_on_person``).

flowgate.v02.0085 T#2 (D0007 §3.3): a confirm gate held by its accepted decision (its Git apply / archive job not
finished: accepted, failed, recovery_required) is unfinished apply work, not a person's wait. It keeps a running /
paused run working, like an integration in progress, so the run is never idle or completed meanwhile; an idle run with
such a gate is idle with the reason ``gate_publish`` (never ``waiting_approval`` for it, never ``completed``).

flowgate.v02.0119 T#3 (T-2, 0004-CH seq 9 T-2): ``waiting_approval`` is split. A confirm gate open for its decision is
``waiting_confirm`` (the only person's decision of v0.2); a general card a person must act on is ``needs_intervention`` —
a card in review that nothing completes after its automatic rework (``node_approval.intervention`` kind
``needs_intervention``: count spent, hold, stall, failure, the AI's abnormal end), an open card a person writes, an
assigned stage that waits for a person, an AI question nobody answered. ``waiting_confirm`` comes first: the confirm
intake (``gate_decisions.intake``) and the approval wake (``command``) see ``waiting_confirm`` only. A card in review
whose automatic completion path failed (kind ``system_error``) is the error family (``error``), retried by the run read's
reconciliation, never a person's wait. The order is now ``completed``, ``no_targets``, ``waiting_confirm``,
``needs_intervention``, ``gate_publish``, ``merge_conflict``, ``error``, ``blocked_recovery``, ``blocked_rejected``,
``hold``, ``step_done``, else ``no_targets``. A run that went idle before stores ``waiting_approval``; it is read as
``waiting_confirm`` when a gate of the run is open for its decision, else ``needs_intervention`` (``reason_as_read``) and
the stored value is never converted — a run read writes an idle reason only when the judged one differs from the one as
read. An intervention card stops only its own descendants (a node not done propagates nothing, an AND join waits: the
``stop_path`` way); a running run goes on opening the independent branches' candidates.

flowgate.v02.0119 T#3 (T-2 rework, review P2): a node's explicit system error is never read as a person's wait. A card
whose automatic path failed (``node_approval.intervention`` kind ``system_error``: in review, or open in its instruction
stage with its instruction's automatic approval / review failed) is the error family even where its stage assignment
also waits (``stage_waits``: an instruction review waiting on a setting that could not be read records
``review_result_unknown``) or its pre-feature instruction is unjudged; ``needs_intervention`` takes only the waits with
no system error.
"""
from __future__ import annotations

from typing import Optional

from flowgate.graph_run import legacy_adapter as engine
from flowgate.graph_run import scheduling

IDLE_COMPLETED, IDLE_NO_TARGETS = "completed", "no_targets"
IDLE_BLOCKED_REJECTED, IDLE_ERROR = "blocked_rejected", "error"
IDLE_WAITING_CONFIRM = "waiting_confirm"                     # 0119 T-2: a confirm gate open for its decision
IDLE_NEEDS_INTERVENTION = "needs_intervention"               # 0119 T-2: a general card a person must act on
LEGACY_WAITING_APPROVAL = "waiting_approval"                 # stored before 0119 T-2: read by ``reason_as_read``
IDLE_WAITING_APPROVAL = LEGACY_WAITING_APPROVAL              # kept name: the stored legacy value, never written now
IDLE_STOPPED = "stopped"                                     # T#7: set by a Stop only
IDLE_MERGE_CONFLICT = "merge_conflict"                       # T#9: approved results whose merge waits (conflict / failed)
IDLE_HOLD = "hold"                                           # T#11: held targets left (open key, no migration)
IDLE_STEP_DONE = "step_done"                                 # a step that opened targets ended (open key, no migration)
IDLE_GATE_PUBLISH = "gate_publish"                           # 0085 T#2: a confirm's accepted apply work unfinished
IDLE_BLOCKED_RECOVERY = "blocked_recovery"                   # 0085 T#3: a gate waits for its round split
IDLE_REASONS = (IDLE_COMPLETED, IDLE_NO_TARGETS, IDLE_WAITING_CONFIRM, IDLE_NEEDS_INTERVENTION, IDLE_BLOCKED_REJECTED,
                IDLE_ERROR, IDLE_STOPPED, IDLE_MERGE_CONFLICT, IDLE_HOLD, IDLE_STEP_DONE, IDLE_GATE_PUBLISH,
                IDLE_BLOCKED_RECOVERY)
CONFIRM_WAITS = (IDLE_WAITING_CONFIRM, LEGACY_WAITING_APPROVAL)   # a confirm wait, as stored now or before 0119 T-2
CAUSE_COMPLETED, CAUSE_IDLE, CAUSE_WORK_RESUMED = "completed", "run_idle", "work_resumed"


def _node_type(ctx, key: str) -> Optional[str]:
    return (ctx.node_rows.get(key) or {}).get("node_type")


def _gate_reserved(ctx) -> dict:
    """0085 T#2: ``{gate node key: decision}`` of the run's gates held by an accepted decision (apply work unfinished)."""
    from flowgate.gate_decisions import repository as gate_decisions
    return gate_decisions.reservations_of_run(ctx.run_id)


def _recovery_reset(ctx, rows: list[dict]) -> bool:
    """0085 T#3: a pending gate of the run whose recovery was abandoned and waits for its Time Machine round split."""
    from flowgate.gate_decisions import repository as gate_decisions
    return any(r["status"] == "pending" and _node_type(ctx, r["node_key"]) == engine.GATE_NODE_TYPE
               and gate_decisions.reset_required(ctx.flow_id, r["node_key"]) is not None for r in rows)


def opening_held(flow_id: str) -> frozenset:
    """0085 T#3 (``graph_execution_hooks`` ``opening_held``): the gates of the Flow that wait for a round split after an
    abandoned recovery - no command, resume or refill opens them (their successors wait behind them)."""
    from flowgate.gate_decisions import repository as gate_decisions
    if not gate_decisions.available():
        return frozenset()
    from modules.flow_gate.db.connection import get_store
    rows = get_store()._fetch_all("SELECT DISTINCT node_key FROM gate_decisions WHERE group_id = ? "
                                  "AND recovery_reset_required = 1", [flow_id]) or []
    return frozenset(dict(r)["node_key"] for r in rows)


def _waits_confirm(ctx, row: dict, reserved: Optional[dict] = None) -> bool:
    """0119 T-2 ``waiting_confirm``: a gate open for its decision (0085 T#2: not one a decision already holds)."""
    return (row["status"] == "open" and _node_type(ctx, row["node_key"]) == engine.GATE_NODE_TYPE
            and row["node_key"] not in (reserved or {}))


def _intervention_kind(ctx, row: dict) -> Optional[str]:
    """0119 T-2: ``needs_intervention`` / ``system_error`` of a general card in review that nothing completes, else
    None (``node_approval.intervention``: not in review with a result, AI still working, its integration waiting).
    0119 T-2 rework: a card open in its instruction stage whose instruction's automatic path failed: ``system_error``."""
    if row["status"] not in ("review", "open"):
        return None
    found = engine.intervention(ctx.flow_id, row, _node_type(ctx, row["node_key"]))
    return found.get("kind") if found else None


def _system_error(ctx, row: dict) -> bool:
    """0119 T-2: a card in review whose automatic completion path failed — the run's error family, retried by the run
    read's reconciliation (``card_settings_ports.reconcile_stage``), never a person's wait."""
    return _intervention_kind(ctx, row) == engine.SYSTEM_ERROR


def _needs_intervention(ctx, row: dict) -> bool:
    """0119 T-2 ``needs_intervention`` (a general card a person must act on): a card in review that nothing completes
    after its automatic rework, an assigned stage in review that waits for a person; an open card a person writes (0083
    WP-2 / 0061 T#2 개정), an assigned stage that waits for a person, an AI question nobody answered (0113 T#3)."""
    node_type = _node_type(ctx, row["node_key"])
    if node_type == engine.GATE_NODE_TYPE:
        return False
    kind = _intervention_kind(ctx, row)
    if kind == engine.SYSTEM_ERROR:
        return False                                                      # 0119 T-2 rework: its error family first
    if row["status"] == "open":
        from modules.flow_gate.graph_workflow.services import card_settings_ports
        judged = card_settings_ports.stage_waits(ctx.flow_id, row)       # 0113 T#3: an assigned attempt's stage
        if judged == card_settings_ports.STAGE_UNREADABLE:
            return False                                                  # its error family (``_failed``)
        if _asks(ctx, row):                                               # 0113 T#3 rework: its AI's question
            return bool(row.get("dispatch_token_issued")) and not _failed(ctx, row)
        if judged is not None:
            return bool(row.get("dispatch_token_issued")) and not _failed(ctx, row) and bool(judged)
        return (bool(row.get("dispatch_token_issued")) and not _failed(ctx, row)
                and not card_settings_ports.uses_ai(ctx.flow_id, ctx.node_rows.get(row["node_key"]) or {}))
    if row["status"] == "review":
        from modules.flow_gate.graph_workflow.services import card_settings_ports
        if card_settings_ports.stage_waits(ctx.flow_id, row) is True:
            return True                                                   # a review assignment waits (L-2 §2.12)
    return kind == engine.NEEDS_INTERVENTION


def _waits_on_person(ctx, row: dict, reserved: Optional[dict] = None) -> bool:
    """A person must act on the node: its confirm (``waiting_confirm``) or the card's intervention
    (``needs_intervention``). Kept for its readers; the idle reason tells the two apart (0119 T-2)."""
    return _waits_confirm(ctx, row, reserved) or _needs_intervention(ctx, row)


def reason_as_read(ctx, rows: Optional[list[dict]] = None) -> Optional[str]:
    """0119 T-2: the run's idle reason as read. A stored ``waiting_approval`` (a run idle before T-2) is
    ``waiting_confirm`` when a gate of the run is open for its decision, else ``needs_intervention``; the stored value is
    not converted. Any other value as stored."""
    stored = ctx.run.get("idle_reason")
    if stored != LEGACY_WAITING_APPROVAL:
        return stored
    reserved = _gate_reserved(ctx)
    rows = engine.node_runs(ctx.run_id) if rows is None else rows
    return IDLE_WAITING_CONFIRM if any(_waits_confirm(ctx, r, reserved) for r in rows) else IDLE_NEEDS_INTERVENTION


def read_idle_reason(run: Optional[dict]) -> Optional[str]:
    """``graph_execution_hooks`` ``idle_reason_read`` (the run read's wire): ``reason_as_read`` of the run row ``run``."""
    if not run or run.get("idle_reason") != LEGACY_WAITING_APPROVAL:
        return (run or {}).get("idle_reason")
    return reason_as_read(engine.load_context(run, None, None))


def _asks(ctx, row: dict) -> bool:
    """flowgate.v02.0113 T#3 rework (0084: an idle run ``no_targets`` whose card's AI asked a question its responder
    failed to answer): an open card whose own AI asked a question nobody has answered yet waits for that answer — a
    person's, or its responder's — so the run waits for a person (``needs_intervention`` since 0119 T-2, before it
    ``waiting_approval``), never "nothing to run". The
    card tells it by its question mark (``run_service.question_nodes``). A failed read: not asking."""
    from modules.flow_gate.graph_workflow.services import run_service
    try:
        return run_service.node_asks(ctx.flow_id, row)
    except Exception:  # noqa: BLE001 — the judgement goes on as before
        return False


def _failed(ctx, row: dict) -> bool:
    """An open non-gate with a failed dispatch or a finished writer missing its result. 0111 T#2 (T-A1): a writer that
    ended asking and waits for its answers (``write_failure`` ``question_pending``) has not failed: no ``error``."""
    from flowgate.graph_run.execution import QUESTION_PENDING, write_failure
    return row["status"] == "open" and _node_type(ctx, row["node_key"]) != engine.GATE_NODE_TYPE and (
        row.get("dispatch_token_issued") is None or bool(row.get("dispatch_error_code"))
        or write_failure(row) not in (None, "", QUESTION_PENDING) or _assignment_unreadable(ctx, row))


def _assignment_unreadable(ctx, row: dict) -> bool:
    """0113 T#3 (L-2 §2.14, T §4.6): the open node's stage assignment could not be read — the node is in the error
    family (idle reason ``error``), never a person's wait nor a node with nothing to do."""
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    return card_settings_ports.stage_waits(ctx.flow_id, row) == card_settings_ports.STAGE_UNREADABLE


def working(ctx, rows: list[dict]) -> bool:
    """Only requested/running AI executions count; a person's approval wait does not. T#7: so does an execution's
    run-end hand-off in flight on a running / paused run (``execution_store.handing_off``) — between an execution's
    ledger end and the stage that follows it (the result stage after its instruction, the result's review or approval)
    the run is not idle. It never wakes an idle run. T#9: neither is a running / paused run with an approved result
    whose merge is pending or in progress (``result_merge.in_progress``). An idle run is woken by its integration
    itself (``result_merge._wake``), never by a settlement."""
    from flowgate.graph_run import execution_store, result_merge
    if execution_store.working(ctx.run_id):
        return True
    return ctx.run.get("status") in (engine.RUNNING, engine.PAUSED) and (
        execution_store.handing_off(ctx.run_id) or result_merge.in_progress(ctx.run_id) or bool(_gate_reserved(ctx)))


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
    """Why a run with no work going on and nothing its command may open is idle (the order: module docstring)."""
    complete, reason = _completion(ctx, state)
    if complete:
        return IDLE_COMPLETED
    if reason == IDLE_NO_TARGETS:
        return IDLE_NO_TARGETS
    reserved = _gate_reserved(ctx)
    if any(_waits_confirm(ctx, r, reserved) for r in rows):
        return IDLE_WAITING_CONFIRM                          # 0119 T-2: the confirm intake sees this one only
    if any(_needs_intervention(ctx, r) for r in rows):
        return IDLE_NEEDS_INTERVENTION                       # 0119 T-2: a general card a person must act on
    if reserved:
        return IDLE_GATE_PUBLISH                             # 0085 T#2: a confirm's apply work is unfinished
    from flowgate.graph_run import result_merge
    if result_merge.waiting(ctx.run_id):
        return IDLE_MERGE_CONFLICT                           # T#9: conflict / failed merges left
    if any(_failed(ctx, r) or _system_error(ctx, r) for r in rows):
        return IDLE_ERROR                                    # 0119 T-2: a failed automatic completion path too
    if _recovery_reset(ctx, rows):
        return IDLE_BLOCKED_RECOVERY                         # 0085 T#3: an abandoned recovery waits for its round split
    if any(r["status"] == "rejected" for r in rows):
        return IDLE_BLOCKED_REJECTED
    from flowgate.graph_run import hold
    if hold.blocks(ctx, state):
        return IDLE_HOLD                                     # T#11
    if ctx.run.get("run_mode") == engine.MODE_STEP and ctx.run.get("step_node_keys"):
        return IDLE_STEP_DONE                                # the step's targets were opened and it ended normally
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
    if status not in (engine.RUNNING, engine.PAUSED, engine.IDLE):
        return None
    rows = engine.node_runs(ctx.run_id)
    if status == engine.RUNNING:
        from flowgate.graph_run import result_merge
        if result_merge.holding(ctx.run_id):
            return None                                      # T#9: the waiting integrations go first
    if status in (engine.RUNNING, engine.PAUSED):
        if scheduling.requests(ctx.run_id) and scheduling.available(ctx.run_id, ctx.flow_id):
            return ("refill", None)
        if status == engine.PAUSED:
            return None
    elif ctx.run.get("idle_reason") == IDLE_STOPPED and not working(ctx, rows):
        return None                                          # T#7: stopped until the next command
    state = engine.read_run_state(ctx)
    if status == engine.RUNNING:
        more = _candidates(ctx, state)
        if more and scheduling.can_open(ctx):
            return ("open", more)
        if working(ctx, rows):
            return None
        return ("idle", idle_reason(ctx, rows, state))
    if working(ctx, rows):
        return ("running", None)
    reason = idle_reason(ctx, rows, state)
    return ("reason", reason) if reason != reason_as_read(ctx, rows) else None      # 0119 T-2: legacy not converted


def _assert_one(affected: int, what: str) -> None:
    if affected != 1:
        raise AssertionError(f"graph run write missed its row: {what}")


def _end_cleanup(ctx) -> None:
    """0085 T#3 (D0007 §7.1): a completed run of a Flow with gate decisions gets its end cleanup - a ``gate_cleanup``
    job written in this completion's transaction and run by the job runner outside every guard, under the Git locks
    (``gate_decisions.cleanup``)."""
    from flowgate.gate_decisions import cleanup
    cleanup.request(ctx)


def settle_run(ctx) -> None:
    """``run_state_ports.settle``, inside the step's Flow guard after its decision."""
    plan = settle_plan(ctx)
    if plan is None:
        return
    action, value = plan
    if action == "refill":
        scheduling.queue_requests(ctx)
        if ctx.run["status"] == engine.RUNNING and scheduling.can_open(ctx):
            more = _candidates(ctx, engine.read_run_state(ctx))
            if more:
                engine.apply_decision(ctx, engine.Decision(open=more), settle=False)
    elif action == "open":
        engine.apply_decision(ctx, engine.Decision(open=list(value)))
    elif action == "idle":
        _assert_one(engine.set_run_status(ctx.run_id, engine.RUNNING, engine.IDLE, str(value)), "idle")
        ctx.run["idle_reason"] = str(value)
        engine.emit_run_status(ctx, engine.RUNNING, engine.IDLE,
                               CAUSE_COMPLETED if value == IDLE_COMPLETED else CAUSE_IDLE)
        if value == IDLE_COMPLETED:
            _end_cleanup(ctx)
    elif action == "running":
        _assert_one(engine.set_run_status(ctx.run_id, engine.IDLE, engine.RUNNING), "work resumed")
        ctx.run["idle_reason"] = None
        engine.emit_run_status(ctx, engine.IDLE, engine.RUNNING, CAUSE_WORK_RESUMED)
    elif engine.set_idle_reason(ctx.run_id, str(value)) == 1:
        ctx.run["idle_reason"] = str(value)
        if value == IDLE_COMPLETED:
            _end_cleanup(ctx)

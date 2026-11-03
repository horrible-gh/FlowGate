"""The step / full command (flowgate.v02.0061 T#2, 0002-CH turn 33).

A command is ``step`` (open the run candidates of the moment; what they make runnable waits for the next command) or
``full`` (go on opening until no candidate is left). ``POST /flows/{flow_id}/runs`` and ``POST /runs/{run_id}/resume``
take it as ``mode``; the engine (``run_service._go_on``) asks here which command a paused / idle run goes on with and
records it through here:

- ``command_of``: a mode given is a new command; an idle run always begins a new one (no mode: its last command's
  mode); a paused run given no mode goes on with its own command (None: a step keeps its targets).
- ``record_command``: the new command is stored on the running run — mode, no step targets yet — before the run
  status event, so the event, the stored run and the response tell the same command.
- ``record_step_targets``: a step's targets are the nodes its command's decision opens (the drained propagations and
  the run candidates of the moment); a full command has none. T#11: never a held node (the decision opens none; one is
  dropped all the same, so a refill of the step cannot open it either). flowgate.v02.0124 T#1 (0003-NR §5.1.1): also
  the cards whose result review the command requested again (``rerequested``) — in review, so never in the
  decision's openings; without them a step that only re-requested would store no targets and settle ``no_targets``.
- ``rerequest_review`` (flowgate.v02.0124 T#1, ``graph_execution_hooks`` ``rerequest_review``): inside an explicit
  step / full command's Flow guard, a card in review whose ``needs_intervention`` is a review loop's spent budget is
  requested again as its result review's next assignment (``card_settings.assigned_review.rerequest_review_locked``,
  0003-NR §5.1.2 / §5.1.3). Step and full alike take every card admitted at the command; a step's next cards still
  wait for the next command (0053: a target's propagation waits, ``frontier.RunState.in_command``), a full command's
  follow as its cards finish.

0083 WP-3 (0061 T#2 개정, revised): a person's approval is the exception path of a card (a review that did not pass or
spent its count, a card a person writes) and moves the node on with no further resume (the flowgate.v02.0056 T#1
contract). ``approval_wakes`` judges whether a person's decision on an idle run may open the next cards — the
conditions of ``card_settings.legacy_adapter.wake_graph_run`` (the Flow's active run, idle, not ``stopped``, no Time
Machine operation unfinished), outside an explicit command (it drains what waits itself), and not a step that has no
targets yet — and ``wake_approval`` takes the run back to running before those openings, as ``resume_approval`` wakes
it (cause ``work_resumed``, reason ``approval``). A step still opens only its targets (``frontier.RunState.in_command``),
and a paused run still waits.
"""
from __future__ import annotations

import json
from typing import Optional

from flowgate.graph_run import legacy_adapter as engine


def command_of(run: dict, mode: Optional[str]) -> Optional[str]:
    if mode is not None:
        return mode
    if run.get("status") == engine.IDLE:
        return run.get("run_mode") or engine.MODE_FULL
    return None


def record_command(ctx, mode: str) -> None:
    if engine.set_run_command(ctx.run_id, mode, None) != 1:
        raise AssertionError("command missed its run")
    ctx.run["run_mode"], ctx.run["step_node_keys"] = mode, None


def record_step_targets(ctx, mode: str, decision, rerequested=()) -> None:
    """``rerequested``: the cards the command requested again (``rerequest_review``), targets of a step too."""
    if mode != engine.MODE_STEP:
        return
    from flowgate.graph_run import hold
    keys = sorted((set(decision.open) | set(rerequested or ())) - hold.held(ctx.flow_id))
    if engine.set_run_command(ctx.run_id, engine.MODE_STEP, keys) != 1:
        raise AssertionError("step command missed its run")
    ctx.run["step_node_keys"] = keys


def rerequest_review(ctx, row: dict) -> bool:
    """flowgate.v02.0124 T#1: the ``rerequest_review`` hook — True when the explicit command requested the result
    review of the node run ``row``'s card again (the card stage judges and writes it inside this guard)."""
    from flowgate.card_settings import assigned_review  # noqa: PLC0415 — the card stage (v0.2), on use

    return bool(assigned_review.rerequest_review_locked(ctx, row))


def _waiting_command(ctx, *, human: bool = False) -> bool:
    """Prove the idle wait belongs to an unfinished command, using its durable status events.

    An idle reason can change on a read, so waiting_confirm alone is not execution intent. 0119 T-2: only a confirm
    wait counts (``waiting_confirm``, or ``waiting_approval`` stored before T-2 — the caller judged the run's reason as
    read, ``settle.reason_as_read``); a ``needs_intervention`` wait is woken by its own memo rework / re-review request,
    or by a new explicit step / full command that asks a spent review again — flowgate.v02.0124 T#1, a review loop's
    spent budget (``rerequest_review``), and flowgate.v02.0125 T#1 (Q #544), a review count spent on a revision no
    review answered (``card_settings_ports.command_review``). That command is its own ``user_command`` /
    ``user_resume`` start, never this reconstruction; an approval never wakes it here.
    A review explicitly requested during that wait may have woken the run; trace only such
    waiting/working pairs back to a command. Stop, Pause, a completed step, and Time Machine
    break the chain. No command is reconstructed from an old run_mode alone.
    """
    from modules.flow_gate.graph_workflow.services import run_events

    before = int(ctx.run["last_event_seq"]) + 1
    while True:
        events = engine.run_store._all(
            "SELECT event_seq, from_status, to_status, cause, payload_json FROM graph_run_events "
            "WHERE run_id = ? AND event_type = ? AND event_seq < ? "
            "AND (COALESCE(from_status, '') <> to_status OR cause = 'graph_time_machine') "
            "ORDER BY event_seq DESC LIMIT 2", [ctx.run_id, run_events.RUN_STATUS, before])
        if len(events) != 2:
            return False
        waiting, started = events
        if (waiting["from_status"], waiting["to_status"], waiting["cause"]) != (
                engine.RUNNING, engine.IDLE, "run_idle"):
            return False
        payload = json.loads(waiting["payload_json"])
        waits = (*_confirm_waits(), "needs_intervention", "error", "no_targets") if human else _confirm_waits()
        if payload.get("idle_reason") not in waits or payload.get("mode") != ctx.run.get("run_mode"):
            return False
        if started["to_status"] != engine.RUNNING:
            return False
        if started["cause"] in ("user_start", "user_command", "user_resume"):
            return True
        if started["cause"] != "work_resumed":
            return False
        before = int(started["event_seq"])


def resume_approval(ctx, decision) -> bool:
    """Before applying a successful approval, wake only its waiting command in the same guard.

    Keep the run, revision, mode and step targets. A deferred code approval still goes through
    its existing integration before becoming done; a failed/rejected approval never gets here.

    0083 WP-3 (0061 T#2 개정): only a run ``approval_wakes`` admits — a stopped run and one under a Time Machine operation
    are not woken here either (their propagation waits for the next command), as ``wake_graph_run``.
    0119 T-2: only a run idle ``waiting_confirm`` (its reason as read: a stored ``waiting_approval`` with a gate open for
    its decision) — never ``needs_intervention``.
    """
    from flowgate.graph_run import settle
    if ctx.run.get("idle_reason") not in settle.CONFIRM_WAITS or not approval_wakes(ctx):
        return False
    if settle.reason_as_read(ctx) != settle.IDLE_WAITING_CONFIRM:
        return False
    if not any(t.to_status == "done" and t.cause in ("review_approved", "gate_confirmed")
               for t in decision.node_transitions):
        return False
    if ctx.run.get("run_mode") not in (engine.MODE_FULL, engine.MODE_STEP):
        return False
    if ctx.run["run_mode"] == engine.MODE_STEP and ctx.run.get("step_node_keys") is None:
        return False
    if ctx.run.get("active_group_id") != ctx.flow_id or not _waiting_command(ctx):
        return False
    if engine.set_run_status(ctx.run_id, engine.IDLE, engine.RUNNING) != 1:
        raise AssertionError("approval continuation missed its run")
    ctx.run["idle_reason"] = None
    engine.emit_run_status(ctx, engine.IDLE, engine.RUNNING, "work_resumed", "approval")
    return True


def _confirm_waits() -> tuple:
    from flowgate.graph_run import settle
    return settle.CONFIRM_WAITS


def approval_wakes(ctx) -> bool:
    """0083 WP-3 (0061 T#2 개정): may a person's decision on this idle run open its next cards at once
    (``frontier.defers``)? ``wake_graph_run``'s conditions, judged on the guarded context: idle, the Flow's active run,
    not stopped, no Time Machine operation unfinished; and not inside an explicit command, nor a step with no targets."""
    run = ctx.run
    if run.get("status") != engine.IDLE or run.get("idle_reason") == "stopped" or ctx.command_pending:
        return False
    if run.get("active_group_id") != ctx.flow_id:
        return False
    if run.get("run_mode") == engine.MODE_STEP and run.get("step_node_keys") is None:
        return False
    from flowgate.graph_time_machine.operation import busy
    return not busy(ctx.flow_id) and (not getattr(ctx, "human_approval", False) or _waiting_command(ctx, human=True))


def wake_approval(ctx) -> None:
    """0083 WP-3 (0061 T#2 개정): before a person's decision opens the next cards of an idle run, the run goes running
    in the same guard (cause ``work_resumed``, reason ``approval``, as ``resume_approval``), so an AI card's dispatch
    requests its execution on a running run; the settlement after the openings judges it idle again when nothing
    works. Only a run ``approval_wakes`` admits gets here (the decision deferred otherwise)."""
    if not approval_wakes(ctx):
        raise AssertionError("an idle run opened nodes it may not wake for")
    if engine.set_run_status(ctx.run_id, engine.IDLE, engine.RUNNING) != 1:
        raise AssertionError("approval wake missed its run")
    ctx.run["idle_reason"] = None
    engine.emit_run_status(ctx, engine.IDLE, engine.RUNNING, "work_resumed", "approval")


def continue_approval(ctx) -> None:
    """Drain the approved node's deferred propagation within the original command's bounds."""
    from modules.flow_gate.graph_workflow.domain.frontier import RunEvent, resolve

    decision = resolve(ctx.revision, engine.read_run_state(ctx), RunEvent("resume"),
                       engine.policies_of(engine.run_store.policy_entries(ctx.run_id)))
    engine.apply_decision(ctx, decision)

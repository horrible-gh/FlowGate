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
  dropped all the same, so a refill of the step cannot open it either).
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


def record_step_targets(ctx, mode: str, decision) -> None:
    if mode != engine.MODE_STEP:
        return
    from flowgate.graph_run import hold
    keys = sorted(set(decision.open) - hold.held(ctx.flow_id))
    if engine.set_run_command(ctx.run_id, engine.MODE_STEP, keys) != 1:
        raise AssertionError("step command missed its run")
    ctx.run["step_node_keys"] = keys


def _waiting_command(ctx) -> bool:
    """Prove the idle wait belongs to an unfinished command, using its durable status events.

    An idle reason can change on a read, so waiting_approval alone is not execution intent.
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
        if payload.get("idle_reason") != "waiting_approval" or payload.get("mode") != ctx.run.get("run_mode"):
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
    """
    if ctx.run.get("status") != engine.IDLE or ctx.run.get("idle_reason") != "waiting_approval":
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


def continue_approval(ctx) -> None:
    """Drain the approved node's deferred propagation within the original command's bounds."""
    from modules.flow_gate.graph_workflow.domain.frontier import RunEvent, resolve

    decision = resolve(ctx.revision, engine.read_run_state(ctx), RunEvent("resume"),
                       engine.policies_of(engine.run_store.policy_entries(ctx.run_id)))
    engine.apply_decision(ctx, decision)

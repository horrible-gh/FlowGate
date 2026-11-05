"""0132 T#3: gate reservation activity and command-intent wake, under the Flow guard.

A parked reservation still owns its confirm card, but does not hold the run's structure.
Wake only the command that accepted it; idle_reason is presentation except for explicit Stop.
"""
from __future__ import annotations

import json

from flowgate.gate_decisions import legacy_adapter as legacy, repository as repo

LIVE, PARKED = "LIVE", "PARKED"
QUEUED = frozenset({"pending", "blocked", "retry_wait", "claimed"})
INTENT_BREAKS = frozenset({"user_stop", "user_pause", "graph_time_machine",
                           "user_start", "user_command", "user_resume"})


def gate_activity(run_id: str) -> dict[str, str]:
    """Writer/valid lease first, then accepted queued work; every other reservation is parked."""
    from modules.flow_gate.services.git import job_store
    out = {}
    for key, decision in repo.reservations_of_run(run_id).items():
        job = legacy.get_job(decision.get("job_id"))
        live = job_store.gate_writer_active(job) or (
            decision["status"] == repo.ACCEPTED and job is not None and job.get("status") in QUEUED)
        out[key] = LIVE if live else PARKED
    return out


def gate_live(run_id: str) -> bool:
    return LIVE in gate_activity(run_id).values()


def accept_intent(ctx, node_key: str) -> dict:
    """Capture the original intake wake contract before inserting the decision (no version bump)."""
    from flowgate.graph_run import command, settle
    run = ctx.run
    wake = run.get("status") == "running"
    if run.get("status") == "idle" and run.get("idle_reason") in settle.CONFIRM_WAITS:
        wake = (run.get("run_mode") in ("full", "step")
                and (run["run_mode"] != "step" or node_key in (run.get("step_node_keys") or []))
                and run.get("active_group_id") == ctx.flow_id and command._waiting_command(ctx))
    return {"accept_wake": int(bool(wake)), "accept_event_seq": int(run.get("last_event_seq") or 0)}


def _intent_unchanged(ctx, decision: dict) -> bool:
    floor = decision.get("accept_event_seq")
    if not decision.get("accept_wake") or floor is None:
        return False
    run = ctx.run
    if run.get("active_group_id") != ctx.flow_id or run.get("run_mode") not in ("full", "step"):
        return False
    if run["run_mode"] == "step" and decision["node_key"] not in (run.get("step_node_keys") or []):
        return False
    # New decisions retain the command at acceptance, including running intakes whose last event may be a node
    # event. Comparing later events only with the current command cannot detect a change to that same command.
    accepted = (decision.get("settings") or {}).get("accept_command")
    if not isinstance(accepted, dict):
        # Migration 174 points old decisions at their original intake wake. Old payloads always carry mode,
        # but may predate step/group fields; their explicit user-command causes are checked below as well.
        original = legacy.run_store._one(
            "SELECT payload_json FROM graph_run_events WHERE run_id = ? AND event_seq = ? "
            "AND event_type = 'graph_run_status_changed' AND cause = 'work_resumed' AND reason = 'gate_publish'",
            [ctx.run_id, int(floor)])
        try:
            payload = json.loads((original or {}).get("payload_json") or "null")
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict) or "mode" not in payload:
            return False
        accepted = {key: payload.get(event_key, run.get(key)) for key, event_key in
                    (("run_mode", "mode"), ("step_node_keys", "step_node_keys"),
                     ("active_group_id", "active_group_id"))}
    if any(
            accepted.get(key) != run.get(key) for key in ("run_mode", "step_node_keys", "active_group_id")):
        return False
    for event in legacy.run_store._all(
            "SELECT cause, payload_json FROM graph_run_events WHERE run_id = ? AND event_seq > ? "
            "AND event_type = 'graph_run_status_changed' ORDER BY event_seq", [ctx.run_id, int(floor)]):
        if event.get("cause") in INTENT_BREAKS:
            return False
        try:
            payload = json.loads(event.get("payload_json") or "{}")
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        for key, current in (("mode", run.get("run_mode")), ("step_node_keys", run.get("step_node_keys")),
                             ("active_group_id", run.get("active_group_id"))):
            if key in payload and payload[key] != current:
                return False
    return True


def gate_resume_wake(ctx, decision: dict, *, reason: str = "gate_resume") -> bool:
    """NR §4.4.4 in order, inside the Flow guard. Only run status changes; other cards keep their waits."""
    run = ctx.run
    if run.get("status") == "running":
        return False
    if run.get("status") in ("paused", "stopping"):
        return False
    if run.get("status") != "idle" or run.get("idle_reason") == "stopped":
        return False
    from flowgate.graph_time_machine.operation import busy
    if busy(ctx.flow_id) or not _intent_unchanged(ctx, decision):
        return False
    if legacy.run_store.set_run_status(ctx.run_id, "idle", "running") != 1:
        raise AssertionError("gate resume wake missed its run")
    run["idle_reason"] = None
    legacy.emit_run_status(ctx, "idle", "running", "work_resumed", reason)
    return True

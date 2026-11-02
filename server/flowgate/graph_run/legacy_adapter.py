"""Reuse boundary of the Graph run state (flowgate.v02.0061 T#2, D0006 §2.2).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never changed: the run store
(the run row, its node runs, its policy snapshot, the conditional status / command / idle reason updates), the run step
writes of ``run_apply`` (a decision, the run status event, the run state the resolver reads), the resolver's run
candidates (``frontier.command_candidates``, a step's targets applied), the completion policy restore and the node
approval state (``node_approval``: a gate, a card awaiting a person's approval, AI still working on a card in review).
0061 T#11 (Hold): the Flow's run (``active_run``), the run context and the node state event (``load_context``,
``emit_node_state``) and the Graph error (``GraphApiError``); the whole run candidates (``frontier.run_candidates``, no
step applied: the idle reason ``hold``) and the Canvas refresh signal (``canvas_events.publish_canvas_changed``).
flowgate.v02.0113 T#5 (the command result, ``command_result``): the run row and its event log as stored, the run / node
status event types, the Flow's card titles, an ended AI run's durable row and the run event each execution request
was made at (``execution_anchors``) — reads only.
flowgate.v02.0119 T#3 (T-2): the node's intervention state (``node_approval.intervention``: ``needs_intervention`` /
``system_error`` of a general card in review that nothing completes) — no card awaits a person's approval any more.
"""
from __future__ import annotations

from modules.flow_gate.graph_workflow.domain.frontier import (
    MODE_FULL, MODE_STEP, Decision, command_candidates, run_candidates,
)
from modules.flow_gate.graph_workflow.domain.run_policies import (
    PolicySnapshotUnavailable, PolicyUndecided, policies_of,
)
from modules.flow_gate.graph_workflow.services import node_approval
from modules.flow_gate.graph_workflow.services.canvas_events import publish_canvas_changed
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.run_apply import (
    apply_decision, emit_node_state, emit_run_status, load_context, read_run_state,
)
from modules.flow_gate.graph_workflow.services import run_events as run_event_types
from modules.flow_gate.graph_workflow.store import run_store, title_store

__all__ = ["MODE_FULL", "MODE_STEP", "RUNNING", "PAUSED", "IDLE", "GATE_NODE_TYPE", "AI_RUNNING", "NO_RESULT",
           "Decision", "PolicySnapshotUnavailable", "PolicyUndecided", "apply_decision", "command_candidates",
           "emit_run_status", "read_run_state", "node_runs", "completion_policy", "refusal", "awaiting_approval",
           "intervention", "NEEDS_INTERVENTION", "SYSTEM_ERROR",
           "set_run_status", "set_idle_reason", "set_run_command", "GraphApiError", "active_run", "load_context",
           "emit_node_state", "run_candidates", "publish_canvas_changed", "RUN_STATUS_EVENT", "NODE_STATE_EVENT",
           "run_by_id", "run_events", "node_titles", "ai_run_row", "execution_anchors"]

RUNNING, PAUSED, IDLE = run_store.RUNNING, run_store.PAUSED, run_store.IDLE
RUN_STATUS_EVENT, NODE_STATE_EVENT = run_event_types.RUN_STATUS, run_event_types.NODE_STATE
GATE_NODE_TYPE = node_approval.GATE_NODE_TYPE
AI_RUNNING, NO_RESULT = node_approval.AI_RUNNING, node_approval.NO_RESULT
NEEDS_INTERVENTION, SYSTEM_ERROR = node_approval.NEEDS_INTERVENTION, node_approval.SYSTEM_ERROR    # 0119 T-2


def active_run(flow_id: str):
    return run_store.active_run(flow_id)


def node_runs(run_id: str) -> list[dict]:
    return run_store.node_runs(run_id)


def completion_policy(run_id: str):
    """The run's completion policy from its snapshot (raises PolicyUndecided / PolicySnapshotUnavailable)."""
    return policies_of(run_store.policy_entries(run_id))["completion"]


def refusal(flow_id: str, row: dict, node_type):
    return node_approval.refusal(flow_id, row, node_type)


def awaiting_approval(flow_id: str, row: dict, node_type) -> bool:
    return node_approval.awaiting_approval(flow_id, row, node_type)


def intervention(flow_id: str, row: dict, node_type):
    """0119 T-2: ``{"kind", "reason"}`` of a general card in review that nothing completes, else None."""
    return node_approval.intervention(flow_id, row, node_type)


def set_run_status(run_id: str, expected: str, status: str, idle_reason=None) -> int:
    return run_store.set_run_status(run_id, expected, status, idle_reason)


def set_idle_reason(run_id: str, idle_reason: str) -> int:
    return run_store.set_idle_reason(run_id, idle_reason)


def set_run_command(run_id: str, mode: str, step_node_keys) -> int:
    return run_store.set_run_command(run_id, mode, step_node_keys)


def run_by_id(run_id: str):
    return run_store.run_by_id(run_id)


def run_events(run_id: str) -> list[dict]:
    """The run's event log rows in ``event_seq`` order (run status and node state events)."""
    return run_store.events(run_id)


def node_titles(flow_id: str) -> dict:
    """``{node_key: {title, title_source, version}}`` of the Flow's cards (none before migration 155)."""
    return title_store.states(flow_id)


def ai_run_row(ai_run_id: str):
    """An ended AI run's durable row (``ai_invoke_runs``: its last message, stop and output tails), or None."""
    from modules.flow_gate.db import ai_invoke_runs  # noqa: PLC0415
    return ai_invoke_runs.get(ai_run_id) if ai_run_id else None


def execution_anchors(run_id: str) -> dict:
    """``{exec_id: run_event_seq}``: the run's ``last_event_seq`` the execution ledger stored with each request of the
    run (``execution_store.request``). A request stored before that has none (absent here)."""
    import json  # noqa: PLC0415
    from modules.flow_gate.db.connection import get_store  # noqa: PLC0415

    if not run_id or not get_store().table_exists("graph_attempt_runs"):
        return {}
    found = {}
    for row in run_store._all("SELECT exec_id, request_json FROM graph_attempt_runs WHERE run_id = ?", [run_id]) or []:
        try:
            seq = json.loads(row["request_json"] or "{}").get("run_event_seq")
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(seq, int) and not isinstance(seq, bool):
            found[row["exec_id"]] = seq
    return found

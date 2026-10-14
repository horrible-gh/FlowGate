"""Reuse boundary of the Graph run state (flowgate.v02.0061 T#2, D0006 §2.2).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never changed: the run store
(the run row, its node runs, its policy snapshot, the conditional status / command / idle reason updates), the run step
writes of ``run_apply`` (a decision, the run status event, the run state the resolver reads), the resolver's run
candidates (``frontier.command_candidates``, a step's targets applied), the completion policy restore and the node
approval state (``node_approval``: a gate, a card awaiting a person's approval, AI still working on a card in review).
0061 T#11 (Hold): the Flow's run (``active_run``), the run context and the node state event (``load_context``,
``emit_node_state``) and the Graph error (``GraphApiError``); the whole run candidates (``frontier.run_candidates``, no
step applied: the idle reason ``hold``) and the Canvas refresh signal (``canvas_events.publish_canvas_changed``).
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
from modules.flow_gate.graph_workflow.store import run_store

__all__ = ["MODE_FULL", "MODE_STEP", "RUNNING", "PAUSED", "IDLE", "GATE_NODE_TYPE", "AI_RUNNING", "NO_RESULT",
           "Decision", "PolicySnapshotUnavailable", "PolicyUndecided", "apply_decision", "command_candidates",
           "emit_run_status", "read_run_state", "node_runs", "completion_policy", "refusal", "awaiting_approval",
           "set_run_status", "set_idle_reason", "set_run_command", "GraphApiError", "active_run", "load_context",
           "emit_node_state", "run_candidates", "publish_canvas_changed"]

RUNNING, PAUSED, IDLE = run_store.RUNNING, run_store.PAUSED, run_store.IDLE
GATE_NODE_TYPE = node_approval.GATE_NODE_TYPE
AI_RUNNING, NO_RESULT = node_approval.AI_RUNNING, node_approval.NO_RESULT


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


def set_run_status(run_id: str, expected: str, status: str, idle_reason=None) -> int:
    return run_store.set_run_status(run_id, expected, status, idle_reason)


def set_idle_reason(run_id: str, idle_reason: str) -> int:
    return run_store.set_idle_reason(run_id, idle_reason)


def set_run_command(run_id: str, mode: str, step_node_keys) -> int:
    return run_store.set_run_command(run_id, mode, step_node_keys)

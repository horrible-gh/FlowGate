"""When a confirm gate's settings may change (flowgate.v02.0085 T#1, D0007 §7.2 last paragraph).

The card settings lock of every other card is the Graph's (``graph_run.editing.require_editable``): a running,
paused or stopping run locks all cards and an open / done / review node stays protected. A gate is the exception,
because its settings are what the person decides on, while it waits for the person:

- allowed: the gate is pending or open in the run (or the Flow has no run), the run is running or idle, no decision is
  reserved on the gate (accepted / failed / recovery_required), the gate is not waiting for a round split after an
  abandoned recovery, no Time Machine operation is unfinished and no merge conflict session of the Flow is open;
- everything else is 409 ``gate_settings_locked`` with the reason (``reason`` in the error params).

Only the settings lock is relaxed. The structure lock of the definition (``editing.protect_definition``: a done / open
gate keeps its definition and input edges) is not touched.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import run_store

from flowgate.gate_decisions import repository

LOCKED_CODE = "gate_settings_locked"
EDITABLE_NODE_STATUSES = frozenset({"pending", "open"})
EDITABLE_RUN_STATUSES = frozenset({run_store.RUNNING, run_store.IDLE})

(TIME_MACHINE, MERGE_CONFLICT, RUN_NOT_EDITABLE, GATE_DECIDED, DECISION_RESERVED, RECOVERY_RESET_REQUIRED) = (
    "time_machine", "merge_conflict", "run_not_editable", "gate_decided", "decision_reserved",
    "recovery_reset_required")


def _merge_conflict_open(flow_id: str) -> bool:
    store = get_store()
    if not store.table_exists("git_merge_session"):
        return False
    return store._fetch_one("SELECT 1 AS n FROM git_merge_session WHERE group_id = ? AND status = 'open' LIMIT 1",
                            [flow_id]) is not None


def lock_reason(flow_id: str, node_key: str) -> Optional[str]:
    """Why the gate's settings cannot change now (a reason code), or None when they can. Read-only; call it inside
    the Flow guard when the answer decides a write."""
    from flowgate.graph_time_machine.operation import busy
    if busy(flow_id):
        return TIME_MACHINE
    if _merge_conflict_open(flow_id):
        return MERGE_CONFLICT
    if repository.reset_required(flow_id, node_key) is not None:
        return RECOVERY_RESET_REQUIRED
    run = run_store.current_run(flow_id)
    if not run:
        return None
    if run["status"] not in EDITABLE_RUN_STATUSES:
        return RUN_NOT_EDITABLE
    node = next((r for r in run_store.node_runs(run["run_id"]) if r["node_key"] == node_key), None)
    if node is not None and node["status"] not in EDITABLE_NODE_STATUSES:
        return GATE_DECIDED
    if repository.reservation(run["run_id"], node_key) is not None:
        return DECISION_RESERVED
    return None


def require_unlocked(flow_id: str, node_key: str) -> None:
    reason = lock_reason(flow_id, node_key)
    if reason is not None:
        raise GraphApiError(409, LOCKED_CODE, {"flow_id": flow_id, "node_key": node_key, "reason": reason})


def locked(flow_id: str, node_key: str) -> bool:
    return lock_reason(flow_id, node_key) is not None

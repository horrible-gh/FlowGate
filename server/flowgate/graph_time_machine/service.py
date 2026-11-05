"""Graph Time Machine assessment, reused by the T#13 API and T#15 response.

inspect() is an internal read service, NOT a preview endpoint.
require_safe() is called inside the executing API's existing Flow guard. It
returns the same assessment on success or raises 409 graph_time_machine_blocked
with the assessment in error.params. The caller must persist its operation in
that guard, before releasing it to perform Git work. This snapshot is not a lease.
"""
from modules.flow_gate.db.connection import in_transaction
from modules.flow_gate.graph_workflow.api.graph_messages import time_machine_reasons
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

from .impact import calculate
from .repository import read_snapshot


def _assess(flow_id: str, node_key: str, locale: str) -> dict:
    result = calculate(read_snapshot(flow_id), node_key)
    result["reasons"] = time_machine_reasons(result["reasons"], locale)
    return result


def inspect(flow_id: str, node_key: str, locale: str = "ko") -> dict:
    return with_flow_guard(flow_id, lambda: _assess(flow_id, node_key, locale))


def require_safe(flow_id: str, node_key: str, locale: str = "ko") -> dict:
    assert in_transaction(), "Time Machine's deciding read requires the caller's Flow guard"
    from flowgate.graph_run.editing import LOCKED
    from flowgate.gate_decisions.run_hold import gate_live
    from modules.flow_gate.graph_workflow.store import run_store
    run = run_store.current_run(flow_id)
    active = bool(run and gate_live(run["run_id"]))
    if run and (run["status"] in LOCKED or active):
        params = {"flow_id": flow_id, "run_id": run["run_id"], "status": run["status"]}
        if active:
            params["reason"] = "gate_git_active"
        raise GraphApiError(409, "graph_run_locked", params)
    result = _assess(flow_id, node_key, locale)
    if not result["allowed"]:
        raise GraphApiError(409, "graph_time_machine_blocked", result)
    return result

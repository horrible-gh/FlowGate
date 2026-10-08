"""Gate decision (D0004 §2.2 게이트 노드 실행기, P0008 S6-1, L0009 §2.6.3 / §2.6.4 / §2.6.7).

``POST /runs/{run_id}/nodes/{node_key}/gate-decision`` runs in the run's Flow guard like stop /
resume: ``flow_id`` from outside the guard picks the guard only, the run is re-read inside it and
every check and write uses that row (L0009 §2.1.1). The checks write nothing and their order
fixes which error wins; the run's policies come from its snapshot only (L0009 §2.6.2).

What a confirm or a reject *means* is the ``gate_node`` slot (D0004 §8 Q-11). Until Q-11 is
answered the slot is ``undecided`` and every gate decision is refused with
``run_policy_undecided`` naming ``gate_node`` (P0008 S6-1); a revision with a gate node cannot be
confirmed either (P0008 S3-5 ``rule_undecided/gate_node``), so the normal path never gets here.
Slot contract: ``gate_node.judge(node_key, decision, comment) -> "confirmed" | "rejected"``, which
becomes the node event ``gate_confirmed`` / ``gate_rejected`` of the L0009 §3.2 table.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.graph_workflow.domain.frontier import ProgrammingError, RunEvent, resolve
from modules.flow_gate.graph_workflow.domain.run_policies import PolicyUndecided, RULE_REF, policies_of
from modules.flow_gate.graph_workflow.store import run_store

from .engine_router import read_ownership
from .errors import GraphApiError
from .graph_service import require_writable
from .run_apply import apply_decision, load_context, read_run_state
from .run_service import _guarded_run, _missing, _ownership_error, _restore_check_once, _undecided, run_payload

GATE_POLICY = "gate_node"
GATE_EVENTS = {"confirmed": "gate_confirmed", "rejected": "gate_rejected"}
GATE_UNDECIDED_MESSAGE_KEY = "gate_policy_undecided"


def _gate_undecided(flow_id: str, revision_no: int, missing: list[dict]) -> GraphApiError:
    error = _undecided(flow_id, revision_no, missing)
    error.message_key = GATE_UNDECIDED_MESSAGE_KEY            # P0008 S6-1 message (envelope picks the locale)
    return error


def decide_gate(group: dict, run_id: str, node_key: str, decision: str, comment: Optional[str],
                user_id: str) -> dict:
    """-> 200 ``{"run": <run with node_runs>}`` (P0008 S6-1). Order of the checks:

    0. run missing (outside or re-read) -> 404 run_not_found
    1. run not running -> 409 run_not_running (a stopped run takes no gate decision, L0009 §2.6.7)
    2. ownership not graph -> 409 (as resume, L0009 §2.2.2)
    3. node not in the run's revision, or not a gate -> 409 node_not_gate
    4. snapshot cannot be restored -> 500 internal_error (L0009 E-33, nothing written)
    5. a needed policy (the revision's, plus ``gate_node``) undecided -> 409 run_policy_undecided
    6. gate node not open -> 409 node_not_open
    7. judge -> node event -> resolver -> apply (one guard transaction)
    """
    require_writable(group["group_id"])
    _restore_check_once()

    def body_of(run: dict, jobs: list) -> dict:
        flow_id, revision_no = run["group_id"], int(run["revision_no"])
        if run["status"] != "running":                                           # 1
            raise GraphApiError(409, "run_not_running", {"run_id": run_id, "status": run["status"]})
        own = read_ownership(flow_id)                                             # 2
        if own.state != "graph":
            raise _ownership_error(flow_id, own)
        ctx = load_context(run, group.get("project_id"), user_id, jobs)
        row = ctx.node_rows.get(node_key)                                         # 3
        if row is None or row.get("node_type") != "gate":
            raise GraphApiError(409, "node_not_gate", {"run_id": run_id, "node_key": node_key,
                                                       "node_type": (row or {}).get("node_type")})
        entries = run_store.policy_entries(run_id)
        policies = policies_of(entries)                                          # 4 (E-33 -> 500)
        missing = _missing(ctx, entries, extra={GATE_POLICY})                    # 5
        if missing:
            raise _gate_undecided(flow_id, revision_no, missing)
        state = read_run_state(ctx)
        node = state.node(node_key)
        if node is None or node.status != "open":                                # 6
            raise GraphApiError(409, "node_not_open", {"run_id": run_id, "node_key": node_key,
                                                       "status": node.status if node else None})
        try:
            outcome = policies[GATE_POLICY].judge(node_key, decision, comment)   # 7
            kind = GATE_EVENTS.get(outcome)
            if kind is None:
                raise ProgrammingError(f"gate_node policy returned {outcome!r}")
            d = resolve(ctx.revision, state, RunEvent(kind, node_key), policies)
            if d.ignored:                                                         # open + gate event is in §3.2
                raise AssertionError(f"gate event {kind} ignored on open node {node_key}")
            apply_decision(ctx, d, note={"actor_user_id": user_id, "reason": comment})
        except PolicyUndecided as exc:                                            # E-21 (defence)
            raise _gate_undecided(flow_id, revision_no, [{"policy": exc.key, "rule_ref": RULE_REF[exc.key]}])
        return {"run": run_payload(run_id, with_nodes=True)}

    return _guarded_run(run_id, body_of)

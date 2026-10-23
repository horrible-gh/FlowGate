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

flowgate.v02.0085 T#2 (D0007 §3.1 / §7.2): Q-11 is answered by the product rule ``human_confirm_v1``
(``flowgate.graph_policies.gate``). Its implementation carries the decision intake (``intake``): the request key and
its replay, the stored decision, the confirm's Git apply / archive job, the D0007 refusals. A run whose snapshot holds
that rule is decided there (``_intake_of``: the run's own snapshot, never the current default); a run on another
decided rule (a test double) keeps the decision below, and an ``undecided`` one is refused as before.

The gate part of a run read is not that choice (``gate_reader``): a run whose stored snapshot still holds ``undecided``
(started before the rule was registered, D0007 §3.1: kept as it is) shows its gates' history and state all the same,
read by the gate rule registered now, with ``can_decide`` false - the decision itself stays refused.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.graph_workflow.domain import policies as registered_policies
from modules.flow_gate.graph_workflow.domain.frontier import ProgrammingError, RunEvent, resolve
from modules.flow_gate.graph_workflow.domain.run_policies import PolicyUndecided, RULE_REF, implementation, policies_of
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


def _intake_of(run_id: str):
    """The decision intake of the run's own ``gate_node`` rule (flowgate.v02.0085 T#2), or None: no run, no snapshot
    entry, an ``undecided`` / unregistered entry, or a rule without an intake."""
    entry = run_store.policy_entries(run_id).get(GATE_POLICY) if run_store.run_by_id(run_id) else None
    if not entry:
        return None
    impl = implementation(GATE_POLICY, str(entry.get("value")), str(entry.get("impl_version")))
    intake = getattr(impl, "intake", None) if impl is not None and entry.get("value") != "undecided" else None
    return intake if intake is not None and callable(getattr(intake, "decide", None)) else None


def gate_reader(run_id: str):
    """The reader of the gate part of a run read (D0007 §7.2, flowgate.v02.0085 T#2), or None: the run's own rule's
    intake, else the intake of the gate rule registered now. Reading never needs the run's rule to decide: a run whose
    snapshot is ``undecided`` is read the same way (its decision is still refused by ``decide_gate``)."""
    own = _intake_of(run_id)
    if own is not None and callable(getattr(own, "run_wire", None)):
        return own
    impl = registered_policies.current().get(GATE_POLICY)
    reader = getattr(impl, "intake", None) if impl is not None and not isinstance(impl, str) else None
    return reader if reader is not None and callable(getattr(reader, "run_wire", None)) else None


def decide_gate(group: dict, run_id: str, node_key: str, decision: str, comment: Optional[str],
                user_id: str, intent: Optional[dict] = None) -> dict:
    """-> 200 ``{"run": <run with node_runs>}`` (P0008 S6-1). flowgate.v02.0085 T#2: a run on the product rule goes
    to its intake (``intent``: the request key and expected values, D0007 §7.2). Order of the checks:

    0. run missing (outside or re-read) -> 404 run_not_found
    1. run not running -> 409 run_not_running (a paused run takes no gate decision, L0009 §2.6.7); 0061 T#2: an
       idle run takes it (an open gate is an idle run's ``waiting_approval``) and its propagation waits for the
       next command
    2. ownership not graph -> 409 (as resume, L0009 §2.2.2)
    3. node not in the run's revision, or not a gate -> 409 node_not_gate
    4. snapshot cannot be restored -> 500 internal_error (L0009 E-33, nothing written)
    5. a needed policy (the revision's, plus ``gate_node``) undecided -> 409 run_policy_undecided
    6. gate node not open -> 409 node_not_open
    7. judge -> node event -> resolver -> apply (one guard transaction)
    """
    require_writable(group["group_id"])
    _restore_check_once()
    intake = _intake_of(run_id)
    if intake is not None:
        return intake.decide(group, run_id, node_key, decision, comment, user_id, intent or {})

    def body_of(run: dict, jobs: list) -> dict:
        flow_id, revision_no = run["group_id"], int(run["revision_no"])
        if run["status"] not in (run_store.RUNNING, run_store.IDLE):             # 1
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

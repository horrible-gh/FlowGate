"""Flow engine router (D0004 §3.1, L0009 §2.2) — the one place that says which engine owns a Flow.

Two facts decide it: a Graph assignment record (DB0010 T13) and a v0.1 sequence
(``find_sequence_by_group_root``, the v0.1 lookup itself — not re-implemented). Before migration
143 every Flow has "no assignment record" (DB0010 §1). The v0.1 contact delegation lives in
``v01_contact``; Graph requests check ownership here inside their Flow guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from modules.flow_gate.graph_workflow.domain.ownership import judge_ownership
from modules.flow_gate.graph_workflow.projection.flow_snapshot import read_sequence
from modules.flow_gate.graph_workflow.store import assignment_store, definition_store

from .errors import GraphApiError


@dataclass
class Ownership:
    state: str                          # "unassigned" | "v01" | "graph" | "conflict"
    sequence: Optional[dict]            # v0.1 workflow_sequences row, or None
    assignment: Optional[dict]          # graph_assignments row, or None

    @property
    def sequence_root_doc_id(self) -> Optional[str]:
        return self.sequence.get("doc_id") if self.sequence else None


def judge(flow_id: str, sequence: Optional[dict]) -> Ownership:
    record = assignment_store.assignment(flow_id)
    return Ownership(judge_ownership(record is not None, sequence is not None), sequence, record)


def read_ownership(flow_id: str) -> Ownership:
    """L0009 §2.2.1 ``read_ownership``. Inside a Flow guard this is the deciding read."""
    sequence, _rows = read_sequence(flow_id)
    return judge(flow_id, sequence)


def block(flow_id: str, own: Ownership) -> dict:
    """P0008 §2.1 ``ownership``."""
    graph_side = own.state in ("graph", "conflict")
    return {
        "state": own.state,
        "sequence_root_doc_id": own.sequence_root_doc_id if own.state in ("v01", "conflict") else None,
        "graph_revision_no": definition_store.latest_revision_no(flow_id) if graph_side else None,
        "assigned_at": (own.assignment or {}).get("assigned_at") if graph_side else None,
    }


def require_graph_editable(flow_id: str, own: Ownership) -> None:
    """L0009 §2.2.2: draft save / validation / confirmation need ``unassigned`` or ``graph``."""
    if own.state == "v01":
        raise GraphApiError(409, "flow_owned_by_v01", {
            "flow_id": flow_id, "ownership": "v01", "sequence_root_doc_id": own.sequence_root_doc_id,
        })
    if own.state == "conflict":
        raise GraphApiError(409, "flow_ownership_conflict", {
            "flow_id": flow_id, "ownership": "conflict", "sequence_root_doc_id": own.sequence_root_doc_id,
            "graph_revision_no": definition_store.latest_revision_no(flow_id),
        })


def confirm_block_reason(state: str) -> Optional[str]:
    """P0008 §5.2 ``draft.blocked_reason``: why a draft cannot be confirmed in this state."""
    return {"v01": "flow_owned_by_v01", "conflict": "flow_ownership_conflict"}.get(state)

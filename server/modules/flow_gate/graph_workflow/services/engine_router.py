"""Flow engine router — read-only ownership judgement for S1/S2 (D0004 §3.1, L0009 §2.2.1).

The Graph assignment table (``graph_assignments``) arrives with S4 (DB0010 M3). Until it
exists every Flow has "no assignment record" (DB0010 §1), so ownership is decided by the v0.1
sequence alone. No v0.1 contact delegates here yet (S4).
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.domain.ownership import judge_ownership

ASSIGNMENT_TABLE = "graph_assignments"


def assignment_record(flow_id: str) -> Optional[dict]:
    store = get_store()
    if not store.table_exists(ASSIGNMENT_TABLE):
        return None
    return store._fetch_one(
        "SELECT group_id, assigned_at FROM graph_assignments WHERE group_id = ?", [flow_id]
    )


def ownership_block(flow_id: str, sequence: Optional[dict]) -> dict:
    """P0008 §2.1 ``ownership`` for a Flow whose v0.1 sequence (or None) is already read.

    ``graph_revision_no`` (latest confirmed revision) needs the S3 revision store and stays
    null until then.
    """
    record = assignment_record(flow_id)
    state = judge_ownership(record is not None, sequence is not None)
    return {
        "state": state,
        "sequence_root_doc_id": sequence.get("doc_id") if sequence and state in ("v01", "conflict") else None,
        "graph_revision_no": None,
        "assigned_at": record.get("assigned_at") if record else None,
    }

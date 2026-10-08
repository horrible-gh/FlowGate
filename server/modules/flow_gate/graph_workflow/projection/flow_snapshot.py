"""Read-only snapshot of one Flow's v0.1 data (D0004 §4.1 '읽기' rows). Never writes v0.1 tables."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import workflow_sequences as db_sequences
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.domain.canvas_mapping import is_alive


@dataclass
class FlowSnapshot:
    flow_id: str
    documents: list[dict] = field(default_factory=list)   # every document of the group, any status
    sequence: Optional[dict] = None                         # v0.1 workflow_sequences row (root R/B)
    rows: list[dict] = field(default_factory=list)          # workflow_sequence_items, sort order

    @property
    def docs_by_id(self) -> dict[str, dict]:
        return {d["doc_id"]: d for d in self.documents if d.get("doc_id")}


def read_live_flow(flow_id: str) -> Optional[dict]:
    """The groups row, or None when it does not exist or is soft deleted (P0008 §1.4)."""
    group = db_groups.get_by_id(flow_id)
    if not group or group.get("deleted_at"):
        return None
    return group


def read_sequence(flow_id: str) -> tuple[Optional[dict], list[dict]]:
    """v0.1 sequence of the Flow through the v0.1 root lookup (L0009 §2.2.1, not re-implemented)."""
    sequence = db_sequences.find_sequence_by_group_root(flow_id)
    if sequence is None:
        return None, []
    rows = db_sequences.get_sequence_items(int(sequence["id"])) or []
    return sequence, sorted(rows, key=lambda r: (r.get("sort_order") or 0, r.get("id") or 0))


def read_snapshot(flow_id: str) -> FlowSnapshot:
    sequence, rows = read_sequence(flow_id)
    documents = db_documents.get_documents_by_group_id(flow_id) or []
    return FlowSnapshot(flow_id=flow_id, documents=list(documents), sequence=sequence, rows=list(rows))


def protected_ids(rows: Iterable[dict]) -> set[int]:
    return db_sequences.protected_row_ids(rows)


def lookup_documents(doc_ids: Iterable[str]) -> dict[str, dict]:
    """Documents outside the snapshot (other Flows), for external relations and slot results."""
    ids = [d for d in dict.fromkeys(doc_ids) if d]
    return db_documents.get_documents_by_ids(ids) if ids else {}


def registered_doc_types(project_id: Optional[str]) -> set[str]:
    """Document type codes registered for the project (system rows + the project's own rows)."""
    rows = get_store()._fetch_all(
        "SELECT DISTINCT type_code FROM document_types WHERE project_id IS NULL OR project_id = ?",
        [project_id or ""],
    )
    return {str(r["type_code"]).upper() for r in rows or [] if r.get("type_code")}


def alive_documents(snapshot: FlowSnapshot) -> list[dict]:
    """The Flow's live documents (P0008 §2.4)."""
    return [d for d in snapshot.documents if is_alive(d, snapshot.flow_id)]

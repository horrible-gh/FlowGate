"""Canvas display mapping tables (P0008 §2.2 kind, §2.3 state, §2.4 alive, §2.5 collapse, §2.6 editable).

Every value here is an open key for the client (P0008 §1.5): an unknown input maps to
``"unknown"`` (kind) or ``None`` (state) instead of failing.
"""
from __future__ import annotations

from typing import Optional

# P0008 §2.2 — v0.1 type_code -> Canvas kind. Types not listed are "unknown".
_KIND_BY_TYPE = {
    "R": "requirement", "B": "requirement",
    "DS": "design", "D": "design", "P": "design", "L": "design", "DB": "design",
    "N": "research", "NR": "research",
    "WP": "plan",
    "T": "task", "TR": "task", "T2": "task", "TR2": "task",
    "TS": "test", "TSR": "test", "V": "test",
    "AC": "approval", "RJ": "approval", "DC": "approval", "C": "approval",
    "M": "memo", "CH": "memo", "Q": "memo", "A": "memo",
}

# P0008 §2.3 — documents.doc_review_status -> Canvas state.
_STATE_BY_REVIEW = {
    "approved": "done",
    "wf_done": "done",
    "pending_review": "review",
    "revised": "review",
    "rejected": "blocked",
    "wf_in_progress": "in_progress",
}
# P0008 §2.3 — graph_node_runs.status -> Canvas state (a value L adds later maps to None).
_STATE_BY_NODE_RUN = {
    "pending": "draft",
    "open": "in_progress",
    "review": "review",
    "done": "done",
    "rejected": "blocked",
}
# Types that are complete the moment they exist (review status stays null).
_DONE_ON_CREATE_TYPES = frozenset({"M", "CH"})

# P0008 §2.5 — collapse groups.
COLLAPSE_GROUPS = {
    "conversation": frozenset({"Q", "A", "CH"}),
    "memo": frozenset({"M"}),
}

# P0008 §2.4 / v0.1 GROUP_HISTORY_EXCLUDED_STATUSES: a deleted document.
CANCELLED_STATUS = "cancelled"

OWNERSHIP_STATES = ("unassigned", "v01", "graph", "conflict")


def normalize_type(type_code) -> str:
    return str(type_code or "").strip().upper()


def kind_for_type(type_code) -> str:
    return _KIND_BY_TYPE.get(normalize_type(type_code), "unknown")


def document_state(doc_review_status, type_code) -> Optional[str]:
    """State of a node shown through a document (P0008 §2.3 first table)."""
    if doc_review_status is None or doc_review_status == "":
        return "done" if normalize_type(type_code) in _DONE_ON_CREATE_TYPES else "in_progress"
    return _STATE_BY_REVIEW.get(str(doc_review_status))


def node_run_state(status, run: Optional[dict] = None) -> Optional[str]:
    """State of a Graph node from its ``node_run.status`` (P0008 §2.3 'Graph 노드 → state').

    flowgate.v02.0083 T#4: ``run`` is the node's run wire (``node_run_wire``). An open node whose writer ended
    without a result — the wire's ``dispatch.error_code`` (``no_result``, a failed AI start or run) — is ``blocked``,
    not ``in_progress``: nothing works on it until a person resumes or recovers it. Any other open node stays
    ``in_progress`` (there is no Canvas state for a writer still to come)."""
    if status == "open" and ((run or {}).get("dispatch") or {}).get("error_code"):
        return "blocked"
    return _STATE_BY_NODE_RUN.get(str(status)) if status is not None else "draft"


def slot_state(result_doc_id, doc_review_status, type_code) -> Optional[str]:
    """State of a v0.1 sequence slot (P0008 §2.3 'v0.1 슬롯 → state')."""
    if result_doc_id is None:
        return "draft"
    return document_state(doc_review_status, type_code)


def is_alive(doc: dict, flow_id: str) -> bool:
    """P0008 §2.4 / L0009 §2.0 '살아 있는 문서'."""
    return (
        doc.get("group_id") == flow_id
        and doc.get("status") != CANCELLED_STATUS
        and not doc.get("superseded_by")
    )


def collapse_group_of(type_code) -> Optional[str]:
    t = normalize_type(type_code)
    for key, types in COLLAPSE_GROUPS.items():
        if t in types:
            return key
    return None


def parse_collapse(raw: Optional[str]) -> tuple[list[str], list[str]]:
    """Split the ``collapse`` query into (applied keys, ignored unknown keys), order kept."""
    applied: list[str] = []
    ignored: list[str] = []
    for part in (raw or "").split(","):
        key = part.strip()
        if not key:
            continue
        bucket = applied if key in COLLAPSE_GROUPS else ignored
        if key not in bucket:
            bucket.append(key)
    return applied, ignored


def editable_for(ownership_state: str, origin: str) -> dict:
    """P0008 §2.6 editable table. S1/S2 never draws draft nodes, so structure stays false."""
    structure = ownership_state in ("unassigned", "graph") and origin == "draft_node"
    return {"move": True, "structure": structure}

"""Workflow sequence CRUD — workflow_sequences / workflow_sequence_items.

Follows the sqloader.load usage pattern (only use SQL registered in queries.json).
Do not add new inline SQL.
"""
from __future__ import annotations

from typing import Optional

from .connection import FlowGateStore, get_store


def _sql(store, key: str) -> str:
    """Resolve registered SQL for stores that omit _sql in focused tests."""
    if hasattr(store, "_sql"):
        return store._sql(key)
    return FlowGateStore._sql(store, key)


# ── Sequence header ───────────────────────────────────────────────────────────

def get_sequence_by_doc_id(doc_id: str) -> Optional[dict]:
    """Return the workflow_sequences row by doc_id."""
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_sequence_by_doc_id")
    return store._fetch_one(sql, [doc_id])


def get_sequence_by_id(seq_id: int) -> Optional[dict]:
    """Return the workflow_sequences row by PK(id)."""
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_sequence_by_id")
    return store._fetch_one(sql, [seq_id])


def insert_sequence(doc_id: str) -> None:
    """Insert into workflow_sequences (initial one-time save)."""
    store = get_store()
    sql = _sql(store, "workflow_sequences.insert_sequence")
    store._execute(sql, [doc_id])


# ── Sequence items ────────────────────────────────────────────────────────────

def get_sequence_items(sequence_id: int) -> list[dict]:
    """Return all sequence items (sort_order ASC)."""
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_sequence_items")
    return store._fetch_all(sql, [sequence_id])


def get_effective_head(sequence_id: int) -> Optional[dict]:
    """Return the current effective head item.

    Priority (D030 §2 SSOT):
      1. If a slot already has result_doc_id and its result document is not approved,
         return that item (prevents re-entry while review is pending)
      2. Otherwise, return the first slot whose result_doc_id is still NULL
    """
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_effective_head")
    return store._fetch_one(sql, [sequence_id])


def get_in_progress_head_by_group(group_id: str, project_id: str) -> Optional[dict]:
    """Return the current head item in in_progress status for the group + project.

    Join workflow_sequences -> documents -> groups.
    The result includes additional seq_id and parent_doc_id columns.

    Requires ``result_doc_id IS NOT NULL`` (slot already linked to a result document
    that is not yet approved). Use :func:`get_pending_head_by_group` for first-time
    worker inbox registration when ``result_doc_id`` is still NULL.
    """
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_in_progress_head_by_group")
    return store._fetch_one(sql, [group_id, project_id])


def get_pending_head_by_group(group_id: str, project_id: str) -> Optional[dict]:
    """Return the effective workflow head for a group (NR150 / D030 §2).

    Same semantics as :func:`get_effective_head`, scoped by group + project via the
    parent R document's sequence. Includes slots with ``result_doc_id IS NULL`` (first
    registration) and slots whose result document is not yet approved.
    """
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_pending_head_by_group")
    return store._fetch_one(sql, [group_id, project_id])


def get_sequence_head_pending(sequence_id: int) -> Optional[dict]:
    """Return the first pending item in the sequence (for looking up the next head after AC)."""
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_sequence_head_pending")
    return store._fetch_one(sql, [sequence_id])


def mark_sequence_done(seq_id: int, done_at: str) -> None:
    """Mark the sequence as completed — update head_advanced_at (called when the final item reaches AC)."""
    store = get_store()
    sql = _sql(store, "workflow_sequences.advance_head")
    store._execute(sql, [done_at, seq_id])


def insert_sequence_item(
    sequence_id: int,
    item_seq: int,
    type_: str,
    label: str,
    doc_class: str,
    sort_order: int,
    note: str = "",
    source_doc_id: Optional[str] = None,
    source_revision_no: Optional[int] = None,
    provider_id: Optional[str] = None,
    provider_display_name: Optional[str] = None,
) -> None:
    """Insert a sequence item.

    0399 DB0012 §2/§4: ``note`` · ``source_doc_id`` · ``source_revision_no`` carry the step
    note and its origin (which work plan poured the row, at which revision). They default to
    the same values migration 079 gives a pre-existing row — an empty note and no source — so
    every existing caller that only knows about the structural columns keeps working and
    stores "this row did not come from a plan", which is the truth for those paths.
    """
    store = get_store()
    sql = _sql(store, "workflow_sequences.insert_sequence_item")
    store._execute(sql, [
        sequence_id, item_seq, type_, label, doc_class, sort_order,
        note or "", source_doc_id, source_revision_no, provider_id, provider_display_name,
    ])


def delete_pending_items(sequence_id: int) -> None:
    """Delete all items in PENDING status (for edit mode)."""
    store = get_store()
    sql = _sql(store, "workflow_sequences.delete_pending_items")
    store._execute(sql, [sequence_id])


def get_max_item_seq(sequence_id: int) -> int:
    """Return the maximum item_seq in the sequence (0 = no items). Used to avoid conflicts during editing."""
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_max_item_seq")
    row = store._fetch_one(sql, [sequence_id])
    return row["max_seq"] if row else 0


# ── result_doc_id helpers ─────────────────────────────────────────────────────

def get_item_by_result_doc_id(result_doc_id: str) -> Optional[dict]:
    """Return the workflow_sequence_items row by result_doc_id (reverse lookup).

    DB004 §3.1: uses the idx_wfseq_items_result_doc index.
    """
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_sequence_item_by_result_doc_id")
    return store._fetch_one(sql, [result_doc_id])


def get_sequence_for_member_doc(doc_id: str) -> Optional[dict]:
    """Resolve the workflow sequence a document belongs to (root *or* produced child).

    ``workflow_sequences.doc_id`` keys the sequence by its *root* document (the R/B
    that owns the workflow), so :func:`get_sequence_by_doc_id` only matches that root.
    A produced child — a slot's ``result_doc_id`` such as an approved N — is a member
    of the same sequence but is not its root. After next-step creation the FE lands on
    that child (openAfter), so a follow-up create must resolve the sequence from the
    child too; otherwise the child's prev_doc_id yields no sequence → 422 (0048 TR0009).
    """
    seq = get_sequence_by_doc_id(doc_id)
    if seq is not None:
        return seq
    item = get_item_by_result_doc_id(doc_id)
    if item is None:
        return None
    return get_sequence_by_id(item["sequence_id"])


def is_orphaned_workflow_member(doc_id: str) -> bool:
    """Return whether a workflow-planned group document is not attached to any slot.

    A group without a decided R/B sequence is not orphaned yet. Sequence roots,
    auto-completed M/CH documents, Q/A conversations, and the synthetic AC final
    approval gate intentionally do not occupy result slots and are therefore excluded.
    """
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.documents.constants import NON_SLOT_WORKFLOW_TYPES

    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return False

    type_code = str(doc.get("type_code") or "").upper()
    if type_code in NON_SLOT_WORKFLOW_TYPES:
        return False

    group_id = doc.get("group_id")
    if not group_id:
        return False

    roots = [
        row for row in (db_documents.get_documents_by_group_id(group_id) or [])
        if str(row.get("type_code") or "").upper() in {"R", "B"}
    ]
    if any(root.get("doc_id") == doc_id for root in roots):
        return False

    has_decided_sequence = any(
        get_sequence_by_doc_id(root.get("doc_id")) is not None
        for root in roots
        if root.get("doc_id")
    )
    if not has_decided_sequence:
        return False

    return get_sequence_for_member_doc(doc_id) is None


def get_item_by_id(item_id: int) -> Optional[dict]:
    """Return the workflow_sequence_items row by PK(id).

    0457 T0005: the occupancy invariant in
    :func:`modules.flow_gate.workflow.pipeline_service.register_workflow_result` has to
    read back *which document a given slot holds*. Every other selector in this module
    answers "which slot is the head" — a different question that must not be reused for
    this one, which is exactly the substitution B0001 was made of.
    """
    store = get_store()
    sql = _sql(store, "workflow_sequences.get_sequence_item_by_id")
    return store._fetch_one(sql, [item_id])


def claim_item_result_doc_id(item_id: int, result_doc_id: str) -> Optional[dict]:
    """Claim a slot for ``result_doc_id`` — only when it is empty or already holds it.

    0457 B0001 / NR0003 §7-3: :func:`set_item_result_doc_id` overwrites unconditionally,
    so a caller that resolved the wrong slot silently evicted the document sitting in it.

    The guard cannot be a read-then-write in Python. Under PostgreSQL READ COMMITTED two
    concurrent registrations would both read an empty slot and the later UPDATE would
    win; under SQLite the same shape becomes a lock-upgrade error rather than a verdict.
    So the condition travels *with* the UPDATE —
    ``WHERE id = ? AND (result_doc_id IS NULL OR result_doc_id = ?)`` — which both engines
    re-evaluate against the row as it stands when the write lock is granted. A losing
    claim matches zero rows, so it leaves neither ``result_doc_id`` nor ``updated_at``
    changed: a refused attempt is invisible in the data.

    Returns the item row as it stands after the attempt (``None`` when no such item
    exists). The claim succeeded iff that row's ``result_doc_id`` equals
    ``result_doc_id``; the caller makes that comparison so it can name both documents in
    the conflict it raises.
    """
    store = get_store()
    sql = _sql(store, "workflow_sequences.claim_item_result_doc_id")
    store._execute(sql, [result_doc_id, item_id, result_doc_id])
    return get_item_by_id(item_id)


def set_item_result_doc_id(item_id: int, result_doc_id: Optional[str]) -> None:
    """Set result_doc_id on the sequence item unconditionally (register / clear).

    Result *registration* goes through
    :func:`modules.flow_gate.workflow.pipeline_service.register_workflow_result`, which
    uses :func:`claim_item_result_doc_id` so it cannot evict another document. This
    unconditional writer remains for the paths that legitimately overwrite: clearing a
    slot (``result_doc_id=None``) on reopen, and filling a slot the caller has already
    established is empty.
    """
    store = get_store()
    sql = _sql(store, "workflow_sequences.set_item_result_doc_id")
    store._execute(sql, [result_doc_id, item_id])


def get_predecessor_result_doc_id(
    sequence_id: int, exclude_item_id: Optional[int] = None
) -> Optional[str]:
    """Return the result_doc_id of the most recently produced item in the sequence.

    "Most recently produced" = the item with the highest sort_order among items that
    have a result document, excluding ``exclude_item_id`` (typically the current head).
    Returns None when no prior item has produced a document yet — i.e. the sequence is
    at its first step and the only context is the sequence-owning R document.

    Used by the mention builders (T0004 / R0001) to anchor Section 1 'Document
    information' at the current step's predecessor document instead of the
    sequence-owning R. Pure Python over get_sequence_items() — no new SQL.
    """
    items = get_sequence_items(sequence_id)
    completed = [
        it for it in items
        if it.get("result_doc_id") and it.get("id") != exclude_item_id
    ]
    if not completed:
        return None
    pred = max(completed, key=lambda it: it.get("sort_order") or 0)
    return pred.get("result_doc_id")


def get_predecessor_result_doc_ids(
    sequence_id: int, exclude_item_id: Optional[int] = None, limit: int = 2
) -> list[str]:
    """Return up to ``limit`` most-recently-produced result_doc_ids (most recent first).

    Generalizes :func:`get_predecessor_result_doc_id` to the top-N produced items by
    sort_order (descending), excluding ``exclude_item_id`` (typically the current head).
    Returns ``[]`` when no prior item has produced a document yet.

    Used by advance_workflow to seed Section 3 'Reference documents' with the immediate
    predecessor and the one before it (R0001 / T0004: the worker should receive
    "previous document + the one before it + R" = 3 reference documents). Pure Python
    over get_sequence_items() — no new SQL.
    """
    items = get_sequence_items(sequence_id)
    completed = [
        it for it in items
        if it.get("result_doc_id") and it.get("id") != exclude_item_id
    ]
    completed.sort(key=lambda it: it.get("sort_order") or 0, reverse=True)
    return [it["result_doc_id"] for it in completed[:limit]]

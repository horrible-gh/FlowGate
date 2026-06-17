"""Document CRUD + state-machine service (aligned with D009 r3 / D017 r1).

State-machine transition table (based on D017 r1, using DB CHECK values):
  draft      → open, cancelled
  open       → in_review, cancelled
  in_review  → approved, rejected
  rejected   → open, cancelled        (resubmission allowed)
  approved   → closed, archived
  closed     → archived
  cancelled  → (terminal)
  archived   → (terminal)

Q auto-close rules:
  - When an A-type document is created, the Q document referenced by target_id
    → status='closed'
  - When close_group_documents() is called, unfinished Q documents in the group
    → status='closed'

CAS pattern: transition_state() checks for changes with SELECT after
UPDATE … WHERE status=<expected_status>. If a race is detected, it raises 409.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_events as db_events
from modules.flow_gate.db.connection import get_store, now_iso

# ── State-machine definitions ─────────────────────────────────────────────────

_VALID_TRANSITIONS: dict[str, list[str]] = {
    "draft":     ["open", "cancelled"],
    "open":      ["in_review", "cancelled"],
    "in_review": ["approved", "rejected"],
    "rejected":  ["open", "cancelled"],
    "approved":  ["closed", "archived"],
    "closed":    ["archived"],
    "cancelled": [],
    "archived":  [],
}

# Q document type code (auto-close target)
_Q_TYPE_CODE = "Q"
_A_TYPE_CODE = "A"
_TERMINAL_EDIT_STATUSES = frozenset({"cancelled", "archived"})
_WORKFLOW_ROOT_TYPES = frozenset({"R", "B"})


# ── CRUD ──────────────────────────────────────────────────────────────────────

def create_document(data: dict[str, Any], actor_user_id: str) -> dict:
    """Create a document and record workflow_event(doc_created).

    If an A-type document has target_id, automatically close the linked Q document.
    """
    doc = db_docs.create(data)

    db_events.create({
        "event_type": "doc_created",
        "project_id": doc["project_id"],
        "group_id": doc.get("group_id"),
        "document_id": doc["id"],
        "actor_user_id": actor_user_id,
        "to_state": doc["status"],
    })

    # Q auto-close: when an A document is created, close the Q document referenced by target_id
    if doc.get("type_code") == _A_TYPE_CODE and doc.get("target_id"):
        _auto_close_q(doc["target_id"], actor_user_id)

    return doc


def get_document(doc_id: str) -> Optional[dict]:
    """Look up a document by doc_id. Return None when it does not exist."""
    return db_docs.get_by_id(doc_id)


def is_final_approved(document: dict[str, Any]) -> bool:
    """Return whether the document's group completed final approval."""
    if document.get("doc_review_status") == "wf_done":
        return True

    project_id = document.get("project_id")
    group_id = document.get("group_id")
    if not project_id or not group_id:
        return False

    if document.get("type_code") in _WORKFLOW_ROOT_TYPES:
        return False

    group_docs = db_docs.list_documents(
        project_id=project_id,
        group_id=group_id,
        limit=200,
    )
    return any(
        item.get("type_code") in _WORKFLOW_ROOT_TYPES
        and item.get("doc_review_status") == "wf_done"
        for item in group_docs
    )


def is_document_editable(
    document: dict[str, Any],
    *,
    final_approved: bool | None = None,
) -> bool:
    """Allow edits until final approval, except for terminal lifecycle states."""
    if document.get("status") in _TERMINAL_EDIT_STATUSES:
        return False
    if final_approved is None:
        final_approved = is_final_approved(document)
    return not final_approved


def list_documents(
    project_id: str,
    module: str | None = None,
    group_id: str | None = None,
    type_code: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return the list of documents matching the conditions."""
    return db_docs.list_documents(
        project_id=project_id,
        module=module,
        group_id=group_id,
        type_code=type_code,
        status=status,
        limit=limit,
        offset=offset,
    )


def update_document(
    doc_id: str,
    updates: dict[str, Any],
    actor_user_id: str,
) -> dict:
    """Update the document's non-state fields.

    Use transition_state() for status changes.
    """
    existing = db_docs.get_by_id(doc_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    final_approved = is_final_approved(existing)
    if not is_document_editable(existing, final_approved=final_approved):
        if final_approved:
            detail = "Modification not allowed after final approval."
        else:
            detail = f"Modification not allowed for status: {existing.get('status')}"
        raise HTTPException(status_code=422, detail=detail)

    # Prevent direct status changes through update
    updates.pop("status", None)
    doc = db_docs.update(doc_id, updates)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    return doc


def delete_document(doc_id: str, actor_user_id: str) -> None:
    """Delete a document and record workflow_event(doc_deleted).

    Because of the workflow_events.document_id FK constraint, clear the reference
    to NULL before deletion (preserving history while removing only the document
    reference), then delete the document.
    """
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

    store = get_store()
    # Release the FK: set document_id to NULL (history is preserved)
    store._execute(
        "UPDATE workflow_events SET document_id = NULL WHERE document_id = ?",
        [doc["id"]],
    )
    db_docs.delete(doc_id)
    db_events.create({
        "event_type": "doc_deleted",
        "project_id": doc["project_id"],
        "group_id": doc.get("group_id"),
        "document_id": None,
        "actor_user_id": actor_user_id,
        "from_state": doc["status"],
    })


# ── State machine ─────────────────────────────────────────────────────────────

def transition_state(
    doc_id: str,
    to_state: str,
    actor_user_id: str,
    reason: str | None = None,
) -> dict:
    """Transition the document state (CAS UPDATE pattern).

    After validating the transition, atomically update with
    UPDATE … WHERE status=<current>.
    Then SELECT to verify the state actually changed; return 409 on mismatch.
    """
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

    from_state = doc["status"]

    if to_state not in _VALID_TRANSITIONS.get(from_state, []):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid transition: {from_state} → {to_state}. "
                f"Allowed transitions: {_VALID_TRANSITIONS.get(from_state, [])}"
            ),
        )

    store = get_store()
    store.update_cas(
        table="documents",
        row_id=doc_id,
        id_col="doc_id",
        expected_col="status",
        expected_val=from_state,
        updates={"status": to_state, "updated_at": now_iso()},
    )

    # Verify the CAS result
    refreshed = db_docs.get_by_id(doc_id)
    if refreshed is None or refreshed["status"] != to_state:
        raise HTTPException(
            status_code=409,
            detail="State transition conflict detected: another request changed the state first.",
        )

    db_events.create({
        "event_type": "state_changed",
        "project_id": refreshed["project_id"],
        "group_id": refreshed.get("group_id"),
        "document_id": refreshed["id"],
        "actor_user_id": actor_user_id,
        "from_state": from_state,
        "to_state": to_state,
        "metadata": reason,
    })

    return refreshed


def close_group_documents(group_id: str, actor_user_id: str) -> int:
    """When a group closes, bulk-close unfinished Q documents in that group.

    Returns: number of processed documents
    """
    store = get_store()
    open_q_docs = store._fetch_all(
        "SELECT * FROM documents "
        "WHERE group_id = ? AND type_code = ? AND status NOT IN (?, ?, ?)",
        [group_id, _Q_TYPE_CODE, "closed", "cancelled", "archived"],
    )

    count = 0
    for doc in open_q_docs:
        doc_id = doc["doc_id"]
        from_state = doc["status"]
        store.update_cas(
            table="documents",
            row_id=doc_id,
            id_col="doc_id",
            expected_col="status",
            expected_val=from_state,
            updates={"status": "closed", "updated_at": now_iso()},
        )
        db_events.create({
            "event_type": "state_changed",
            "project_id": doc["project_id"],
            "group_id": group_id,
            "document_id": doc["id"],
            "actor_user_id": actor_user_id,
            "from_state": from_state,
            "to_state": "closed",
            "metadata": "group_closed",
        })
        count += 1

    return count


# ── Internal helpers ──────────────────────────────────────────────────────────

def _auto_close_q(q_doc_id: str, actor_user_id: str) -> None:
    """Automatically transition the linked Q document to closed when an A document is created."""
    q_doc = db_docs.get_by_id(q_doc_id)
    if q_doc is None:
        return
    if q_doc.get("type_code") != _Q_TYPE_CODE:
        return
    if q_doc["status"] in ("closed", "cancelled", "archived"):
        return

    store = get_store()
    from_state = q_doc["status"]
    store.update_cas(
        table="documents",
        row_id=q_doc_id,
        id_col="doc_id",
        expected_col="status",
        expected_val=from_state,
        updates={"status": "closed", "updated_at": now_iso()},
    )
    db_events.create({
        "event_type": "state_changed",
        "project_id": q_doc["project_id"],
        "group_id": q_doc.get("group_id"),
        "document_id": q_doc["id"],
        "actor_user_id": actor_user_id,
        "from_state": from_state,
        "to_state": "closed",
        "metadata": "q_answered",
    })

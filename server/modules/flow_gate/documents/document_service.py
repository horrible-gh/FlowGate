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

import os
from typing import Any, Optional

from fastapi import HTTPException

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_events as db_events
from modules.flow_gate.db import workflow_sequences as db_sequences
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.numbering import id_formatter
from modules.flow_gate.storage import paths as storage_paths

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


# ── Root-type conversion (R ↔ B) ──────────────────────────────────────────────
#
# Background (NR0066.0003): a workflow root's type code is part of its identity —
# it is embedded in the doc_id ("…0001-R"), the stored filename ("0001-R_*.md")
# and every inbound reference. There is no whitelist field that flips it, by
# design. This implements the NR0003 §5 recommendation: an R↔B converter that is
# allowed ONLY on a pristine root (before the workflow decision is taken) and
# rewrites the identity atomically so referential integrity is preserved.

# Every column anywhere in the schema that stores a documents.doc_id value. The
# documents row itself is rewritten separately; this table covers inbound and
# self-references. Each entry is rewritten old_id → new_id inside one
# transaction. Unknown tables/columns (dialect or migration drift) are skipped.
_DOC_REFERENCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("documents", "target_id"),
    ("documents", "triggered_by"),
    ("documents", "superseded_by"),
    ("documents", "previous_tv"),
    ("documents", "previous_t"),
    ("documents", "previous_ds"),
    ("events", "doc_id"),
    ("events", "related_doc_id"),
    ("events", "related_target_id"),
    ("tv_scenarios", "tv_doc_id"),
    ("document_reviews", "doc_id"),
    ("questions", "doc_id"),
    ("document_mention_copies", "doc_id"),
    ("document_revisions", "doc_id"),
    ("document_revisions", "linked_doc_id"),
    ("tokens", "doc_ref"),
    ("workflow_sequences", "doc_id"),
    ("workflow_sequence_items", "result_doc_id"),
    ("remote_tool_grant", "report_doc_id"),
)

# Columns that reference the documents *integer* primary key (documents.id) rather
# than the textual doc_id. The portable converter (NR0108.0003 §7 candidate b)
# inserts a fresh identity row — which gets a new SERIAL id — and drops the old one,
# so these must be repointed old_id → new_id too. (workflow_events.document_id is the
# only such FK in the schema; doc_id-based references live in _DOC_REFERENCE_COLUMNS.)
_DOC_REFERENCE_ID_COLUMNS: tuple[tuple[str, str], ...] = (
    ("workflow_events", "document_id"),
)


def compute_converted_doc_id(doc_id: str, new_type: str) -> str:
    """Return the doc_id with its trailing document-code type suffix swapped.

    "flowgate.default.0066.0001-R" + "B" -> "flowgate.default.0066.0001-B".
    The seq and every other segment are preserved (group seq is type-agnostic,
    so only the suffix changes).
    """
    head, _, doc_code = doc_id.rpartition(".")
    if not head:
        raise ValueError(f"Malformed doc_id: {doc_id!r}")
    _cur_type, seq = id_formatter.parse_doc_code(doc_code)
    width = len(id_formatter.extract_numeric_suffix(doc_code))
    return f"{head}.{id_formatter.format_doc_code(new_type, seq, width)}"


def _converted_filename(filename: Optional[str], old_code: str, new_code: str) -> Optional[str]:
    """Swap the leading doc-code prefix of a stored filename ("0001-R_x.md")."""
    if not filename:
        return filename
    if filename.startswith(old_code):
        return new_code + filename[len(old_code):]
    return filename


def _rename_document_file(doc: dict, old_code: str, new_code: str) -> Optional[str]:
    """Rename the document's .md file to the new doc-code, returning the new
    storage-relative path (or the unchanged stored value when there is no file)."""
    stored = (doc.get("file_path") or "").strip()
    if not stored:
        return doc.get("file_path")
    project_id = doc.get("project_id")
    branch = (doc.get("branch") or "main") or "main"
    abs_path = storage_paths.resolve_storage_path(stored, project_id, branch=branch)
    if abs_path is None or not abs_path.is_file():
        # No physical file (e.g. a memo-only root). Still update the stored string
        # so the persisted path tracks the new code.
        new_basename = _converted_filename(os.path.basename(stored), old_code, new_code)
        return stored[: -len(os.path.basename(stored))] + new_basename if new_basename else stored
    new_name = _converted_filename(abs_path.name, old_code, new_code)
    new_abs = abs_path.with_name(new_name)
    if new_abs != abs_path:
        os.replace(abs_path, new_abs)
    return storage_paths.to_storage_relative(new_abs, project_id)


def convert_root_document_type(
    doc_id: str,
    new_type: str,
    actor_user_id: str,
) -> dict:
    """Convert a workflow root document between R and B (NR0066.0003 §5).

    Allowed only on a pristine root — i.e. before the workflow decision is taken
    (no child documents reference it and no workflow sequence has been created).
    Rewrites the doc_id, type_code, filename and every inbound reference atomically.

    Raises HTTPException on every rejection path (404 missing, 409 not convertible,
    422 invalid target type).
    """
    new_type = (new_type or "").strip().upper()
    if new_type not in _WORKFLOW_ROOT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Target type must be one of {sorted(_WORKFLOW_ROOT_TYPES)}; got {new_type!r}.",
        )

    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

    current_type = (doc.get("type_code") or "").upper()
    if current_type not in _WORKFLOW_ROOT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Only workflow root documents (R/B) can be converted; {doc_id} is {current_type}.",
        )
    if current_type == new_type:
        # Idempotent no-op: already the requested type.
        return doc

    # Gate: a workflow decision must not have been taken yet. The decision creates
    # the workflow_sequence and the first child document(s) referencing this root.
    if db_sequences.get_sequence_by_doc_id(doc_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Cannot convert root type after the workflow decision was taken.",
        )
    store = get_store()
    group_id = doc.get("group_id")
    if group_id:
        children = store._fetch_all(
            "SELECT doc_id FROM documents "
            "WHERE group_id = ? AND doc_id != ? AND (triggered_by = ? OR target_id = ?)",
            [group_id, doc_id, doc_id, doc_id],
        )
        if children:
            raise HTTPException(
                status_code=409,
                detail="Cannot convert root type: child documents already reference it.",
            )

    new_doc_id = compute_converted_doc_id(doc_id, new_type)
    if db_docs.get_by_id(new_doc_id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Target doc_id already exists: {new_doc_id}.",
        )

    old_code = doc_id.rpartition(".")[2]
    new_code = new_doc_id.rpartition(".")[2]
    new_file_path = _rename_document_file(doc, old_code, new_code)
    new_filename = _converted_filename(doc.get("filename"), old_code, new_code)

    old_int_id = doc.get("id")

    try:
        with store.transaction():
            # Portable identity rewrite (NR0108.0003 §7 candidate b). An in-place
            # UPDATE of documents.doc_id is impossible under IMMEDIATE foreign keys
            # (PostgreSQL/MySQL): the rename momentarily diverges from the rows that
            # reference it — and every document keeps at least its `created` event —
            # so the FK check aborts the transaction (B0108). The earlier SQLite-only
            # `PRAGMA defer_foreign_keys` masked this on SQLite but had no equivalent
            # on other backends. Instead create the new identity row FIRST, repoint
            # every reference to it, then drop the old row: a parent-first ordering
            # that satisfies IMMEDIATE FKs on every dialect and needs no deferral.
            new_row = dict(doc)
            new_row.pop("id", None)  # let the backend assign a fresh SERIAL id
            new_row["doc_id"] = new_doc_id
            new_row["type_code"] = new_type
            new_row["file_path"] = new_file_path
            new_row["filename"] = new_filename
            new_row["updated_at"] = now_iso()
            inserted = db_docs.create(new_row)
            new_int_id = (inserted or {}).get("id")

            # Repoint every inbound / self reference keyed on the textual doc_id.
            for table, column in _DOC_REFERENCE_COLUMNS:
                try:
                    store._execute(
                        f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                        [new_doc_id, doc_id],
                    )
                except Exception:
                    # Table/column absent for this dialect or migration level — skip.
                    continue

            # Repoint references keyed on the integer documents.id (new SERIAL).
            if old_int_id is not None and new_int_id is not None:
                for table, column in _DOC_REFERENCE_ID_COLUMNS:
                    try:
                        store._execute(
                            f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                            [new_int_id, old_int_id],
                        )
                    except Exception:
                        continue

            # Drop the old identity row; nothing references it anymore.
            store._execute("DELETE FROM documents WHERE doc_id = ?", [doc_id])
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive: surface as 409, file already moved is best-effort
        # Best-effort rollback of the file rename so disk and DB stay consistent.
        if new_file_path and new_file_path != doc.get("file_path"):
            try:
                back = storage_paths.resolve_storage_path(
                    new_file_path, doc.get("project_id"), branch=(doc.get("branch") or "main")
                )
                old_abs = storage_paths.resolve_storage_path(
                    doc.get("file_path"), doc.get("project_id"), branch=(doc.get("branch") or "main")
                )
                if back is not None and back.is_file() and old_abs is not None:
                    os.replace(back, old_abs)
            except Exception:
                pass
        raise HTTPException(status_code=409, detail=f"Conversion failed: {exc}") from exc

    refreshed = db_docs.get_by_id(new_doc_id)
    if refreshed is None:
        raise HTTPException(status_code=409, detail="Conversion failed: document missing after rewrite.")

    db_events.create({
        "event_type": "doc_type_converted",
        "project_id": refreshed["project_id"],
        "group_id": refreshed.get("group_id"),
        "document_id": refreshed["id"],
        "actor_user_id": actor_user_id,
        "metadata": f"{current_type}->{new_type} ({doc_id} -> {new_doc_id})",
    })
    return refreshed

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

import copy
import json
from typing import Any, Optional

from fastapi import HTTPException

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import tr_history_recovery as db_recovery
from modules.flow_gate.db import workflow_events as db_events
from modules.flow_gate.db import workflow_sequences as db_sequences
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.numbering import id_formatter
from modules.flow_gate.services import tr2_file_policy
from modules.flow_gate.services.git import lock_manager
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

    from modules.flow_gate.documents.live_root import is_live_root, is_succeeded_root

    # 0064 T#4 (L0005 2.10 P6): a root answers for itself, except one succeeded by a later document — that
    # one is history and follows the live root at its chain's end, as the console read (P5) does.
    if document.get("type_code") in _WORKFLOW_ROOT_TYPES and not is_succeeded_root(document):
        return False

    group_docs = db_docs.list_documents(
        project_id=project_id,
        group_id=group_id,
        limit=200,
    )

    # 0064 T#4 (L0005 2.10 P6): a root succeeded by a later document is not the group's live root.
    return any(
        is_live_root(item)
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
    if str(existing.get("type_code") or "").upper() == "WP" and "title" in updates:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "wp_title_is_derived",
                "message": "Work-plan titles are derived from canonical totals and cannot be edited manually.",
            },
        )

    final_approved = is_final_approved(existing)
    if not is_document_editable(existing, final_approved=final_approved):
        if final_approved:
            detail = "Modification not allowed after final approval."
        else:
            detail = f"Modification not allowed for status: {existing.get('status')}"
        raise HTTPException(status_code=422, detail=detail)

    # Prevent direct status changes through update
    updates.pop("status", None)
    ended = _chat_auto_title_ended(existing, updates)
    if ended is not None:
        updates["meta"] = ended
    doc = db_docs.update(doc_id, updates)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    return doc


# flowgate.v02.0047: a CH created owing its one AI title (flowgate/conversation/auto_title.py)
# stops owing it once its title is written here -- by hand, or as the AI title itself, which
# marks the debt paid before writing -- so the AI never replaces a title the user set.
_CHAT_AUTO_TITLE_KEY = "chat_auto_title"
_CHAT_AUTO_TITLE_OPEN = ("owed", "claimed")


def _chat_auto_title_ended(existing: dict, updates: dict[str, Any]) -> Optional[str]:
    if "title" not in updates or str(existing.get("type_code") or "").upper() != "CH":
        return None
    try:
        meta = json.loads(existing.get("meta") or "null")
    except ValueError:
        return None
    marker = meta.get(_CHAT_AUTO_TITLE_KEY) if isinstance(meta, dict) else None
    if not isinstance(marker, dict) or marker.get("state") not in _CHAT_AUTO_TITLE_OPEN:
        return None
    meta[_CHAT_AUTO_TITLE_KEY] = {**marker, "state": "manual"}
    return json.dumps(meta, ensure_ascii=False)


def _delete_document_row(doc: dict, actor_user_id: str) -> None:
    """Atomic DB half of deletion; caller owns any required source/history mutex."""
    store = get_store()
    # The transaction also enables SQLite FK enforcement, so inactive TR2 deletion may
    # cascade ledger rows while migration 125 preserves the succeeded attempt and
    # SET NULLs attempt.ledger_row_id.
    with store.transaction():
        store._execute(
            "UPDATE workflow_events SET document_id = NULL WHERE document_id = ?",
            [doc["id"]],
        )
        db_docs.delete(doc["doc_id"])
        db_events.create({
            "event_type": "doc_deleted",
            "project_id": doc["project_id"],
            "group_id": doc.get("group_id"),
            "document_id": None,
            "actor_user_id": actor_user_id,
            "from_state": doc["status"],
        })


def delete_document(doc_id: str, actor_user_id: str) -> None:
    """Delete a document, serializing existing TR2 identity with source history."""
    initial = db_docs.get_by_id(doc_id)
    if initial is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    if str(initial.get("type_code") or "").upper() != "TR2":
        _delete_document_row(initial, actor_user_id)
        return

    project_id = initial.get("project_id")
    group_id = initial.get("group_id")
    if not project_id or not group_id:
        raise HTTPException(status_code=409, detail={
            "code": "TR2_OWNERSHIP_INVARIANT",
            "message": "TR2 document is missing project/group ownership metadata.",
        })

    # 0666 D 3.10: no Git command runs here, but ownership/history must not move under
    # the decision, so the Group's G (not the whole project) serializes it.
    outcome, ctx = lock_manager.acquire_group(project_id, group_id, holder_kind="source_mutation")
    if not outcome.ok:
        raise HTTPException(status_code=409, detail={
            "code": "SOURCE_MUTATION_BUSY",
            "message": "Source history is busy; retry document deletion.",
            "details": lock_manager.outcome_details(outcome),
        })
    try:
        # The pre-lock read is admission only. All authority is re-read under the lock.
        fresh = db_docs.get_by_id(doc_id)
        if fresh is None:
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
        if (str(fresh.get("type_code") or "").upper() != "TR2"
                or fresh.get("project_id") != project_id
                or fresh.get("group_id") != group_id):
            raise HTTPException(status_code=409, detail={
                "code": "TR2_OWNERSHIP_INVARIANT",
                "message": "TR2 document identity changed while deletion was waiting.",
            })

        try:
            unresolved = db_recovery.has_unresolved(group_id)
        except Exception as exc:
            raise HTTPException(status_code=409, detail={
                "code": "TR_HISTORY_RECOVERY_REQUIRED",
                "message": "TR source history recovery state cannot be verified.",
            }) from exc
        if unresolved:
            raise HTTPException(status_code=409, detail={
                "code": "TR_HISTORY_RECOVERY_REQUIRED",
                "message": "TR source history recovery must finish before deleting this document.",
            })

        try:
            active = tr2_file_policy.has_active_source_effect(group_id, doc_id)
        except tr2_file_policy.Tr2OwnershipInvariantError as exc:
            raise HTTPException(status_code=409, detail={
                "code": tr2_file_policy.TR2_OWNERSHIP_INVARIANT,
                "message": "TR2 source ownership history is inconsistent; deletion is blocked.",
            }) from exc
        if active:
            raise HTTPException(status_code=409, detail={
                "code": "TR2_ACTIVE_SOURCE_EFFECT",
                "message": "This TR2 still has an active source effect. Cancel it with Time Machine before deletion.",
            })

        # Decision and DELETE remain in one lock window.
        _delete_document_row(fresh, actor_user_id)
    finally:
        lock_manager.release(ctx, outcome.lock_key)


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
    ("test_runs", "doc_id"),
    ("test_runs", "tsr_doc_id"),
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


def convert_root_document_type(
    doc_id: str,
    new_type: str,
    actor_user_id: str,
) -> dict:
    """Convert a workflow root document between R and B (NR0066.0003 §5) and return its row.

    0064 T#3: the judgement, the file stage and the identity rewrite run in
    ``root_conversion.execute_root_conversion`` (one Flow guard transaction, the new body file staged
    next to the original and the original removed only after the commit). This keeps the direct
    service entry point: the same refusals (HTTPException 404 / 409 / 422, ``ConversionRefused`` for the
    new reasons) and the converted (or unchanged, for the same type) document row.
    """
    from modules.flow_gate.documents import root_conversion

    return root_conversion.execute_root_conversion(doc_id, new_type, actor_user_id)["data"]


def convert_by_number_swap(
    doc: dict,
    new_doc_id: str,
    new_type: str,
    new_file_path: Optional[str],
    new_filename: Optional[str],
    actor_user_id: Optional[str],
    *,
    before_delete=None,
    note_suffix: str = "",
) -> dict:
    """The DB part of a number-swap conversion (0064 L0005 2.7), inside the caller's transaction.

    The body file is already staged by the coordinator (``root_conversion``); nothing here touches a
    file. ``before_delete`` runs after every reference points at the new row and before the old row is
    deleted — the attachment rows move there, because ``attachments.doc_id`` cascades on delete.
    """
    doc_id = doc["doc_id"]
    current_type = (doc.get("type_code") or "").upper()
    old_int_id = doc.get("id")
    store = get_store()

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

    if before_delete is not None:
        before_delete()

    # Drop the old identity row; nothing references it anymore.
    store._execute("DELETE FROM documents WHERE doc_id = ?", [doc_id])

    refreshed = db_docs.get_by_id(new_doc_id)
    if refreshed is None:
        raise HTTPException(status_code=409, detail="Conversion failed: document missing after rewrite.")

    db_events.create({
        "event_type": "doc_type_converted",
        "project_id": refreshed["project_id"],
        "group_id": refreshed.get("group_id"),
        "document_id": refreshed["id"],
        "actor_user_id": actor_user_id,
        "metadata": f"{current_type}->{new_type} ({doc_id} -> {new_doc_id}){note_suffix}",
    })
    return refreshed


# Columns of a succession's new row that are not copied from the original (L0005 2.9.2): the identity, the
# file, the per-document history (revisions, rejections live on the original) and the timestamps.
_SUCCESSION_NOT_COPIED = frozenset({
    "id", "doc_id", "type_code", "seq", "file_path", "filename", "superseded_by", "revision_no",
    "rejection_reason", "rejection_history", "meta", "created_at", "updated_at",
})


def _meta_object(meta: Any) -> dict:
    from modules.flow_gate.documents.live_root import meta_dict

    return copy.deepcopy(meta_dict(meta))


def convert_by_succession(
    doc: dict,
    new_doc_id: str,
    new_type: str,
    new_file_path: Optional[str],
    new_filename: Optional[str],
    children: int,
    actor_user_id: Optional[str],
    *,
    after_original=None,
) -> dict:
    """The DB part of a succession conversion (0064 L0005 2.9.2, DB0007 §2.3 / §2.4), in the caller's transaction.

    A new row of ``new_type`` takes the original's columns — title, owner, status and review status included —
    with the staged body copy, ``revision_no`` 0 and ``meta`` deep-copied plus ``converted_from``
    (``graph_card.created_empty`` forced to false: the copied body is no empty draft). The original changes in
    ``superseded_by``, ``meta.converted_to`` and ``updated_at`` only; no reference is rewritten, so child
    documents, reviews, events, revisions, tokens, Q&A and runs keep pointing at it. ``after_original`` runs
    once both rows are written (the attachment rows are copied there). Events: ``doc_type_succeeded`` on the
    original, ``doc_type_converted`` on the new row (P0006 §3.4) and ``doc_review_inherited`` (``events``,
    ``related_doc_id`` = the original).
    """
    from modules.flow_gate.db import events as db_doc_events
    from modules.flow_gate.numbering.id_formatter import parse_doc_code

    doc_id = doc["doc_id"]
    current_type = (doc.get("type_code") or "").upper()
    store = get_store()
    now = now_iso()
    _code_type, new_seq = parse_doc_code(new_doc_id.rpartition(".")[2])

    new_meta = _meta_object(doc.get("meta"))
    new_meta["converted_from"] = {"doc_id": doc_id, "mode": "succession", "at": now, "by": actor_user_id,
                                  "children": children}
    if isinstance(new_meta.get("graph_card"), dict):
        new_meta["graph_card"]["created_empty"] = False
    new_row = {k: v for k, v in doc.items() if k not in _SUCCESSION_NOT_COPIED}
    new_row.update({
        "doc_id": new_doc_id, "type_code": new_type, "seq": new_seq, "file_path": new_file_path,
        "filename": new_filename, "superseded_by": None, "meta": json.dumps(new_meta, ensure_ascii=False),
        "created_at": now, "updated_at": now,
    })
    inserted = db_docs.create(new_row)
    # db_docs.create writes its own column list; the rest of the original's columns (doc_review_status,
    # workflow_steps, ...) follow here. Column names come from the row itself.
    rest = {k: doc[k] for k in doc if k not in _SUCCESSION_NOT_COPIED and k in (inserted or {})
            and inserted.get(k) != doc[k]}
    if rest:
        store._execute(f"UPDATE documents SET {', '.join(f'{k} = ?' for k in rest)} WHERE doc_id = ?",
                       [*rest.values(), new_doc_id])

    old_meta = _meta_object(doc.get("meta"))
    old_meta["converted_to"] = {"doc_id": new_doc_id, "mode": "succession", "at": now, "by": actor_user_id}
    store._execute("UPDATE documents SET superseded_by = ?, meta = ?, updated_at = ? WHERE doc_id = ?",
                   [new_doc_id, json.dumps(old_meta, ensure_ascii=False), now, doc_id])

    if after_original is not None:
        after_original()

    refreshed = db_docs.get_by_id(new_doc_id)
    if refreshed is None:
        raise HTTPException(status_code=409, detail="Conversion failed: document missing after succession.")
    note = f"{current_type}->{new_type} ({doc_id} -> {new_doc_id}) mode=succession children={children}"
    for event_type, row in (("doc_type_succeeded", doc), ("doc_type_converted", refreshed)):
        db_events.create({
            "event_type": event_type,
            "project_id": refreshed["project_id"],
            "group_id": refreshed.get("group_id"),
            "document_id": row.get("id"),
            "actor_user_id": actor_user_id,
            "metadata": note,
        })
    if doc.get("status") or doc.get("doc_review_status"):
        db_doc_events.insert_event(new_doc_id, "doc_review_inherited", related_doc_id=doc_id, note=json.dumps({
            "from": doc_id, "status": doc.get("status"), "doc_review_status": doc.get("doc_review_status"),
        }, ensure_ascii=False))
    return refreshed

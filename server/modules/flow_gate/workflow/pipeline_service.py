"""Pipeline service — group/document lifecycle management (D017 r1).

Responsibilities:
- Group creation and state transitions (draft→in_progress→clarifying→approved→closed)
- CAS (Compare-And-Swap) handling for document state transitions
- Automatic Q state management (§8-5)
- Group completion candidate checks (§6)

Because T_documents is incomplete, this module directly uses the documents DB layer
(db.documents). The TR059 stub status is explicitly documented.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import workflow_item_results as db_wir
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.documents.constants import (
    AUTO_COMPLETE_TYPES,
    FILELESS_APPROVABLE_TYPES,
)
from modules.flow_gate.services.content_search_service import _strip_frontmatter
from modules.flow_gate.storage import paths as storage_paths

from .event_logger import (
    EVT_GROUP_APPROVED,
    EVT_WORKFLOW_SLOT_CONFLICT,
    log_event,
    log_group_approved,
    log_group_completion_candidate,
    log_state_changed,
)
from .rejection_identity import new_rejection_id
from .transition_rules import (
    check_permission,
    get_doc_review_rule,
    get_doc_rule,
    get_group_rule,
)

_log = logging.getLogger(__name__)


class TransitionError(Exception):
    """Attempted an invalid transition."""


class WorkflowSlotConflictError(Exception):
    """A workflow slot already holds a different document (0457 B0001 / NR0003 §7-3).

    Carries the three identifiers an operator needs to trace a refused eviction — which
    slot, what was in it, and what tried to take it — and exposes them in the same
    ``{"error": {"code": ...}}`` shape as
    :class:`modules.flow_gate.services.mutation_policy.MutationPolicyError`, so an API
    layer can answer 409 without parsing an English sentence back apart.
    """

    code = "workflow_slot_occupied"

    def __init__(
        self,
        *,
        item_id: int,
        existing_doc_id: str | None,
        requested_doc_id: str,
    ):
        message = (
            f"Workflow slot item_id={item_id} is already held by {existing_doc_id}; "
            f"refusing to overwrite it with {requested_doc_id}."
        )
        super().__init__(message)
        self.item_id = item_id
        self.existing_doc_id = existing_doc_id
        self.requested_doc_id = requested_doc_id
        self.error = {
            "code": self.code,
            "message": message,
            "item_id": item_id,
            "existing_doc_id": existing_doc_id,
            "requested_doc_id": requested_doc_id,
        }

    def body(self) -> dict:
        return {"error": self.error}


class WorkflowDocumentAlreadyLinkedError(WorkflowSlotConflictError):
    """The document being registered already occupies a *different* slot (0457 T0007).

    The sibling refusal to :class:`WorkflowSlotConflictError`, and the mirror image of it.
    That one defends the target slot: it holds someone else's document, so do not evict it.
    This one defends the document: it is already somebody's slot's result, so do not link
    it to a second slot. B0001 produced exactly this shape — 0454's ``0005-TR`` sitting in
    slots 4 and 6 at once — and migration 090's uq_wfseq_items_result_doc now refuses it at
    the database. Raised here so the refusal arrives as an identified workflow conflict
    rather than as a driver's IntegrityError, and so the check does not depend on which
    engine is underneath.

    Deliberately a *subclass*: every caller that already answers 409 to a refused slot
    write answers 409 to this one without being taught about it, while ``code`` keeps the
    two distinguishable for anyone who looks. The occupancy conflict keeps its own meaning
    — it is still raised, unchanged, whenever the target slot holds another document.

    ``existing_item_id`` names the slot that already holds the document; ``item_id`` is the
    slot the caller was trying to write. ``existing_doc_id`` is None because the target
    slot's occupant is not what is wrong here.
    """

    code = "workflow_document_already_linked"

    def __init__(
        self,
        *,
        item_id: int,
        existing_item_id: int | None,
        requested_doc_id: str,
    ):
        message = (
            f"Document {requested_doc_id} already occupies workflow slot "
            f"item_id={existing_item_id}; refusing to link it to item_id={item_id} as well."
        )
        Exception.__init__(self, message)
        self.item_id = item_id
        self.existing_item_id = existing_item_id
        self.existing_doc_id = None
        self.requested_doc_id = requested_doc_id
        self.error = {
            "code": self.code,
            "message": message,
            "item_id": item_id,
            "existing_item_id": existing_item_id,
            "requested_doc_id": requested_doc_id,
        }


class PermissionError(Exception):
    """Insufficient permissions."""


# ── Group service ─────────────────────────────────────────────────────────────

def create_group(
    *,
    project_id: str,
    module: str,
    title: str,
    actor_user_id: str,
    user_permissions: set[str],
    group_id: str,
    priority: str | None = None,
) -> dict:
    """Create a new group and register it in draft state (D017 r1 Step 1).

    Parameters
    ----------
    group_id:
        Reserved group_id (determined by numbering_service).
    """
    if not check_permission(user_permissions, ("project.group.manage",)):
        raise PermissionError("Permission 'project.group.manage' is required.")

    now = now_iso()
    return db_groups.create(
        {
            "group_id": group_id,
            "project_id": project_id,
            "module": module,
            "title": title,
            "priority": priority,
            "status": "draft",
            "created_at": now,
            "updated_at": now,
        }
    )


def transition_group(
    *,
    group_id: str,
    action: str,
    actor_user_id: str,
    user_permissions: set[str],
    comment: str | None = None,
) -> dict:
    """Transition the group state.

    Returns
    -------
    dict
        Updated group row plus warning information.
    """
    group = db_groups.get_by_id(group_id)
    if not group:
        raise ValueError(f"Group not found: {group_id}")

    current_status = group["status"]
    rule = get_group_rule(current_status, action)
    if rule is None:
        raise TransitionError(
            f"Invalid transition: group {current_status} + action {action}"
        )

    if not check_permission(user_permissions, rule.required_permissions):
        raise PermissionError(
            f"Insufficient permissions: one of {rule.required_permissions} is required"
        )

    next_status = rule.next_state
    warnings: list[str] = []

    # Warn about incomplete child documents when approving the group
    if action == "approve":
        warnings = _collect_incomplete_warnings(group_id)

    # Update the database
    update_data: dict[str, Any] = {"status": next_status}
    if next_status == "closed":
        update_data["closed_at"] = now_iso()

    updated = db_groups.update(group_id, update_data)

    # Record the event
    log_state_changed(
        project_id=group["project_id"],
        actor_user_id=actor_user_id,
        from_state=current_status,
        to_state=next_status,
        group_id=group_id,
        action_code=action,
    )
    if action == "approve":
        log_group_approved(
            project_id=group["project_id"],
            actor_user_id=actor_user_id,
            group_id=group_id,
        )

    result = dict(updated or {})
    if warnings:
        result["__warnings"] = warnings
    return result


def _collect_incomplete_warnings(group_id: str) -> list[str]:
    """Return incomplete documents (open/draft/rejected) in the group."""
    terminal = {"approved", "closed", "cancelled"}
    docs = db_docs.list_documents(
        project_id=_get_group_project(group_id) or "",
        group_id=group_id,
    )
    incomplete = [d["doc_id"] for d in docs if d.get("status") not in terminal]
    return incomplete


def _get_group_project(group_id: str) -> str | None:
    g = db_groups.get_by_id(group_id)
    return g["project_id"] if g else None


# ── Document transition service ───────────────────────────────────────────────

def transition_document(
    *,
    doc_id: str,
    action: str,
    actor_user_id: str,
    user_permissions: set[str],
    comment: str | None = None,
) -> dict:
    """Transition document state using the CAS (Compare-And-Swap) pattern.

    D017 r1 §8-3: UPDATE documents SET status=? WHERE doc_id=? AND status=?
    If rowcount=0, treat it as a concurrent update and raise TransitionError.
    """
    doc = db_docs.get_by_id(doc_id)
    if not doc:
        raise ValueError(f"Document not found: {doc_id}")

    current_status = doc["status"]
    rule = get_doc_rule(current_status, action)
    if rule is None:
        raise TransitionError(
            f"Invalid transition: document status {current_status} + action {action}"
        )

    # own.draft permission check: allow operating on the caller's own draft
    # document even without document.update
    effective_perms = set(user_permissions)
    if doc.get("owner_id") == actor_user_id and current_status == "draft":
        effective_perms.add("own.draft")
    if doc.get("owner_id") == actor_user_id:
        effective_perms.add("document.delete.own.draft")

    if not check_permission(effective_perms, rule.required_permissions):
        raise PermissionError(
            f"Insufficient permissions: one of {rule.required_permissions} is required"
        )

    # comment is required for reject
    if action == "reject" and not comment:
        raise ValueError("Comment required when rejecting (reason for rejection).")

    next_status = rule.next_state
    store = get_store()

    # CAS UPDATE
    update_fields: dict[str, Any] = {"status": next_status, "updated_at": now_iso()}
    if comment:
        existing_meta = {}
        if doc.get("meta"):
            try:
                existing_meta = json.loads(doc["meta"])
            except (json.JSONDecodeError, TypeError):
                existing_meta = {}
        existing_meta["reject_comment"] = comment
        update_fields["meta"] = json.dumps(existing_meta, ensure_ascii=False)

    success = store.update_cas(
        table="documents",
        row_id=doc_id,
        id_col="doc_id",
        expected_col="status",
        expected_val=current_status,
        updates=update_fields,
    )
    if not success:
        raise TransitionError("State transition failed (no fields to update)")

    # Re-read to confirm the actual change and detect CAS races
    updated = db_docs.get_by_id(doc_id)
    if not updated or updated["status"] != next_status:
        raise TransitionError(
            "Concurrent transition conflict detected. Please retry."
        )

    # Record the event
    project_id = doc.get("project_id", "")
    group_id = doc.get("group_id")
    log_state_changed(
        project_id=project_id,
        actor_user_id=actor_user_id,
        from_state=current_status,
        to_state=next_status,
        group_id=group_id,
        document_id=doc.get("id"),
        action_code=action,
    )

    # Handle automatic follow-up actions
    _handle_side_effects(
        doc=updated,
        action=action,
        actor_user_id=actor_user_id,
        user_permissions=user_permissions,
    )

    return updated


def _handle_side_effects(
    *,
    doc: dict,
    action: str,
    actor_user_id: str,
    user_permissions: set[str],
) -> None:
    """Automatic follow-up after a document transition (group updates, Q handling, etc.)."""
    doc_type = doc.get("type_code", "")
    group_id = doc.get("group_id")
    project_id = doc.get("project_id", "")

    # Workflow-root submit → group draft → in_progress
    if doc_type in {"R", "B"} and action == "submit" and group_id:
        group = db_groups.get_by_id(group_id)
        if group and group.get("status") == "draft":
            try:
                transition_group(
                    group_id=group_id,
                    action="start",
                    actor_user_id=actor_user_id,
                    user_permissions=user_permissions,
                )
            except (TransitionError, PermissionError):
                pass  # Ignore if it is already at in_progress or beyond

    # Q document creation (open) → group in_progress → clarifying
    if doc_type == "Q" and action == "submit" and group_id:
        group = db_groups.get_by_id(group_id)
        if group and group.get("status") == "in_progress":
            try:
                transition_group(
                    group_id=group_id,
                    action="clarify",
                    actor_user_id=actor_user_id,
                    user_permissions=user_permissions,
                )
            except (TransitionError, PermissionError):
                pass

    # A document submit → linked Q.status = 'answered' (D017 r1 §8-5)
    if doc_type == "A" and action == "submit" and group_id:
        _auto_answer_linked_q(doc=doc, actor_user_id=actor_user_id, user_permissions=user_permissions)

    # Q document approve → Q.status = 'closed'
    if doc_type == "Q" and action == "approve":
        pass  # Already handled in transition_document

    # When a Q document closes, check group clarifying → in_progress
    if doc_type == "Q" and action in ("approve", "close") and group_id:
        _check_clarifying_resume(
            group_id=group_id,
            actor_user_id=actor_user_id,
            user_permissions=user_permissions,
            project_id=project_id,
        )

    # Emit group_completion_candidate when all child documents are complete
    if action in ("approve", "close") and group_id:
        _check_group_completion(
            group_id=group_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
        )


def _auto_answer_linked_q(
    *,
    doc: dict,
    actor_user_id: str,
    user_permissions: set[str],
) -> None:
    """Set the Q linked by an A document's target_id or triggered_by to 'answered'."""
    q_doc_id = doc.get("target_id") or doc.get("triggered_by")
    if not q_doc_id:
        return
    q_doc = db_docs.get_by_id(q_doc_id)
    if q_doc and q_doc.get("type_code") == "Q" and q_doc.get("status") == "open":
        store = get_store()
        now = now_iso()
        store.update_cas(
            table="documents",
            row_id=q_doc_id,
            id_col="doc_id",
            expected_col="status",
            expected_val="open",
            updates={"status": "answered", "updated_at": now},
        )
        log_state_changed(
            project_id=q_doc.get("project_id", ""),
            actor_user_id=actor_user_id,
            from_state="open",
            to_state="answered",
            group_id=q_doc.get("group_id"),
            document_id=q_doc.get("id"),
            action_code="auto_answered",
        )


def _check_clarifying_resume(
    *,
    group_id: str,
    actor_user_id: str,
    user_permissions: set[str],
    project_id: str,
) -> None:
    """Return clarifying → in_progress when the group has no open Q documents."""
    open_qs = db_docs.list_documents(
        project_id=project_id,
        group_id=group_id,
        type_code="Q",
        status="open",
    )
    if open_qs:
        return
    group = db_groups.get_by_id(group_id)
    if group and group.get("status") == "clarifying":
        try:
            transition_group(
                group_id=group_id,
                action="resume",
                actor_user_id=actor_user_id,
                user_permissions=user_permissions,
            )
        except (TransitionError, PermissionError):
            pass


def _check_group_completion(
    *,
    group_id: str,
    project_id: str,
    actor_user_id: str,
) -> None:
    """Emit completion_candidate when all group child documents are in terminal states."""
    terminal = {"approved", "closed", "cancelled"}
    docs = db_docs.list_documents(project_id=project_id, group_id=group_id)
    incomplete = [d for d in docs if d.get("status") not in terminal]
    log_group_completion_candidate(
        project_id=project_id,
        actor_user_id=actor_user_id,
        group_id=group_id,
        incomplete_count=len(incomplete),
    )


# ── Document review-state transition service ─────────────────────────────────

_EMPTY_BODY_APPROVAL_MESSAGES = {
    "ko": "본문이 비어 있어 승인할 수 없습니다. 문서 내용을 채운 뒤 다시 승인하십시오.",
    "en": "The body is empty and cannot be approved. Fill in the document content, then approve again.",
    "ja": "本文が空のため承認できません。文書の内容を入力してから、もう一度承認してください。",
}
_FILELESS_APPROVAL_STRUCTURE_MESSAGES = {
    "ko": "최종 승인이 현재 워크플로 헤드가 아니어서 승인할 수 없습니다.",
    "en": "This final approval is not the current workflow head, so it cannot be approved.",
    "ja": "この最終承認は現在のワークフローヘッドではないため承認できません。",
}
_APPROVED_REVIEW_STATUSES = {"approved", "wf_done"}
_WORKFLOW_ROOT_TYPES = {"R", "B"}


def _empty_body_approval_message(locale: str = "ko") -> str:
    return _EMPTY_BODY_APPROVAL_MESSAGES.get(locale) or _EMPTY_BODY_APPROVAL_MESSAGES["ko"]


def _fileless_approval_structure_message(locale: str = "ko") -> str:
    return (
        _FILELESS_APPROVAL_STRUCTURE_MESSAGES.get(locale)
        or _FILELESS_APPROVAL_STRUCTURE_MESSAGES["ko"]
    )


def _require_fileless_approval_structure(doc: dict, locale: str = "ko") -> None:
    """Require a file-less AC to be the structurally valid current group head."""
    type_code = str(doc.get("type_code") or "").upper()
    project_id = doc.get("project_id")
    group_id = doc.get("group_id")
    target_id = doc.get("target_id")
    if type_code != "AC" or not project_id or not group_id or not target_id:
        raise TransitionError(_fileless_approval_structure_message(locale))

    try:
        group_docs = db_docs.list_documents(
            project_id=project_id, group_id=group_id, limit=200
        ) or []
        root = next(
            (
                item
                for item in group_docs
                if item.get("doc_id") == target_id
                and str(item.get("type_code") or "").upper() in _WORKFLOW_ROOT_TYPES
            ),
            None,
        )
        sequence = db_wfseq.get_sequence_by_doc_id(target_id) if root else None
        effective_head = (
            db_wfseq.get_effective_head(sequence["id"])
            if sequence and sequence.get("id") is not None
            else None
        )
    except Exception as exc:
        raise TransitionError(_fileless_approval_structure_message(locale)) from exc

    if root is None or sequence is None or effective_head is not None:
        raise TransitionError(_fileless_approval_structure_message(locale))
    if root.get("doc_review_status") == "wf_done":
        raise TransitionError(_fileless_approval_structure_message(locale))
    if any(
        item.get("doc_id") != doc.get("doc_id")
        and str(item.get("type_code") or "").upper() == "AC"
        and item.get("status") != "archived"
        and item.get("doc_review_status") in _APPROVED_REVIEW_STATUSES
        for item in group_docs
    ):
        raise TransitionError(_fileless_approval_structure_message(locale))

    non_head_types = _WORKFLOW_ROOT_TYPES | {"Q"} | AUTO_COMPLETE_TYPES
    current_heads = [
        item
        for item in group_docs
        if str(item.get("type_code") or "").upper() not in non_head_types
        and item.get("status") != "archived"
        and item.get("doc_review_status") not in _APPROVED_REVIEW_STATUSES
    ]
    current_heads.sort(key=lambda item: (item.get("seq") or 0, item.get("doc_id") or ""))
    if not current_heads or current_heads[0].get("doc_id") != doc.get("doc_id"):
        raise TransitionError(_fileless_approval_structure_message(locale))


def _require_document_body_for_approval(doc: dict, locale: str = "ko") -> None:
    """Reject approval when a reviewable document has no readable, non-blank body."""
    type_code = str(doc.get("type_code") or "").upper()
    if type_code in FILELESS_APPROVABLE_TYPES:
        _require_fileless_approval_structure(doc, locale)
        return
    if type_code in AUTO_COMPLETE_TYPES:
        return

    stored_path = (doc.get("file_path") or "").strip()
    if not stored_path:
        raise TransitionError(_empty_body_approval_message(locale))

    branch = (doc.get("branch") or "main").strip() or "main"
    resolved = storage_paths.resolve_storage_path(
        stored_path,
        doc.get("project_id"),
        branch=branch,
    )
    if resolved is None or not resolved.is_file():
        raise TransitionError(_empty_body_approval_message(locale))

    try:
        content = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TransitionError(_empty_body_approval_message(locale)) from exc

    if not _strip_frontmatter(content).strip():
        raise TransitionError(_empty_body_approval_message(locale))


def transition_document_review(
    *,
    doc_id: str,
    action: str,
    actor_user_id: str,
    user_permissions: set[str],
    comment: str | None = None,
    locale: str = "ko",
    review_id: Any = None,
) -> dict:
    """Transition the document review state (doc_review_status column).

    M026 §8-1: based on the DOC_REVIEW_TRANSITIONS rules.
    action: approve | reject | mark_revised

    T0005 2.1.5: `review_id` is the `document_reviews.id` an AUTOMATIC review rejection
    came from. Optional, defaults to None, so every existing caller -- the human [반려]
    button above all -- keeps writing exactly the item shape it wrote before. When given
    and identifiable, the new rejection_history item carries the key, which is what lets
    the review gate tell "this review row was already rejected" from "the document is
    momentarily not in `rejected`" apart.
    """
    doc = db_docs.get_by_id(doc_id)
    if not doc:
        raise ValueError(f"Document not found: {doc_id}")

    current_review_status = doc.get("doc_review_status") or ""
    next_status = get_doc_review_rule(current_review_status, action)
    if next_status is None:
        raise TransitionError(
            f"Invalid review transition: doc_review_status={current_review_status!r} + action={action!r}"
        )

    # Permission check
    if action == "reject":
        required = ("document.reject",)
    elif action in ("mark_revised", "submit"):
        required = ("document.update", "own.draft")
    else:
        required = ("document.approve",)
    if not check_permission(user_permissions, required):
        raise PermissionError(f"Insufficient permissions: one of {required} is required")

    # comment is required for reject
    if action == "reject" and not comment:
        raise ValueError("Comment required when rejecting (reason for rejection).")

    if action == "approve":
        _require_document_body_for_approval(doc, locale)

    update_fields: dict[str, Any] = {
        "doc_review_status": next_status,
        "updated_at": now_iso(),
    }
    if comment:
        existing_meta: dict = {}
        if doc.get("meta"):
            try:
                existing_meta = json.loads(doc["meta"])
            except (json.JSONDecodeError, TypeError):
                existing_meta = {}
        existing_meta["review_comment"] = comment
        update_fields["meta"] = json.dumps(existing_meta, ensure_ascii=False)
    if action == "reject" and comment:
        update_fields["rejection_reason"] = comment  # Compatibility: keep the latest reason
        existing_history: list = parse_rejection_history(doc.get("rejection_history"))
        # P0005/T0006: assign a time-sortable rejection_id at creation so the AI
        # response can later be attached to this exact item (index/time alone is
        # unsafe). The four response fields start null — they are filled in when
        # the AI re-submits the rejected document through the inbox edit path
        # (see record_rejection_response below), not via any manual-input UI.
        item: dict[str, Any] = {
            "rejection_id": new_rejection_id(),
            "reason": comment,
            "rejected_at": now_iso(),
            "rejected_by": actor_user_id,
            "ai_response": None,
            "responded_at": None,
            "response_recorded_by": None,
            "response_revision_no": None,
        }
        if is_review_row_id(review_id):
            # T0005 2.1.5: only a valid row id earns the key. A human rejection never
            # passes review_id, so it never grows a key it did not have; readers that do
            # not know it (the client, older rejection_history items) simply ignore it.
            item["review_id"] = review_id
        existing_history.append(item)
        update_fields["rejection_history"] = json.dumps(existing_history, ensure_ascii=False)

    updated = db_docs.update(doc_id, update_fields)
    if not updated:
        raise TransitionError("Review status transition failed")

    log_state_changed(
        project_id=doc.get("project_id", ""),
        actor_user_id=actor_user_id,
        from_state=f"review:{current_review_status}",
        to_state=f"review:{next_status}",
        group_id=doc.get("group_id"),
        document_id=doc.get("id"),
        action_code=f"review_{action}",
    )

    # 0449 T0004 item 5.2 (NR0003 E4): this is the forward way home. A rewind leaves a return
    # point behind whose snapshot documents are all pending_review; approving the last of them
    # here means the rewound range has been rebuilt without restore, so the ledger no longer
    # describes a state anyone can return to. Clearing it at THIS write boundary is what lets
    # the read APIs report only coherent return points (item 5.4). The helper refuses to act
    # while any snapshot document is still pending (live or nested rewind) and ignores
    # approvals of never-snapshotted types (R/B, Q, AC, M). Best effort: the approval has
    # already been written and must not be undone by a bookkeeping failure.
    group_id = doc.get("group_id")
    if next_status == "approved" and group_id:
        from modules.flow_gate.services import workflow_rework_service

        try:
            workflow_rework_service.clear_return_point_if_complete(group_id, doc)
        except Exception as exc:  # pragma: no cover - bookkeeping stays best-effort
            _log.warning(
                "[return point] cleanup after approving %s failed: %s", doc_id, exc, exc_info=True
            )

    return updated


# P0005/T0006: AI response length ceiling (fixed by T0006).
AI_RESPONSE_MAX_LEN = 4000


class _UnidentifiableReviewId:
    """The submission DID name a review row -- with a value that cannot be one.

    T0005 2.2.2/2.2.4. `True`, `""`, `"   "`, an explicit `null`, a float, a list: each is
    a claim about which rejection is being answered, and each is a broken one. Deliberately
    NOT the same thing as sending no `review_id` at all -- only the latter is the old
    mention the legacy latest-item fallback exists for. Folding a broken claim into
    "absent" is how one bad value ends up answering somebody else's rejection.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<unidentifiable review_id>"


UNIDENTIFIABLE_REVIEW_ID = _UnidentifiableReviewId()


def is_review_row_id(value: Any) -> bool:
    """True only when `value` could BE a `document_reviews.id` (T0005 2.2.2).

    The column is an autoincrement primary key -- a positive integer, never a bool
    (`isinstance(True, int)` is True in Python, so bool is excluded first) and never an
    arbitrary string. A JSON round trip can turn 244 into "244", so a string is accepted
    too, but only when every character left after stripping is a decimal digit and the
    value it names is >= 1 -- `str.isdecimal()` is the predicate `int()` honors for a pure
    ASCII-digit string, closing both the Unicode-digit trap (a superscript digit character
    is `.isdigit()`-true but `int()` rejects it) and, with the `try/except`, the
    absurdly-long-digit-string trap (`int()` refuses beyond a few thousand digits). Either
    way the caller handed this function a value that cannot be a row id, so it resolves to
    False rather than propagating an exception through a boundary submitters control.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    if isinstance(value, str):
        text = value.strip()
        if not text or not text.isdecimal():
            return False
        try:
            return int(text) > 0
        except ValueError:
            return False
    return False


def rejection_review_key(value: Any) -> str:
    """One comparable form for a `document_reviews.id` stored in rejection_history.

    T0005 2.2.3. The column is an integer, but it round-trips through the
    rejection_history JSON and through an inbox request body, so it can come back as
    "244". Normalizing to a stripped string makes 244 and "244" one key. Values that
    cannot identify a review row -- None, a bool, an empty/whitespace-only string, a
    non-numeric string, a float, the UNIDENTIFIABLE_REVIEW_ID marker -- fold to the empty
    key, which matches nothing and is never an explicit target.
    """
    if not is_review_row_id(value):
        return ""
    return str(value).strip()


def parse_rejection_history(raw: Any) -> list:
    """documents.rejection_history as a list of dict items.

    The column is free-form JSON text. Absent, unparseable, not-a-list, or a list with
    non-dict entries all degrade to as much of an empty/clean history as can be salvaged
    -- a malformed column must never raise past this boundary or break a caller that only
    wants to know what happened before.
    """
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


def record_rejection_response(
    *,
    doc_id: str,
    response_text: str | None,
    recorded_by: str,
    revision_no: int | None,
    review_id: Any = None,
) -> dict | None:
    """Annotate the rejection this response answers with how the AI addressed it.

    Reviewer intent (TR0007 rework): the AI's response to a rejection arrives
    *with the inbox re-submission* of the rejected document -- the body stays the
    body, and this records, against a rejection_history item, what the AI did
    about the reviewer's comment. There is no separate manual-input UI/API.

    Which item (T0005 2.2.3-2.2.6):

    * ``review_id`` given and identifying -> the item carrying that same `review_id`,
      compared through :func:`rejection_review_key` so 244 and "244" are one row. Scanned
      forward: if several items carry it (a defensive case -- one review row should make
      one rejection), the FIRST one written wins and later duplicates stay untouched. If
      NO item carries it, nothing is recorded and ``None`` comes back -- a stale or
      mistaken submission losing its response is strictly better than it overwriting a
      different rejection's.
    * ``review_id`` given but unable to name a row (a bool, a blank string, an explicit
      null, UNIDENTIFIABLE_REVIEW_ID) -> nothing is recorded and ``None`` comes back. A
      broken claim is NOT an absent one, so it must not reach the legacy fallback below --
      the whole point of that rule is that one malformed value cannot quietly answer a
      different rejection.
    * ``review_id`` absent (the default) -> the legacy latest-item policy, unchanged: the
      pre-T0005 mention has no such field, and neither does an internal caller with no
      review row to name.

    Re-submitting with the same target overwrites that one item idempotently -- no new
    history entry is ever appended. Returns the updated item, or None if there is
    nothing to record (blank response, missing document, no history, an explicit
    ``review_id`` that names no item, or one that cannot name any item).
    """
    text = (response_text or "").strip()
    if not text:
        return None
    if review_id is not None and not rejection_review_key(review_id):
        # Field present, value unusable (2.2.4). Decided BEFORE the document is even
        # read: there is no reading of this submission under which some item should be
        # updated, and the legacy fallback below must never see it.
        return None
    if len(text) > AI_RESPONSE_MAX_LEN:
        text = text[:AI_RESPONSE_MAX_LEN]

    doc = db_docs.get_by_id(doc_id)
    if not doc:
        return None

    history: list = parse_rejection_history(doc.get("rejection_history"))
    if not history:
        return None

    if review_id is None:
        # THE one legacy case (2.2.6): no field at all. Every other shape was resolved
        # above, so this branch can no longer be reached by a submission that named a
        # row and named it wrong.
        target = history[-1]
    else:
        # An explicit, usable target (the guard above proved the key is non-empty). Scan
        # FORWARD so the first item written for this review row wins over a defensive
        # duplicate, and never fall back to the last item when nothing matches (2.2.5) --
        # no item, no record. Position in the array and the reason text are both
        # ignored: only the stored identifier decides.
        wanted = rejection_review_key(review_id)
        target = next(
            (item for item in history
             if isinstance(item, dict)
             and "review_id" in item
             and rejection_review_key(item.get("review_id")) == wanted),
            None,
        )
        if target is None:
            return None
    if not isinstance(target, dict):
        return None

    target["ai_response"] = text
    target["responded_at"] = now_iso()
    target["response_recorded_by"] = recorded_by
    target["response_revision_no"] = revision_no

    db_docs.update(doc_id, {
        "rejection_history": json.dumps(history, ensure_ascii=False),
        "updated_at": now_iso(),
    })
    return target


# ── Workflow result registration ──────────────────────────────────────────────

def _log_workflow_slot_conflict(
    conflict: WorkflowSlotConflictError, *, actor_user_id: str
) -> None:
    """Record a refused slot write in workflow_events (0457 NR0003 §7-4).

    Takes either refusal — the slot is held by another document
    (:class:`WorkflowSlotConflictError`) or the document already holds another slot
    (:class:`WorkflowDocumentAlreadyLinkedError`, 0457 T0007). Both land under the same
    ``workflow_slot_conflict`` event type; ``metadata.code`` tells them apart, so an
    operator reconstructing an incident sees one stream of refused writes rather than two.

    Best-effort on purpose: the caller is about to receive the conflict itself, so a
    failure to write the audit row must not replace it with a different error.
    """
    try:
        doc = db_docs.get_by_id(conflict.requested_doc_id) or {}
        project_id = doc.get("project_id")
        if not project_id:
            return
        log_event(
            event_type=EVT_WORKFLOW_SLOT_CONFLICT,
            project_id=project_id,
            actor_user_id=actor_user_id,
            group_id=doc.get("group_id"),
            document_id=doc.get("id"),
            metadata=conflict.error,
        )
    except Exception as exc:  # pragma: no cover - audit must never mask the conflict
        _log.warning(
            "[workflow] slot conflict audit event failed (item_id=%s, requested=%s): %s",
            conflict.item_id, conflict.requested_doc_id, exc,
        )


def register_workflow_result(
    *,
    item_id: int,
    registered_path: str,
    registered_doc_id: str,
    registered_at: str,
    actor_user_id: str,
) -> dict | None:
    """Register a workflow result — claim the slot, then INSERT into workflow_item_results.

    DB004 §6.3: set workflow_sequence_items.result_doc_id when registering a result doc.

    0457 B0001 / NR0003 §7-3 — the occupancy invariant. A slot is claimable only when it
    is empty or already holds ``registered_doc_id``; a slot holding a *different*
    document raises :exc:`WorkflowSlotConflictError` and keeps what it has. Re-registering
    the same document stays allowed, because that is what every rejected-resubmit
    revision does to its own slot.

    0457 T0007 / NR0003 §7-1 — the other half of the same invariant, now enforced by
    uq_wfseq_items_result_doc (migration 090): one document, one slot. Linking a document
    that already occupies a different slot raises
    :exc:`WorkflowDocumentAlreadyLinkedError` even when the target slot is empty. Both
    refusals leave the data and the ledger exactly as they were.

    Two details of the order matter and are not incidental:

    * The claim is a conditional UPDATE (``db.workflow_sequences.claim_item_result_doc_id``),
      not a read followed by a write. Two callers racing for the same empty slot are
      resolved by the database, so exactly one wins and the loser writes nothing at all.
    * The claim runs **before** the ledger INSERT. The old order wrote
      ``workflow_item_results`` first, so a registration that had no business happening
      still left a row that reads like a successful one — that ledger is precisely what
      NR0003 §3 had to untangle to date the eviction. Now a refused attempt leaves no
      ledger row, and the refusal itself is what gets recorded (as a
      ``workflow_slot_conflict`` event).

    .. deprecated:: T601
        The doc_review_status transition logic was removed from this function.
        Callers must perform doc_review_status transitions directly.
        T602 is expected to remove this function itself.
    """
    from modules.flow_gate.db import workflow_sequences as _db_wfseq

    # 0457 T0007: the document may already be some other slot's result. Migration 090
    # makes that a unique-index violation, which would surface as the driver's
    # IntegrityError — engine-specific, and a 500 on paths that already know how to
    # answer 409. Ask the reverse lookup first and refuse in the vocabulary the callers
    # speak. Re-registering into the slot it is already in is not this case and stays
    # allowed: that is what every rejected-resubmit revision does.
    linked = _db_wfseq.get_item_by_result_doc_id(registered_doc_id)
    if linked is not None and linked.get("id") != item_id:
        already = WorkflowDocumentAlreadyLinkedError(
            item_id=item_id,
            existing_item_id=linked.get("id"),
            requested_doc_id=registered_doc_id,
        )
        _log_workflow_slot_conflict(already, actor_user_id=actor_user_id)
        raise already

    # `item is None` means the row does not exist — there is no occupant to defend and
    # the conditional UPDATE was a no-op, so this keeps the pre-0457 behaviour of
    # recording the result rather than inventing a conflict against a missing slot.
    #
    # The lookup above is a check, not a lock, so a claim can still lose the same race the
    # CAS below resolves — the constraint is what actually decides it. Re-read rather than
    # inspect the driver's error: if the document ended up in a slot that is not this one,
    # this call is the loser and gets the same refusal it would have got a moment earlier.
    try:
        item = _db_wfseq.claim_item_result_doc_id(item_id, registered_doc_id)
    except Exception as claim_exc:
        raced = _db_wfseq.get_item_by_result_doc_id(registered_doc_id)
        if raced is None or raced.get("id") == item_id:
            raise
        already = WorkflowDocumentAlreadyLinkedError(
            item_id=item_id,
            existing_item_id=raced.get("id"),
            requested_doc_id=registered_doc_id,
        )
        _log_workflow_slot_conflict(already, actor_user_id=actor_user_id)
        raise already from claim_exc
    occupant = (item or {}).get("result_doc_id")
    if item is not None and occupant != registered_doc_id:
        conflict = WorkflowSlotConflictError(
            item_id=item_id,
            existing_doc_id=occupant,
            requested_doc_id=registered_doc_id,
        )
        _log_workflow_slot_conflict(conflict, actor_user_id=actor_user_id)
        raise conflict

    db_wir.insert_result(
        item_id=item_id,
        registered_path=registered_path,
        registered_doc_id=registered_doc_id,
        registered_at=registered_at,
    )

    return db_docs.get_by_id(registered_doc_id)


"""Document REST API router (aligned with D009 r3 / D017 r1).

Endpoints:
  POST   /documents                       Create document
  GET    /documents                       List documents
  GET    /documents/{doc_id}              Get single document
  PATCH  /documents/{doc_id}              Update document fields
  DELETE /documents/{doc_id}              Delete document
  POST   /documents/{doc_id}/transitions  State transition
  POST   /documents/related               Create related document (T180)

RBAC: assumes rbac.decorators.require_permission.
      When not implemented, stub as no-op decorator (noted in TR).
"""
from __future__ import annotations

import json as _json
import hashlib as _hashlib
import logging as _logging
import re as _re
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from modules.flow_gate import db
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import mention_copies as db_mention_copies
from modules.flow_gate.documents import document_service, document_types, template_service
from modules.flow_gate.numbering import numbering_service
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.storage.paths import get_storage_root

_log = _logging.getLogger(__name__)

# Auto-complete workflow step types: created already-approved (see create flow,
# M sets doc_review_status='approved'), have no review action, and are therefore
# never an actionable workflow head. They are notes, not gates — the time machine
# does not reset them, and head derivation never selects an existing one.
# CH (conversation/chat) joins M here per L0044.0008 §2/§3: a chat doc is a
# non-gate, auto-approved note whose body accumulates dialogue turns.
AUTO_COMPLETE_TYPES = {"M", "CH"}
# Conversation (chat) types — auto-complete like M, plus two extra behaviours:
# owner-targeted SSE on edit (L0044.0008 §8) and turn carry-over at the content
# cap (§7). Kept separate from AUTO_COMPLETE_TYPES so memo (M) behaviour is
# untouched by those conversation-only paths.
CONVERSATION_TYPE_CODES = {"CH"}
WORKFLOW_ROOT_TYPES = {"R", "B"}


def _reject_if_group_disposed(doc: dict) -> None:
    """Reject a forward modification when the document's group has been disposed (409).

    TR0079.0003 rework (2nd pass). The inbox ingestion guard (rev3) made the AI-worker
    edit path inert for a discarded group, but a logged-in user editing through the web
    UI reaches the document-content / workflow / field-update / transition / conversation
    endpoints in THIS router, which only checked is_document_editable (status / final
    approval) and never the disposed-group signal. That is the exact rejected symptom —
    "documents in a discarded group can still be edited just fine" (test.test.0024.0001-R edits fine after the group's
    DC discard). Shares process_service.is_group_disposed with the inbox and workflow
    guards (single source of truth: the file-less DC marker), so it fails open for live
    groups and on any lookup failure — legitimate work on a live group is never blocked.
    """
    from modules.flow_gate import process_service as _process_service

    if _process_service.is_group_disposed(doc.get("group_id")):
        raise HTTPException(
            status_code=409,
            detail="Modification not allowed: the group has been disposed.",
        )


def _get_project_branch(project_id: str) -> str:
    """Dynamically look up project_settings.branch. Falls back to 'main' on failure."""
    try:
        from modules.flow_gate.db import projects as _proj

        settings = _proj.get_settings(project_id)
        if settings:
            return (settings.get("branch") or "main").strip() or "main"
    except Exception:
        pass
    return "main"


# ── RBAC stub ────────────────────────────────────────────────────────────────
try:
    from rbac.decorators import require_permission  # type: ignore[import]
except ImportError:
    def require_permission(perm: str):
        """No-op stub until T_rbac is implemented."""
        def _decorator(func):
            return func
        return _decorator

# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/documents", tags=["Documents"])

# Document types subject to parent transition on child creation (T528)
_PARENT_CLOSE_TYPE_CODES = {"R", "B", "M"}


def _try_close_parent_on_child_created(
    parent_doc_id: str,
    actor_user_id: str,
) -> None:
    """Automatically transition parent document (R/M) from open → closed on child creation (T528).

    On transition failure, only log and ignore (T518 pattern).
    """
    from modules.flow_gate.db import documents as _db_docs
    from modules.flow_gate.workflow.pipeline_service import (
        transition_document as _pipeline_transition,
    )

    try:
        parent = _db_docs.get_by_id(parent_doc_id)
        if parent is None:
            return
        if parent.get("type_code") not in _PARENT_CLOSE_TYPE_CODES:
            return
        if parent.get("status") != "open":
            return
        _pipeline_transition(
            doc_id=parent_doc_id,
            action="child_created",
            actor_user_id=actor_user_id,
            user_permissions={"document.update", "document.approve"},
        )
    except Exception as _e:
        _log.warning(
            "open→closed (child_created) transition failed for parent %s: %s",
            parent_doc_id,
            _e,
            exc_info=True,
        )


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _document_file_path(doc: dict) -> Path:
    """Resolve a document's stored file_path to a jailed absolute Path.

    Delegates to the unified storage.paths.resolve_storage_path() (L0054.0002 §4).
    Kept as a thin wrapper so callers retain the HTTPException contract: a missing
    column → 404 "missing", an unresolvable/escaping path → 404 "Not found".
    """
    raw = (doc.get("file_path") or "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="Document file path is missing.")

    project_id = doc.get("project_id")
    branch = (doc.get("branch") or "main") or "main"
    resolved = storage_paths.resolve_storage_path(raw, project_id, branch=branch)
    if resolved is None:
        # Fall back to a best-effort jailed path so existing 404-on-missing-file
        # behaviour at call sites (which separately check .is_file()) is preserved.
        root = get_storage_root(project_id).resolve()
        cand = (Path(raw) if (Path(raw).is_absolute() or _re.match(r"^[A-Za-z]:", raw))
                else root / raw).resolve(strict=False)
        if not storage_paths._within_allowed_roots(cand, project_id):
            raise HTTPException(status_code=404, detail="Not found")
        return cand
    return resolved


def _short_doc_code(doc: dict) -> str:
    """Derive the short ``{seq}-{type}`` doc_code from a document's full doc_id.

    Mirrors inbox_routes._resolve_storage_path: strip the group_id prefix so the
    recomputed canonical filename matches the rest of the storage layout.
    """
    doc_id = doc.get("doc_id") or ""
    group_id = doc.get("group_id") or ""
    if group_id and (doc_id.startswith(group_id + ".") or doc_id.startswith(group_id + "-")):
        return doc_id[len(group_id) + 1:]
    return doc_id


def _regenerate_target_path(doc: dict) -> Path:
    """Resolve the path a regenerated file should be written to (R0001 / NR0003).

    Unlike :func:`_document_file_path`, this never raises and never requires the file
    to already exist — the whole point is that the file is *missing*. When ``file_path``
    is present it reconstructs the originally-intended absolute location (so recovery
    lands where the document expects it); when ``file_path`` is empty or escapes the
    storage jail it recomputes the canonical D013 §5 path from DB metadata.
    """
    raw = (doc.get("file_path") or "").strip()
    project_id = doc.get("project_id")
    branch = (doc.get("branch") or "main") or "main"
    if raw:
        resolved = storage_paths.resolve_storage_path(raw, project_id, branch=branch)
        if resolved is not None:
            return resolved  # file unexpectedly exists / branch-drift hit
        root = get_storage_root(project_id).resolve()
        cand = (
            Path(raw) if (Path(raw).is_absolute() or _re.match(r"^[A-Za-z]:", raw))
            else root / raw
        ).resolve(strict=False)
        if storage_paths._within_allowed_roots(cand, project_id):
            return cand
        # escaping path → fall through to a recomputed jailed path
    return storage_paths.document_path(
        project_id=project_id,
        group_code=doc.get("group_id") or "",
        doc_code=_short_doc_code(doc),
        filename="document.md",
        module=doc.get("module") or "none",
        branch=branch,
    )


def _latest_revision_body(doc_id: str, project_id: Optional[str]) -> Optional[str]:
    """Return the body of the newest readable revision backup, or None (NR0003 §3a).

    On every inbox edit the prior file is copied to ``revisions/{doc_id}.r{n}.md`` and
    recorded in ``document_revisions``. When the live file is lost we can restore the
    last-saved body from the newest backup that still resolves to a real file. Returns
    None when there is no backup (e.g. revision_no=0 / pruned by delete_old).
    """
    from modules.flow_gate.db import document_revisions as _db_rev
    try:
        revisions = _db_rev.list_by_doc(doc_id)  # newest first (revision_no DESC)
    except Exception:
        return None
    for row in revisions:
        backup_path = (row.get("backup_path") or "").strip()
        if not backup_path:
            continue
        resolved = storage_paths.resolve_storage_path(backup_path, project_id)
        if resolved is not None and resolved.is_file():
            try:
                return resolved.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


def _broadcast_document_refresh(doc: dict) -> None:
    """Best-effort SSE so an open MdViewer reloads after regeneration (NR0003 §4).

    Mirrors the append_conversation_turn broadcast; failures are swallowed so a
    successful regeneration is never undone by an SSE hiccup.
    """
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )
        from modules.flow_gate.api.v1.events.event_types import EventType

        broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
            payload={
                "operation": "updated",
                "doc_id": doc.get("doc_id"),
                "type": doc.get("type_code"),
                "title": doc.get("title"),
                "status": doc.get("status"),
            },
            audience="*",
            project=doc.get("project_id"),
            group_id=doc.get("group_id"),
            doc_id=doc.get("doc_id"),
        ))
    except Exception as _sse_exc:  # pragma: no cover - defensive
        _log.warning("[regenerate] SSE publish failed (ignored): %s", _sse_exc)


# ── Request/response models ──────────────────────────────────────────────────────────

class DocumentCreate(BaseModel):
    doc_id: str
    project_id: str
    type_code: str
    seq: int
    title: str
    module: str = "none"
    group_id: Optional[str] = None
    sub_group_id: Optional[str] = None
    file_path: Optional[str] = None
    status: str = "draft"
    priority: Optional[str] = None
    due_date: Optional[str] = None
    direction: Optional[str] = None
    review_required: int = 0
    tv_type: Optional[str] = None
    pass_criteria: str = "all"
    worker_tier: Optional[str] = None
    target_id: Optional[str] = None
    triggered_by: Optional[str] = None
    meta: Optional[str] = None


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    file_path: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    direction: Optional[str] = None
    review_required: Optional[int] = None
    meta: Optional[str] = None
    worker_tier: Optional[str] = None
    pass_criteria: Optional[str] = None


class DocumentContentUpdate(BaseModel):
    content: str


class RootTypeConvert(BaseModel):
    """Convert a workflow root document between R and B (NR0066.0003 §5)."""
    new_type: Literal["R", "B"]


class ConversationTurnAppend(BaseModel):
    """A single conversation (CH) turn submitted from the session UI (L0044.0008 §6).

    The body is the human's message text; `speaker` defaults to "user" (the PM side).
    The wire format is built server-side by the shared serializer, so the FE never
    constructs turn headers / escaping itself.
    """
    body: str = Field(..., min_length=1)
    speaker: Literal["user", "ai"] = "user"


class TransitionRequest(BaseModel):
    to_state: str = Field(..., description="State to transition to (draft/open/in_review/approved/rejected/cancelled/closed/archived)")
    reason: Optional[str] = None


class DocumentTypeCreate(BaseModel):
    project_id: str
    type_code: str
    type_name: str
    series: Literal["general", "instruction", "design", "work", "action"]
    sort_order: int = 0


class RelatedDocCreate(BaseModel):
    """Request to create a new related document (T180)."""
    project_id: str
    type_code: str
    title: str
    group_id: str
    target_id: str  # doc_id of the current document — relationship is set automatically
    template: str = "default"  # "default" | "none"
    module: str = "none"


class NextEmptyQuestionIn(BaseModel):
    """An AI-attached query for the next-empty document (group 0022 §5)."""
    title: Optional[str] = None
    body: str


class NextEmptyDocumentCreate(BaseModel):
    """Request to create an empty document for the next workflow step."""
    project_id: str
    group_id: str
    prev_doc_id: str
    type_code: str
    title: str
    module: str = "none"
    # group 0022 D0005 §3.4 form ②: empty document + queries. Right after creation, in the
    # same transaction, attach them as asker_kind='ai' queries (L0007 §5). None/[] behaves the same as before.
    questions: Optional[list["NextEmptyQuestionIn"]] = None


class NextApprovedDocumentCreate(BaseModel):
    """Request to create an auto-approved instruction document for the next step.

    R0001 #2 / group 0048 P0005 §2-2: title/content are NOT accepted — the server
    generates them from the type label. type_code must be one of N | T | TS.
    """
    project_id: str
    group_id: str
    prev_doc_id: str
    type_code: str  # N | T | TS only (gated in handler)
    module: str = "none"


class WorkflowUpdate(BaseModel):
    """Workflow finalization update request (T212)."""
    workflow_steps: Optional[list[str]] = None


class DocumentContentUpdateRpc(BaseModel):
    doc_id: str
    content: str


class WorkflowUpdateRpc(BaseModel):
    doc_id: str
    workflow_steps: Optional[list[str]] = None


class DocumentUpdateRpc(BaseModel):
    doc_id: str
    title: Optional[str] = None
    file_path: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    direction: Optional[str] = None
    review_required: Optional[int] = None
    meta: Optional[str] = None
    worker_tier: Optional[str] = None
    pass_criteria: Optional[str] = None


class DocumentDeleteRpc(BaseModel):
    doc_id: str


class TransitionRequestRpc(BaseModel):
    doc_id: str
    to_state: str = Field(..., description="State to transition to")
    reason: Optional[str] = None


class MentionCopyRecordRpc(BaseModel):
    # R0001 group 0015 / NR0003 rev4: record that the current user copied this document's
    # mention block (to hand it off to an AI worker). mention_kind is a stable code (NR0005);
    # the client maps it to a localized badge label.
    doc_id: str
    mention_kind: str = Field(..., min_length=1, max_length=64)


# ── Internal helpers ─────────────────────────────────────────────────────────────────

def _parse_doc_workflow(doc: dict) -> dict:
    """Parse the workflow_steps JSON string from a documents row into a list.

    For non-root documents, find the R/B workflow root in the same group and
    attach its workflow metadata for sequence/action-bar display.
    """
    raw = doc.get("workflow_steps")
    parsed: Optional[list] = None
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
        except Exception:
            parsed = None
    elif isinstance(raw, list):
        parsed = raw

    out: dict = {
        **doc,
        "workflow_steps": parsed,
    }
    if doc.get("type_code") in WORKFLOW_ROOT_TYPES:
        out["workflow_root_type"] = doc.get("type_code")

    # rejection_history: DB TEXT → list
    raw_history = doc.get("rejection_history")
    if isinstance(raw_history, str):
        try:
            parsed_history = _json.loads(raw_history)
            if not isinstance(parsed_history, list):
                parsed_history = []
        except (ValueError, TypeError):
            parsed_history = []
    elif isinstance(raw_history, list):
        parsed_history = raw_history
    else:
        parsed_history = []
    out["rejection_history"] = parsed_history

    if doc.get("type_code") not in WORKFLOW_ROOT_TYPES and doc.get("group_id") and doc.get("project_id"):
        from modules.flow_gate.db import documents as _db_docs
        try:
            roots = _db_docs.list_documents(
                project_id=doc["project_id"],
                group_id=doc["group_id"],
                type_code="R",
                limit=1,
            )
            if not roots:
                roots = _db_docs.list_documents(
                    project_id=doc["project_id"],
                    group_id=doc["group_id"],
                    type_code="B",
                    limit=1,
                )
            if roots:
                parent_root = roots[0]
                root_doc_id = parent_root.get("doc_id")
                out["parent_root_doc_id"] = root_doc_id
                out["workflow_root_type"] = parent_root.get("type_code")
                # Kept for older clients until the response contract is fully renamed.
                out["parent_r_doc_id"] = root_doc_id
                if not parsed:
                    parent_raw = parent_root.get("workflow_steps")
                    if isinstance(parent_raw, str):
                        try:
                            out["workflow_steps"] = _json.loads(parent_raw)
                        except Exception:
                            pass
                    elif isinstance(parent_raw, list):
                        out["workflow_steps"] = parent_raw
        except Exception:
            pass

    # Resolve group head by direct documents-table lookup (PM 4-step spec, T818).
    # NOT using workflow_sequence_items.result_doc_id (rejected by PM thrice).
    group_head = None
    candidates: list = []  # all group docs; also used for 'pending' derivation below
    if doc.get("project_id") and doc.get("group_id"):
        from modules.flow_gate.db import documents as _db_docs_chain
        try:
            candidates = _db_docs_chain.list_documents(
                project_id=doc["project_id"],
                group_id=doc["group_id"],
                limit=100,   # group sizes are tiny; we filter in Python below
            )
            # Filter: workflow-step docs only, not yet approved.
            # Auto-complete types (memos) are never an actionable head — invariant
            # guard so an existing memo can never surface as the head regardless of
            # its stored status (B / defence-in-depth alongside the reopen guard).
            NON_HEAD_TYPES = WORKFLOW_ROOT_TYPES | {"Q"} | AUTO_COMPLETE_TYPES
            APPROVED_STATUSES = {"approved", "wf_done"}
            in_progress = [
                c for c in candidates
                if c.get("type_code") not in NON_HEAD_TYPES
                and (c.get("doc_review_status") is None
                     or c.get("doc_review_status") not in APPROVED_STATUSES)
            ]
            # Pick the earliest-seq in-progress doc (the next step in workflow order).
            in_progress.sort(key=lambda c: (c.get("seq") or 0))
            group_head = in_progress[0] if in_progress else None
        except Exception:
            pass

    final_approved = (
        doc.get("doc_review_status") == "wf_done"
        or any(
            c.get("type_code") in WORKFLOW_ROOT_TYPES
            and c.get("doc_review_status") == "wf_done"
            for c in candidates
        )
    )
    out["is_final_approved"] = final_approved
    # TR0079.0003 (rework): a disposed group is recorded by a file-less DC document
    # (process_service.dispose_group) — the requirement stays wf_in_progress and the
    # group status is untouched, so the DC document is the only reliable discard marker
    # (same signal the dashboard exclusion uses). Surface it so the client can collapse
    # the review/workflow action bar for every document in a discarded group.
    out["group_disposed"] = any(
        c.get("type_code") == "DC" for c in candidates
    )
    out["is_editable"] = document_service.is_document_editable(
        doc,
        final_approved=final_approved,
    )

    # D030 §3.5: single-shot sequence lookup — reused for 'pending' derivation below
    # and workflow_steps / next_step_exists population (T818; no sequence-walk).
    root_doc_id_for_head = (
        doc["doc_id"]
        if doc.get("type_code") in WORKFLOW_ROOT_TYPES
        else out.get("parent_root_doc_id") or out.get("parent_r_doc_id")
    )
    seq_items: list = []
    _seq_found = False
    if root_doc_id_for_head:
        from modules.flow_gate.db import workflow_sequences as _db_wfseq
        try:
            _seq = _db_wfseq.get_sequence_by_doc_id(root_doc_id_for_head)
            if _seq:
                seq_items = _db_wfseq.get_sequence_items(_seq["id"]) or []
                _seq_found = True
        except Exception:
            pass

    def _effective_head_from_seq_items(items: list[dict]) -> dict | None:
        review_by_doc_id = {
            c.get("doc_id"): c.get("doc_review_status")
            for c in candidates
            if c.get("doc_id")
        }

        # B0001 (group 0105): a *pending* (not-yet-created) auto-approve slot (M / CH)
        # IS an actionable "create next document" step — it just auto-approves on
        # creation. Excluding it from `pending` made the resolver return None when the
        # only remaining slot was a memo/chat, which collapsed the head to the synthetic
        # AC gate (action bar showed [final approval] instead of [create document]).
        # The canonical SSOT (workflow_sequences.get_effective_head) does NOT type-filter
        # the `result_doc_id IS NULL` branch, so the action-bar head diverged from the
        # head that create_next_empty / worker inbox actually advance to. Align them:
        # keep excluding REALIZED auto-approve docs (T818 invariant — an existing memo
        # never re-surfaces as head) but allow PENDING M / CH as head candidates.
        pending_non_head = NON_HEAD_TYPES - AUTO_COMPLETE_TYPES  # roots + Q only
        linked: list[tuple[dict, int]] = []
        pending: list[tuple[dict, int]] = []
        for index, item in enumerate(items):
            item_type = item.get("type")
            result_doc_id = item.get("result_doc_id")
            if result_doc_id:
                result_review = item.get("result_doc_review_status")
                if result_review is None:
                    result_review = review_by_doc_id.get(result_doc_id)
                if item_type not in NON_HEAD_TYPES and result_review not in APPROVED_STATUSES:
                    linked.append((item, index))
            elif item_type not in pending_non_head:
                pending.append((item, index))

        sort_key = lambda pair: (pair[0].get("sort_order", pair[1]), pair[1])
        if linked:
            linked.sort(key=sort_key)
            return linked[0][0]
        if pending:
            pending.sort(key=sort_key)
            return pending[0][0]
        return None

    # Premature-AC guard: an already-opened AC (final-approval) document lingers
    # as an in_progress group doc after new pending steps are inserted before it
    # (sequence edit). It must NOT win head resolution while earlier actionable
    # slots are still unrealized — otherwise head_index lands at len(steps) and the
    # strip paints those empty steps 'done'. When the sequence still has an
    # unrealized non-AC slot, defer to the sequence head (resolved in the branch
    # below). The legitimate "all steps realized → AC head" case is preserved:
    # get_effective_head returns None there, so group_head stays the AC doc.
    if (
        group_head is not None
        and group_head.get("type_code") == "AC"
        and _seq_found
    ):
        _eff = _effective_head_from_seq_items(seq_items)
        if _eff and _eff.get("type") not in (None, "AC"):
            group_head = None

    if group_head is not None:
        out["workflow_head_doc_id"]            = group_head["doc_id"]
        out["workflow_head_doc_number"]        = group_head["doc_id"]
        out["workflow_head_doc_title"]         = group_head.get("title")
        out["workflow_head_doc_review_status"] = group_head.get("doc_review_status")
        out["workflow_head_type"]              = group_head.get("type_code")
        out["workflow_head_status"]            = "in_progress"
    elif doc.get("project_id") and doc.get("group_id"):
        # 'pending' if sequence has unrealized steps, 'done' if complete.
        # Applies to all doc types (NR158: removes T829's R-only restriction).
        if seq_items:
            # Next pending step from the sequence SLOTS (each has its own identity
            # + result_doc_id), via the same SSOT as create's get_effective_head.
            # NOT by matching document TYPE — type matching collapses repeated types
            # (e.g. M appearing twice) to one and skips the later occurrence, which
            # made the head disagree with create_next_empty for such workflows.
            eff_head = _effective_head_from_seq_items(seq_items)
            head_type = eff_head.get("type") if eff_head else None
            if head_type is not None:
                out["workflow_head_status"] = "pending"
                out["workflow_head_type"] = head_type
            else:
                # All actionable steps realized. The final approval (AC) is an
                # explicit step, not a document: keep the head at AC/pending until
                # the R doc is finalized (wf_done), then the workflow is done.
                root_done = any(
                    c.get("type_code") in WORKFLOW_ROOT_TYPES
                    and c.get("doc_review_status") == "wf_done"
                    for c in candidates
                )
                # Mandatory final-approval (AC) gate (M042 / group 0104): every workflow ends
                # in an explicit final approval, even memo / auto-approved instruction chains.
                # When all document steps are realized + approved but final approval has NOT
                # happened yet, keep the head at AC/pending so the action bar surfaces the
                # [final approval] control. Only report 'done' once final approval is actually
                # performed — the root is finalized (wf_done) or an approved AC document exists.
                # (b39f6b8 had collapsed this to always-'done', auto-finalizing the workflow
                #  and removing the final-approval action — the regression in group 0104.)
                ac_done = any(
                    c.get("type_code") == "AC"
                    and c.get("doc_review_status") in APPROVED_STATUSES
                    for c in candidates
                )
                if root_done or ac_done:
                    out["workflow_head_status"] = "done"
                else:
                    out["workflow_head_type"] = "AC"
                    out["workflow_head_status"] = "pending"
        else:
            # seq_items is empty. Two very different situations collapse here — keep
            # them apart (0119 B0001 / NR0009 §6.1):
            #  • _seq_found True  → the sequence ROW exists but every item was deleted
            #    (a decided workflow emptied of all steps — the B0001 "결정됨+빈" zombie).
            #    This is NOT a finished workflow: the only legitimate terminal state is the
            #    AC (final-approval) gate (M042 / group 0104). Reporting 'done' here made the
            #    strip paint [완료] and let the group chain advance past a broken/empty
            #    workflow. Report a distinct 'empty' status so the client routes to recovery
            #    ([시퀀스 수정]) instead of [완료]/auto-advance.
            #  • _seq_found False → no sequence at all (undecided root, or a non-workflow
            #    doc with no R parent). Preserve the pre-existing terminal 'done' fallback
            #    (NR158 — test_n158_non_r_no_seq_items_fallback_done).
            out["workflow_head_status"] = "empty" if _seq_found else "done"

    # Populate workflow_steps and next_step_exists from the already-fetched seq_items.
    if _seq_found:
        if not out.get("workflow_steps") and seq_items:
            steps = [item.get("type") for item in seq_items if item.get("type")]
            if steps:
                out["workflow_steps"] = steps
        # Resolve the head's POSITION in the sequence (workflow_head_index) by
        # slot IDENTITY, so the strip can colour the correct cell when a type
        # repeats (e.g. M twice). Matching by type alone hits the first M and
        # mis-colours the strip.
        eff_head_type = out.get("workflow_head_type")
        head_doc_id = out.get("workflow_head_doc_id")
        head_idx = None
        if head_doc_id:
            # in-progress existing doc → the slot it is registered to
            for i, item in enumerate(seq_items):
                if item.get("result_doc_id") == head_doc_id:
                    head_idx = i
                    break
        if head_idx is None and eff_head_type:
            # pending step → first not-yet-realized slot of this type
            for i, item in enumerate(seq_items):
                if item.get("type") == eff_head_type and not item.get("result_doc_id"):
                    head_idx = i
                    break
        if eff_head_type == "AC" and head_idx is None:
            head_idx = len([it for it in seq_items if it.get("type")])
        out["workflow_head_index"] = head_idx
        # Resolve the VIEWED doc's own slot index (workflow_self_index) by slot
        # identity, so the DocInfoPanel "next" box can find the step that follows
        # THIS doc even when its type repeats (e.g. M at slots 1 and 4). A client
        # type-based indexOf collapses repeats to the first slot. None when the
        # viewed doc is not a realized step slot (R / AC / not yet registered).
        self_idx = None
        _self_doc_id = doc.get("doc_id")
        for i, item in enumerate(seq_items):
            if item.get("result_doc_id") == _self_doc_id:
                self_idx = i
                break
        out["workflow_self_index"] = self_idx
        # next_step_exists = there IS a head step to advance to — NOT "a step after the
        # head". A present head_idx already means an advanceable step (the next doc to
        # create/approve, or the synthetic AC final-approval head whose head_idx is
        # len(steps)). b39f6b8 changed this to `head_idx < step_count - 1`, which goes
        # False whenever the head is the LAST sequence slot — i.e. the last real doc
        # before the (synthetic, off-sequence) AC gate. Concretely, group test.test.0007
        # has steps [N,NR,T,TR] with TR pending (head_idx=3, step_count=4): 3 < 3 is
        # False, so the action bar blanked on every approved doc whose next step is TR
        # (next-next = final approval). This is the group 0104 regression the prior fix
        # missed — restored to the pre-b39f6b8 semantics.
        if eff_head_type == "AC":
            out["next_step_exists"] = True
        else:
            out["next_step_exists"] = head_idx is not None

    return out


# ── Document endpoints ──────────────────────────────────────────────────────────

@router.post("", status_code=201)
@require_permission("perm_document_create")
def create_document(
    body: DocumentCreate,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Create a document."""
    import logging as _logging
    data = body.model_dump()
    if not data.get("owner_id"):
        data["owner_id"] = current_user["user_id"]

    # Auto-create a .md file when no file_path is given but group_id is available
    if not data.get("file_path") and data.get("group_id"):
        doc_code = f"{data['type_code']}{data['seq']:03d}"
        slug = _slugify_title(data["title"])
        doc_file_path = storage_paths.document_path(
            project_id=data["project_id"],
            group_code=data["group_id"],
            doc_code=doc_code,
            filename=f"{slug}.md",
            subgroup_code=data.get("sub_group_id") or None,
            module=data.get("module", "none"),
            branch=_get_project_branch(data["project_id"]),
        )
        md_content = (
            f"---\n"
            f"title: {data['title']}\n"
            f"type: {data['type_code']}\n"
            f"doc_id: {data['doc_id']}\n"
            f"---\n\n"
        )
        try:
            doc_file_path.parent.mkdir(parents=True, exist_ok=True)
            doc_file_path.write_text(md_content, encoding="utf-8")
            data["file_path"] = storage_paths.to_storage_relative(
                doc_file_path, data["project_id"]
            )
        except Exception:
            _logging.getLogger(__name__).warning(
                "Failed to create document file: %s", doc_file_path, exc_info=True
            )

    doc = document_service.create_document(data, actor_user_id=current_user["user_id"])
    # T823: transition non-root docs to pending_review immediately.
    if data.get("type_code", "").upper() not in WORKFLOW_ROOT_TYPES:
        from modules.flow_gate.workflow.pipeline_service import transition_document_review
        transition_document_review(
            doc_id=data["doc_id"],
            action="submit",
            actor_user_id=current_user["user_id"],
            user_permissions={"document.update"},
        )
        from modules.flow_gate.db import documents as _db_docs
        refreshed = _db_docs.get_by_id(data["doc_id"])
        if refreshed is not None:
            doc = refreshed
    return doc


# ── Create related document (T180) ─────────────────────────────────────────────────

_RELATED_ALLOWED_TYPES = {"DS", "N", "T", "TS", "M", "Q"}


def _slugify_title(text: str) -> str:
    text = text.strip().lower()
    text = _re.sub(r"[\s\-]+", "_", text)
    text = _re.sub(r"[^a-z0-9_]", "", text)
    text = _re.sub(r"_+", "_", text).strip("_")
    return text[:50] or "untitled"


def _short_group_code(group_id: str) -> str:
    return group_id.rsplit(".", 1)[-1] if group_id else ""


def _next_workflow_type(sequence_id: int, current_item_id: int) -> str:
    from modules.flow_gate.db import workflow_sequences as _db_wfseq

    items = _db_wfseq.get_sequence_items(sequence_id)
    for idx, item in enumerate(items):
        if item.get("id") == current_item_id and idx + 1 < len(items):
            return str(items[idx + 1].get("type") or "")
    return ""


def _build_next_empty_content(
    *,
    project_id: str,
    module: str,
    group_id: str,
    type_code: str,
    doc_code: str,
    title: str,
    target_id: str,
    next_type: str,
) -> str:
    lines = [
        "---",
        f"project: {project_id}",
        f"module: {module}",
        f"group: {_short_group_code(group_id)}",
        f"group_id: {group_id}",
        f"type: {type_code}",
        f"doc_number: {doc_code}",
        f"title: {title}",
        f"target_id: {target_id}",
    ]
    if next_type:
        lines.append(f"next: {next_type}")
    lines.extend(["---", ""])
    return "\n".join(lines)


# Locale-branched copy for auto-approved instruction documents (group 0099 B0001).
# The stored document title/body are NOT re-rendered through FE i18n — they are persisted
# verbatim in the .md and shown as-is — so the only place to honor the selected locale is
# here, at generation time. The earlier "delegate the locale-specific copy to i18n" design
# (D0004 §4-4) never materialized (no auto-approved i18n key exists), which left the
# unmanned continuous chain emitting Korean regardless of the chosen locale. The label
# itself is already localized by get_type_name; these tables localize the surrounding copy
# and drop the Korean subject particle for non-ko locales.
_AUTO_APPROVED_TITLE = {
    "ko": "{label} 승인",
    "ja": "{label} 承認",
    "en": "{label} approved",
}
_AUTO_APPROVED_BODY = {
    "ko": "{label} 가 승인되었습니다.",
    "ja": "{label} が承認されました。",
    "en": "{label} has been approved.",
}


def _auto_approved_title(label: str, locale: str) -> str:
    """Title for an auto-approved instruction document (R0001 #2 / 0048 D0004 §4-4).

    Locale-branched (ko/ja/en, group 0099 B0001); ``get_type_name`` already localizes the
    label, this localizes the surrounding copy. Unsupported locales fold to ko.
    """
    from modules.flow_gate.template_provision import normalize_locale
    loc = normalize_locale(locale)
    return _AUTO_APPROVED_TITLE.get(loc, _AUTO_APPROVED_TITLE["ko"]).format(label=label)


def _auto_approved_body(label: str, locale: str) -> str:
    """Body line for an auto-approved instruction document.

    Locale-branched (ko/ja/en, group 0099 B0001). The Korean subject particle ``가`` is
    only emitted in the ko branch; non-ko branches use natural copy. Unsupported → ko.
    """
    from modules.flow_gate.template_provision import normalize_locale
    loc = normalize_locale(locale)
    return _AUTO_APPROVED_BODY.get(loc, _AUTO_APPROVED_BODY["ko"]).format(label=label)


@router.post("/related", status_code=201)
@require_permission("perm_document_create")
def create_related_document(
    body: RelatedDocCreate,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Create a new related document (T180).

    - Assigns a number using D009 id_counter (numbering)
    - Automatically sets target_id to the current document's doc_id
    - Creates a default markdown file (template="default") or empty file (template="none")
    """
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title is required.")
    if len(title) > 100:
        raise HTTPException(status_code=422, detail="Title must be 100 characters or fewer.")
    if body.type_code not in _RELATED_ALLOWED_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Disallowed document type: {body.type_code}. Allowed: {sorted(_RELATED_ALLOWED_TYPES)}",
        )
    if not body.group_id:
        raise HTTPException(status_code=422, detail="group_id is required.")
    if not body.target_id:
        raise HTTPException(status_code=422, detail="target_id is required.")

    # Assign a number using D009 id_counter (numbering)
    try:
        doc_code = numbering_service.reserve_document(
            group_id=body.group_id,
            doc_type=body.type_code,
            module=body.module,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # full doc_id: {group_id}.{doc_code} (D013 canonical)
    doc_id = f"{body.group_id}.{doc_code}"

    # seq is the numeric part of doc_code
    m = _re.match(r'^(\d+)-[A-Za-z]+$', doc_code)
    seq = int(m.group(1)) if m else 0

    # Create markdown file
    slug = _slugify_title(title)
    doc_file_path = storage_paths.document_path(
        project_id=body.project_id,
        group_code=body.group_id,
        doc_code=doc_code,
        filename=f"{slug}.md",
        module=body.module,
        branch=_get_project_branch(body.project_id),
    )

    if body.template == "default":
        md_content = (
            f"---\n"
            f"title: {title}\n"
            f"type: {body.type_code}\n"
            f"doc_id: {doc_id}\n"
            f"target_id: {body.target_id}\n"
            f"---\n\n"
        )
    else:
        md_content = ""

    stored_file_path: Optional[str] = None
    try:
        doc_file_path.parent.mkdir(parents=True, exist_ok=True)
        doc_file_path.write_text(md_content, encoding="utf-8")
        stored_file_path = storage_paths.to_storage_relative(
            doc_file_path, body.project_id
        )
    except Exception:
        pass  # on file creation failure, continue with DB registration

    data: dict[str, Any] = {
        "doc_id": doc_id,
        "project_id": body.project_id,
        "module": body.module,
        "group_id": body.group_id,
        "type_code": body.type_code,
        "seq": seq,
        "title": title,
        "status": "draft",
        "target_id": body.target_id,
        "owner_id": current_user["user_id"],
        "file_path": stored_file_path,
    }

    doc = document_service.create_document(data, actor_user_id=current_user["user_id"])
    # T823: transition non-root related docs to pending_review.
    if body.type_code.upper() not in WORKFLOW_ROOT_TYPES:
        from modules.flow_gate.workflow.pipeline_service import transition_document_review
        transition_document_review(
            doc_id=doc_id,
            action="submit",
            actor_user_id=current_user["user_id"],
            user_permissions={"document.update"},
        )
        from modules.flow_gate.db import documents as _db_docs
        refreshed = _db_docs.get_by_id(doc_id)
        if refreshed is not None:
            doc = refreshed
    # T528: child creation → automatically transition parent (R/M) open → closed
    _try_close_parent_on_child_created(body.target_id, current_user["user_id"])
    return {"data": doc, "doc_id": doc_id}

@router.post("/next-empty", status_code=201)
@require_permission("perm_document_create")
def create_next_empty_document(
    body: NextEmptyDocumentCreate,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Create an empty document corresponding to the current workflow head.

    For types other than M, the document is registered in a review-pending state
    rather than immediately marked as complete.
    """
    from modules.flow_gate.db import workflow_sequences as _db_wfseq
    from modules.flow_gate.db.connection import get_store, now_iso

    title = (body.title or "").strip()
    type_code = (body.type_code or "").strip().upper()
    module = body.module or "none"
    if not title:
        raise HTTPException(status_code=422, detail="Title is required.")
    if len(title) > 100:
        raise HTTPException(status_code=422, detail="Title must be 100 characters or fewer.")
    if type_code in {"AC", "RJ", "V", "C"}:
        raise HTTPException(status_code=422, detail=f"Cannot create an empty document for type: {type_code}")

    prev_doc = document_service.get_document(body.prev_doc_id)
    if prev_doc is None:
        raise HTTPException(status_code=404, detail=f"Previous document not found: {body.prev_doc_id}")
    if prev_doc.get("project_id") != body.project_id:
        raise HTTPException(status_code=422, detail="project_id does not match the previous document.")
    if prev_doc.get("group_id") != body.group_id:
        raise HTTPException(status_code=422, detail="group_id does not match the previous document.")

    # prev_doc may be the sequence root OR a produced child (the just-approved doc the
    # FE navigated to). Resolve the owning sequence either way (0048 TR0009 — creating
    # from a child gave seq=None → 422 "Workflow is not defined.").
    seq = _db_wfseq.get_sequence_for_member_doc(body.prev_doc_id)
    if seq is None:
        raise HTTPException(status_code=422, detail="Workflow is not defined.")

    head = _db_wfseq.get_effective_head(seq["id"])
    if head is None:
        raise HTTPException(status_code=409, detail="No next workflow step exists.")
    head_type = str(head.get("type") or "").upper()
    if head_type != type_code:
        raise HTTPException(
            status_code=409,
            detail=f"Current next step is {head_type}. Requested type: {type_code}",
        )
    result_doc_id = head.get("result_doc_id")
    result_review = head.get("result_doc_review_status")
    if result_doc_id is not None and result_review != "approved":
        raise HTTPException(
            status_code=409,
            detail="Workflow step has already been created.",
        )

    try:
        doc_code = numbering_service.reserve_document(
            group_id=body.group_id,
            doc_type=type_code,
            module=module,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    m = _re.match(r"^(\d+)-[A-Za-z]+$", doc_code)
    seq_no = int(m.group(1)) if m else 0
    doc_id = f"{body.group_id}.{doc_code}"
    next_type = _next_workflow_type(seq["id"], head["id"])
    doc_file_path = storage_paths.document_path(
        project_id=body.project_id,
        group_code=body.group_id,
        doc_code=doc_code,
        filename="document.md",
        module=module,
        branch=_get_project_branch(body.project_id),
    )
    md_content = _build_next_empty_content(
        project_id=body.project_id,
        module=module,
        group_id=body.group_id,
        type_code=type_code,
        doc_code=doc_code,
        title=title,
        target_id=body.prev_doc_id,
        next_type=next_type,
    )

    try:
        doc_file_path.parent.mkdir(parents=True, exist_ok=True)
        doc_file_path.write_text(md_content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create document file: {exc}") from exc

    data: dict[str, Any] = {
        "doc_id": doc_id,
        "project_id": body.project_id,
        "module": module,
        "group_id": body.group_id,
        "type_code": type_code,
        "seq": seq_no,
        "title": title,
        "status": "draft",
        "target_id": body.prev_doc_id,
        "triggered_by": body.prev_doc_id,
        "owner_id": current_user["user_id"],
        "file_path": storage_paths.to_storage_relative(doc_file_path, body.project_id),
    }

    try:
        store = get_store()
        with store.transaction():
            doc = document_service.create_document(data, actor_user_id=current_user["user_id"])
            if type_code in AUTO_COMPLETE_TYPES:
                _db_wfseq.set_item_result_doc_id(head["id"], doc["doc_id"])
                from modules.flow_gate.db import documents as _db_docs
                _db_docs.update(doc["doc_id"], {"doc_review_status": "approved"})
                # NOTE: do NOT silently finalize here even when this memo fills the
                # last pending slot. AC (final approval) is an explicit review step
                # (M042 §3.1 — PM rejected silent wf_done). With no pending slots
                # left, head resolution surfaces AC/pending so the final-approval
                # screen appears; wf_done is set only when AC is approved.
            else:
                from modules.flow_gate.workflow.pipeline_service import (
                    register_workflow_result,
                    transition_document_review,
                )
                register_workflow_result(
                    item_id=head["id"],
                    registered_path=storage_paths.to_storage_relative(
                        doc_file_path, body.project_id
                    ),
                    registered_doc_id=doc["doc_id"],
                    registered_at=now_iso(),
                    actor_user_id=current_user["user_id"],
                )
                # DB004 §6.1: doc_review_status transitions go through transition_document_review()
                transition_document_review(
                    doc_id=doc["doc_id"],
                    action="submit",
                    actor_user_id=current_user["user_id"],
                    user_permissions={"document.update"},
                )
                from modules.flow_gate.db import documents as _db_docs
                refreshed = _db_docs.get_by_id(doc["doc_id"])
                if refreshed is not None:
                    doc = refreshed
            # group 0022 §5 (D0005 §3.4 form ②): attach AI queries in the SAME transaction
            # so a failure rolls them back with the document (+ .md unlink below).
            if body.questions:
                from modules.flow_gate.services import q_service
                q_service.add_questions(
                    doc_id=doc["doc_id"],
                    questions=[{"title": q.title, "body": q.body} for q in body.questions],
                    asker_kind="ai",
                    project_id=body.project_id,
                )
    except Exception:
        try:
            doc_file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # T528: child creation → automatically transition parent (R/M) open → closed
    _try_close_parent_on_child_created(body.prev_doc_id, current_user["user_id"])
    return {"data": doc, "doc_id": doc_id, "stored_path": str(doc_file_path)}


class NextApprovedError(Exception):
    """Raised by :func:`create_next_approved_core`, carrying the HTTP status the caller

    should surface. The HTTP router translates it to an ``HTTPException`` (preserving the
    original 0048 status codes); the continuous-chain caller (workflow_decision_service)
    translates it to a plain error so the unmanned run pauses honestly instead of 500ing.
    """

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def create_next_approved_core(
    *,
    project_id: str,
    group_id: str,
    module: str,
    prev_doc_id: str,
    type_code: str,
    actor_user_id: str,
    approver_perms: set,
    locale: str = "ko",
) -> dict:
    """Create + approve an instruction document (N | T) for the current head.

    Extracted from the ``POST /next-approved`` handler (0048) so the same create-and-approve
    mechanics are reusable off the HTTP path — specifically by the unmanned continuous chain
    (group 0092 / NR0003 B안), which auto-completes instruction-series steps server-side
    instead of spending an AI worker cycle on them. Title/body are server-generated from the
    type label (P0005 §2-2 / D-B); the approve transition is enforced with ``approver_perms``
    (resolved by the caller from the SAME resolver the live approve button uses — P0005 §4,
    approve is never bypassed). Emits doc-created SSE so the auto-generated "○○ 승인" appears
    in the UI without a worker-action push (NR0003 §알림).

    Raises :class:`NextApprovedError` (status_code + detail) on every rejection so callers can
    map it to their own surface. Returns ``{"data", "doc_id", "stored_path"}`` on success.
    """
    from modules.flow_gate.db import workflow_sequences as _db_wfseq
    from modules.flow_gate.db import documents as _db_docs
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.db.document_type_labels import get_type_name
    from modules.flow_gate.workflow.pipeline_service import (
        TransitionError,
        register_workflow_result,
        transition_document_review,
    )

    type_code = (type_code or "").strip().upper()
    module = module or "none"

    # (G1) Type whitelist — stronger than next-empty's blacklist (P0005 §5).
    # AC is excluded naturally (AC ∉ {N,T}, D0004 §3-3). TS removed (group 0121 R0001):
    # a test-scenario directive is token-issued (AI authors it), never auto-approved.
    if type_code not in {"N", "T"}:
        raise NextApprovedError(422, f"Auto-approved document not allowed for type: {type_code}")

    # (G2) Shared guards — replicated from next-empty (documents.py next-empty path)
    # for behavior parity and next-empty regression = 0 (L0007 §3).
    prev_doc = document_service.get_document(prev_doc_id)
    if prev_doc is None:
        raise NextApprovedError(404, f"Previous document not found: {prev_doc_id}")
    if prev_doc.get("project_id") != project_id:
        raise NextApprovedError(422, "project_id does not match the previous document.")
    if prev_doc.get("group_id") != group_id:
        raise NextApprovedError(422, "group_id does not match the previous document.")

    # prev_doc may be the sequence root OR a produced child (parity with next-empty;
    # 0048 TR0009). Resolve the owning sequence either way.
    seq = _db_wfseq.get_sequence_for_member_doc(prev_doc_id)
    if seq is None:
        raise NextApprovedError(422, "Workflow is not defined.")

    head = _db_wfseq.get_effective_head(seq["id"])
    if head is None:
        raise NextApprovedError(409, "No next workflow step exists.")
    head_type = str(head.get("type") or "").upper()
    if head_type != type_code:
        raise NextApprovedError(409, f"Current next step is {head_type}. Requested type: {type_code}")
    result_doc_id = head.get("result_doc_id")
    result_review = head.get("result_doc_review_status")
    if result_doc_id is not None and result_review != "approved":
        raise NextApprovedError(409, "Workflow step has already been created.")

    # (G3) Approve permission check — block with 403 BEFORE reserving a number (avoid
    # wasting a doc number). The caller resolves the real/effective permission set via the
    # same resolver as the live approve action; approve must never be bypassed with a
    # hardcoded set (P0005 §4). A non-approver still gets 403.
    if "document.approve" not in (approver_perms or set()):
        raise NextApprovedError(403, "document.approve permission is required.")

    # (T1) Server-side title/body templates — no client input (P0005 D-B).
    label = get_type_name(type_code, locale)
    gen_title = _auto_approved_title(label, locale)
    gen_body = _auto_approved_body(label, locale)

    try:
        doc_code = numbering_service.reserve_document(
            group_id=group_id,
            doc_type=type_code,
            module=module,
        )
    except ValueError as exc:
        raise NextApprovedError(400, str(exc)) from exc

    m = _re.match(r"^(\d+)-[A-Za-z]+$", doc_code)
    seq_no = int(m.group(1)) if m else 0
    doc_id = f"{group_id}.{doc_code}"
    next_type = _next_workflow_type(seq["id"], head["id"])
    doc_file_path = storage_paths.document_path(
        project_id=project_id,
        group_code=group_id,
        doc_code=doc_code,
        filename="document.md",
        module=module,
        branch=_get_project_branch(project_id),
    )
    md_content = _build_next_empty_content(
        project_id=project_id,
        module=module,
        group_id=group_id,
        type_code=type_code,
        doc_code=doc_code,
        title=gen_title,
        target_id=prev_doc_id,
        next_type=next_type,
    ) + gen_body + "\n"

    try:
        doc_file_path.parent.mkdir(parents=True, exist_ok=True)
        doc_file_path.write_text(md_content, encoding="utf-8")
    except OSError as exc:
        raise NextApprovedError(500, f"Failed to create document file: {exc}") from exc

    data: dict[str, Any] = {
        "doc_id": doc_id,
        "project_id": project_id,
        "module": module,
        "group_id": group_id,
        "type_code": type_code,
        "seq": seq_no,
        "title": gen_title,
        "status": "draft",
        "target_id": prev_doc_id,
        "triggered_by": prev_doc_id,
        "owner_id": actor_user_id,
        "file_path": storage_paths.to_storage_relative(doc_file_path, project_id),
    }

    # (TX) create → register slot → submit → approve, in a single transaction.
    # On any failure: roll back the DB and unlink the .md file (next-empty pattern).
    try:
        store = get_store()
        with store.transaction():
            doc = document_service.create_document(data, actor_user_id=actor_user_id)
            # P0005 §3 D-C: NOT the M direct-write shortcut. Register the slot fully
            # and go through the state machine (submit → approve) so the slot meta,
            # audit log and permission check are preserved (N/T/TS are real steps).
            register_workflow_result(
                item_id=head["id"],
                registered_path=storage_paths.to_storage_relative(
                    doc_file_path, project_id
                ),
                registered_doc_id=doc["doc_id"],
                registered_at=now_iso(),
                actor_user_id=actor_user_id,
            )
            # '' → pending_review. submit is mechanical (creator == author), so the
            # hardcoded {"document.update"} is kept (mirrors next-empty).
            transition_document_review(
                doc_id=doc["doc_id"],
                action="submit",
                actor_user_id=actor_user_id,
                user_permissions={"document.update"},
            )
            # pending_review → approved. approve is an approval action, enforced with
            # the caller's REAL permission set (P0005 §4 — the asymmetry vs submit).
            transition_document_review(
                doc_id=doc["doc_id"],
                action="approve",
                actor_user_id=actor_user_id,
                user_permissions=approver_perms,
            )
            refreshed = _db_docs.get_by_id(doc["doc_id"])
            if refreshed is not None:
                doc = refreshed
    except NextApprovedError:
        try:
            doc_file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except PermissionError as exc:
        try:
            doc_file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise NextApprovedError(403, str(exc)) from exc
    except TransitionError as exc:
        try:
            doc_file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise NextApprovedError(409, str(exc)) from exc
    except ValueError as exc:
        try:
            doc_file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise NextApprovedError(422, str(exc)) from exc
    except Exception:
        try:
            doc_file_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # T528: child creation → automatically transition parent (R/M) open → closed
    _try_close_parent_on_child_created(prev_doc_id, actor_user_id)

    # doc-created SSE so the auto-generated "○○ 승인" instruction appears in the explorer /
    # group view without an F5. broadcast (audience="*") + threadsafe because this runs from
    # a sync worker thread (mirrors the conversation-turn publish). Best-effort: a push
    # failure must never undo the already-committed document (NR0003 §알림).
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )
        from modules.flow_gate.api.v1.events.event_types import EventType

        for _evt in (
            FlowEvent(
                event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
                payload={
                    "operation": "created",
                    "doc_id": doc_id,
                    "type": type_code,
                    "title": gen_title,
                    "status": doc.get("status"),
                    "revision_no": 0,
                },
                audience="*",
                project=project_id,
                group_id=group_id,
                doc_id=doc_id,
            ),
            FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": group_id, "reason": "document_added"},
                audience="*",
                project=project_id,
                group_id=group_id,
                doc_id=doc_id,
            ),
        ):
            broadcast_event_threadsafe(_evt)
    except Exception as _sse_exc:  # pragma: no cover - defensive
        _log.warning("[next-approved] doc-created SSE publish failed (ignored): %s", _sse_exc)

    return {"data": doc, "doc_id": doc_id, "stored_path": str(doc_file_path)}


@router.post("/next-approved", status_code=201)
@require_permission("perm_document_create")
def create_next_approved_document(
    body: NextApprovedDocumentCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Create an auto-approved instruction document for the current workflow head.

    R0001 #2 (group 0048): when the next step is N | T | TS, create a document and
    approve it in the same transaction. Thin HTTP wrapper over
    :func:`create_next_approved_core` (group 0092: the same core also drives the
    unmanned continuous chain). Title/body are generated by the server from the type
    label (P0005 §2-2 / D-B). The approve transition is enforced with the caller's
    *real* permissions (P0005 §4) — FE hiding the menu item is not the source of truth.
    """
    # Permission source of truth = the SAME resolver the live approve button uses
    # (workflow router `_get_user_permissions`, the TR059 is_admin-based stub). The
    # real RBAC tables (user_project_roles / role_permissions) are unpopulated in
    # the live system, so permission_service.get_user_permissions returns ∅ for an
    # is_admin reviewer → every approve here would 403 (the reported regression).
    # Both approval paths must move together when T_rbac replaces the stub.
    from modules.flow_gate.workflow.routers.workflow import (
        _get_user_permissions as _resolve_user_permissions,
    )

    locale = request.headers.get("x-locale") or "ko"
    approver_perms = _resolve_user_permissions(current_user)
    try:
        return create_next_approved_core(
            project_id=body.project_id,
            group_id=body.group_id,
            module=body.module or "none",
            prev_doc_id=body.prev_doc_id,
            type_code=body.type_code,
            actor_user_id=current_user["user_id"],
            approver_perms=approver_perms,
            locale=locale,
        )
    except NextApprovedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


# ── Final-approval (AC) workflow step — file-less ─────────────────────────────

class _GroupDocRef(BaseModel):
    doc_id: str


class _ReopenBody(BaseModel):
    doc_id: str
    target_seq: int


class _RestoreBody(BaseModel):
    doc_id: str
    destination_seq: Optional[int] = None


_RETURN_POINT_NON_RESTORE_TYPES = tuple(sorted(WORKFLOW_ROOT_TYPES | {"Q", "AC"} | AUTO_COMPLETE_TYPES))
_FINGERPRINT_IGNORED_FRONTMATTER = {
    "doc_review_status",
    "updated_at",
    "created_at",
    "revision_no",
}


def _normalise_markdown_for_fingerprint(content: str) -> bytes:
    text = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        end_idx = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break
        if end_idx is not None:
            frontmatter = []
            for line in lines[1:end_idx]:
                key = line.split(":", 1)[0].strip().lower()
                if key in _FINGERPRINT_IGNORED_FRONTMATTER:
                    continue
                frontmatter.append(line)
            lines = ["---", *frontmatter, "---", *lines[end_idx + 1:]]
            text = "\n".join(lines)
    return (text.rstrip() + "\n").encode("utf-8")


def _content_fingerprint(doc: dict) -> str | None:
    try:
        path = _document_file_path(doc)
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    return _hashlib.sha256(_normalise_markdown_for_fingerprint(raw)).hexdigest()


def _record_return_point(
    group_id: str, docs: list[dict], *, root_was_done: bool,
) -> dict | None:
    from modules.flow_gate.db import workflow_return_points as _db_rp

    # A return point is the completed workflow you can walk "home" to. It is meaningful ONLY when
    # the rewind is leaving a genuinely completed (wf_done) workflow: reaching front later declares
    # the root wf_done, so if the workflow was mid-flight (a step never approved) that would be a
    # lie. Create one only when root_was_done; a pre-existing return point is always extended (a
    # nested rewind to an earlier step), preserving the completed baseline the first rewind captured.
    existing = _db_rp.get_by_group(group_id)
    if existing is None and not root_was_done:
        return None

    # The return point is the APPROVED baseline you are rewinding away from — the "home"
    # position a later restore walks back to. Only genuinely-approved steps belong in it.
    # Snapshotting a step that is currently pending_review (a step left pending by an earlier
    # cycle) would record prev_status=pending_review and later "restore" it to pending — a no-op
    # that also lets reached_front lie about wf_done. That laundered a pending status into the next
    # cycle's "original" state, and once tangled every restore became a permanent no-op. Recording
    # only approved steps closes the laundering at the source; docs already in the return point are
    # preserved by add_doc_if_absent, so nested rewinds keep the true approved baseline (0142 T0013).
    affected = [
        d for d in docs
        if d.get("type_code") not in _RETURN_POINT_NON_RESTORE_TYPES
        and (d.get("seq") or 0) > 0
        and (d.get("doc_review_status") or "approved") == "approved"
    ]
    if not affected:
        return existing

    front_seq = max(int(d.get("seq") or 0) for d in affected)
    rp = _db_rp.ensure(group_id, front_seq)
    for d in affected:
        fingerprint = _content_fingerprint(d)
        if fingerprint is None:
            # Conservative sentinel: later restore will stop at this document because
            # no real SHA-256 content digest can equal this value.
            fingerprint = "!" * 64
        _db_rp.add_doc_if_absent(
            return_point_id=rp["id"],
            doc_id=d["doc_id"],
            seq=int(d.get("seq") or 0),
            prev_status=d.get("doc_review_status") or "approved",
            fingerprint=fingerprint,
        )
    return _db_rp.get_by_group(group_id)


def _group_workflow_root_doc(doc: dict) -> Optional[dict]:
    from modules.flow_gate.db import documents as _db_docs
    if doc.get("type_code") in WORKFLOW_ROOT_TYPES:
        return doc
    roots = _db_docs.list_documents(
        project_id=doc.get("project_id"),
        group_id=doc.get("group_id"),
        type_code="R",
        limit=1,
    )
    if not roots:
        roots = _db_docs.list_documents(
            project_id=doc.get("project_id"),
            group_id=doc.get("group_id"),
            type_code="B",
            limit=1,
        )
    return roots[0] if roots else None


def _workflow_step_doc_ids(root_doc: Optional[dict]) -> Optional[set[str]]:
    """The doc_ids that actually fill this group's workflow-sequence slots.

    A "workflow step" is a document wired into the sequence (its result_doc_id) — NOT
    merely any document that shares the group's seq space. Groups accumulate phantom
    documents (superseded revisions, abandoned TRs) whose type is a normal workflow type
    but that never filled a slot; a raw ``seq >= target`` filter sweeps those in and
    corrupts the rewind + return-point (0142 rework: live group 0094 had an abandoned
    0003-TR that inflated restore counts and got reset spuriously).

    Returns the membership set when the group has a decided sequence, or ``None`` when no
    sequence is decided (caller falls back to the legacy type+seq filter).
    """
    if root_doc is None:
        return None
    from modules.flow_gate.db import workflow_sequences as _db_wfseq
    seq = _db_wfseq.get_sequence_by_doc_id(root_doc["doc_id"])
    if seq is None:
        return None
    items = _db_wfseq.get_sequence_items(seq["id"])
    return {it["result_doc_id"] for it in items if it.get("result_doc_id")}


def _return_point_payload(group_id: str) -> dict:
    from modules.flow_gate.db import workflow_return_points as _db_rp

    rp = _db_rp.get_by_group(group_id)
    if rp is None:
        return {
            "exists": False,
            "front_seq": None,
            "front_label": None,
            "restorable_count": 0,
            "current_min_seq": None,
            "destination_default": None,
            "destination_min": None,
        }

    front_doc = _db_rp.get_front_doc(group_id, int(rp["front_seq"]))
    current_min = _db_rp.current_pending_min_seq(rp["id"])
    return {
        "exists": True,
        "front_seq": rp["front_seq"],
        "front_label": (front_doc or {}).get("title") or (front_doc or {}).get("type_code"),
        "restorable_count": _db_rp.count_docs(rp["id"]),
        "current_min_seq": current_min,
        "destination_default": rp["front_seq"],
        "destination_min": current_min,
    }


@router.post("/workflow/final-approval", status_code=201)
@require_permission("perm_document_create")
def open_final_approval(
    body: _GroupDocRef,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Create the file-less AC (final-approval) document for a group.

    AC is an explicit workflow step, not an artifact: a documents row is created
    (so it gets a tab / header / action bar like any doc) but no .md file is
    written. Allowed only when the computed workflow head is AC (every step doc
    approved). Idempotent — reuses an existing un-approved AC.
    """
    from modules.flow_gate.db import documents as _db_docs
    doc = _db_docs.get_by_id(body.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {body.doc_id}")
    project_id = doc.get("project_id")
    group_id = doc.get("group_id")
    module = doc.get("module") or "none"
    if not project_id or not group_id:
        raise HTTPException(status_code=400, detail="Document has no project/group")

    APPROVED = {"approved", "wf_done"}
    group_docs = _db_docs.list_documents(project_id=project_id, group_id=group_id, limit=200)
    existing = [
        c for c in group_docs
        if c.get("type_code") == "AC" and c.get("doc_review_status") not in APPROVED
    ]
    if existing:
        return {"data": existing[0], "doc_id": existing[0]["doc_id"]}

    # Guard: the final-approval step is reachable only when the head is AC.
    if _parse_doc_workflow(doc).get("workflow_head_type") != "AC":
        raise HTTPException(status_code=409, detail="Final approval is not the current step")

    root_doc = _group_workflow_root_doc(doc)
    try:
        # AC is an ephemeral, always-terminal, file-less doc that reopen deletes
        # and recreates. Using reserve_document would burn a permanent group
        # number on every reopen→reapprove cycle (0004 → 0006). Derive it from
        # MAX(seq)+1 without consuming the shared counter so the number stays
        # stable (always the slot right after the last real doc).
        doc_code = numbering_service.peek_document_code(
            group_id=group_id, doc_type="AC", module=module,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    m = _re.match(r"^(\d+)-[A-Za-z]+$", doc_code)
    seq_no = int(m.group(1)) if m else 0
    new_id = f"{group_id}.{doc_code}"

    data: dict[str, Any] = {
        "doc_id": new_id,
        "project_id": project_id,
        "module": module,
        "group_id": group_id,
        "type_code": "AC",
        "seq": seq_no,
        "title": "Final Approval",
        "status": "draft",
        "target_id": (root_doc or doc).get("doc_id"),
        "owner_id": current_user["user_id"],
        "file_path": None,   # file-less by design
    }
    created = document_service.create_document(data, actor_user_id=current_user["user_id"])
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    transition_document_review(
        doc_id=new_id, action="submit",
        actor_user_id=current_user["user_id"], user_permissions={"document.update"},
    )
    refreshed = _db_docs.get_by_id(new_id)
    return {"data": refreshed or created, "doc_id": new_id}


@router.post("/workflow/reopen")
@require_permission("perm_document_update")
def reopen_workflow(
    body: _ReopenBody,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Time-machine reopen: roll the workflow back to an earlier step.

    Every workflow-step doc with seq >= target_seq has its approval revoked
    (reset to pending_review); the file-less AC doc is deleted; the R doc
    returns to wf_in_progress. Documents are preserved — only approvals roll
    back — so the worker revises and re-submits.
    """
    from modules.flow_gate.db import documents as _db_docs
    doc = _db_docs.get_by_id(body.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {body.doc_id}")
    project_id = doc.get("project_id")
    group_id = doc.get("group_id")
    if not project_id or not group_id:
        raise HTTPException(status_code=400, detail="Document has no project/group")

    from modules.flow_gate.db.connection import get_store

    with get_store().transaction():
        group_docs = _db_docs.list_documents(project_id=project_id, group_id=group_id, limit=200)
        # Only documents wired into the workflow sequence are rewindable steps. Phantom docs
        # (superseded/abandoned revisions sharing the group's seq space) must NOT be swept in
        # by the raw seq threshold — 0142 rework. Falls back to type+seq when no sequence.
        root_doc = _group_workflow_root_doc(doc)
        step_ids = _workflow_step_doc_ids(root_doc)

        def _is_rewindable_step(c: dict) -> bool:
            if c.get("type_code") in _RETURN_POINT_NON_RESTORE_TYPES:
                return False
            if step_ids is not None and c["doc_id"] not in step_ids:
                return False
            return (c.get("seq") or 0) >= body.target_seq

        # Capture completion state BEFORE any rollback: only a wf_done workflow has a "home" to
        # restore to (see _record_return_point). root_doc is read here, pre-mutation.
        root_was_done = root_doc is not None and root_doc.get("doc_review_status") == "wf_done"
        snapshot_docs = [c for c in group_docs if _is_rewindable_step(c)]
        _record_return_point(group_id, snapshot_docs, root_was_done=root_was_done)

        reopened: list = []
        for c in group_docs:
            tc = c.get("type_code")
            if tc == "AC":
                _db_docs.delete(c["doc_id"])   # ephemeral final-approval doc
                continue
            # R/Q are structural; auto-complete types (memos) are notes with no review
            # action — resetting them to pending_review would strand them as an
            # un-clearable head, so they are never rolled back (A).
            if tc in WORKFLOW_ROOT_TYPES or tc == "Q" or tc in AUTO_COMPLETE_TYPES:
                continue
            if _is_rewindable_step(c):
                _db_docs.update(c["doc_id"], {"doc_review_status": "pending_review"})
                reopened.append(c["doc_id"])

        if root_was_done and root_doc is not None:
            _db_docs.update(root_doc["doc_id"], {"doc_review_status": "wf_in_progress"})

        # Audit trail: the rewind side previously logged nothing, so a return point could appear
        # (and its restore fire) with no record of what created it — a gap when reconstructing a
        # tangled workflow. Mirror the restore event so both directions leave a trace (0142 T0013).
        from modules.flow_gate.workflow import event_logger as _event_logger
        try:
            _event_logger.log_event(
                event_type="workflow_reopen",
                project_id=project_id,
                actor_user_id=current_user["user_id"],
                group_id=group_id,
                document_id=(root_doc or doc).get("id"),
                metadata={
                    "reopened": reopened,
                    "target_seq": body.target_seq,
                    "return_point": _return_point_payload(group_id),
                },
            )
        except Exception as exc:  # pragma: no cover - best-effort event trail
            _log.warning("[workflow reopen] event logging failed: %s", exc, exc_info=True)

        return {"ok": True, "reopened": reopened, "return_point": _return_point_payload(group_id)}


@router.get("/workflow/{doc_id}/return-point")
@require_permission("perm_document_read")
def get_workflow_return_point(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    from modules.flow_gate.db import documents as _db_docs

    doc = _db_docs.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    group_id = doc.get("group_id")
    if not group_id:
        raise HTTPException(status_code=400, detail="Document has no group")
    return {"ok": True, "return_point": _return_point_payload(group_id)}


@router.post("/workflow/restore")
@require_permission("perm_document_update")
def restore_workflow(
    body: _RestoreBody,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Reverse time-machine restore: re-approve unchanged rollback steps."""
    from modules.flow_gate.db import documents as _db_docs
    from modules.flow_gate.db import workflow_return_points as _db_rp
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.workflow import event_logger as _event_logger

    doc = _db_docs.get_by_id(body.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {body.doc_id}")
    group_id = doc.get("group_id")
    project_id = doc.get("project_id")
    if not project_id or not group_id:
        raise HTTPException(status_code=400, detail="Document has no project/group")

    with get_store().transaction():
        rp = _db_rp.get_by_group(group_id)
        root_doc = _group_workflow_root_doc(doc)
        root_status = (root_doc or doc).get("doc_review_status") or "wf_in_progress"
        if rp is None:
            return {
                "ok": True,
                "restored": [],
                "stopped_at": None,
                "stopped_doc_id": None,
                "reached_front": False,
                "root_status": root_status,
                "return_point_cleared": False,
            }

        front_seq = int(rp["front_seq"])
        current_min = _db_rp.current_pending_min_seq(rp["id"])
        lower_bound = current_min if current_min is not None else front_seq
        destination = body.destination_seq if body.destination_seq is not None else front_seq
        destination = max(lower_bound, min(int(destination), front_seq))

        restored: list[str] = []
        stopped_at: int | None = None
        stopped_doc_id: str | None = None
        for candidate in _db_rp.list_candidates(rp["id"], destination):
            fingerprint = _content_fingerprint(candidate)
            if fingerprint is None or fingerprint != candidate.get("fingerprint"):
                stopped_at = int(candidate.get("seq") or 0)
                stopped_doc_id = candidate.get("doc_id")
                break
            restore_status = candidate.get("prev_status") or "approved"
            _db_docs.update(candidate["doc_id"], {"doc_review_status": restore_status})
            restored.append(candidate["doc_id"])

        reached_front = stopped_at is None and destination == front_seq
        return_point_cleared = False
        if reached_front:
            # Finalize (root → wf_done, clear the return point) ONLY when the restore actually
            # reconstructed the approved baseline. If any snapshot doc is still pending_review
            # — a pre-existing polluted return point whose prev_status was itself pending — keep
            # the return point and leave the root untouched rather than declaring the workflow
            # done over a pending step and laundering the pollution on the next rewind (0142 T0013).
            still_pending = _db_rp.current_pending_min_seq(rp["id"])
            if still_pending is None:
                if root_doc is not None and root_doc.get("doc_review_status") == "wf_in_progress":
                    _db_docs.update(root_doc["doc_id"], {"doc_review_status": "wf_done"})
                    root_doc = _db_docs.get_by_id(root_doc["doc_id"])
                _db_rp.delete(rp["id"])
                return_point_cleared = True

        final_root = root_doc or _group_workflow_root_doc(doc) or doc
        root_status = final_root.get("doc_review_status") or root_status
        try:
            _event_logger.log_event(
                event_type="reverse_time_machine",
                project_id=project_id,
                actor_user_id=current_user["user_id"],
                group_id=group_id,
                document_id=final_root.get("id"),
                metadata={
                    "restored": restored,
                    "stopped_at": stopped_at,
                    "destination_seq": destination,
                    "front_seq": front_seq,
                    "return_point_cleared": return_point_cleared,
                },
            )
        except Exception as exc:  # pragma: no cover - best-effort event trail
            _log.warning("[workflow restore] event logging failed: %s", exc, exc_info=True)

        return {
            "ok": True,
            "restored": restored,
            "stopped_at": stopped_at,
            "stopped_doc_id": stopped_doc_id,
            "reached_front": reached_front,
            "root_status": root_status,
            "return_point_cleared": return_point_cleared,
        }


def _create_next_empty_document_for_auto_draft(
    *,
    project_id: str,
    group_id: str,
    r_doc_id: str,
    type_code: str,
    title: str,
    module: str = "none",
    actor_user_id: str,
) -> dict | None:
    """AUTO_RESULT_DRAFT automatic draft creation — thin wrapper callable without HTTP context.

    T605 design choice A: compliant with NR143 §0 / DB004 §6.2.
    Called from pipeline_service._on_approval_advance_sequence() to auto-create
    N→NR and T→TR drafts after transition_document_review approval.

    Returns the created doc dict, or None if not applicable / failed.
    """
    from modules.flow_gate.db import workflow_sequences as _db_wfseq
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.workflow.pipeline_service import (
        register_workflow_result,
        transition_document_review,
    )

    seq = _db_wfseq.get_sequence_by_doc_id(r_doc_id)
    if seq is None:
        return None

    head = _db_wfseq.get_effective_head(seq["id"])
    if head is None:
        return None

    head_type = str(head.get("type") or "").upper()
    if head_type != type_code:
        return None  # next slot type differs from the requested type

    result_doc_id = head.get("result_doc_id")
    if result_doc_id is not None:
        return None  # slot already has a result doc — not pending

    try:
        doc_code = numbering_service.reserve_document(
            group_id=group_id,
            doc_type=type_code,
            module=module,
        )
    except ValueError:
        return None

    m = _re.match(r"^(\d+)-[A-Za-z]+$", doc_code)
    seq_no = int(m.group(1)) if m else 0
    doc_id = f"{group_id}.{doc_code}"
    next_type = _next_workflow_type(seq["id"], head["id"])
    doc_file_path = storage_paths.document_path(
        project_id=project_id,
        group_code=group_id,
        doc_code=doc_code,
        filename="document.md",
        module=module,
        branch=_get_project_branch(project_id),
    )
    md_content = _build_next_empty_content(
        project_id=project_id,
        module=module,
        group_id=group_id,
        type_code=type_code,
        doc_code=doc_code,
        title=title,
        target_id=r_doc_id,
        next_type=next_type,
    )

    try:
        doc_file_path.parent.mkdir(parents=True, exist_ok=True)
        doc_file_path.write_text(md_content, encoding="utf-8")
    except OSError:
        return None

    data: dict[str, Any] = {
        "doc_id": doc_id,
        "project_id": project_id,
        "module": module,
        "group_id": group_id,
        "type_code": type_code,
        "seq": seq_no,
        "title": title,
        "status": "draft",
        "target_id": r_doc_id,
        "triggered_by": r_doc_id,
        "owner_id": actor_user_id,
        "file_path": storage_paths.to_storage_relative(doc_file_path, project_id),
    }

    created_doc: dict | None = None
    try:
        store = get_store()
        with store.transaction():
            created_doc = document_service.create_document(data, actor_user_id=actor_user_id)
            register_workflow_result(
                item_id=head["id"],
                registered_path=storage_paths.to_storage_relative(
                    doc_file_path, project_id
                ),
                registered_doc_id=created_doc["doc_id"],
                registered_at=now_iso(),
                actor_user_id=actor_user_id,
            )
            transition_document_review(
                doc_id=created_doc["doc_id"],
                action="submit",
                actor_user_id=actor_user_id,
                user_permissions={"document.update"},
            )
            from modules.flow_gate.db import documents as _db_docs
            refreshed = _db_docs.get_by_id(created_doc["doc_id"])
            if refreshed is not None:
                created_doc = refreshed
    except Exception:
        try:
            doc_file_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    return created_doc



@router.get("")
@require_permission("perm_document_read")
def list_documents(
    project_id: str = Query(...),
    module: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None),
    type_code: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    """Fetch the list of documents."""
    return document_service.list_documents(
        project_id=project_id,
        module=module,
        group_id=group_id,
        type_code=type_code,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/detail")
@require_permission("perm_document_read")
def get_document_rpc(
    doc_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Fetch a single document (RPC style — query param)."""
    return get_document(doc_id, current_user)


@router.get("/content")
@require_permission("perm_document_read")
def get_document_content_rpc(
    doc_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Fetch document content (RPC style — query param)."""
    return get_document_content(doc_id, current_user)


@router.post("/mention-copy")
@require_permission("perm_document_read")
def record_mention_copy(
    body: MentionCopyRecordRpc,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Record that the current user copied this document's mention block (R0001 / NR0003 rev4).

    Persists per-(user, doc) state so the header badge survives reloads/tabs/devices. A fresh
    copy overwrites the previous (UPSERT) — only the last copied mention is shown. Read-scope
    permission is sufficient: this records the caller's own state, not a document mutation.
    """
    row = db_mention_copies.upsert(current_user["user_id"], body.doc_id, body.mention_kind)
    return {"ok": True, "mention_kind": row["mention_kind"], "copied_at": row["copied_at"]}


@router.get("/mention-copy")
@require_permission("perm_document_read")
def get_mention_copy(
    doc_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the current user's last copied-mention state for a document (badge hydration).

    `copied: false` means the user has not copied this document's mention — the header renders
    no badge (NR0003 rev4: there is no 'before copy' state, absence == not copied).
    """
    row = db_mention_copies.get(current_user["user_id"], doc_id)
    if not row:
        return {"ok": True, "copied": False}
    return {
        "ok": True,
        "copied": True,
        "mention_kind": row["mention_kind"],
        "copied_at": row["copied_at"],
    }


@router.patch("/content")
@require_permission("perm_document_update")
def update_document_content_rpc(
    body: DocumentContentUpdateRpc,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save document content (RPC style — body)."""
    return update_document_content(
        body.doc_id,
        DocumentContentUpdate(content=body.content),
        current_user,
    )


@router.patch("/workflow")
@require_permission("perm_document_update")
def update_document_workflow_rpc(
    body: WorkflowUpdateRpc,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save finalized workflow steps (RPC style — body)."""
    return update_document_workflow(
        body.doc_id,
        WorkflowUpdate(workflow_steps=body.workflow_steps),
        current_user,
    )


@router.patch("/update")
@require_permission("perm_document_update")
def update_document_rpc(
    body: DocumentUpdateRpc,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Update document fields (RPC style — body)."""
    return update_document(
        body.doc_id,
        DocumentUpdate(**body.model_dump(exclude={"doc_id"})),
        current_user,
    )


@router.delete("/delete", status_code=204, response_model=None)
@require_permission("perm_document_delete")
def delete_document_rpc(
    body: DocumentDeleteRpc,
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete a document (RPC style — body)."""
    return delete_document(body.doc_id, current_user)


@router.post("/transitions")
@require_permission("perm_document_update")
def transition_document_rpc(
    body: TransitionRequestRpc,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Perform document state transition (RPC style — body)."""
    return transition_document(
        body.doc_id,
        TransitionRequest(to_state=body.to_state, reason=body.reason),
        current_user,
    )


@router.post("/attachments", status_code=201)
async def upload_attachment_rpc(
    doc_id: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Attach a file to a document (RPC style — form field doc_id)."""
    return await upload_attachment(doc_id, file, current_user)


def _shape_review(row: dict) -> dict:
    """Convert a document_reviews row for the frontend, parsing findings JSON and deriving its count."""
    raw = row.get("findings")
    findings: list = []
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                findings = parsed
        except (ValueError, TypeError):
            findings = []
    elif isinstance(raw, list):
        findings = raw
    # Resolve reviewer_id (raw UUID) → human-readable name for display. The raw UUID is
    # meaningless in the UI; fall back to None so the client shows a generic label.
    reviewer_id = row.get("reviewer_id")
    reviewer_name = None
    if reviewer_id:
        try:
            from modules.flow_gate.db import users as _db_users
            u = _db_users.get_by_id(reviewer_id)
            if u:
                reviewer_name = u.get("username") or u.get("display_name")
        except Exception:
            reviewer_name = None
    return {
        "id": row.get("id"),
        "revision_no": row.get("revision_no"),
        "reviewer_id": reviewer_id,
        "reviewer_name": reviewer_name,
        "verdict": row.get("verdict"),
        "finding_count": len(findings),  # Computed by the server, not the AI.
        "findings": findings,
        "comment": row.get("comment"),
        "reviewed_at": row.get("reviewed_at"),
        "created_at": row.get("created_at"),
    }


def _load_ai_reviews(doc_id: str) -> tuple[Optional[dict], list[dict]]:
    """Return the latest review and newest-first history, or (None, []) when no reviews exist."""
    from modules.flow_gate.db import document_reviews as _db_reviews
    try:
        rows = _db_reviews.list_by_doc(doc_id)
    except Exception:
        return None, []
    shaped = [_shape_review(r) for r in rows]
    return (shaped[0] if shaped else None), shaped


def _load_test_runs(doc_id: str) -> tuple[Optional[dict], list[dict]]:
    try:
        from modules.flow_gate.services import test_run_service as _test_run_service

        return _test_run_service.load_test_run_embed(doc_id)
    except Exception:
        return None, []


@router.get("/{doc_id}")
@require_permission("perm_document_read")
def get_document(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Fetch a single document."""
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    out = _parse_doc_workflow(doc)
    # AI review results (document_reviews child records), variant C: latest review plus full history.
    out["ai_review"], out["ai_review_history"] = _load_ai_reviews(doc_id)
    out["test_run"], out["test_run_history"] = _load_test_runs(doc_id)
    return out


@router.get("/{doc_id}/content")
@require_permission("perm_document_read")
def get_document_content(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the content of the Markdown file linked to the document."""
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

    file_path = _document_file_path(doc)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found.")
    return {"content": file_path.read_text(encoding="utf-8")}


@router.patch("/{doc_id}/content")
@require_permission("perm_document_update")
def update_document_content(
    doc_id: str,
    body: DocumentContentUpdate,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save the content of the Markdown file linked to the document."""
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    _reject_if_group_disposed(doc)
    final_approved = document_service.is_final_approved(doc)
    if not document_service.is_document_editable(doc, final_approved=final_approved):
        if final_approved:
            detail = "Modification not allowed after final approval."
        else:
            detail = f"Modification not allowed for status: {doc.get('status')}"
        raise HTTPException(status_code=422, detail=detail)

    file_path = _document_file_path(doc)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(body.content, encoding="utf-8")
    # L0054.0002 correction: this is a real documents.file_path write (persisted via
    # update_document), not a runtime-only path. Store it relative so a content edit
    # does not silently re-introduce an absolute path (B0001 regression).
    updated = document_service.update_document(
        doc_id,
        {"file_path": storage_paths.to_storage_relative(file_path, doc.get("project_id"))},
        actor_user_id=current_user["user_id"],
    )
    return {"data": updated, "content": body.content}


@router.post("/{doc_id}/regenerate")
@require_permission("perm_document_update")
def regenerate_document_file(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Recreate the Markdown file for a document whose stored file is missing (R0001).

    This is a *recovery* operation, not a content edit, so it deliberately does NOT
    apply the editable/final-approval gate (NR0003 §5) — a missing file must be
    recoverable regardless of workflow state (R0001 itself is ``closed``). Safety
    instead comes from two guards: the document row must exist, and an existing file
    is never clobbered (409).

    Recovery source priority (NR0003 §3): the newest readable revision backup (restores
    the last-saved body) → else a frontmatter stub synthesized from DB metadata (always
    works, but the body is lost — surfaced as ``body_lost: true``).
    """
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    _reject_if_group_disposed(doc)

    target = _regenerate_target_path(doc)
    if target.is_file():
        # The file is present — there is nothing to recover, and overwriting it would
        # destroy good data. Treat as a no-op conflict (NR0003 §7-3).
        raise HTTPException(
            status_code=409,
            detail="Document file already exists; nothing to regenerate.",
        )

    project_id = doc.get("project_id")
    body = _latest_revision_body(doc_id, project_id)
    if body is not None:
        restored_from = "revision"
    else:
        restored_from = "metadata"
        body = _build_next_empty_content(
            project_id=project_id,
            module=doc.get("module") or "none",
            group_id=doc.get("group_id") or "",
            type_code=doc.get("type_code") or "",
            doc_code=_short_doc_code(doc),
            title=doc.get("title") or "",
            target_id=doc.get("target_id") or "",
            next_type="",
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Storage error: {exc}") from exc

    # L0054.0002: persist the path relative so recovery never re-introduces an
    # absolute path (B0001 regression).
    updated = document_service.update_document(
        doc_id,
        {"file_path": storage_paths.to_storage_relative(target, project_id)},
        actor_user_id=current_user["user_id"],
    )

    _broadcast_document_refresh(doc)

    return {
        "data": updated,
        "restored_from": restored_from,
        "body_lost": restored_from == "metadata",
        "content": body,
    }


@router.patch("/{doc_id}/root-type")
@require_permission("perm_document_update")
def convert_root_type(
    doc_id: str,
    body: RootTypeConvert,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Convert a workflow root document between R (requirement) and B (bug).

    Implements NR0066.0003 §5: the type code is part of the document identity
    (doc_id, filename, inbound references), so a plain field update cannot flip it.
    This is allowed ONLY on a pristine root — before the workflow decision is taken
    — and rewrites the identity atomically. Rejections: 404 (missing), 422 (not a
    root / invalid target), 409 (decision already taken or target id collision).
    """
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    _reject_if_group_disposed(doc)
    return {
        "data": document_service.convert_root_document_type(
            doc_id, body.new_type, actor_user_id=current_user["user_id"]
        )
    }


@router.post("/{doc_id}/conversation/turn", status_code=201)
@require_permission("perm_document_update")
def append_conversation_turn(
    doc_id: str,
    body: ConversationTurnAppend,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Append one turn to a conversation (CH) document from the session UI.

    The conversation body IS the chat log (L0044.0008 §6); this is the human-side
    counterpart to the AI worker's inbox-edit accumulation path, which is token-bound
    and so unavailable to a logged-in PM viewing the chat. The reject on TR0044.0010
    rev1 was that selecting CH just yielded a plain document with workflow buttons and
    no way to converse — this endpoint (plus the FE ConversationView composer) makes a
    CH document actually conversational.

    The turn is serialized server-side via the shared `conversation` module (single
    source of truth — the FE never builds the wire format), the file is re-saved
    wholesale (same model as the AI accumulation path), and on overflow the chat rolls
    over to a successor CH doc (§7). An all-audience SSE refresh lets every open chat
    view — the owner's and the AI worker's — update live (§8). This is a sync route
    (worker thread), so SSE goes through the threadsafe broadcaster: a bare
    asyncio.get_event_loop() would raise here (the 0059 trap).
    """
    from modules.flow_gate import conversation as _conv

    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    if (doc.get("type_code") or "").upper() not in CONVERSATION_TYPE_CODES:
        raise HTTPException(status_code=400, detail="Not a conversation document.")
    _reject_if_group_disposed(doc)
    final_approved = document_service.is_final_approved(doc)
    if not document_service.is_document_editable(doc, final_approved=final_approved):
        raise HTTPException(status_code=422, detail="Conversation is closed for new turns.")

    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Turn body must not be empty.")

    from modules.flow_gate.db.connection import now_iso

    file_path = _document_file_path(doc)
    existing = file_path.read_text(encoding="utf-8") if file_path.is_file() else ""
    new_content = _conv.append_turn(existing, body.speaker, now_iso(), text)

    # Enforce the same content cap the inbox edit path uses, so a session turn cannot
    # push the body past the hard limit the carry-over threshold is sized against.
    from modules.flow_gate.api.inbox_routes import _content_max

    max_bytes = _content_max()
    if max_bytes > 0 and len(new_content.encode("utf-8")) > max_bytes:
        raise HTTPException(status_code=422, detail="Conversation content limit exceeded.")

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(new_content, encoding="utf-8")
    document_service.update_document(
        doc_id,
        {"file_path": storage_paths.to_storage_relative(file_path, doc.get("project_id"))},
        actor_user_id=current_user["user_id"],
    )

    # §7 carry-over: best-effort, must never break the turn that already committed.
    carried_over_doc_id: Optional[str] = None
    try:
        if _conv.should_carry_over(len(new_content.encode("utf-8")), max_bytes):
            from modules.flow_gate.api.inbox_routes import _carry_over_conversation

            carried_over_doc_id = _carry_over_conversation(
                doc.get("project_id"),
                doc.get("module"),
                {"group_id": doc.get("group_id")},
                doc_id,
                new_content,
                current_user["user_id"],
            )
    except Exception as _co_exc:  # pragma: no cover - defensive
        _log.warning("[conversation turn] carry-over failed (ignored): %s", _co_exc)

    # §8 live delivery: refresh every open chat view (owner + AI worker), not just the
    # sender's, so a turn appears on the other side without an F5.
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )
        from modules.flow_gate.api.v1.events.event_types import EventType

        broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
            payload={
                "operation": "updated",
                "doc_id": doc_id,
                "type": doc.get("type_code"),
                "title": doc.get("title"),
                "status": doc.get("status"),
            },
            audience="*",
            project=doc.get("project_id"),
            group_id=doc.get("group_id"),
            doc_id=doc_id,
        ))
    except Exception as _sse_exc:  # pragma: no cover - defensive
        _log.warning("[conversation turn] SSE publish failed (ignored): %s", _sse_exc)

    resp: dict = {"ok": True, "doc_id": doc_id, "content": new_content}
    if carried_over_doc_id:
        resp["carried_over_doc_id"] = carried_over_doc_id
    return resp


@router.patch("/{doc_id}/workflow")
@require_permission("perm_document_update")
def update_document_workflow(
    doc_id: str,
    body: WorkflowUpdate,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save finalized workflow steps for R/B workflow-root documents."""
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    _reject_if_group_disposed(doc)
    if doc.get("type_code") not in WORKFLOW_ROOT_TYPES:
        raise HTTPException(status_code=400, detail="Only R/B workflow roots can have workflows configured.")

    steps_json = _json.dumps(body.workflow_steps) if body.workflow_steps is not None else None
    updated = document_service.update_document(
        doc_id,
        {"workflow_steps": steps_json},
        actor_user_id=current_user["user_id"],
    )
    return _parse_doc_workflow(updated)


@router.patch("/{doc_id}")
@require_permission("perm_document_update")
def update_document(
    doc_id: str,
    body: DocumentUpdate,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Update document fields (excluding status changes)."""
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    _reject_if_group_disposed(doc)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update.")
    return document_service.update_document(doc_id, updates, actor_user_id=current_user["user_id"])


@router.delete("/{doc_id}", status_code=204, response_model=None)
@require_permission("perm_document_delete")
def delete_document(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete a document."""
    document_service.delete_document(doc_id, actor_user_id=current_user["user_id"])


@router.post("/{doc_id}/transitions")
@require_permission("perm_document_update")
def transition_document(
    doc_id: str,
    body: TransitionRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Perform document state transition (CAS pattern)."""
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    _reject_if_group_disposed(doc)
    return document_service.transition_state(
        doc_id=doc_id,
        to_state=body.to_state,
        actor_user_id=current_user["user_id"],
        reason=body.reason,
    )


# ── Document type endpoints ──────────────────────────────────────────────────────

@router.get("/types/list")
@require_permission("perm_document_read")
def list_document_types(
    project_id: Optional[str] = Query(None),
    series: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    """List document types."""
    return document_types.list_types(project_id=project_id, series=series)


@router.post("/types", status_code=201)
@require_permission("perm_document_type_manage")
def create_document_type(
    body: DocumentTypeCreate,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Add a custom document type for a specific project."""
    return document_types.extend_type(body.model_dump())


@router.delete("/types/{type_id}", status_code=204, response_model=None)
@require_permission("perm_document_type_manage")
def delete_document_type(
    type_id: int,
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete a document type (system types cannot be deleted)."""
    document_types.delete_type(type_id)


# ── Attachment upload ──────────────────────────────────────────────────────────

@router.post("/{doc_id}/attachments", status_code=201)
async def upload_attachment(
    doc_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Attach a file to a document."""
    from pathlib import Path
    import time

    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

    project_id = doc.get("project_id") or doc.get("data", {}).get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail=f"Project information missing for: {doc_id}")

    storage_root = get_storage_root(project_id)
    attach_dir = storage_root / "projects" / storage_paths.project_dir_name(project_id) / "attachments" / doc_id
    attach_dir.mkdir(parents=True, exist_ok=True)

    original_name = file.filename or "upload"
    safe_name = Path(original_name).name
    dest = attach_dir / safe_name

    if dest.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        safe_name = f"{stem}_{int(time.time())}{suffix}"
        dest = attach_dir / safe_name

    content = await file.read()
    dest.write_bytes(content)

    return {
        "data": {
            "filename": safe_name,
            "size": len(content),
            "path": str(dest),
        }
    }

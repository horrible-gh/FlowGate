"""Workflow FastAPI router (D017 r1 §7).

Endpoints:
  POST  /api/v1/groups                              create a new group
  PUT   /api/v1/groups/{group_id}                   update a group (T191)
  POST  /api/v1/groups/{gid}/transitions/{action}  transition a group state
  GET   /api/v1/groups/{gid}/timeline               group event log
  POST  /api/v1/documents/{id}/transitions/{action} transition a document state
  GET   /api/v1/documents/{id}/prompt               generate the next-action prompt

RBAC: rbac.decorators.require_permission does not exist, so an inline stub is
used. The dependency on the TR059 stub status is explicitly documented.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import anyio.to_thread

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import workflow_events as db_events
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.documents.constants import NON_SLOT_WORKFLOW_TYPES
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate import process_service
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git_service import GitServiceError

from ..pipeline_service import (
    PermissionError as WFPermissionError,
    TransitionError,
    create_group,
    register_workflow_result,
    transition_document,
    transition_document_review,
    transition_group,
)
from ..prompt_copy_service import build_prompt
from ..rejection_identity import new_rejection_id

router = APIRouter(prefix="/api/v1", tags=["workflow"])


def _parse_rejection_history(raw: Any) -> list:
    """Convert the DB rejection_history JSON string to a Python list; return an empty list on parse failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ── RBAC permission lookup stub ───────────────────────────────────────────────
# Replace the stub below with the real implementation when T_rbac is complete.
# (TR059 unresolved section)

def _get_user_permissions(user: dict) -> set[str]:
    """Return the current user's permission set.

    Stub behavior: admins get all permissions, while other users only receive
    the default permission set. The real implementation should query the DB
    through the rbac module.
    """
    if user.get("is_admin"):
        return {
            "project.group.manage",
            "document.create",
            "document.read",
            "document.update",
            "document.approve",
            "document.reject",
            "document.delete",
            "document.delete.own.draft",
            "own.draft",
        }
    # Default: worker-level permissions
    return {
        "document.create",
        "document.read",
        "document.update",
        "own.draft",
        "document.delete.own.draft",
    }


# ── Disposed-group action guard (TR0079.0003 rework) ──────────────────────────
# The review feedback asked for an authoritative server-side block on actions
# against documents in a disposed (DC) group — hiding the action bar client-side
# is UX only and can be bypassed by a stale tab that never received the SSE
# refresh. Every forward workflow action endpoint calls this so the disposed
# group's documents are inert regardless of client state ("reject even if an action is taken").

def _guard_group_not_disposed(doc: Optional[dict], doc_id: str) -> None:
    """Raise 409 group_disposed when the document's group has been disposed.

    No-op for live groups and for unknown documents (the caller's own 404 path
    handles a missing document). The check is centralized in process_service so
    the dashboard exclusion, the document-detail flag, and this guard share one
    definition of "disposed".
    """
    group_id = (doc or {}).get("group_id")
    if group_id and process_service.is_group_disposed(group_id):
        raise HTTPException(
            status_code=409,
            detail=f"group_disposed: {doc_id} belongs to a disposed group",
        )


def _guard_group_not_ai_running(doc: Optional[dict], doc_id: str) -> None:
    """Compatibility entry point backed by the authoritative DB mutation policy."""
    from modules.flow_gate.services.mutation_policy import (
        assert_group_mutation_allowed,
        human_principal,
    )

    assert_group_mutation_allowed(
        (doc or {}).get("group_id"), human_principal(), f"workflow mutation: {doc_id}"
    )

# ── Request/response schemas ──────────────────────────────────────────────────

class GroupCreateRequest(BaseModel):
    project_id: str
    module: str = "none"
    title: str
    group_id: Optional[str] = None   # Auto-reserved when omitted
    parent_id: Optional[str] = None
    priority: Optional[str] = None


class GroupUpdateRequest(BaseModel):
    title: Optional[str] = None
    priority: Optional[str] = None
    module: Optional[str] = None


class GroupTransitionRequest(BaseModel):
    comment: Optional[str] = None


class DocumentTransitionRequest(BaseModel):
    comment: Optional[str] = None


class RejectionReasonRequest(BaseModel):
    reason: str


class DocumentBodyRequest(BaseModel):
    doc_id: str
    comment: Optional[str] = None
    # flowgate.default.0162 §1 — final-approval git ride-along (merge/push/wait).
    # Only honored on approve of a git-active group's AC document.
    git_action: Optional[str] = None


class RejectionReasonBodyRequest(BaseModel):
    doc_id: str
    reason: str


class OrphanRecoveryRequest(BaseModel):
    item_seq: Optional[int] = None


class RegisterResultBodyRequest(BaseModel):
    doc_id: str


# ── Group endpoints ───────────────────────────────────────────────────────────

@router.get("/groups")
def list_groups_endpoint(
    project_id: str = Query(...),
    module: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """List groups (project_id required).

    Permission: document.read
    """
    user_permissions = _get_user_permissions(current_user)
    if "document.read" not in user_permissions:
        raise HTTPException(status_code=403, detail="document.read permission required.")
    groups = db_groups.list_groups(project_id=project_id, module=module, status=status)
    return {"groups": groups, "total": len(groups)}


@router.post("/groups", status_code=201)
def create_group_endpoint(
    body: GroupCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a new group (D017 r1 Step 1 / T191).

    Automatically reserve group_id when omitted.
    Permission: project.group.manage
    """
    user_permissions = _get_user_permissions(current_user)
    if "project.group.manage" not in user_permissions:
        raise HTTPException(status_code=403, detail="project.group.manage permission required.")

    result = process_service.create_group(
        project_id=body.project_id,
        title=body.title,
        module=body.module,
        parent_id=body.parent_id,
        priority=body.priority,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return {"group_id": result["group_id"], "created_at": result["created_at"]}


@router.put("/groups/{group_id}")
def update_group_endpoint(
    group_id: str,
    body: GroupUpdateRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Update a group (title / priority / module) (T191).

    Changing parent_id is out of scope here.
    Permission: project.group.manage
    """
    user_permissions = _get_user_permissions(current_user)
    if "project.group.manage" not in user_permissions:
        raise HTTPException(status_code=403, detail="project.group.manage permission required.")

    # TR0079.0003 rework (3rd pass): a disposed (DC) group's title (group-name change) must
    # not be editable. _guard_group_not_disposed keys off a document; here we have the
    # group_id directly, so call the shared signal straight. Fails open for live groups.
    if process_service.is_group_disposed(group_id):
        raise HTTPException(
            status_code=409,
            detail=f"group_disposed: {group_id} has been disposed",
        )

    result = process_service.update_group(
        group_id=group_id,
        title=body.title,
        priority=body.priority,
        module=body.module,
    )
    if result.get("status") == "error":
        message = result.get("message", "")
        status_code = 404 if "not found" in message else 400
        raise HTTPException(status_code=status_code, detail=message)
    return {"group_id": result["group_id"], "updated_at": result["updated_at"]}


@router.post("/groups/{gid}/transitions/{action}")
def group_transition_endpoint(
    gid: str,
    action: str,
    body: GroupTransitionRequest = GroupTransitionRequest(),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Transition the group state.

    action: approve | close | start | clarify | resume
    Permission: varies by action (D017 r1 §4-1, §9-1).
    """
    user_permissions = _get_user_permissions(current_user)
    try:
        result = transition_group(
            group_id=gid,
            action=action,
            actor_user_id=current_user["user_id"],
            user_permissions=user_permissions,
            comment=body.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except WFPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"group": result, "warnings": result.pop("__warnings", [])}


@router.put("/groups/{group_id}/archive")
def archive_group_endpoint(
    group_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Soft-delete a group (D019).

    Set the deleted_at field to the current time. The DB row is not physically deleted.
    Permission: project.group.manage
    """
    from modules.flow_gate.db.connection import now_iso
    user_permissions = _get_user_permissions(current_user)
    if "project.group.manage" not in user_permissions:
        raise HTTPException(status_code=403, detail="project.group.manage permission required.")
    row = db_groups.get_by_id(group_id)
    if not row:
        raise HTTPException(status_code=404, detail="Group not found.")
    if row.get("deleted_at"):
        raise HTTPException(status_code=409, detail="Group already deleted.")
    updated = db_groups.update(group_id, {"deleted_at": now_iso()})
    return {"group_id": group_id, "deleted_at": updated["deleted_at"]}


@router.get("/groups/{gid}/timeline")
def group_timeline_endpoint(
    gid: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch the group event timeline.

    Permission: document.read
    """
    user_permissions = _get_user_permissions(current_user)
    if "document.read" not in user_permissions:
        raise HTTPException(status_code=403, detail="document.read permission required.")

    group = db_groups.get_by_id(gid)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group not found: {gid}")

    store = __import__(
        "modules.flow_gate.db.connection", fromlist=["get_store"]
    ).get_store()
    events = store._fetch_all(
        "SELECT * FROM workflow_events WHERE group_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        [gid, limit],
    )
    return {"group_id": gid, "events": events, "total": len(events)}


# ── Document transition endpoints ─────────────────────────────────────────────

@router.post("/documents/transitions/{action}")
def document_transition_rpc(
    action: str,
    body: DocumentBodyRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    return document_transition_endpoint(
        body.doc_id,
        action,
        DocumentTransitionRequest(comment=body.comment),
        current_user,
    )


@router.post("/documents/review_transitions/{action}")
async def document_review_transition_rpc(
    action: str,
    body: DocumentBodyRequest,
    current_user: dict = Depends(get_current_user),
):
    # flowgate.default.0162 §1 — an optional git_action rides along on the AC
    # final approval. The pre-check runs BEFORE the approval so a violation
    # rejects WITHOUT approving (L §2.1 step 1 / §4.2). The git finalize runs
    # AFTER the approval commits and NEVER turns a git failure into an approval
    # failure (D §3.1) — git failures surface as {git: {ok: false}} at HTTP 200.
    guarded_doc = db_docs.get_by_id(body.doc_id)
    if guarded_doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {body.doc_id}")
    _guard_group_not_disposed(guarded_doc, body.doc_id)
    _guard_group_not_ai_running(guarded_doc, body.doc_id)

    git_action = body.git_action
    group_id: Optional[str] = None
    if git_action is not None:
        if action != "approve":
            return JSONResponse(
                status_code=422,
                content={"ok": False, "error": {
                    "code": "invalid_request",
                    "message": "git_action is only accepted on an approve transition",
                }},
            )
        try:
            # 0275 T0007 (NR0003 원인 2): the git precheck/finalize run sync
            # subprocess + DB work — keep them off the event loop.
            group_id = await anyio.to_thread.run_sync(
                lambda: git_service.precheck_approve_git_action(
                    db_docs.get_by_id(body.doc_id), git_action
                )
            )
        except GitServiceError as exc:
            return JSONResponse(
                status_code=exc.status,
                content={"ok": False, "error": {"code": exc.code, "message": exc.message}},
            )

    response = await document_review_transition_endpoint(
        body.doc_id,
        action,
        DocumentTransitionRequest(comment=body.comment),
        current_user,
    )

    if git_action is not None and group_id:
        response["git"] = await anyio.to_thread.run_sync(
            lambda: git_service.run_approve_git_action(group_id, git_action)
        )
    return response


# 0275 T0007 (NR0003 원인 2): sync DB/git work only — plain `def` runs in the
# threadpool instead of blocking the event loop.
@router.post("/documents/workflow/finalize")
def finalize_workflow_endpoint(
    body: DocumentBodyRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Final approval (the AC step): move the group's R/B workflow root from
    wf_in_progress → wf_done.

    This is NOT a document review transition — AC is not a persisted document.
    The root document's workflow is finalized only after every workflow-step
    document in the group is approved (i.e. the computed head is AC).
    """
    user_permissions = _get_user_permissions(current_user)
    if "document.approve" not in user_permissions:
        raise HTTPException(status_code=403, detail="document.approve permission required")

    doc = db_docs.get_by_id(body.doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {body.doc_id}")
    project_id = doc.get("project_id")
    group_id = doc.get("group_id")
    if not project_id or not group_id:
        raise HTTPException(status_code=400, detail="Document has no project/group")

    _guard_group_not_disposed(doc, body.doc_id)
    _guard_group_not_ai_running(doc, body.doc_id)

    # Resolve the group's workflow-root document.
    if doc.get("type_code") in {"R", "B"}:
        root_doc = doc
    else:
        group_docs = db_docs.list_documents(
            project_id=project_id, group_id=group_id, limit=200
        )
        roots = [item for item in group_docs if item.get("type_code") in {"R", "B"}]
        roots.sort(key=lambda item: (item.get("seq") or 0, item.get("doc_id") or ""))
        root_doc = roots[0] if roots else None
    if not root_doc:
        raise HTTPException(status_code=404, detail="Workflow root document not found for group")

    # Guard: every workflow-step doc must be approved (head must be AC).
    group_docs = db_docs.list_documents(project_id=project_id, group_id=group_id, limit=100)
    NON_HEAD = {"R", "B", "Q"}
    APPROVED = {"approved", "wf_done"}
    pending = [
        c for c in group_docs
        if c.get("type_code") not in NON_HEAD
        and c.get("doc_review_status") not in APPROVED
    ]
    if pending:
        raise HTTPException(status_code=409, detail="Not all workflow steps are approved")

    if root_doc.get("doc_review_status") == "wf_done":
        return {"document": root_doc}  # idempotent

    updated = db_docs.update(root_doc["doc_id"], {"doc_review_status": "wf_done"})
    # 0177 NR0016 §3: same eager realization as the AC-approve cascade — emit the
    # git_pending_changed SSE at final-approval time. Never raises.
    git_service.realize_wf_done_transition(group_id)
    return {"document": updated or root_doc}


@router.patch("/documents/rejection_reason")
def update_rejection_reason_rpc(
    body: RejectionReasonBodyRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    return update_rejection_reason_endpoint(
        body.doc_id,
        RejectionReasonRequest(reason=body.reason),
        current_user,
    )


@router.post("/documents/register_result", status_code=201)
async def register_document_result_rpc(
    body: RegisterResultBodyRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    return await register_document_result_endpoint(
        body.doc_id,
        DocumentTransitionRequest(),
        current_user,
    )


@router.get("/documents/prompt")
def document_prompt_rpc(
    doc_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    return document_prompt_endpoint(doc_id, current_user)


@router.post("/documents/{doc_id}/transitions/{action}")
def document_transition_endpoint(
    doc_id: str,
    action: str,
    body: DocumentTransitionRequest = DocumentTransitionRequest(),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Transition the document state (CAS pattern).

    action: submit | approve | reject | cancel | resubmit | redraft | close
    Permission: varies by action (D017 r1 §7-3, §9-1).
    """
    user_permissions = _get_user_permissions(current_user)
    _guard_group_not_disposed(db_docs.get_by_id(doc_id), doc_id)
    try:
        result = transition_document(
            doc_id=doc_id,
            action=action,
            actor_user_id=current_user["user_id"],
            user_permissions=user_permissions,
            comment=body.comment,
        )
    except ValueError as exc:
        # Missing document or missing comment
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail)
    except WFPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"document": result}


@router.post("/documents/{doc_id}/workflow/recover")
def recover_orphaned_workflow_document_endpoint(
    doc_id: str,
    body: OrphanRecoveryRequest = OrphanRecoveryRequest(),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Attach an orphaned document to a compatible empty workflow slot.

    item_seq defaults to the group's effective head. Reattachment changes
    workflow progression, so it deliberately reuses document.approve.
    """
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

    _guard_group_not_disposed(doc, doc_id)
    _guard_group_not_ai_running(doc, doc_id)
    if "document.approve" not in _get_user_permissions(current_user):
        raise HTTPException(
            status_code=403,
            detail="document.approve permission required to recover an orphaned document.",
        )

    type_code = str(doc.get("type_code") or "").upper()
    if type_code in NON_SLOT_WORKFLOW_TYPES:
        raise HTTPException(
            status_code=409,
            detail=f"Document type {type_code} is not a recoverable workflow slot type.",
        )

    if not db_wfseq.is_orphaned_workflow_member(doc_id):
        raise HTTPException(
            status_code=409,
            detail=f"Document {doc_id} is not an orphaned workflow member.",
        )

    group_id = doc.get("group_id")
    project_id = doc.get("project_id")
    if not group_id or not project_id:
        raise HTTPException(
            status_code=409,
            detail="The orphaned document has no group or project and cannot be recovered.",
        )

    target = None
    if body.item_seq is None:
        target = db_wfseq.get_pending_head_by_group(group_id, project_id)
    else:
        roots = [
            row for row in (db_docs.get_documents_by_group_id(group_id) or [])
            if str(row.get("type_code") or "").upper() in {"R", "B"}
        ]
        sequence = next(
            (
                seq for root in roots
                if root.get("doc_id")
                for seq in [db_wfseq.get_sequence_by_doc_id(root["doc_id"])]
                if seq is not None
            ),
            None,
        )
        if sequence is not None:
            target = next(
                (
                    item for item in (db_wfseq.get_sequence_items(sequence["id"]) or [])
                    if item.get("item_seq") == body.item_seq
                ),
                None,
            )

    if target is None:
        requested = "the current workflow head" if body.item_seq is None else f"item_seq={body.item_seq}"
        raise HTTPException(
            status_code=409,
            detail=f"Cannot recover {doc_id}: {requested} does not identify an available slot.",
        )
    if target.get("result_doc_id"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot recover {doc_id}: workflow slot item_seq={target.get('item_seq')} "
                f"is already filled by {target.get('result_doc_id')}."
            ),
        )

    expected_type = str(target.get("type") or "").upper()
    actual_type = str(doc.get("type_code") or "").upper()
    if expected_type != actual_type:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot recover {doc_id}: workflow slot item_seq={target.get('item_seq')} "
                f"expects type {expected_type}, but the document type is {actual_type}."
            ),
        )

    file_path = doc.get("file_path")
    if not file_path:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot recover {doc_id}: the document has no registered file path.",
        )

    registered = register_workflow_result(
        item_id=target["id"],
        registered_path=storage_paths.to_storage_relative(file_path, project_id),
        registered_doc_id=doc_id,
        registered_at=now_iso(),
        actor_user_id=current_user["user_id"],
    )
    return {
        "document": registered,
        "item_seq": target.get("item_seq"),
        "recovered": True,
    }


@router.post("/documents/{doc_id}/review_transitions/{action}")
async def document_review_transition_endpoint(
    doc_id: str,
    action: str,
    body: DocumentTransitionRequest = DocumentTransitionRequest(),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Transition the document review state (doc_review_status column).

    action: approve | reject | mark_revised
    Permission: approve → document.approve, reject → document.reject, mark_revised → document.update
    M026 §8-1.
    """
    # 0275 T0007 (NR0003 원인 2): the transition + AC cascade are sync DB/git
    # work — run them in the threadpool so a slow transition cannot stall the
    # event loop. Only the SSE broadcast below needs the loop; HTTPExceptions
    # raised inside the closure propagate unchanged through run_sync.
    def _transition_sync():
        user_permissions = _get_user_permissions(current_user)

        # Capture the pre-transition state for SSE emission
        prev_doc = db_docs.get_by_id(doc_id)
        prev_review_status = prev_doc.get("doc_review_status") if prev_doc else None

        _guard_group_not_disposed(prev_doc, doc_id)
        _guard_group_not_ai_running(prev_doc, doc_id)

        try:
            result = transition_document_review(
                doc_id=doc_id,
                action=action,
                actor_user_id=current_user["user_id"],
                user_permissions=user_permissions,
                comment=body.comment,
            )
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if "not found" in detail else 400
            raise HTTPException(status_code=status_code, detail=detail)
        except WFPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except TransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # AC (final approval) approval finalizes the group's R/B workflow (wf_done).
        if action == "approve" and (result or {}).get("type_code") == "AC":
            try:
                project_id = result.get("project_id")
                group_id = result.get("group_id")
                if project_id and group_id:
                    group_docs = db_docs.list_documents(
                        project_id=project_id, group_id=group_id, limit=200
                    )
                    roots = [item for item in group_docs if item.get("type_code") in {"R", "B"}]
                    roots.sort(key=lambda item: (item.get("seq") or 0, item.get("doc_id") or ""))
                    if roots:
                        db_docs.update(roots[0]["doc_id"], {"doc_review_status": "wf_done"})
                        # 0177 NR0016 §3: realize the git none→awaiting_choice transition
                        # NOW so the header badge SSE (git_pending_changed) fires at
                        # approval time instead of on the next status query. Never raises.
                        git_service.realize_wf_done_transition(group_id)
            except Exception:
                pass

        return prev_review_status, result

    prev_review_status, result = await anyio.to_thread.run_sync(_transition_sync)

    # SSE broadcast (M026 §8-1 Phase 5-C)
    try:
        from modules.flow_gate.api.v1.events.publisher import broadcast_event, FlowEvent
        from modules.flow_gate.api.v1.events.event_types import EventType
        await broadcast_event(FlowEvent(
            event_type=EventType.DOC_REVIEW_STATUS_CHANGED,
            payload={
                "doc_id": doc_id,
                "prev_status": prev_review_status,
                "next_status": result.get("doc_review_status"),
                "rejection_reason": body.comment if action == "reject" else None,
                "rejection_history": _parse_rejection_history(result.get("rejection_history")),
            },
            audience="*",
            doc_id=doc_id,
            project=result.get("project_id"),
        ))
    except Exception:
        pass  # SSE emission failure does not affect the transition result

    return {"document": result}


@router.patch("/documents/{doc_id}/rejection_reason")
def update_rejection_reason_endpoint(
    doc_id: str,
    body: RejectionReasonRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Store only the rejection reason (no transition).

    M026 §6 — Message Save button: save immediately, without closing the dialog.
    Update only the rejection_reason column without a doc_review_status transition.
    Permission: document.reject
    """
    user_permissions = _get_user_permissions(current_user)
    if "document.reject" not in user_permissions:
        raise HTTPException(status_code=403, detail="document.reject permission required.")

    doc = db_docs.get_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

    # TR0079.0003 rework (6th pass): rejection_reason + rejection_history are document
    # writes. A disposed (DC) group must not accept new rejection records — same class
    # as the other write surfaces. _guard_group_not_disposed fails open for live groups.
    _guard_group_not_disposed(doc, doc_id)
    _guard_group_not_ai_running(doc, doc_id)

    from modules.flow_gate.db.connection import now_iso as _now_iso
    existing_history = _parse_rejection_history(doc.get("rejection_history"))
    # P0005/T0006: this path also appends a history item, so it carries the same
    # rejection_id + nullable response fields as the reject-transition path.
    existing_history.append({
        "rejection_id": new_rejection_id(),
        "reason": body.reason.strip(),
        "rejected_at": _now_iso(),
        "rejected_by": current_user["user_id"],
        "ai_response": None,
        "responded_at": None,
        "response_recorded_by": None,
        "response_revision_no": None,
    })
    updated = db_docs.update(doc_id, {
        "rejection_reason": body.reason.strip(),
        "rejection_history": json.dumps(existing_history, ensure_ascii=False),
        "updated_at": _now_iso(),
    })
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to save rejection reason")
    return {"document": updated}


@router.post("/documents/{doc_id}/register_result", status_code=201)
async def register_document_result_endpoint(
    doc_id: str,
    body: DocumentTransitionRequest = DocumentTransitionRequest(),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Register a workflow result + automatically transition a rejected document to 'revised' (M026 §8-1 Phase 5-E).

    Re-registering a result for a document in rejected state automatically
    transitions doc_review_status to 'revised'.
    """
    from modules.flow_gate.db.connection import now_iso as _now_iso
    from modules.flow_gate.db import workflow_sequences as _db_wseq

    # 0275 T0007 (NR0003 원인 2): permission check, result registration and the
    # auto submit transition are sync DB work — run them in the threadpool so
    # they cannot stall the event loop. Only the SSE broadcast below needs the
    # loop; HTTPExceptions raised inside the closure propagate through run_sync.
    def _register_sync():
        user_permissions = _get_user_permissions(current_user)
        if "document.update" not in user_permissions and "own.draft" not in user_permissions:
            raise HTTPException(status_code=403, detail="document.update or own.draft permission required.")

        doc = db_docs.get_by_id(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

        # TR0079.0003 rework (6th pass): register_result mutates the document
        # (register_workflow_result + rejected->revised transition). A disposed (DC)
        # group must not accept result re-registration that flips review_status. This
        # endpoint lives in workflow.py, so documents.py's _reject_if_group_disposed
        # never covered it — the exact gap the review flagged. Fails open for live groups.
        _guard_group_not_disposed(doc, doc_id)
        _guard_group_not_ai_running(doc, doc_id)

        # Look up the workflow sequence and head item (item_id required)
        seq = _db_wseq.get_sequence_by_doc_id(doc_id)
        if not seq:
            raise HTTPException(status_code=404, detail="Workflow sequence not found.")
        head_item = _db_wseq.get_effective_head(seq["id"])
        if not head_item:
            raise HTTPException(status_code=404, detail="Workflow head item not found.")

        prev_review_status = doc.get("doc_review_status")
        result = register_workflow_result(
            item_id=head_item["id"],
            registered_path=doc.get("file_path") or "",
            registered_doc_id=doc_id,
            registered_at=_now_iso(),
            actor_user_id=current_user["user_id"],
        )

        # DB004 §6.1: route doc_review_status transitions through
        # transition_document_review() (single writer)
        next_review_status = prev_review_status
        try:
            _updated = transition_document_review(
                doc_id=doc_id,
                action="submit",
                actor_user_id=current_user["user_id"],
                user_permissions=user_permissions,
            )
            if _updated:
                next_review_status = _updated.get("doc_review_status", prev_review_status)
        except TransitionError:
            # submit transition not needed in the current state
            # (pending_review/revised, etc.) — normal path
            pass
        except Exception:
            pass

        return doc, prev_review_status, next_review_status, result

    doc, prev_review_status, next_review_status, result = await anyio.to_thread.run_sync(_register_sync)

    # Broadcast SSE when an automatic transition occurs
    if next_review_status != prev_review_status:
        try:
            from modules.flow_gate.api.v1.events.publisher import broadcast_event, FlowEvent
            from modules.flow_gate.api.v1.events.event_types import EventType
            await broadcast_event(FlowEvent(
                event_type=EventType.DOC_REVIEW_STATUS_CHANGED,
                payload={
                    "doc_id": doc_id,
                    "prev_status": prev_review_status,
                    "next_status": next_review_status,
                    "rejection_reason": None,
                },
                audience="*",
                doc_id=doc_id,
                project=doc.get("project_id"),
            ))
        except Exception:
            pass

    return {"document": result or doc}


@router.get("/documents/{doc_id}/prompt")
def document_prompt_endpoint(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Generate the next-action prompt (D017 r1 §5, Hook §10).

    Permission: document.read
    Record the prompt_copied event (PM decision No.5).
    """
    user_permissions = _get_user_permissions(current_user)
    if "document.read" not in user_permissions:
        raise HTTPException(status_code=403, detail="document.read permission required.")

    try:
        result = build_prompt(
            doc_id=doc_id,
            actor_user_id=current_user["user_id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result

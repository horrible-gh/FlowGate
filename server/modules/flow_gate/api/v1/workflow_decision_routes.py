"""Workflow decision + advance-to-next-step API (T301 — R016 T-C).

Endpoints:
  POST /api/v1/workflow/{doc_id}/decide   Save workflow decision (initial, once only)
  POST /api/v1/workflow/{doc_id}/advance  Advance to next step (numbering + token + comment)

Auth: Authorization: Bearer <token>  (auth_outbound.verify_bearer)

T-C scope:
  - decide: Save sequence (initial, once only). Includes auto-mapping rule validation.
  - advance: Head numbering + token issuance + comment generation. head → in_progress.
  - No auto_advance flag (R016 correction).
  - doc_class (R/Q/B) input + response.
  - Head advancement occurs at PM AC API call (outside this API scope).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.services.auth_outbound import verify_bearer

_log = logging.getLogger(__name__)
from modules.flow_gate.services.workflow_decision_service import (
    SequenceChanged,
    decide_workflow,
    advance_workflow,
    request_review,
    request_workflow_decision,
    request_sequence_edit,
    get_workflow_sequence,
    edit_workflow_pending,
    continuation_kickoff_after_decide,
    normalize_continuation_instruction_mode,
    normalize_continuation_auto_approve_item_seqs,
    validate_continuation_auto_approve_item_seqs,
)
from modules.flow_gate.db import documents as _db_documents
from modules.flow_gate import process_service as _process_service
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.workflow.pipeline_service import transition_document as _transition_document
from config import settings

router = APIRouter(prefix="/api/v1", tags=["WorkflowDecision"])


def _disposed_group_response(doc_id: str, doc: Optional[dict]):
    """Return a 409 group_disposed JSONResponse when the doc's group is disposed.

    TR0079.0003 rework: workflow decide/advance and review-request are forward
    actions exposed by the action bar; a disposed (DC) group must reject them at
    the server even if a stale client still renders the buttons. Returns None for
    live groups. Shares process_service.is_group_disposed with the other guards.
    """
    group_id = (doc or {}).get("group_id")
    if group_id and _process_service.is_group_disposed(group_id):
        return JSONResponse(
            status_code=409,
            content={"error": "group_disposed", "doc_id": doc_id},
        )
    return None


def _active_ai_run_response_for_user(doc: Optional[dict], auth: dict):
    """Return the central policy response for human mutations; owner workers pass."""
    from modules.flow_gate.services.mutation_policy import (
        MutationPolicyError,
        assert_group_mutation_allowed,
        human_principal,
        mutation_error_response,
        worker_principal,
    )

    principal = human_principal(auth) if auth.get("_is_user_jwt") else worker_principal(auth)
    try:
        assert_group_mutation_allowed(
            (doc or {}).get("group_id"), principal, "workflow decision mutation"
        )
    except MutationPolicyError as exc:
        return mutation_error_response(exc)
    return None

# ── Request schemas ───────────────────────────────────────────────────────────

class SequenceItem(BaseModel):
    id: int
    type: str
    label: str


class DecideRequest(BaseModel):
    doc_class: str
    sequence: list[SequenceItem]
    # 0391 T0005 §5-6: same escape hatch name as the inbox/chat paths — a corrupted
    # step label is rejected unless a real reason (>=10 non-whitespace chars) is given.
    force_encoding_reason: Optional[str] = None


class AdvanceRequest(BaseModel):
    """T358: advance request body (optional). Existing behavior preserved if ref_doc_ids is omitted.

    Continuous work (group 0086 / 0051 R0001 / NR0003 B안): when ``continuous`` is set the
    minted token carries the unmanned-chain stop point (``continuation_target_seq`` = target
    item_seq) + the AI-review-mode flag, and the mention swaps its Q-guard for the
    delegation/unmanned/no-stop/autonomous block. Omitted ⇒ an ordinary advance (no chain).
    """
    ref_doc_ids: Optional[List[str]] = None
    continuous: bool = False
    continuation_target_seq: Optional[int] = None
    continuation_review_mode: bool = False
    continuation_instruction_mode: Optional[str] = None
    # 0352 T0004 §2/§3.4: the ai_direct chain's per-item_seq N/T auto-approve selection.
    # Ignored (never even normalized) unless continuous + ai_direct — see post_workflow_advance.
    continuation_auto_approve_item_seqs: Optional[List[int]] = None


class DecideBodyRequest(BaseModel):
    doc_id: str
    doc_class: str
    sequence: list[SequenceItem]
    force_encoding_reason: Optional[str] = None  # 0391 T0005 §5-6


class AdvanceBodyRequest(BaseModel):
    doc_id: str
    ref_doc_ids: Optional[List[str]] = None
    continuous: bool = False
    continuation_target_seq: Optional[int] = None
    continuation_review_mode: bool = False
    continuation_instruction_mode: Optional[str] = None
    continuation_auto_approve_item_seqs: Optional[List[int]] = None


class ReviewRequestBody(BaseModel):
    doc_id: str
    ref_doc_ids: Optional[List[str]] = None


class WorkflowDecisionRequestBody(BaseModel):
    """Issue an AI worker token + prompt to decide an R workflow.

    Continuous work (group 0086 R0001): when ``continuous`` is set, the continuous run is
    started before the workflow is decided ("워크플로 결정부터"). The minted workflow_decide
    token carries the run-to-end sentinel + review-mode flag so the server self-chains the
    rest of the run once the decision is saved. ``continuation_review_mode`` pauses the
    chain after the first produced step for human Q&A.
    """
    doc_id: str
    continuous: bool = False
    continuation_review_mode: bool = False
    continuation_instruction_mode: Optional[str] = None


class EditSequenceItem(BaseModel):
    type: str
    label: str
    # 0399 P0013 ② / DB0012 §2: the step note and where the row came from. All three default
    # to "no plan poured this row", which is what every caller that predates 0399 means.
    note: Optional[str] = None
    source_doc_id: Optional[str] = None
    source_revision_no: Optional[int] = None


class EditSequenceBodyRequest(BaseModel):
    doc_id: str
    items: list[EditSequenceItem]
    force_encoding_reason: Optional[str] = None  # 0391 T0005 §5-6
    # 0399 P0013 ②: the fingerprint handed out by /work-plan/sequence-candidates. Absent on
    # an ordinary [시퀀스 수정] save, and absent means "do not compare" — the check exists for
    # the pour path, whose rows were computed against a sequence read some time ago.
    expected_workflow_tag: Optional[str] = None


class SequenceEditRequestBody(BaseModel):
    """Issue an AI worker token + prompt to EDIT a decided workflow sequence (R0001 0208)."""
    doc_id: str


@router.post("/workflow/decide")
def post_workflow_decide_rpc(body: DecideBodyRequest, request: Request):
    return post_workflow_decide(
        body.doc_id,
        DecideRequest(
            doc_class=body.doc_class,
            sequence=body.sequence,
            force_encoding_reason=body.force_encoding_reason,
        ),
        request,
    )


@router.post("/workflow/advance")
def post_workflow_advance_rpc(body: AdvanceBodyRequest, request: Request):
    return post_workflow_advance(
        body.doc_id,
        request,
        AdvanceRequest(
            ref_doc_ids=body.ref_doc_ids,
            continuous=body.continuous,
            continuation_target_seq=body.continuation_target_seq,
            continuation_review_mode=body.continuation_review_mode,
            continuation_instruction_mode=body.continuation_instruction_mode,
            continuation_auto_approve_item_seqs=body.continuation_auto_approve_item_seqs,
        ),
    )


# ── POST /workflow/{doc_id}/decide ───────────────────────────────────────────

@router.post("/workflow/{doc_id}/decide")
def post_workflow_decide(doc_id: str, body: DecideRequest, request: Request):
    """Save workflow decision (initial, once only).

    Ref. P002 §2.
    - Empty sequence: 400 invalid_sequence
    - Document not found: 404 doc_not_found
    - Sequence already decided: 409 already_decided
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    is_worker_token = auth.get("action_scope") is not None
    if is_worker_token:
        if (
            auth.get("action_scope") != "workflow_decide"
            or auth.get("doc_ref") != doc_id
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "workflow_decision_token_mismatch",
                    "doc_id": doc_id,
                },
            )

    if not body.sequence:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_sequence", "fields": ["sequence"]},
        )

    # Capture the pre-decision review status so the SSE event can report the transition.
    _pre_doc = _db_documents.get_by_id(doc_id)
    _prev_review_status = _pre_doc.get("doc_review_status") if _pre_doc else None

    _disposed = _disposed_group_response(doc_id, _pre_doc)
    if _disposed is not None:
        return _disposed

    try:
        result = decide_workflow(
            doc_id=doc_id,
            doc_class=body.doc_class,
            sequence=[item.model_dump() for item in body.sequence],
            force_encoding_reason=body.force_encoding_reason,
        )
    except LookupError as exc:
        code, _, val = str(exc).partition(":")
        if code == "doc_not_found":
            return JSONResponse(
                status_code=404,
                content={"error": "doc_not_found", "doc_id": val},
            )
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("already_decided:"):
            doc_id_val = msg.split(":", 1)[1]
            return JSONResponse(
                status_code=409,
                content={"error": "already_decided", "doc_id": doc_id_val},
            )
        # 0391 T0005 §5-5/§5-6: a corrupted step label is now REJECTED (was: silently
        # swapped for the type name). The service raises the full explanation — why it
        # was refused and how to get it through — so pass it straight to the sender.
        if msg.startswith("corrupted_label:"):
            return JSONResponse(
                status_code=422,
                content={"error": "corrupted_label", "detail": msg.split(":", 1)[1]},
            )
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_sequence", "detail": msg},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )

    # draft → open transition: auto-transition head document (R) status on workflow decision
    _doc = _db_documents.get_by_id(doc_id)
    if _doc is not None and _doc.get("status") == "draft":
        try:
            _user_id: str = auth["issued_to"]
            _perms: set = (
                {"document.update", "document.approve", "document.reject",
                 "document.delete", "own.draft"}
                if auth.get("is_admin")
                else {"document.update"}
            )
            _transition_document(
                doc_id=doc_id,
                action="workflow_decide",
                actor_user_id=_user_id,
                user_permissions=_perms,
            )
        except Exception as _e:
            # decide already succeeded; log the transition failure for diagnostics
            _log.warning("draft→open transition failed for %s: %s", doc_id, _e, exc_info=True)

    # SSE broadcast: the decision flips doc_review_status (→ wf_in_progress) and the
    # group head. Without this, already-open clients (and explorer/group views) stay
    # on the "undecided" placeholder until a manual reload. Best-effort; never fatal.
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            broadcast_event_threadsafe,
            FlowEvent,
        )
        from modules.flow_gate.api.v1.events.event_types import EventType

        _next_status = _doc.get("doc_review_status") if _doc else None
        _project = _doc.get("project_id") if _doc else None
        _group_id = _doc.get("group_id") if _doc else None
        review_status_delivered = broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.DOC_REVIEW_STATUS_CHANGED,
            payload={
                "doc_id": doc_id,
                "prev_status": _prev_review_status,
                "next_status": _next_status,
                "rejection_reason": None,
                # Actor tagging (R0001/NR0003): identify the human decider so the
                # SSE self-echo can suppress the redundant "info" toast on the
                # decider's own connections. Manual decision → user JWT subject;
                # AI/worker decision → None (worker token), so the requester still
                # receives the info toast unchanged.
                "actor_user_id": None if is_worker_token else auth["issued_to"],
            },
            audience="*",
            doc_id=doc_id,
            project=_project,
            group_id=_group_id,
        ))
        if _group_id:
            group_refresh_delivered = broadcast_event_threadsafe(FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": _group_id, "reason": "workflow_decided"},
                audience="*",
                doc_id=doc_id,
                project=_project,
                group_id=_group_id,
            ))
            if review_status_delivered == 0 and group_refresh_delivered == 0:
                _log.info(
                    "workflow decide SSE had no active subscribers for %s",
                    doc_id,
                )
    except Exception as _sse_exc:
        _log.warning("workflow decide SSE broadcast failed for %s: %s", doc_id, _sse_exc)

    if is_worker_token:
        from modules.flow_gate.services import token_service
        token_service.consume(
            token_id=auth["token_id"],
            project_id=auth["project"],
            doc_id=doc_id,
        )

        # Continuous work (group 0086 R0001): if this workflow_decide token started an
        # unmanned chain BEFORE the workflow was decided ("워크플로 결정부터"), the token
        # carries continuation metadata. Now that the sequence exists, kick off the first
        # real step and enclose its token/mention in the decide response so the worker
        # proceeds — from here the inbox self-chain carries the rest of the run. Consume
        # first so the readvance guard sees no stale token. Never fatal: a chain failure
        # only pauses the continuation (mirrors inbox_routes self-chain).
        if auth.get("continuation_target_seq") is not None:
            try:
                chain = continuation_kickoff_after_decide(
                    doc_id=doc_id,
                    issued_to=auth["issued_to"],
                    api_base_url=_build_api_base(request),
                    locale=request.headers.get("x-locale") or "ko",
                    continuation_target_seq=auth.get("continuation_target_seq"),
                    continuation_review_mode=bool(auth.get("continuation_review_mode")),
                    continuation_instruction_mode=auth.get("continuation_instruction_mode"),
                    ai_run_id=auth.get("ai_run_id"),
                    # 0352 T0004 §3.3: always empty in practice today (request_workflow_decision
                    # never accepts a selection — see that function's docstring), threaded for
                    # contract symmetry with advance_workflow.
                    continuation_auto_approve_item_seqs=auth.get(
                        "continuation_auto_approve_item_seqs"
                    ),
                )
                if chain:
                    result = {**result, **chain}
            except Exception as _chain_exc:  # pragma: no cover - defensive
                _log.warning(
                    "continuous decide self-chain failed for %s: %s",
                    doc_id, _chain_exc, exc_info=True,
                )

    return JSONResponse(status_code=201, content=result)


# ── POST /workflow/{doc_id}/advance──────────────────────────────────────────

@router.post("/workflow/{doc_id}/advance")
def post_workflow_advance(
    doc_id: str,
    request: Request,
    body: Optional[AdvanceRequest] = Body(default=None),
):
    """Advance to next step (numbering + token issuance + comment generation).

    Ref. P002 §3.
    - Document not found: 404 doc_not_found
    - Sequence not decided: 400 sequence_not_decided
    - Sequence fully exhausted: 409 sequence_exhausted
    - Head already in progress: 409 head_in_progress

    T358: Optionally pass an array of reference document canonical IDs in body.ref_doc_ids.
    Head advancement (DONE) only occurs at PM AC API call (no auto mode, R016 correction).
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    issued_to: str = auth["issued_to"]
    api_base_url = _build_api_base(request)
    ref_doc_ids = body.ref_doc_ids if body else None
    # Continuous work (group 0086 R0001): forward the unmanned-chain stop point + review
    # flag so advance_workflow mints a continuation token + continuous mention.
    continuous = bool(body.continuous) if body else False
    continuation_target_seq = body.continuation_target_seq if body else None
    continuation_review_mode = bool(body.continuation_review_mode) if body else False
    continuation_instruction_mode = normalize_continuation_instruction_mode(
        body.continuation_instruction_mode if body else None
    )

    _disposed = _disposed_group_response(doc_id, _db_documents.get_by_id(doc_id))
    if _disposed is not None:
        return _disposed

    # 0352 T0004 §2/§3.4: this is a FRESH client request naming the selection, so the full
    # 422 validation (including "already done") runs here — not inside advance_workflow,
    # which reuses the same set on every later hop of an ongoing chain where an earlier
    # selection legitimately reads back 'done' once the server has completed it.
    try:
        continuation_auto_approve_item_seqs = normalize_continuation_auto_approve_item_seqs(
            body.continuation_auto_approve_item_seqs if body else None
        )
        if continuous:
            validate_continuation_auto_approve_item_seqs(
                continuation_auto_approve_item_seqs, doc_id, continuation_target_seq,
            )
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_auto_approve_item_seq", "detail": str(exc)},
        )

    try:
        result = advance_workflow(
            doc_id=doc_id,
            issued_to=issued_to,
            api_base_url=api_base_url,
            ref_doc_ids=ref_doc_ids,
            locale=request.headers.get("x-locale") or "ko",
            continuous=continuous,
            continuation_target_seq=continuation_target_seq,
            continuation_review_mode=continuation_review_mode,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
    except LookupError as exc:
        code, _, val = str(exc).partition(":")
        if code == "doc_not_found":
            return JSONResponse(
                status_code=404,
                content={"error": "doc_not_found", "doc_id": val},
            )
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("sequence_not_decided:"):
            doc_id_val = msg.split(":", 1)[1]
            return JSONResponse(
                status_code=400,
                content={"error": "sequence_not_decided", "doc_id": doc_id_val},
            )
        if msg.startswith("sequence_exhausted:"):
            doc_id_val = msg.split(":", 1)[1]
            return JSONResponse(
                status_code=409,
                content={"error": "sequence_exhausted", "doc_id": doc_id_val},
            )
        if msg.startswith("head_in_progress:"):
            parts = msg.split(":", 2)
            return JSONResponse(
                status_code=409,
                content={
                    "error": "head_in_progress",
                    "head_type": parts[1] if len(parts) > 1 else None,
                    "head_label": parts[2] if len(parts) > 2 else None,
                },
            )
        if msg.startswith("group_not_found:"):
            doc_id_val = msg.split(":", 1)[1]
            return JSONResponse(
                status_code=404,
                content={"error": "group_not_found", "doc_id": doc_id_val},
            )
        return JSONResponse(
            status_code=400,
            content={"error": msg},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )

    return JSONResponse(status_code=201, content=result)


# ── POST /documents/review-request ────────────────────────────────────────────

@router.post("/documents/review-request")
def post_review_request(body: ReviewRequestBody, request: Request):
    """Issue a review-request token + mention for an existing document.

    Distinct from /workflow/advance: this does NOT advance the sequence or create a
    document. It hands a worker the context + token to REVIEW body.doc_id and submit a
    verdict via inbox action:review.
    - Document not found: 404 doc_not_found
    - Group missing:      404 group_not_found
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    issued_to: str = auth["issued_to"]
    api_base_url = _build_api_base(request)

    _disposed = _disposed_group_response(body.doc_id, _db_documents.get_by_id(body.doc_id))
    if _disposed is not None:
        return _disposed

    try:
        result = request_review(
            doc_id=body.doc_id,
            issued_to=issued_to,
            api_base_url=api_base_url,
            ref_doc_ids=body.ref_doc_ids,
            locale=request.headers.get("x-locale") or "ko",
        )
    except LookupError as exc:
        code, _, val = str(exc).partition(":")
        if code == "doc_not_found":
            return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": val})
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("group_not_found:"):
            return JSONResponse(
                status_code=404,
                content={"error": "group_not_found", "doc_id": msg.split(":", 1)[1]},
            )
        return JSONResponse(status_code=400, content={"error": msg})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})

    return JSONResponse(status_code=201, content=result)


@router.post("/workflow/decision-request")
def post_workflow_decision_request(body: WorkflowDecisionRequestBody, request: Request):
    """Issue an AI worker token and prompt dedicated to deciding an R workflow."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not auth.get("_is_user_jwt"):
        return JSONResponse(
            status_code=403,
            content={"error": "user_session_required"},
        )

    target_doc = _db_documents.get_by_id(body.doc_id)
    if target_doc is None:
        return JSONResponse(
            status_code=404,
            content={"error": "doc_not_found", "doc_id": body.doc_id},
        )
    project_id = target_doc.get("project_id") or ""
    can_read = has_permission(auth["issued_to"], project_id, "perm_document_read")
    can_update = has_permission(auth["issued_to"], project_id, "perm_document_update")
    if not can_read or not can_update:
        return JSONResponse(
            status_code=403,
            content={"error": "workflow_decision_permission_denied"},
        )

    try:
        result = request_workflow_decision(
            doc_id=body.doc_id,
            issued_to=auth["issued_to"],
            api_base_url=_build_api_base(request),
            locale=request.headers.get("x-locale") or "ko",
            continuous=bool(body.continuous),
            continuation_review_mode=bool(body.continuation_review_mode),
            continuation_instruction_mode=body.continuation_instruction_mode,
        )
    except LookupError as exc:
        _code, _, value = str(exc).partition(":")
        return JSONResponse(
            status_code=404,
            content={"error": "doc_not_found", "doc_id": value},
        )
    except ValueError as exc:
        code, _, value = str(exc).partition(":")
        status = 409 if code == "already_decided" else 400
        return JSONResponse(
            status_code=status,
            content={"error": code, "doc_id": value},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )

    return JSONResponse(status_code=201, content=result)


@router.post("/workflow/sequence-edit-request")
def post_workflow_sequence_edit_request(body: SequenceEditRequestBody, request: Request):
    """Issue an AI worker token + prompt to EDIT a decided workflow's pending sequence.

    R0001 group 0208: the post-decision "시퀀스 수정" counterpart of
    /workflow/decision-request. Requires a user session (a human mints the token/mention and
    hands it to AI, exactly like the initial-decision path); the worker then applies the edit
    autonomously via PATCH /workflow/sequence. The workflow must already be decided.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not auth.get("_is_user_jwt"):
        return JSONResponse(
            status_code=403,
            content={"error": "user_session_required"},
        )

    target_doc = _db_documents.get_by_id(body.doc_id)
    if target_doc is None:
        return JSONResponse(
            status_code=404,
            content={"error": "doc_not_found", "doc_id": body.doc_id},
        )
    project_id = target_doc.get("project_id") or ""
    can_read = has_permission(auth["issued_to"], project_id, "perm_document_read")
    can_update = has_permission(auth["issued_to"], project_id, "perm_document_update")
    if not can_read or not can_update:
        return JSONResponse(
            status_code=403,
            content={"error": "workflow_decision_permission_denied"},
        )

    _disposed = _disposed_group_response(body.doc_id, target_doc)
    if _disposed is not None:
        return _disposed

    try:
        result = request_sequence_edit(
            doc_id=body.doc_id,
            issued_to=auth["issued_to"],
            api_base_url=_build_api_base(request),
            locale=request.headers.get("x-locale") or "ko",
        )
    except LookupError as exc:
        _code, _, value = str(exc).partition(":")
        return JSONResponse(
            status_code=404,
            content={"error": "doc_not_found", "doc_id": value},
        )
    except ValueError as exc:
        code, _, value = str(exc).partition(":")
        status = 404 if code == "group_not_found" else 400
        return JSONResponse(
            status_code=status,
            content={"error": code, "doc_id": value},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )

    return JSONResponse(status_code=201, content=result)


# ── GET /workflow/sequence ────────────────────────────────────────────────────

@router.get("/workflow/sequence")
def get_workflow_sequence_endpoint(
    doc_id: str = Query(..., description="Target document canonical ID"),
    request: Request = None,
):
    """Retrieve workflow sequence + item statuses (for entering edit mode).

    Response: { doc_id, sequence_id, items: [{ id, type, label, sort_order, status }] }
    - status: pending | in_progress | done
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        result = get_workflow_sequence(doc_id)
    except LookupError as exc:
        code, _, val = str(exc).partition(":")
        if code == "doc_not_found":
            return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": val})
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("sequence_not_decided:"):
            doc_id_val = msg.split(":", 1)[1]
            return JSONResponse(
                status_code=400,
                content={"error": "sequence_not_decided", "doc_id": doc_id_val},
            )
        return JSONResponse(status_code=400, content={"error": msg})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})

    return JSONResponse(status_code=200, content=result)


# ── PATCH /workflow/sequence ──────────────────────────────────────────────────

@router.patch("/workflow/sequence")
def patch_workflow_sequence_endpoint(body: EditSequenceBodyRequest, request: Request):
    """Replace PENDING items (save in edit mode).

    Completed (done) / in-progress (in_progress) items are preserved unchanged.
    body: { doc_id, items: [{ type, label }] }
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    # R0001 group 0208 (NR0003 §3-2): this PATCH previously accepted any valid bearer with no
    # action_scope check, unlike /workflow/decide. Now that a worker can be handed a dedicated
    # sequence-edit token, a worker token MUST carry action_scope=workflow_sequence_edit and be
    # bound to this exact doc_ref. A user JWT (the human edit modal) has action_scope None and
    # still passes unchanged — mirrors the decide endpoint's worker-token guard.
    if auth.get("action_scope") is not None:
        if (
            auth.get("action_scope") != "workflow_sequence_edit"
            or auth.get("doc_ref") != body.doc_id
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "workflow_sequence_edit_token_mismatch",
                    "doc_id": body.doc_id,
                },
            )

    # TR0079.0003 rework (3rd pass): editing the workflow sequence of a
    # document in a disposed (DC) group is a forward write and must be rejected at the
    # server, mirroring decide/advance/review-request above.
    _pre_doc = _db_documents.get_by_id(body.doc_id)
    _prev_review_status = _pre_doc.get("doc_review_status") if _pre_doc else None
    _disposed = _disposed_group_response(body.doc_id, _pre_doc)
    if _disposed is not None:
        return _disposed
    _active_run = _active_ai_run_response_for_user(_pre_doc, auth)
    if _active_run is not None:
        return _active_run

    try:
        result = edit_workflow_pending(
            doc_id=body.doc_id,
            new_items=[item.model_dump() for item in body.items],
            force_encoding_reason=body.force_encoding_reason,
            expected_workflow_tag=body.expected_workflow_tag,
        )
    except SequenceChanged as exc:
        # 0399 P0013 ②: do not overwrite. The rows in hand were built against a sequence
        # that no longer exists, so the person is sent back to re-open rather than told
        # "saved" over somebody else's change.
        return JSONResponse(
            status_code=409,
            content={
                "error": "sequence_changed",
                "doc_id": exc.doc_id,
                "expected_workflow_tag": exc.expected,
                "current_workflow_tag": exc.current,
            },
        )
    except ValueError as exc:
        msg = str(exc)
        # 0399 P0013 ② / DB0012 §5: a revision number with no document to belong to.
        if msg.startswith("invalid_sequence_item:"):
            _, index, detail = msg.split(":", 2)
            return JSONResponse(
                status_code=422,
                content={
                    "error": "invalid_sequence_item",
                    "detail": detail,
                    "item_index": int(index),
                },
            )
        if msg.startswith("sequence_not_decided:"):
            doc_id_val = msg.split(":", 1)[1]
            return JSONResponse(
                status_code=400,
                content={"error": "sequence_not_decided", "doc_id": doc_id_val},
            )
        # 0391 T0005 §5-5/§5-6: a corrupted step label is now REJECTED (was: silently
        # swapped for the type name). The service raises the full explanation — why it
        # was refused and how to get it through — so pass it straight to the sender.
        if msg.startswith("corrupted_label:"):
            return JSONResponse(
                status_code=422,
                content={"error": "corrupted_label", "detail": msg.split(":", 1)[1]},
            )
        # 0119 B0001 (NR0003 §6-A): emptying a decided-but-unstarted workflow would
        # create the unrecoverable zombie sequence — reject like the decide path does.
        if msg.startswith("invalid_sequence_empty:"):
            doc_id_val = msg.split(":", 1)[1]
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_sequence_empty", "doc_id": doc_id_val},
            )
        return JSONResponse(status_code=400, content={"error": msg})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})

    # SSE broadcast: AI sequence edits happen through a worker PATCH, so the user's
    # browser does not receive the modal-local saved event. Reuse the existing group
    # refresh pipeline so explorer/open documents silently refetch the updated steps.
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            broadcast_event_threadsafe,
            FlowEvent,
        )
        from modules.flow_gate.api.v1.events.event_types import EventType

        _post_doc = _db_documents.get_by_id(body.doc_id)
        _next_review_status = _post_doc.get("doc_review_status") if _post_doc else None
        _project = _post_doc.get("project_id") if _post_doc else ((_pre_doc or {}).get("project_id"))
        _group_id = _post_doc.get("group_id") if _post_doc else ((_pre_doc or {}).get("group_id"))
        review_status_delivered = 0
        if _prev_review_status != _next_review_status:
            review_status_delivered = broadcast_event_threadsafe(FlowEvent(
                event_type=EventType.DOC_REVIEW_STATUS_CHANGED,
                payload={
                    "doc_id": body.doc_id,
                    "prev_status": _prev_review_status,
                    "next_status": _next_review_status,
                    "rejection_reason": None,
                    "actor_user_id": None if auth.get("action_scope") is not None else auth.get("issued_to"),
                },
                audience="*",
                doc_id=body.doc_id,
                project=_project,
                group_id=_group_id,
            ))
        if _group_id:
            group_refresh_delivered = broadcast_event_threadsafe(FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": _group_id, "reason": "workflow_sequence_edited"},
                audience="*",
                doc_id=body.doc_id,
                project=_project,
                group_id=_group_id,
            ))
            if review_status_delivered == 0 and group_refresh_delivered == 0:
                _log.info(
                    "workflow sequence edit SSE had no active subscribers for %s",
                    body.doc_id,
                )
    except Exception as _sse_exc:
        _log.warning("workflow sequence edit SSE broadcast failed for %s: %s", body.doc_id, _sse_exc)

    return JSONResponse(status_code=200, content=result)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _build_api_base(request: Request) -> str:
    """Build API base URL from the request.

    Example: http://127.0.0.1:8088/flowgate/api/v1
    """
    base = str(request.base_url).rstrip("/")
    context = settings.CONTEXT.rstrip("/")
    return f"{base}{context}/api/v1"

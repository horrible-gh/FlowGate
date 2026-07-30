"""AI invoke endpoints (flowgate.default.0187 P0005).

POST /api/v1/ai-invoke/start          — admit + launch a run (session auth)
GET  /api/v1/ai-invoke/{run_id}       — status (running / finished payload)
GET  /api/v1/ai-invoke/active         — active run for a group (session auth)
POST /api/v1/ai-invoke/{run_id}/cancel — tree-kill cancel

The work token is minted server-side and injected only into the run's
environment — unlike the copy-mention flow, the raw token is never returned to
the browser (P0005 표기 규칙).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.services import invoke_mention_service
from modules.flow_gate.services import token_service
from modules.flow_gate.services import workflow_decision_service
from modules.flow_gate.workflow import prompt_copy_service
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.utils.id_validators import (
    validate_doc_id,
    validate_group_id,
    validate_project_id,
)

router = APIRouter(prefix="/api/v1/ai-invoke", tags=["AiInvoke"])


class AiInvokeStartRequest(BaseModel):
    project: str
    module: Optional[str] = None
    group: str
    doc_ref: Optional[str] = None
    action_scope: str = "new"
    mode: str = "single"
    continuation_target_seq: Optional[int] = None
    continuation_review_mode: bool = False
    continuation_instruction_mode: Optional[str] = None
    # 0317 T0010 rev4: item_seq (string keys) -> provider_id, from ContinuousWorkDialog's
    # per-step override table. Session-scoped — consulted once, at start_run, never persisted.
    continuation_provider_overrides: Optional[dict[str, str]] = None
    # 0346 T0005: [전달멘트] tab values — a common note for every hop and item_seq (string
    # keys) -> note overrides for individual hops. Session-scoped, same as the provider map.
    continuation_default_note: Optional[str] = None
    continuation_note_overrides: Optional[dict[str, str]] = None
    provider_id: Optional[str] = None
    merge_id: Optional[int] = None
    # Parallel-invoke extras (group 0223): context the matching copy-mention flow
    # assembled in the browser, so the invoke prompt can stay byte-identical.
    selected_docs: Optional[list[str]] = None      # next_step_message reference docs
    messages: Optional[list[str]] = None           # next_step_message user messages
    reject_reason: Optional[str] = None            # rework: live (possibly unsaved) reason
    design_types: Optional[list[str]] = None       # design_handoff selected types
    design_mode: Optional[str] = None              # design_handoff "batch" | "single"
    design_first_label: Optional[str] = None       # design_handoff single-mode type label


# Wire scope → token scope. The extra invoke scopes reuse the edit/new token
# grants (the inbox only honours new/edit/review/workflow_decide); what differs
# is the MENTION each scope feeds the worker.
_TOKEN_SCOPE = {
    "new": "new",
    "edit": "edit",
    "workflow_decide": "workflow_decide",
    "chat": "chat",
    "rework": "edit",
    "vr_correction": "edit",
    "next_step_message": "new",
    "design_handoff": "new",
}
# review/resolve_conflict/workflow_sequence_edit/test_run keep their OWN token scope (the
# identity fallthrough of `_TOKEN_SCOPE.get`), because each is minted by a dedicated service
# that also builds its mention — see the issue_builder branches in `start_ai_invoke`.
#
# 0268 B0001: workflow_sequence_edit and test_run were the last two token scopes with a
# [멘트복사] entrance but no in-app AI 호출 — their surfaces (WorkflowDecisionModal,
# TestRunStrip) sit outside MainPanel's invoke wiring, so the parallel-invoke pass
# (group 0223) never reached them and this allowlist kept the gap structural.
_ALLOWED_SCOPES = (
    *_TOKEN_SCOPE.keys(),
    "review",
    "resolve_conflict",
    "workflow_sequence_edit",
    "test_run",
)


def _err(exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


def _validation_failed(errors: list[dict]) -> JSONResponse:
    return JSONResponse(status_code=422, content={"code": "validation_failed", "errors": errors})


def _continuation_target_error(doc_ref: str, target_seq: int) -> Optional[dict]:
    """Reject a continuation target that is not a remaining step of the real sequence.

    0242 NR0003 발견 4: the only checks here used to be "not null" and "-1 for a pre-decision
    run", so any other number was accepted and failed SILENTLY at chain-termination time
    (inbox_routes: ``completed_seq >= target_seq``) — an already-done seq stopped the chain
    after one document, and a too-large seq ran the sequence to its end. Neither surfaced an
    error, which for an UNMANNED run is a safety problem, not just a UX one.

    The UI now picks the target from the live sequence, but /ai-invoke/start is reachable
    without it, so the rule the hint text only ever *described* is enforced here.

    Returns an error dict for :func:`_validation_failed`, or None when the target is fine.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        items = db_wfseq.get_sequence_items(seq["id"]) if seq is not None else []
    except Exception:  # noqa: BLE001
        # This is a typo guard, not a security control — the run's own DB work would surface a
        # real outage anyway. Fail OPEN so a lookup failure cannot turn a start that worked
        # before 0242 into a 500 from a validation helper.
        return None
    if seq is None:
        # Undecided (or non-sequence) document: there are no item_seqs to validate against.
        # The pre-decision run is already covered by the -1 sentinel rule above.
        return None
    if not items:
        return None
    match = next((i for i in items if i.get("item_seq") == target_seq), None)
    if match is None:
        return {"loc": "continuation_target_seq",
                "msg": f"step {target_seq} does not exist in this workflow sequence"}
    # Slot status per D030 §2 SSOT (mirrors workflow_head_routes._derive_status): a slot is
    # done once its result document exists AND is approved.
    if match.get("result_doc_id") is not None and match.get("result_doc_review_status") == "approved":
        return {"loc": "continuation_target_seq",
                "msg": f"step {target_seq} is already complete — pick a remaining step"}
    return None


def _require_user(request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not auth.get("_is_user_jwt"):
        return JSONResponse(status_code=403, content={"code": "user_session_required",
                                                      "message": "A user session is required."})
    return auth


@router.post("/start")
def start_ai_invoke(body: AiInvokeStartRequest, request: Request):
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth

    errors: list[dict] = []
    if body.mode not in ("single", "continuous"):
        errors.append({"loc": "mode", "msg": "must be single or continuous"})
    if body.action_scope not in _ALLOWED_SCOPES:
        errors.append({"loc": "action_scope", "msg": f"must be one of {', '.join(_ALLOWED_SCOPES)}"})
    if body.mode == "continuous" and body.action_scope not in ("new", "edit", "workflow_decide"):
        errors.append({"loc": "mode", "msg": "continuous mode is not available for this action_scope"})
    if body.mode == "continuous" and body.continuation_target_seq is None:
        errors.append({"loc": "continuation_target_seq", "msg": "required for continuous mode"})
    if (
        body.action_scope == "workflow_decide"
        and body.mode == "continuous"
        and body.continuation_target_seq != -1
    ):
        errors.append({"loc": "continuation_target_seq", "msg": "must be -1 for a pre-decision run"})
    try:
        validate_project_id(body.project)
    except ValueError as exc:
        errors.append({"loc": "project", "msg": str(exc)})
    module_part = body.module if body.module else "none"
    group_id = f"{body.project}.{module_part}.{body.group}"
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        errors.append({"loc": "group", "msg": str(exc)})
    if body.action_scope == "resolve_conflict":
        if body.merge_id is None:
            errors.append({"loc": "merge_id", "msg": "required for resolve_conflict"})
        if body.mode != "single":
            errors.append({"loc": "mode", "msg": "resolve_conflict must be single"})
    else:
        if not body.doc_ref:
            errors.append({"loc": "doc_ref", "msg": "required"})
        else:
            try:
                validate_doc_id(body.doc_ref)
            except ValueError as exc:
                errors.append({"loc": "doc_ref", "msg": str(exc)})
    if errors:
        return _validation_failed(errors)

    if db_projects.get_by_id(body.project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {body.project}"})

    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, body.project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})

    # Needs the DB, so it runs after the cheap field checks above (0242 NR0003 권고 3).
    if (
        body.mode == "continuous"
        and body.action_scope != "workflow_decide"
        and body.doc_ref
        and body.continuation_target_seq is not None
    ):
        target_error = _continuation_target_error(body.doc_ref, body.continuation_target_seq)
        if target_error:
            return _validation_failed([target_error])

    # The mention is built through the exact token_routes path so the prompt the
    # invoked AI reads stays byte-identical to the copy-mention flow.
    from modules.flow_gate.api import token_routes as _token_routes

    locale = request.headers.get("x-locale") or "ko"
    is_continuous = body.mode == "continuous"
    token_scope = _TOKEN_SCOPE.get(body.action_scope, body.action_scope)

    def _standard_mention(raw_token: str, scratch_dir: str, ref_doc_ids=None):
        return _token_routes._build_mention_for_token(
            doc_ref=body.doc_ref,
            group_id=group_id,
            project_id=body.project,
            scratch_dir=scratch_dir,
            raw_token=raw_token,
            request=request,
            ref_doc_ids=ref_doc_ids,
            action_scope=token_scope,
            locale=locale,
            continuous=is_continuous,
            merge_id=body.merge_id,
            # 0226 NR0003 §4 (부수): the review-mode flag previously never reached the
            # mention builder, so a review-mode first hop got the no-stop block instead
            # of the Q-allowed review variant.
            continuous_review_mode=body.continuation_review_mode,
        )

    def _mention_builder(raw_token: str, scratch_dir: str):
        # Each extra scope reproduces the text its [멘트복사] counterpart put on the
        # clipboard (group 0223 parallel-invoke; builders in invoke_mention_service).
        if body.action_scope == "chat":
            return invoke_mention_service.build_conversation_mention(
                doc_id=body.doc_ref,
                project=body.project,
                module=body.module,
                group_name=group_id,
                raw_token=raw_token,
                token_id=token_service.inspect_for_replay(raw_token)["token_id"],
                api_base_url=_token_routes._build_api_base(request),
                # 0293: the AI turn header carries the provider. Unlike the copy path,
                # here the server knows who is being invoked — but only when the run
                # cannot fall back to a different provider (see the helper's docstring).
                provider=ai_invoke_service.resolve_pinned_provider_name(
                    body.project, body.provider_id,
                ),
                provider_id=body.provider_id,
            )
        if body.action_scope == "rework":
            doc = db_docs.get_by_id(body.doc_ref) or {}
            history = doc.get("rejection_history") or []
            if isinstance(history, str):
                try:
                    import json as _json
                    history = _json.loads(history) or []
                except Exception:
                    history = []
            last = body.reject_reason or doc.get("rejection_reason")
            section = invoke_mention_service.build_rejection_section(history, last)
            base = _standard_mention(raw_token, scratch_dir)
            if not base:
                return None
            return section + "\n" + base if section else base
        if body.action_scope == "vr_correction":
            try:
                prompt = prompt_copy_service.build_prompt(
                    doc_id=body.doc_ref, actor_user_id=user_id, locale=locale,
                ).get("prompt_text") or ""
            except Exception:
                prompt = ""
            # The copied VR prompt carries no token; the invoked worker still needs
            # credentials, so the standard edit mention follows the copy text.
            base = _standard_mention(raw_token, scratch_dir)
            if not base:
                return None
            return (prompt + invoke_mention_service.SECTION_SEPARATOR + base) if prompt else base
        if body.action_scope == "next_step_message":
            base = _standard_mention(raw_token, scratch_dir, ref_doc_ids=body.selected_docs)
            if not base:
                return None
            return invoke_mention_service.prepend_messages_section(
                base, body.messages or [], locale,
            )
        if body.action_scope == "design_handoff":
            context = invoke_mention_service.build_design_handoff_context(
                types=body.design_types or [],
                mode=body.design_mode or "batch",
                doc_ref=body.doc_ref,
                locale=locale,
                first_label=body.design_first_label,
            )
            base = _standard_mention(raw_token, scratch_dir)
            if not base:
                return None
            return context + invoke_mention_service.SECTION_SEPARATOR + base
        return _standard_mention(raw_token, scratch_dir)

    issue_builder = None
    if body.action_scope == "review":
        def _issue_review():
            issued = workflow_decision_service.request_review(
                doc_id=body.doc_ref,
                issued_to=user_id,
                api_base_url=_token_routes._build_api_base(request),
                ref_doc_ids=None,
                locale=locale,
            )
            return {
                "raw_token": issued["token"],
                "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"],
                "mention": issued.get("mention") or "",
            }
        issue_builder = _issue_review
    if body.action_scope == "workflow_sequence_edit":
        # 0268 B0001 (NR0003 결함 1): the invoke twin of WorkflowDecisionModal's [멘트 복사].
        # Same issuer as POST /workflow/sequence-edit-request, so the worker reads the exact
        # prompt the clipboard path produced — only the delivery differs.
        def _issue_sequence_edit():
            issued = workflow_decision_service.request_sequence_edit(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_token_routes._build_api_base(request),
                locale=locale,
            )
            return {
                "raw_token": issued["raw_token"],
                "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"],
                "mention": issued.get("mention") or "",
            }
        issue_builder = _issue_sequence_edit
    if body.action_scope == "test_run":
        # 0268 B0001 (NR0003 결함 2): the invoke twin of TestRunStrip's delegation copy.
        # issue_test_run_request returns the raw token under "token" (not "raw_token"),
        # so it is remapped here to the issue_builder contract start_run expects.
        def _issue_test_run():
            from modules.flow_gate.services import test_run_service

            issued = test_run_service.issue_test_run_request(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_token_routes._build_api_base(request),
                locale=locale,
            )
            return {
                "raw_token": issued["token"],
                "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"],
                "mention": issued.get("mention") or "",
            }
        issue_builder = _issue_test_run
    if body.action_scope == "workflow_decide":
        def _issue_workflow_decision():
            return workflow_decision_service.request_workflow_decision(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_token_routes._build_api_base(request),
                locale=locale,
                continuous=is_continuous,
                continuation_review_mode=body.continuation_review_mode,
                continuation_instruction_mode=body.continuation_instruction_mode,
            )
        issue_builder = _issue_workflow_decision
    elif is_continuous and body.action_scope == "new" and not body.continuation_review_mode:
        # 0226 B0001 ④ / NR0003 §4 (§5-5): the continuous first hop used to mint the
        # token directly, bypassing advance_workflow — the ONLY place instruction heads
        # (N/T) are auto-created + auto-approved for an unmanned chain. Starting on an
        # N/T head therefore handed the AI the instruction document to write by hand.
        # Route the first hop through advance_workflow, exactly like every later inbox
        # self-chain hop. Review mode stays on the direct-issue path above: the
        # pre-flight Q phase must not create documents.
        def _issue_first_hop():
            adv = workflow_decision_service.advance_workflow(
                doc_id=body.doc_ref,
                issued_to=user_id,
                api_base_url=_token_routes._build_api_base(request),
                locale=locale,
                continuous=True,
                continuation_target_seq=body.continuation_target_seq,
                continuation_review_mode=body.continuation_review_mode,
                # 0317 T0013 결함 ②: forward the instruction mode, exactly like
                # _issue_workflow_decision above. Omitting it let it normalize to
                # auto_approved, which then got baked into the token and propagated down the
                # whole chain — so [지시서 작성 후 진행](ai_direct) died on every hop, not just
                # the first, and N/T instruction steps were silently auto-approved away.
                continuation_instruction_mode=body.continuation_instruction_mode,
            )
            return {
                "raw_token": adv["token"],
                "token_id": adv["token_id"],
                "scratch_dir": adv["scratch_dir"],
                "mention": adv["mention"],
            }
        issue_builder = _issue_first_hop

    try:
        result = ai_invoke_service.start_run(
            project_id=body.project,
            module=body.module,
            group_id=group_id,
            doc_ref=body.doc_ref or "",
            action_scope=token_scope,
            mode=body.mode,
            continuation_target_seq=body.continuation_target_seq,
            continuation_review_mode=body.continuation_review_mode,
            continuation_instruction_mode=body.continuation_instruction_mode,
            continuation_locale=locale if is_continuous else None,
            issued_to=user_id,
            api_base_url=_token_routes._build_api_base(request),
            mention_builder=_mention_builder,
            provider_id=body.provider_id,
            issue_builder=issue_builder,
            merge_id=body.merge_id,
            continuation_provider_overrides=body.continuation_provider_overrides,
            continuation_default_note=body.continuation_default_note,
            continuation_note_overrides=body.continuation_note_overrides,
        )
    except HTTPException as exc:
        return _err(exc)
    except LookupError as exc:
        return JSONResponse(status_code=404, content={
            "code": "workflow_decision_unavailable", "message": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=409, content={
            "code": "workflow_decision_conflict", "message": str(exc)})
    return JSONResponse(status_code=200, content=result)


@router.get("/providers")
def get_ai_invoke_providers(project: str, request: Request):
    """Return only safe provider briefs for the header runtime selector."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        validate_project_id(project)
    except ValueError as exc:
        return _validation_failed([{"loc": "project", "msg": str(exc)}])
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    effective = ai_invoke_service.list_runtime_providers(project)
    return JSONResponse(status_code=200, content=effective)


@router.get("/active")
def get_active_ai_invoke(group_id: str, request: Request):
    """Restore the group-scoped progress indicator after navigation or reload."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        return _validation_failed([{"loc": "group_id", "msg": str(exc)}])
    project = group_id.split(".", 1)[0]
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    return JSONResponse(status_code=200, content=ai_invoke_service.get_active_status(group_id))


@router.get("/active-all")
def get_active_all_ai_invoke(request: Request):
    """Miniplayer bootstrap (group 0252 P0008 S1): every live run the requesting user
    started plus every chain they paused, in one shot — restores the widget list after
    a reload or a server restart (runs are in-memory, paused rows are DB-persisted)."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    return JSONResponse(status_code=200, content=ai_invoke_service.active_all(auth["issued_to"]))


@router.post("/resume")
def resume_ai_invoke(body: dict, request: Request):
    """Resume a user-paused continuous chain (group 0252 P0008 S5). Group-keyed:
    a paused chain has no live run to address."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    group_id = str(body.get("group_id") or "")
    try:
        validate_group_id(group_id)
    except ValueError as exc:
        return _validation_failed([{"loc": "group_id", "msg": str(exc)}])
    project = group_id.split(".", 1)[0]
    if db_projects.get_by_id(project) is None:
        return JSONResponse(status_code=404, content={"code": "project_not_found",
                                                      "message": f"Project not found: {project}"})
    user_id = auth["issued_to"]
    if not (bool(auth.get("is_admin")) or has_permission(user_id, project, "perm_document_read")):
        return JSONResponse(status_code=403, content={"code": "permission_denied",
                                                      "message": "perm_document_read required"})
    from modules.flow_gate.api import token_routes as _token_routes

    locale = request.headers.get("x-locale") or "ko"
    try:
        result = ai_invoke_service.resume_chain(
            group_id=group_id,
            user_id=user_id,
            api_base_url=_token_routes._build_api_base(request),
            locale=locale,
        )
    except HTTPException as exc:
        return _err(exc)
    return JSONResponse(status_code=200, content=result)


@router.get("/{run_id}")
def get_ai_invoke_status(run_id: str, request: Request):
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return JSONResponse(status_code=200, content=ai_invoke_service.get_status(run_id))
    except HTTPException as exc:
        return _err(exc)


@router.post("/{run_id}/pause")
def pause_ai_invoke(run_id: str, request: Request):
    """Boundary pause for a continuous run (group 0252 P0008 S4): the in-flight step
    completes, the chain stops before the next token — never a mid-step freeze."""
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return JSONResponse(status_code=200,
                            content=ai_invoke_service.pause_run(run_id, auth["issued_to"]))
    except HTTPException as exc:
        return _err(exc)


@router.post("/{run_id}/cancel")
def cancel_ai_invoke(run_id: str, request: Request):
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return JSONResponse(status_code=200, content=ai_invoke_service.cancel_run(run_id))
    except HTTPException as exc:
        return _err(exc)

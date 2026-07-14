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

from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.services import workflow_decision_service
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
    provider_id: Optional[str] = None
    merge_id: Optional[int] = None


def _err(exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


def _validation_failed(errors: list[dict]) -> JSONResponse:
    return JSONResponse(status_code=422, content={"code": "validation_failed", "errors": errors})


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
    if body.action_scope not in ("new", "edit", "workflow_decide", "resolve_conflict"):
        errors.append({"loc": "action_scope", "msg": "must be new, edit, workflow_decide, or resolve_conflict"})
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

    # The mention is built through the exact token_routes path so the prompt the
    # invoked AI reads stays byte-identical to the copy-mention flow.
    from modules.flow_gate.api import token_routes as _token_routes

    locale = request.headers.get("x-locale") or "ko"
    is_continuous = body.mode == "continuous"

    def _mention_builder(raw_token: str, scratch_dir: str):
        return _token_routes._build_mention_for_token(
            doc_ref=body.doc_ref,
            group_id=group_id,
            project_id=body.project,
            scratch_dir=scratch_dir,
            raw_token=raw_token,
            request=request,
            ref_doc_ids=None,
            action_scope=body.action_scope,
            locale=locale,
            continuous=is_continuous,
            merge_id=body.merge_id,
        )

    issue_builder = None
    if body.action_scope == "workflow_decide":
        def _issue_workflow_decision():
            return workflow_decision_service.request_workflow_decision(
                doc_id=body.doc_ref or "",
                issued_to=user_id,
                api_base_url=_token_routes._build_api_base(request),
                locale=locale,
                continuous=is_continuous,
                continuation_review_mode=body.continuation_review_mode,
            )
        issue_builder = _issue_workflow_decision

    try:
        result = ai_invoke_service.start_run(
            project_id=body.project,
            module=body.module,
            group_id=group_id,
            doc_ref=body.doc_ref or "",
            action_scope=body.action_scope,
            mode=body.mode,
            continuation_target_seq=body.continuation_target_seq,
            continuation_review_mode=body.continuation_review_mode,
            continuation_locale=locale if is_continuous else None,
            issued_to=user_id,
            api_base_url=_token_routes._build_api_base(request),
            mention_builder=_mention_builder,
            provider_id=body.provider_id,
            issue_builder=issue_builder,
            merge_id=body.merge_id,
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


@router.get("/{run_id}")
def get_ai_invoke_status(run_id: str, request: Request):
    auth = _require_user(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return JSONResponse(status_code=200, content=ai_invoke_service.get_status(run_id))
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

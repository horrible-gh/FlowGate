"""Remote TS test execution endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.services import test_run_service
from modules.flow_gate.services import token_service
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.rbac.permission_service import has_permission

router = APIRouter(prefix="/api/v1", tags=["TestRun"])


class TestRunBody(BaseModel):
    doc_id: str


def _err(exc) -> JSONResponse:
    detail = exc.detail if hasattr(exc, "detail") else {"error": "internal_error"}
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code, content={"error": str(detail)})


@router.post("/documents/test-run")
def post_test_run(body: TestRunBody, request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    doc = db_docs.get_by_id(body.doc_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": body.doc_id})
    if auth.get("_is_user_jwt"):
        # UI / human-session run — the existing permission gate.
        if not test_run_service.user_can_run_tests(
            auth["issued_to"], doc.get("project_id") or "", bool(auth.get("is_admin"))
        ):
            return JSONResponse(status_code=403, content={"error": "permission_denied"})
        triggered_via = "ui"
        locale = request.headers.get("x-locale") or "ko"
    elif auth.get("action_scope") == "test_run" and auth.get("doc_ref") == body.doc_id:
        # flowgate.default.0157 (P §relaunch after repair): an auto-recovery repair token bound to THIS doc
        # opens the user_session wall so the unmanned chain re-fires itself without a human. Single-use
        # — consume it here so it cannot be replayed. The consumed token still carries the chain's
        # continuation fields, so a passing re-run auto-approves the TSR (_maybe_chain_auto_approve_tsr).
        # T0004 (flowgate.default.0520 NR0003): the token's own continuation_locale -- inherited hop-to-hop
        # by engine_recipe_service._emit_repair -- outranks the request header, the same priority order
        # every other continuation-token consumer in this codebase already uses (inbox_routes.py).
        locale = auth.get("continuation_locale") or request.headers.get("x-locale") or "ko"
        try:
            token_service.consume(auth["token_id"], doc.get("project_id") or "", body.doc_id)
        except Exception:
            pass
        triggered_via = "repair_token"
    else:
        return JSONResponse(status_code=403, content={"error": "user_session_required"})
    try:
        result = test_run_service.validate_and_create_run(
            doc_id=body.doc_id,
            runner_id=auth.get("issued_to") or "system",
            triggered_via=triggered_via,
            locale=locale,
        )
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    return JSONResponse(status_code=202, content=result)


@router.post("/documents/test-run/{run_id}/cancel")
def post_test_run_cancel(run_id: str, request: Request):
    """Immediate cancel for a running/cancelling test run (flowgate.default.0358 T0004).

    Plain `def`, like the two routes above: FastAPI runs it in the threadpool, so the
    synchronous DB/process work below never blocks the event loop
    (test_event_loop_blocking_0279.py only flags `async def` handlers).

    Same permission gate as starting a run (admin or perm_test_run) rather than
    start-author-only — NR0003 §permissions: another operator must be able to reclaim a
    runaway execution, and start-author-only would be inconsistent with the admin
    bypass that already exists. Worker tokens (test_run/repair_token scopes) are not
    accepted here; consumed automation tokens are not widened into a cancel grant.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not auth.get("_is_user_jwt"):
        return JSONResponse(status_code=403, content={"error": "user_session_required"})
    run = db_test_runs.get_run(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"error": "run_not_found", "run_id": run_id})
    doc = db_docs.get_by_id(run.get("doc_id"))
    project_id = (doc or {}).get("project_id") or ""
    if not test_run_service.user_can_run_tests(
        auth["issued_to"], project_id, bool(auth.get("is_admin"))
    ):
        return JSONResponse(status_code=403, content={"error": "permission_denied"})
    try:
        result = test_run_service.request_cancel(run_id)
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    return JSONResponse(status_code=200, content={"ok": True, **result})


@router.post("/documents/test-run-request")
def post_test_run_request(body: TestRunBody, request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not auth.get("_is_user_jwt"):
        return JSONResponse(status_code=403, content={"error": "user_session_required"})
    doc = db_docs.get_by_id(body.doc_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": body.doc_id})
    if not test_run_service.user_can_run_tests(
        auth["issued_to"], doc.get("project_id") or "", bool(auth.get("is_admin"))
    ):
        return JSONResponse(status_code=403, content={"error": "permission_denied"})
    try:
        result = test_run_service.issue_test_run_request(
            doc_id=body.doc_id,
            issued_to=auth["issued_to"],
            api_base_url=_build_api_base(request),
        )
    except LookupError as exc:
        _, _, doc_id = str(exc).partition(":")
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": doc_id})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return JSONResponse(status_code=201, content=result)


def _build_api_base(request: Request) -> str:
    from config import settings

    base = str(request.base_url).rstrip("/")
    context = settings.CONTEXT.rstrip("/")
    return f"{base}{context}/api/v1"

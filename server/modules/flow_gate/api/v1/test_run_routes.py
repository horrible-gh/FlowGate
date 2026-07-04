"""Remote TS test execution endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.services import test_run_service
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
        result = test_run_service.validate_and_create_run(
            doc_id=body.doc_id,
            runner_id=auth["issued_to"],
            triggered_via="ui",
        )
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    return JSONResponse(status_code=202, content=result)


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

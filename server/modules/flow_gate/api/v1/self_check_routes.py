"""TR Self-check resource endpoints; all identity comes from the document/token."""
from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import tr_self_check_service as selfcheck

router = APIRouter(prefix="/api/v1/documents", tags=["TR Self-check"])


def _error(exc: selfcheck.SelfCheckError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"ok": False,
        "error": {"code": exc.code, "message": exc.code,
                  "details": {"self_check_run_id": exc.detail} if exc.code == "selfcheck_already_running" else {}}})


def _forbidden() -> JSONResponse:
    return JSONResponse(status_code=403, content={"ok": False,
        "error": {"code": "forbidden", "message": "forbidden", "details": {}}})


def _auth(request: Request, doc_id: str, mutate: bool):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        doc = selfcheck._document(doc_id)
    except selfcheck.SelfCheckError as exc:
        return _error(exc)
    if auth.get("_is_user_jwt"):
        permission = "perm_document_update" if mutate else "perm_document_read"
        if not has_permission(auth.get("issued_to"), doc["project_id"], permission):
            return _forbidden()
    elif not (auth.get("action_scope") == "edit" and auth.get("doc_ref") == doc_id
              and auth.get("group_id") == doc["group_id"] and auth.get("project") == doc["project_id"]):
        return _forbidden()
    return auth


@router.post("/{tr_doc_id}/self-check/runs")
def start_run(request: Request, tr_doc_id: str, body: dict = Body(...)):
    auth = _auth(request, tr_doc_id, True)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        row = selfcheck.start(tr_doc_id, body, auth.get("issued_to"))
        return JSONResponse(status_code=202, content={"ok": True, **row})
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@router.get("/{tr_doc_id}/self-check/runs")
def list_runs(request: Request, tr_doc_id: str, limit: int = Query(20, ge=1, le=100)):
    auth = _auth(request, tr_doc_id, False)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return {"ok": True, "runs": selfcheck.list_runs(tr_doc_id, limit)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@router.get("/{tr_doc_id}/self-check/runs/{run_id}")
def read_run(request: Request, tr_doc_id: str, run_id: str):
    auth = _auth(request, tr_doc_id, False)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return {"ok": True, **selfcheck.read(tr_doc_id, run_id)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@router.post("/{tr_doc_id}/self-check/runs/{run_id}/cancel")
def cancel_run(request: Request, tr_doc_id: str, run_id: str):
    auth = _auth(request, tr_doc_id, True)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return {"ok": True, **selfcheck.cancel(tr_doc_id, run_id)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)

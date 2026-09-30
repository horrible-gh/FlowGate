"""Project scoped AI connection test API (flowgate.v02.0017 T0002 §3.1).

The baseline "does this AI connection answer?" probe, isolated from AI Evaluation and
offered from AI connection management.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_connection_test_service as service
from modules.flow_gate.settings import ai_settings_service

router = APIRouter(tags=["AiConnectionTest"])
logger = logging.getLogger(__name__)


class ConnectionTestStart(BaseModel):
    provider_id: str


def _error(exc: service.ConnectionTestError) -> JSONResponse:
    detail = {"code": exc.code, "message": exc.code.replace("_", " ").capitalize() + "."}
    if exc.test_id:
        detail["test_id"] = exc.test_id
    return JSONResponse({"error": detail}, status_code=exc.status)


def _internal_error() -> JSONResponse:
    logger.exception("AI connection test request failed")
    return _error(service.ConnectionTestError("internal_error", 500))


def _project(project_id: str):
    try:
        ai_settings_service.resolve_effective(project_id)
    except LookupError:
        return JSONResponse({"error": {"code": "project_not_found", "message": "Project not found."}}, status_code=404)
    return None


def _run(project_id: str, fn, *args, status_code: int = 200):
    missing = _project(project_id)
    if missing is not None:
        return missing
    try:
        result = fn(*args)
        return JSONResponse(result, status_code=status_code) if status_code != 200 else result
    except service.ConnectionTestError as exc:
        return _error(exc)
    except Exception:
        return _internal_error()


@router.post("/projects/{project_id}/ai-connection-tests")
def start_connection_test(project_id: str, body: ConnectionTestStart,
                          user=Depends(require_permission("project.settings.edit", "project_id"))):
    return _run(project_id, service.start, project_id, body.provider_id, user.get("user_id"),
                status_code=202)


@router.get("/projects/{project_id}/ai-connection-tests/current")
def current_connection_test(project_id: str, provider_id: str,
                            user=Depends(require_permission("project.settings.read", "project_id"))):
    return _run(project_id, service.current, project_id, provider_id)


@router.get("/projects/{project_id}/ai-connection-tests")
def connection_test_history(project_id: str, provider_id: str | None = None,
                            limit: int = Query(default=20, ge=1, le=100), cursor: str | None = None,
                            user=Depends(require_permission("project.settings.read", "project_id"))):
    return _run(project_id, service.history, project_id, provider_id, limit, cursor)


@router.get("/projects/{project_id}/ai-connection-tests/{test_id}")
def connection_test_detail(project_id: str, test_id: str,
                           user=Depends(require_permission("project.settings.read", "project_id"))):
    return _run(project_id, service.detail, project_id, test_id)


@router.post("/projects/{project_id}/ai-connection-tests/{test_id}/cancel")
def cancel_connection_test(project_id: str, test_id: str,
                           user=Depends(require_permission("project.settings.edit", "project_id"))):
    return _run(project_id, service.cancel, project_id, test_id)

"""Project scoped AI Evaluation API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_evaluation_service as service
from modules.flow_gate.settings import ai_settings_service

router = APIRouter(tags=["AiEvaluation"])
logger = logging.getLogger(__name__)


class EvaluationStart(BaseModel):
    provider_id: str


def _error(exc: service.EvaluationError) -> JSONResponse:
    detail = {"code": exc.code, "message": exc.code.replace("_", " ").capitalize() + "."}
    if exc.evaluation_id:
        detail["evaluation_id"] = exc.evaluation_id
    return JSONResponse({"error": detail}, status_code=exc.status)


def _internal_error() -> JSONResponse:
    logger.exception("AI Evaluation request failed")
    return _error(service.EvaluationError("internal_error", 500))


def _project(project_id: str):
    try:
        ai_settings_service.resolve_effective(project_id)
    except LookupError:
        return JSONResponse({"error": {"code": "project_not_found", "message": "Project not found."}}, status_code=404)
    return None


@router.post("/projects/{project_id}/ai-evaluations")
def start_evaluation(project_id: str, body: EvaluationStart,
                     user=Depends(require_permission("project.settings.edit", "project_id"))):
    missing = _project(project_id)
    if missing is not None:
        return missing
    try:
        return JSONResponse(service.start(project_id, body.provider_id, user.get("user_id")), status_code=202)
    except service.EvaluationError as exc:
        return _error(exc)
    except Exception:
        return _internal_error()


@router.get("/projects/{project_id}/ai-evaluations/current")
def current_evaluation(project_id: str, provider_id: str,
                       user=Depends(require_permission("project.settings.read", "project_id"))):
    missing = _project(project_id)
    if missing is not None:
        return missing
    try:
        return service.current(project_id, provider_id)
    except Exception:
        return _internal_error()


@router.get("/projects/{project_id}/ai-evaluations")
def evaluation_history(project_id: str, provider_id: str | None = None,
                       limit: int = Query(default=20, ge=1, le=100), cursor: str | None = None,
                       user=Depends(require_permission("project.settings.read", "project_id"))):
    missing = _project(project_id)
    if missing is not None:
        return missing
    try:
        return service.history(project_id, provider_id, limit, cursor)
    except service.EvaluationError as exc:
        return _error(exc)
    except Exception:
        return _internal_error()


@router.get("/projects/{project_id}/ai-evaluations/{evaluation_id}")
def evaluation_detail(project_id: str, evaluation_id: str,
                      user=Depends(require_permission("project.settings.read", "project_id"))):
    missing = _project(project_id)
    if missing is not None:
        return missing
    try:
        return service.detail(project_id, evaluation_id)
    except service.EvaluationError as exc:
        return _error(exc)
    except Exception:
        return _internal_error()


@router.post("/projects/{project_id}/ai-evaluations/{evaluation_id}/cancel")
def cancel_evaluation(project_id: str, evaluation_id: str,
                      user=Depends(require_permission("project.settings.edit", "project_id"))):
    missing = _project(project_id)
    if missing is not None:
        return missing
    try:
        return service.cancel(project_id, evaluation_id)
    except service.EvaluationError as exc:
        return _error(exc)
    except Exception:
        return _internal_error()

"""Project scoped AI Evaluation API (flowgate.v02.0017 T0002 §3).

An evaluation names a target AI and a judge AI. The judge prepares a real task in Scratch,
the target performs it, and the judge scores it per criterion with a review. An evaluation set
fixes the task; a human review may be added to a completed evaluation.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_evaluation_service as service
from modules.flow_gate.settings import ai_settings_service

router = APIRouter(tags=["AiEvaluation"])
logger = logging.getLogger(__name__)


class EvaluationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_provider_id: str
    judge_provider_id: str
    # UI locale; the judge writes the task and review in that language.
    locale: str | None = None
    evaluation_set_id: str | None = None


class HumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 0-100; score and comment both null clears the human review.
    score: int | None = None
    comment: str | None = None


class EvaluationSetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Limits are checked by the service so every rejected field is reported with its reason.
    name: str
    instruction: str
    constraints: list[str] | None = None


def _error(exc: service.EvaluationError) -> JSONResponse:
    detail = {"code": exc.code, "message": exc.code.replace("_", " ").capitalize() + "."}
    if exc.evaluation_id:
        detail["evaluation_id"] = exc.evaluation_id
    if exc.errors:
        detail["errors"] = exc.errors
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


def _run(project_id: str, fn, *args, status_code: int = 200):
    missing = _project(project_id)
    if missing is not None:
        return missing
    try:
        result = fn(*args)
        return JSONResponse(result, status_code=status_code) if status_code != 200 else result
    except service.EvaluationError as exc:
        return _error(exc)
    except Exception:
        return _internal_error()


@router.post("/projects/{project_id}/ai-evaluations")
def start_evaluation(project_id: str, body: EvaluationStart,
                     user=Depends(require_permission("project.settings.edit", "project_id"))):
    return _run(project_id, service.start, project_id, body.target_provider_id, body.judge_provider_id,
                user.get("user_id"), body.locale, body.evaluation_set_id, status_code=202)


@router.get("/projects/{project_id}/ai-evaluation-sets")
def list_evaluation_sets(project_id: str,
                         user=Depends(require_permission("project.settings.read", "project_id"))):
    return _run(project_id, service.list_sets, project_id)


@router.post("/projects/{project_id}/ai-evaluation-sets")
def create_evaluation_set(project_id: str, body: EvaluationSetInput,
                          user=Depends(require_permission("project.settings.edit", "project_id"))):
    return _run(project_id, service.create_set, project_id, body.model_dump(), user.get("user_id"),
                status_code=201)


@router.put("/projects/{project_id}/ai-evaluation-sets/{set_id}")
def update_evaluation_set(project_id: str, set_id: str, body: EvaluationSetInput,
                          user=Depends(require_permission("project.settings.edit", "project_id"))):
    return _run(project_id, service.update_set, project_id, set_id, body.model_dump())


@router.delete("/projects/{project_id}/ai-evaluation-sets/{set_id}")
def delete_evaluation_set(project_id: str, set_id: str,
                          user=Depends(require_permission("project.settings.edit", "project_id"))):
    return _run(project_id, service.delete_set, project_id, set_id)


@router.get("/projects/{project_id}/ai-evaluations")
def evaluation_history(project_id: str, target_provider_id: str | None = None,
                       limit: int = Query(default=20, ge=1, le=100), cursor: str | None = None,
                       user=Depends(require_permission("project.settings.read", "project_id"))):
    return _run(project_id, service.history, project_id, target_provider_id, limit, cursor)


@router.get("/projects/{project_id}/ai-evaluations/{evaluation_id}")
def evaluation_detail(project_id: str, evaluation_id: str,
                      user=Depends(require_permission("project.settings.read", "project_id"))):
    return _run(project_id, service.detail, project_id, evaluation_id)


@router.post("/projects/{project_id}/ai-evaluations/{evaluation_id}/cancel")
def cancel_evaluation(project_id: str, evaluation_id: str,
                      user=Depends(require_permission("project.settings.edit", "project_id"))):
    return _run(project_id, service.cancel, project_id, evaluation_id)


@router.put("/projects/{project_id}/ai-evaluations/{evaluation_id}/human-review")
def human_review(project_id: str, evaluation_id: str, body: HumanReview,
                 user=Depends(require_permission("project.settings.edit", "project_id"))):
    return _run(project_id, service.human_review, project_id, evaluation_id, body.score, body.comment,
                user.get("user_id"))

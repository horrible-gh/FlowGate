"""AI Evaluation API (flowgate.v02.0017 T0002 §3).

An evaluation names a target AI and a judge AI. The judge prepares a real task in Scratch,
the target performs it, and the judge scores it per criterion with a review. An evaluation set
fixes the task; a human review may be added to a completed evaluation.

Evaluations and sets belong to no project (CH0006 (4)): one list for every signed-in user on the
AI 관리 screen, the same way the system AI they evaluate is shared.

Who may act follows the owner of the evaluated AI (CH0006 #48): a system AI's evaluation is run,
cancelled and human-reviewed by an administrator (system.settings.manage) and read by everyone;
evaluation sets are administrator-managed for the same reason. A user's own AI never runs on the
server (db/ai_providers.py), so it cannot be evaluated yet: starting one answers 409
evaluation_user_ai_unsupported instead of a misleading provider_not_found.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import ai_providers
from modules.flow_gate.rbac import decorators as _rbac
from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_evaluation_service as service

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


_MANAGE = "system.settings.manage"


def _require_manage(user: dict) -> None:
    if not _rbac._has_permission(user, _MANAGE, None):
        raise service.EvaluationError("forbidden", 403)


def _run(fn, *args, status_code: int = 200, check=None):
    try:
        if check is not None:
            check()
        result = fn(*args)
        return JSONResponse(result, status_code=status_code) if status_code != 200 else result
    except service.EvaluationError as exc:
        return _error(exc)
    except Exception:
        return _internal_error()


@router.post("/ai-evaluations")
def start_evaluation(body: EvaluationStart, user=Depends(get_current_user)):
    def check():
        if ai_providers.get_system_row(body.target_provider_id) is None and any(
                r["provider_id"] == body.target_provider_id for r in ai_providers.list_user(user["user_id"])):
            raise service.EvaluationError("evaluation_user_ai_unsupported", 409)
        _require_manage(user)
    return _run(service.start, body.target_provider_id, body.judge_provider_id,
                user.get("user_id"), body.locale, body.evaluation_set_id, status_code=202, check=check)


@router.get("/ai-evaluation-sets")
def list_evaluation_sets(user=Depends(get_current_user)):
    return _run(service.list_sets)


@router.post("/ai-evaluation-sets")
def create_evaluation_set(body: EvaluationSetInput, user=Depends(require_permission(_MANAGE))):
    return _run(service.create_set, body.model_dump(), user.get("user_id"), status_code=201)


@router.put("/ai-evaluation-sets/{set_id}")
def update_evaluation_set(set_id: str, body: EvaluationSetInput, user=Depends(require_permission(_MANAGE))):
    return _run(service.update_set, set_id, body.model_dump())


@router.delete("/ai-evaluation-sets/{set_id}")
def delete_evaluation_set(set_id: str, user=Depends(require_permission(_MANAGE))):
    return _run(service.delete_set, set_id)


@router.get("/ai-evaluations")
def evaluation_history(target_provider_id: str | None = None,
                       limit: int = Query(default=20, ge=1, le=100), cursor: str | None = None,
                       user=Depends(get_current_user)):
    return _run(service.history, target_provider_id, limit, cursor)


@router.get("/ai-evaluations/{evaluation_id}")
def evaluation_detail(evaluation_id: str, user=Depends(get_current_user)):
    return _run(service.detail, evaluation_id)


@router.post("/ai-evaluations/{evaluation_id}/cancel")
def cancel_evaluation(evaluation_id: str, user=Depends(require_permission(_MANAGE))):
    return _run(service.cancel, evaluation_id)


@router.put("/ai-evaluations/{evaluation_id}/human-review")
def human_review(evaluation_id: str, body: HumanReview, user=Depends(require_permission(_MANAGE))):
    return _run(service.human_review, evaluation_id, body.score, body.comment, user.get("user_id"))

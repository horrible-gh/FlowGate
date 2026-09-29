"""Read-only Project AI summary composed from authoritative resources."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from modules.flow_gate.db import projects
from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_evaluation_service, ai_group_service
from modules.flow_gate.settings import ai_settings_service

router = APIRouter(tags=["AiOverview"])
logger = logging.getLogger(__name__)


def _providers(project_id: str) -> tuple[dict, list[str]]:
    settings = ai_settings_service.get_project_settings(project_id, include_catalog=False)
    effective = settings["effective"]
    ids = [row["id"] for row in effective["providers"]]
    return ({"mode": settings["mode"], "total": effective["registered_count"],
             "enabled": len(ids), "default_provider_id": effective["default_provider_id"]}, ids)


def _evaluation(project_id: str, provider_ids: list[str]) -> dict:
    counts = {"providers_with_current_result": 0, "running": 0, "failed": 0, "stale": 0}
    for provider_id in provider_ids:
        status = ai_evaluation_service.current(project_id, provider_id)
        current = status["current"]
        if current is not None:
            counts["providers_with_current_result"] += 1
            if current["lifecycle"] == "running":
                counts["running"] += 1
            elif current["lifecycle"] == "failed":
                counts["failed"] += 1
        elif status["latest_history"] is not None and status["latest_history"]["is_stale"]:
            counts["stale"] += 1
    return counts


def _groups(project_id: str) -> dict:
    groups = ai_group_service.list_groups(project_id)["items"]
    invalid = sum(any(role is not None and not role["is_available"]
                      for role in (group["work_executor"], group["reviewer"], group["reworker"]))
                  for group in groups)
    return {"total": len(groups), "invalid": invalid}


@router.get("/projects/{project_id}/ai-overview")
def ai_overview(project_id: str,
                user=Depends(require_permission("project.settings.read", "project_id"))):
    # Project absence is a whole-request failure, not a missing section.
    if projects.get_by_id(project_id) is None:
        return JSONResponse({"error": {"code": "project_not_found", "message": "Project not found."}},
                            status_code=404)
    result = {"providers": None, "evaluation": None, "ai_groups": None, "errors": []}
    provider_ids = None
    try:
        result["providers"], provider_ids = _providers(project_id)
    except Exception:
        logger.exception("AI overview providers section failed")
        result["errors"].append({"section": "providers", "code": "overview_section_failed"})
    try:
        # Evaluation still uses its authoritative Provider resolution if the summary read failed.
        if provider_ids is None:
            provider_ids = [row["id"] for row in ai_settings_service.resolve_effective(project_id)["providers"]]
        result["evaluation"] = _evaluation(project_id, provider_ids)
    except Exception:
        logger.exception("AI overview evaluation section failed")
        result["errors"].append({"section": "evaluation", "code": "overview_section_failed"})
    try:
        result["ai_groups"] = _groups(project_id)
    except Exception:
        logger.exception("AI overview AI Group section failed")
        result["errors"].append({"section": "ai_groups", "code": "overview_section_failed"})
    return result

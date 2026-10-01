"""Read-only AI summary composed from authoritative resources (flowgate.v02.0017 T0002 §6),
still served under a project for the v0.1 settings screen.

connections  the system AI and which of them are usable (no project policy since CH0006 (4))
evaluation   recent scores, active runs, judge and last evaluation time (not per project)
ai_groups    Supervisor, members, roles, their counts and state per team (not per project)

Also serves the repeat-count ceiling shown under AI connection management
(system setting ai_repeat_count_max; saved through PATCH /system/settings).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from modules.flow_gate.db import projects
from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_evaluation_service, ai_group_service
from modules.flow_gate.settings import ai_execution_policy_service, ai_settings_service

router = APIRouter(tags=["AiOverview"])
logger = logging.getLogger(__name__)


def _connections(project_id: str) -> dict:
    settings = ai_settings_service.get_project_settings(project_id, include_catalog=False)
    effective = settings["effective"]
    usable = {row["id"] for row in effective["providers"]}
    # Every system AI; a disabled one stays visible as "off".
    items = [{"id": row["id"], "name": row["name"], "exec_type": row["exec_type"], "kind": row["kind"],
              "enabled": bool(row.get("enabled", True)), "usable": row["id"] in usable}
             for row in settings["providers"]]
    return {"source": effective["source"], "total": len(items), "usable": len(usable),
            "default_provider_id": effective["default_provider_id"], "items": items}


def _evaluation(project_id: str) -> dict:
    return ai_evaluation_service.summary()


def _groups(project_id: str) -> dict:
    groups = ai_group_service.list_groups()["items"]
    return {"total": len(groups),
            "attention": sum(group["status"] == "attention" for group in groups),
            "items": [{"ai_group_id": group["ai_group_id"], "name": group["name"],
                       "member_count": group["member_count"], "role_count": group["role_count"],
                       "supervisor": group["supervisor"], "status": group["status"],
                       "enabled": group["enabled"], "is_default": group["is_default"],
                       "members": [{key: member[key] for key in ("member_id", "provider_id", "provider_name",
                                                                  "is_available", "status")}
                                   for member in group["members"]],
                       "roles": [{"role_id": role["role_id"], "name": role["name"]} for role in group["roles"]]}
                      for group in groups]}


@router.get("/projects/{project_id}/ai-execution-policy")
def ai_execution_policy(project_id: str,
                        user=Depends(require_permission("project.settings.read", "project_id"))):
    return ai_execution_policy_service.execution_policy_payload()


@router.get("/projects/{project_id}/ai-overview")
def ai_overview(project_id: str,
                user=Depends(require_permission("project.settings.read", "project_id"))):
    # Project absence is a whole-request failure, not a missing section.
    if projects.get_by_id(project_id) is None:
        return JSONResponse({"error": {"code": "project_not_found", "message": "Project not found."}},
                            status_code=404)
    result = {"connections": None, "evaluation": None, "ai_groups": None, "errors": []}
    sections = (("connections", _connections), ("evaluation", _evaluation), ("ai_groups", _groups))
    for name, build in sections:
        try:
            result[name] = build(project_id)
        except Exception:
            logger.exception("AI overview %s section failed", name)
            result["errors"].append({"section": name, "code": "overview_section_failed"})
    return result

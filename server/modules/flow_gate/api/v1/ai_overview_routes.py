"""Read-only Project AI summary composed from authoritative resources
(flowgate.v02.0017 T0002 §6).

connections  the project AI policy (on/off, allowed system AI, own AI) and usable AI
evaluation   recent scores, active runs, judge and last evaluation time
ai_groups    Supervisor, members, roles, their counts and state per team

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
    policy = ai_settings_service.get_project_policy(project_id)
    effective = policy["effective"]
    usable = {row["id"] for row in effective["providers"]}
    # The system AI this project's policy allows; a disabled one stays visible as "off".
    allowed = [row for row in policy["system_providers"] if row["allowed"]]
    items = [{"id": row["id"], "name": row["name"], "exec_type": row["exec_type"], "kind": row["kind"],
              "enabled": bool(row.get("enabled", True)), "usable": row["id"] in usable}
             for row in allowed]
    return {"ai_enabled": policy["ai_enabled"], "system_ai_scope": policy["system_ai_scope"],
            "allow_user_ai": policy["allow_user_ai"], "source": effective["source"],
            "total": len(allowed), "usable": len(usable),
            "default_provider_id": effective["default_provider_id"], "items": items}


def _groups(project_id: str) -> dict:
    groups = ai_group_service.list_groups(project_id)["items"]
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
    sections = (("connections", _connections), ("evaluation", ai_evaluation_service.summary),
                ("ai_groups", _groups))
    for name, build in sections:
        try:
            result[name] = build(project_id)
        except Exception:
            logger.exception("AI overview %s section failed", name)
            result["errors"].append({"section": name, "code": "overview_section_failed"})
    return result

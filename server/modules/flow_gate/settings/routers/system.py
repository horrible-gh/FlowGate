"""System settings API router (D018 r1 §A, D-1).

GET   /api/v1/system/settings  — retrieve all K/V settings
GET   /api/v1/system/settings/storage-root — configured/effective storage root contract
PATCH /api/v1/system/settings  — update multiple K/V settings
GET   /api/v1/system/info      — retrieve version and DB status
GET   /api/v1/system/storage-root — stored vs effective storage root
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.settings.system_settings_service import get_all, get_system_info, set_values
from modules.flow_gate.storage.paths import describe_storage_root, get_storage_root_info

router = APIRouter(prefix="/system", tags=["SystemSettings"])


class SettingsPatch(BaseModel):
    updates: dict[str, str | None] = Field(
        ...,
        description=(
            "Setting key-value pairs. Allowed keys: "
            "storage_root, log_retention_days, log_level, jwt_expiry_minutes, "
            "refresh_token_expiry_days, mail_smtp_host, mail_smtp_port, mail_from, "
            "ai_repeat_count_max"
        )
    )


class StorageRootConfigured(BaseModel):
    system: str | None
    project: str | None


class StorageRootOverrides(BaseModel):
    environment: bool
    project: bool


class StorageRootInfo(BaseModel):
    project_id: str | None
    configured: StorageRootConfigured
    effective: str
    source: Literal["environment", "project", "system", "default"]
    overrides: StorageRootOverrides


@router.get("/settings")
def list_settings(user=Depends(require_permission("system.settings.manage"))):
    return {"settings": get_all()}


@router.get("/settings/storage-root", response_model=StorageRootInfo)
def storage_root_info(
    project_id: str | None = None,
    user=Depends(require_permission("system.settings.manage")),
):
    """Return configured values separately from the effective storage root."""
    return get_storage_root_info(project_id)


@router.patch("/settings")
def update_settings(body: SettingsPatch, user=Depends(require_permission("system.settings.manage"))):
    try:
        results = set_values(body.updates, updated_by=user.get("user_id"))
    except ValueError as exc:
        # flowgate.default.0578 T0010 task 1: only this one setting_key gets a stable
        # code the client can key i18n off of. Other ValueError messages stay raw
        # (out of scope — see T0010 §3 task 1 point 3).
        if "ai_repeat_count_max" in body.updates and str(exc) == (
            "ai_repeat_count_max must be an integer between 1 and 30"
        ):
            raise HTTPException(
                status_code=422,
                detail={"code": "ai_repeat_count_out_of_range", "params": {"min": 1, "max": 30}},
            )
        raise HTTPException(status_code=422, detail=str(exc))
    return {"updated": results}


@router.get("/info")
def system_info(user=Depends(require_permission("system.settings.manage"))):
    return get_system_info()


@router.get("/storage-root")
def storage_root_describe(user=Depends(require_permission("system.settings.manage"))):
    return describe_storage_root()

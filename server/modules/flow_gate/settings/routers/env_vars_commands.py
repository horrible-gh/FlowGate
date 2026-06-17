"""Environment variables and commands management API router.

GET    /api/v1/env-vars
POST   /api/v1/env-vars
PUT    /api/v1/env-vars/{var_id}
DELETE /api/v1/env-vars/{var_id}

GET    /api/v1/commands
POST   /api/v1/commands
PUT    /api/v1/commands/{command_id}
DELETE /api/v1/commands/{command_id}
POST   /api/v1/commands/{command_id}/resolve
POST   /api/v1/commands/{command_id}/execute
"""
from __future__ import annotations

import locale
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac.decorators import _has_permission
from modules.flow_gate.db import env_vars as _ev_db
from modules.flow_gate.db import commands as _cmd_db
from modules.flow_gate.commands import resolve_template

router = APIRouter(tags=["EnvVarsCommands"])

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OS_NATIVE_ENCODING = locale.getpreferredencoding(False) if platform.system() == 'Windows' else 'utf-8'


def _safe_decode(data: bytes) -> str:
    """Decode bytes trying UTF-8 → OS native encoding → UTF-8 replace in order."""
    if not data:
        return ''
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass
    try:
        return data.decode(_OS_NATIVE_ENCODING)
    except UnicodeDecodeError:
        pass
    return data.decode('utf-8', errors='replace')


def _require_settings_read(user=Depends(get_current_user)):
    if not (_has_permission(user, "project.settings.read", None) or user.get("is_admin")):
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


# ── Environment variables ─────────────────────────────────────────────────────

class EnvVarCreate(BaseModel):
    kind: str = "user"
    name: str
    value: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty.")
        if not _NAME_RE.match(v):
            raise ValueError("name may only contain letters, digits, and underscores (first character must be a letter or underscore).")
        return v

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in ("system", "user"):
            raise ValueError("kind must be either \"system\" or \"user\".")
        return v


class EnvVarUpdate(BaseModel):
    name: str | None = None
    value: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty.")
        if not _NAME_RE.match(v):
            raise ValueError("name may only contain letters, digits, and underscores (first character must be a letter or underscore).")
        return v


@router.get("/env-vars")
def list_env_vars(
    include_system: bool = Query(False),
    user=Depends(_require_settings_read),
):
    if include_system and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view system variables.")
    rows = _ev_db.list_env_vars(include_system=include_system)
    return {"env_vars": rows}


@router.post("/env-vars", status_code=201)
def create_env_var(body: EnvVarCreate, user=Depends(_require_settings_read)):
    # Only user kind is allowed from the admin UI
    if body.kind == "system" and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can create system variables.")
    try:
        row = _ev_db.create({"kind": body.kind, "name": body.name, "value": body.value})
    except Exception as exc:
        msg = str(exc)
        if "UNIQUE constraint" in msg:
            raise HTTPException(status_code=409, detail=f"Variable name already exists: {body.name}")
        raise HTTPException(status_code=400, detail=msg)
    return row


@router.put("/env-vars/{var_id}")
def update_env_var(var_id: str, body: EnvVarUpdate, user=Depends(_require_settings_read)):
    existing = _ev_db.get_by_id(var_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Environment variable not found.")
    if existing["kind"] == "system" and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can modify system variables.")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        row = _ev_db.update(var_id, updates)
    except Exception as exc:
        msg = str(exc)
        if "UNIQUE constraint" in msg:
            raise HTTPException(status_code=409, detail="Variable name already exists.")
        raise HTTPException(status_code=400, detail=msg)
    return row


@router.delete("/env-vars/{var_id}", status_code=200)
def delete_env_var(var_id: str, user=Depends(_require_settings_read)):
    existing = _ev_db.get_by_id(var_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Environment variable not found.")
    if existing["kind"] == "system" and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can delete system variables.")
    _ev_db.delete(var_id)
    return {"detail": "deleted"}


# ── Commands ──────────────────────────────────────────────────────────────────

class CommandCreate(BaseModel):
    kind: str = "user"
    name: str
    template: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty.")
        if not _NAME_RE.match(v):
            raise ValueError("name may only contain letters, digits, and underscores (first character must be a letter or underscore).")
        return v

    @field_validator("template")
    @classmethod
    def validate_template(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("template must not be empty.")
        return v

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in ("system", "user"):
            raise ValueError("kind must be either \"system\" or \"user\".")
        return v


class CommandUpdate(BaseModel):
    name: str | None = None
    template: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty.")
        if not _NAME_RE.match(v):
            raise ValueError("name may only contain letters, digits, and underscores (first character must be a letter or underscore).")
        return v


@router.get("/commands")
def list_commands(
    include_system: bool = Query(False),
    user=Depends(_require_settings_read),
):
    if include_system and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view system commands.")
    rows = _cmd_db.list_commands(include_system=include_system)
    return {"commands": rows}


@router.post("/commands", status_code=201)
def create_command(body: CommandCreate, user=Depends(_require_settings_read)):
    if body.kind == "system" and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can create system commands.")
    try:
        row = _cmd_db.create({"kind": body.kind, "name": body.name, "template": body.template})
    except Exception as exc:
        msg = str(exc)
        if "UNIQUE constraint" in msg:
            raise HTTPException(status_code=409, detail=f"Command name already exists: {body.name}")
        raise HTTPException(status_code=400, detail=msg)
    return row


@router.put("/commands/{command_id}")
def update_command(command_id: str, body: CommandUpdate, user=Depends(_require_settings_read)):
    existing = _cmd_db.get_by_id(command_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Command not found.")
    if existing["kind"] == "system" and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can modify system commands.")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        row = _cmd_db.update(command_id, updates)
    except Exception as exc:
        msg = str(exc)
        if "UNIQUE constraint" in msg:
            raise HTTPException(status_code=409, detail="Command name already exists.")
        raise HTTPException(status_code=400, detail=msg)
    return row


@router.delete("/commands/{command_id}", status_code=200)
def delete_command(command_id: str, user=Depends(_require_settings_read)):
    existing = _cmd_db.get_by_id(command_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Command not found.")
    if existing["kind"] == "system" and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can delete system commands.")
    _cmd_db.delete(command_id)
    return {"detail": "deleted"}


@router.post("/commands/{command_id}/resolve")
def resolve_command(command_id: str, user=Depends(_require_settings_read)):
    cmd = _cmd_db.get_by_id(command_id)
    if cmd is None:
        raise HTTPException(status_code=404, detail="Command not found.")
    return resolve_template(cmd["template"])


@router.post("/commands/{command_id}/execute")
def execute_command(
    command_id: str,
    body: Optional[Dict] = Body(default=None),
    user=Depends(_require_settings_read),
):
    cmd = _cmd_db.get_by_id(command_id)
    if cmd is None:
        raise HTTPException(status_code=404, detail="Command not found.")
    resolved = resolve_template(cmd["template"])["resolved"]
    executed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Merge env_overrides (e.g. FLOWGATE_TOKEN, FLOWGATE_SCRATCH) into the subprocess env
    env = None
    env_overrides: Optional[Dict[str, str]] = (body or {}).get("env_overrides")
    if env_overrides:
        env = {**os.environ, **{str(k): str(v) for k, v in env_overrides.items()}}

    try:
        result = subprocess.run(
            resolved,
            shell=True,
            capture_output=True,
            timeout=60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail={"detail": "command timed out after 60s", "resolved": resolved},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"detail": str(exc), "resolved": resolved},
        )
    return {
        "command_id": command_id,
        "resolved": resolved,
        "stdout": _safe_decode(result.stdout),
        "stderr": _safe_decode(result.stderr),
        "return_code": result.returncode,
        "executed_at": executed_at,
    }

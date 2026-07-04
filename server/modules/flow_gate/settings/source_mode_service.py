"""Local/remote source-mode settings.

Global mode lives in system_settings.source_mode. Project override is mapped to
project_settings.source_mode_override because the live project_settings table is
project-scoped, not a generic key/value table.
"""
from __future__ import annotations

from modules.flow_gate.db import projects as _projects
from modules.flow_gate.db import system_settings as _system_settings

MODE_LOCAL = "local"
MODE_REMOTE = "remote"
MODE_DOMAIN = {MODE_LOCAL, MODE_REMOTE}
SETTING_KEY = "source_mode"
DEFAULT_GLOBAL_MODE = MODE_REMOTE


def _valid_mode(value: object) -> bool:
    return value in MODE_DOMAIN


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text in MODE_DOMAIN else None


def get_global_mode() -> str:
    value = _system_settings.get_value(SETTING_KEY)
    return _clean(value) or DEFAULT_GLOBAL_MODE


def set_global_mode(mode: str, updated_by: str | None = None) -> dict:
    if not _valid_mode(mode):
        raise ValueError("mode must be one of: local, remote")
    _system_settings.set_value(
        SETTING_KEY,
        mode,
        "string",
        "Default project source access mode",
        updated_by=updated_by,
    )
    return {"ok": True, "scope": "global", "mode": mode}


def get_project_override(project_id: str) -> str | None:
    row = _projects.get_settings(project_id)
    if not row:
        return None
    return _clean(row.get("source_mode_override"))


def resolve_effective_mode(project_id: str) -> str:
    override = get_project_override(project_id)
    if override:
        return override
    return get_global_mode()


def include_remote_api_section(project_id: str) -> bool:
    return resolve_effective_mode(project_id) == MODE_REMOTE


def get_project_mode(project_id: str) -> dict:
    if _projects.get_by_id(project_id) is None:
        raise LookupError(f"project not found: {project_id}")
    global_mode = get_global_mode()
    override = get_project_override(project_id)
    return {
        "ok": True,
        "scope": "project",
        "project": project_id,
        "override": override,
        "global_mode": global_mode,
        "effective_mode": override or global_mode,
    }


def set_project_mode(project_id: str, override: str | None) -> dict:
    if _projects.get_by_id(project_id) is None:
        raise LookupError(f"project not found: {project_id}")
    if override is not None and not _valid_mode(override):
        raise ValueError("override must be one of: local, remote, null")

    current = _projects.get_settings(project_id) or {}
    merged = {**current, "project_id": project_id, "source_mode_override": override}
    _projects.upsert_settings(project_id, merged)
    return get_project_mode(project_id)

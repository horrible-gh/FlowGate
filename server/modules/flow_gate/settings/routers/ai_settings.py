"""AI settings API router (flowgate.default.0164 P0003).

GET  /api/v1/system/ai-settings                       — global settings (+ catalog)
PUT  /api/v1/system/ai-settings                       — global full save
GET  /api/v1/projects/{project_id}/ai-settings        — project settings (+ effective)
PUT  /api/v1/projects/{project_id}/ai-settings        — project save (incl. mode switch)
GET  /api/v1/projects/{project_id}/ai-settings/effective — effective chain only
GET  /api/v1/system/ai-providers                      — system AI, read-only, any signed-in user
GET  /api/v1/me/ai-providers                          — the caller's own AI (rev7)
PUT  /api/v1/me/ai-providers                          — full save of the caller's own AI
PUT  /api/v1/me/ai-default                            — the caller's default AI (null = system default)

flowgate.v02.0017 TR0003 rev7: projects no longer own connection rows; a custom list save and the
project command probe answer 409 project_owned_ai_retired. CH0006 (4) retired the project AI
policy as well: the project PUT accepts only "inherit" (409 project_ai_policy_retired otherwise).

api_key is write-only: requests may carry it (omit/null = keep, "" = delete,
value = replace); responses only ever carry api_key_set + api_key_hint (L0004 §2.3).
Validation failures return 422 {code: "validation_failed", errors: [...]} (P0003).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import ai_provider_probe_service as _probe
from modules.flow_gate.settings import ai_settings_service as _svc

router = APIRouter(tags=["AiSettings"])


class ProviderIn(BaseModel):
    id: str | None = None
    name: str = ""
    exec_type: str = ""
    kind: str = ""
    enabled: bool = True
    cli_command: str | None = None
    api_base_url: str | None = None
    api_model: str | None = None
    api_key: str | None = None
    project_access: str | None = None
    exec_location: str | None = None


class SystemAiSettingsPut(BaseModel):
    providers: list[ProviderIn] | None = None
    default_provider_id: str | None = None
    default_provider_index: int | None = None


class ProviderProbeIn(BaseModel):
    """Current editor form values for a connection test (0281 T0005 / NR0003 R1).

    Carries the live form — not a saved provider id — so the operator can test BEFORE
    saving, exactly as the Git settings test-connection does (GitSettingsView P0005 §3-2).
    """

    exec_type: str = "cli"
    kind: str = ""
    cli_command: str | None = None
    api_base_url: str | None = None
    prompt: str | None = None


class CliPresetCommandIn(BaseModel):
    """Inputs for the canonical CLI command builder (flowgate.default.0519 T0007 §1).

    Read-only: this never touches a stored provider row. kind/model_name/skip_permissions
    map straight onto ai_settings_service.build_preset_command()'s own parameters.
    """

    kind: str = ""
    model_name: str | None = None
    skip_permissions: bool = False


class ProjectAiSettingsPut(SystemAiSettingsPut):
    mode: str


class UserAiSettingsPut(BaseModel):
    providers: list[ProviderIn] | None = None


class UserAiDefaultPut(BaseModel):
    """null = follow the system default."""

    provider_id: str | None = None


class DoctypeAssignmentIn(BaseModel):
    doc_type: str = ""
    provider_id: str = ""


class DoctypeProvidersPut(BaseModel):
    """Full-replace the project's "document type -> provider" assignment rules (0317 D0004).
    An empty list clears the map (back to the single default-provider behavior)."""

    assignments: list[DoctypeAssignmentIn] | None = None


def _providers_payload(body: SystemAiSettingsPut) -> list[dict] | None:
    if body.providers is None:
        return None
    return [p.model_dump() for p in body.providers]


def _validation_error(exc: _svc.AiSettingsValidationError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "validation_failed", "errors": exc.errors},
    )


def _conflict(exc: _svc.AiSettingsConflict) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": exc.code})


def _preset_command_payload(command: str | None) -> dict:
    """None means the kind has no preset (custom, or anything unrecognized) — answered
    as the same 422 validation_failed shape as an invalid model_name (T0007 §1)."""
    if command is None:
        raise _validation_error(
            _svc.AiSettingsValidationError([{"field": "kind", "reason": "unsupported_kind"}])
        )
    return {"ok": True, "cli_command": command}


@router.get("/system/ai-settings")
def get_system_ai_settings(
    user=Depends(require_permission("system.settings.manage")),
):
    return _svc.get_system_settings()


@router.put("/system/ai-settings")
def put_system_ai_settings(
    body: SystemAiSettingsPut,
    user=Depends(require_permission("system.settings.manage")),
):
    try:
        return _svc.save_system_settings(
            _providers_payload(body),
            body.default_provider_id,
            body.default_provider_index,
            updated_by=user.get("user_id"),
        )
    except _svc.AiSettingsValidationError as exc:
        raise _validation_error(exc)


@router.get("/projects/{project_id}/ai-settings")
def get_project_ai_settings(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    try:
        return _svc.get_project_settings(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/projects/{project_id}/ai-settings")
def put_project_ai_settings(
    project_id: str,
    body: ProjectAiSettingsPut,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        return _svc.save_project_settings(
            project_id,
            body.mode,
            _providers_payload(body),
            body.default_provider_id,
            body.default_provider_index,
            updated_by=user.get("user_id"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except _svc.AiSettingsValidationError as exc:
        raise _validation_error(exc)
    except _svc.AiSettingsConflict as exc:
        raise _conflict(exc)


@router.get("/system/ai-providers")
def get_visible_system_ai(user=Depends(get_current_user)):
    """System AI as a read-only list for the AI 관리 screen (no commands or keys)."""
    return _svc.get_visible_system_ai()


@router.get("/me/ai-providers")
def get_my_ai_providers(user=Depends(get_current_user)):
    """The caller's own AI. There is no route that reads another user's list."""
    return _svc.get_user_settings(user["user_id"])


@router.put("/me/ai-providers")
def put_my_ai_providers(body: UserAiSettingsPut, user=Depends(get_current_user)):
    try:
        return _svc.save_user_settings(user["user_id"], _providers_payload(body))
    except _svc.AiSettingsValidationError as exc:
        raise _validation_error(exc)


@router.put("/me/ai-default")
def put_my_ai_default(body: UserAiDefaultPut, user=Depends(get_current_user)):
    try:
        return _svc.save_user_default(user["user_id"], body.provider_id)
    except _svc.AiSettingsValidationError as exc:
        raise _validation_error(exc)


@router.post("/me/ai-providers/cli-preset-command")
def build_my_cli_preset_command(body: CliPresetCommandIn, user=Depends(get_current_user)):
    try:
        command = _svc.build_preset_command(
            body.kind, body.model_name, skip_permissions=body.skip_permissions,
        )
    except _svc.AiSettingsValidationError as exc:
        raise _validation_error(exc)
    return _preset_command_payload(command)


@router.post("/system/ai-settings/test-provider")
def test_system_ai_provider(
    body: ProviderProbeIn,
    user=Depends(require_permission("system.settings.manage")),
):
    """Launch the form's cli_command on this host with a short timeout and report exit
    code + stderr tail (0281 T0005 R1). Diagnostic only — never persists anything."""
    return _probe.probe_provider(body.model_dump())


@router.post("/projects/{project_id}/ai-settings/test-provider")
def test_project_ai_provider(
    project_id: str,
    body: ProviderProbeIn,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Retired with project-owned AI: a project editor may no longer launch an arbitrary
    command on the server. System AI is probed from the system settings route."""
    raise _conflict(_svc.AiSettingsConflict("project_owned_ai_retired"))


@router.post("/system/ai-settings/cli-preset-command")
def build_system_cli_preset_command(
    body: CliPresetCommandIn,
    user=Depends(require_permission("system.settings.manage")),
):
    """Canonical claude/codex/copilot command for kind + model_name + permission state
    (flowgate.default.0519 T0007 §1). Always calls ai_settings_service.build_preset_command()
    — this router never reimplements a CLI's flags. Read-only: never saves a provider row;
    the editor only persists the result through the ordinary settings PUT."""
    try:
        command = _svc.build_preset_command(
            body.kind, body.model_name, skip_permissions=body.skip_permissions,
        )
    except _svc.AiSettingsValidationError as exc:
        raise _validation_error(exc)
    return _preset_command_payload(command)


@router.post("/projects/{project_id}/ai-settings/cli-preset-command")
def build_project_cli_preset_command(
    project_id: str,
    body: CliPresetCommandIn,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        command = _svc.build_preset_command_for_project(
            project_id, body.kind, body.model_name, skip_permissions=body.skip_permissions,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except _svc.AiSettingsValidationError as exc:
        raise _validation_error(exc)
    return _preset_command_payload(command)


@router.get("/projects/{project_id}/ai-settings/effective")
def get_project_ai_settings_effective(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    try:
        return _svc.resolve_effective(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/projects/{project_id}/ai-doctype-providers")
def get_project_ai_doctype_providers(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """The continuous chain's per-document-type provider assignment rules + the effective provider
    options the UI renders (0317 D0004 §6)."""
    try:
        return _svc.get_doctype_providers(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/projects/{project_id}/ai-doctype-providers")
def put_project_ai_doctype_providers(
    project_id: str,
    body: DoctypeProvidersPut,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    assignments = None if body.assignments is None else [a.model_dump() for a in body.assignments]
    try:
        return _svc.save_doctype_providers(
            project_id, assignments, updated_by=user.get("user_id"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except _svc.AiSettingsValidationError as exc:
        raise _validation_error(exc)

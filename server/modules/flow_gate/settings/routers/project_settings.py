"""Project settings API router (D018 r1 §C, D-3).

GET/POST/PATCH/DELETE /api/v1/projects/{project_id}/document-types
POST                  /api/v1/projects/{project_id}/templates
GET/PATCH             /api/v1/projects/{project_id}/settings
GET/PATCH             /api/v1/projects/{project_id}/paths  (→ settings alias)
GET                   /api/v1/projects/{project_id}/numbering/impact
POST                  /api/v1/projects/{project_id}/numbering/migrate
POST                  /api/v1/projects/{project_id}/numbering/verify
GET                   /api/v1/jobs/{job_id}/status
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.settings.project_settings_service import (
    KoFallbackProtected,
    create_document_type,
    delete_document_type,
    delete_template,
    delete_template_content,
    enqueue_numbering_migrate,
    get_numbering_impact,
    get_numbering_job,
    get_project_settings,
    get_template_content,
    get_template_registry,
    list_document_types,
    list_template_contents,
    list_templates,
    put_template_content,
    register_template_content,
    update_document_type,
    update_project_settings,
    update_template,
    verify_numbering,
)
from modules.flow_gate.settings import source_mode_service
from modules.flow_gate import template_provision as _tp
from modules.flow_gate.db import projects as projects_db
from modules.flow_gate.db import messages as messages_db
from modules.flow_gate.utils.slug import project_name_to_slug
from modules.flow_gate.utils.id_validators import validate_project_id
from modules.flow_gate.storage.migration import apply_storage_change

router = APIRouter(tags=["ProjectSettings"])


@router.get("/projects")
def list_projects_endpoint(user=Depends(get_current_user)):
    """List all active projects, each with its modules (project_modules = SSOT, M036)."""
    projects = projects_db.list_projects(is_active=1)
    for p in projects:
        modules = projects_db.list_modules(p["project_id"])
        p["modules"] = [
            {"name": m["name"], "title": m.get("title") or m["name"]}
            for m in modules
        ]
    return {"projects": projects}


class ProjectCreate(BaseModel):
    project_name: str
    project_id: str | None = None
    description: str | None = None
    color: str | None = None


@router.post("/projects", status_code=201)
def create_project_endpoint(
    body: ProjectCreate,
    user=Depends(get_current_user),
):
    if projects_db.get_by_name(body.project_name):
        raise HTTPException(
            status_code=422,
            detail=f"A project with the same name already exists: {body.project_name}",
        )
    if body.project_id:
        try:
            project_id = validate_project_id(body.project_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    else:
        try:
            project_id = project_name_to_slug(body.project_name)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Failed to convert project name: {e}")
    if projects_db.get_by_id(project_id):
        raise HTTPException(
            status_code=422,
            detail=f"Slug conversion result conflicts with an existing project ID: {project_id!r}",
        )
    project = projects_db.create({
        "project_id": project_id,
        "project_name": body.project_name,
        "description": body.description or "",
        "color": body.color,
        "is_active": 1,
    })
    return project


def _doc_type_to_view(row: dict) -> dict:
    """Map a DB row to frontend field names."""
    template_path = row.get("template_path")
    return {
        "id": row.get("id"),
        "project_id": row.get("project_id"),
        "code": row.get("type_code"),
        "label": row.get("type_name"),
        "category": row.get("series"),
        "color": row.get("color"),
        "template": os.path.basename(template_path) if template_path else None,
        "template_path": template_path,
        "is_system": row.get("is_system"),
        "is_active": row.get("is_active"),
        "sort_order": row.get("sort_order"),
    }


@router.get("/projects/{project_id}/document-types")
def list_doc_types(
    project_id: str,
    locale: str = Query("ko"),
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    types = list_document_types(project_id, locale=locale)
    return {"data": [_doc_type_to_view(t) for t in types]}


class DocTypeCreate(BaseModel):
    # Frontend sends: code, label, category, color, i18n_key
    # Legacy server format: type_code, type_name, series
    code: str | None = None
    type_code: str | None = None
    label: str | None = None
    type_name: str | None = None
    category: str | None = None
    series: str | None = None
    color: str | None = None
    is_active: int = 1
    sort_order: int = 0
    i18n_key: str | None = None  # accepted but not stored (schema removed)


@router.post("/projects/{project_id}/document-types", status_code=201)
def create_doc_type(
    project_id: str,
    body: DocTypeCreate,
    user=Depends(require_permission("project.document_type.create", "project_id")),
):
    resolved_code = body.code or body.type_code
    resolved_name = body.label or body.type_name
    resolved_series = body.category or body.series
    if not resolved_code:
        raise HTTPException(status_code=422, detail="type_code (or code) is required")
    if not resolved_series:
        raise HTTPException(status_code=422, detail="series (or category) is required")
    data = {
        "type_code": resolved_code,
        "type_name": resolved_name,
        "series": resolved_series,
        "color": body.color,
        "is_active": body.is_active,
        "sort_order": body.sort_order,
    }
    try:
        return create_document_type(project_id, data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class DocTypePatch(BaseModel):
    # Frontend sends: code, label, category, color, i18n_key (or just is_active for toggle)
    # Legacy server format: type_name, series, color, is_active, sort_order
    label: str | None = None
    type_name: str | None = None
    category: str | None = None
    series: str | None = None
    color: str | None = None
    is_active: int | None = None
    sort_order: int | None = None
    code: str | None = None      # accepted but ignored (code change not supported)
    i18n_key: str | None = None  # accepted but not stored


@router.patch("/projects/{project_id}/document-types/{type_id}")
def update_doc_type(
    project_id: str,
    type_id: int,
    body: DocTypePatch,
    user=Depends(require_permission("project.document_type.update", "project_id")),
):
    updates: dict = {}
    resolved_name = body.label if body.label is not None else body.type_name
    if resolved_name is not None:
        updates["type_name"] = resolved_name
    resolved_series = body.category if body.category is not None else body.series
    if resolved_series is not None:
        updates["series"] = resolved_series
    for field in ("color", "is_active", "sort_order"):
        val = getattr(body, field)
        if val is not None:
            updates[field] = val
    row = update_document_type(project_id, type_id, updates)
    if not row:
        raise HTTPException(status_code=404, detail="Document type not found")
    return row


@router.delete("/projects/{project_id}/document-types/{type_id}")
def delete_doc_type(
    project_id: str,
    type_id: int,
    user=Depends(require_permission("project.document_type.delete", "project_id")),
):
    try:
        if not delete_document_type(project_id, type_id):
            raise HTTPException(status_code=404, detail="Document type not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"detail": "deleted"}


# ─────────────────────────────────────────────────────────────────────────────
# Project messages (R0001 group 0004; P0006 §3-5, L0007, DB0008).
# Settings > Project management > Message management CRUD + mention-add dialog query.
# Reuses the project.settings.* RBAC keys (P0006 §3.1); dedicated message keys
# were DEFERRED by DB0008. [All] travels as doc_type="*" with no transform (§3.4).
# ─────────────────────────────────────────────────────────────────────────────

def _message_to_view(row: dict) -> dict:
    """Map a project_messages row to the P0006 §3.3 wire shape (project -> project_id)."""
    return {
        "id": row.get("id"),
        "project_id": row.get("project"),
        "doc_type": row.get("doc_type"),
        "message": row.get("message"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/projects/{project_id}/messages")
def list_messages(
    project_id: str,
    doc_type: str | None = Query(None),
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Management list (no doc_type) or mention-add dialog union (doc_type given).

    With doc_type: returns the requested type + [All]('*') union (P0006 §4.5).
    Display order/priority/dedup is L0007's concern; this returns the wire set as-is.
    """
    if doc_type is not None:
        rows = messages_db.list_for_dialog(project_id, doc_type)
    else:
        rows = messages_db.list_by_project(project_id)
    return {"data": [_message_to_view(r) for r in rows]}


class MessageCreate(BaseModel):
    doc_type: str | None = None
    message: str | None = None


@router.post("/projects/{project_id}/messages", status_code=201)
def create_message(
    project_id: str,
    body: MessageCreate,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    doc_type = (body.doc_type or "").strip()
    message = (body.message or "").strip()
    if not doc_type:
        raise HTTPException(status_code=422, detail="doc_type is required")
    if not message:
        raise HTTPException(status_code=422, detail="message is required")
    row = messages_db.create(project_id, doc_type, message)
    return _message_to_view(row)


class MessagePatch(BaseModel):
    doc_type: str | None = None
    message: str | None = None


@router.patch("/projects/{project_id}/messages/{message_id}")
def update_message_endpoint(
    project_id: str,
    message_id: int,
    body: MessagePatch,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    updates: dict = {}
    if body.doc_type is not None:
        doc_type = body.doc_type.strip()
        if not doc_type:
            raise HTTPException(status_code=422, detail="doc_type must not be empty")
        updates["doc_type"] = doc_type
    if body.message is not None:
        message = body.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="message must not be empty")
        updates["message"] = message
    row = messages_db.update(message_id, updates)
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    return _message_to_view(row)


@router.delete("/projects/{project_id}/messages/{message_id}")
def delete_message_endpoint(
    project_id: str,
    message_id: int,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    if not messages_db.delete(message_id):
        raise HTTPException(status_code=404, detail="Message not found")
    return {"detail": "deleted"}


# ── Template-body provision (group 0024 — P0012 G1/G2) ───────────────────────
# Registered BEFORE the /templates/{template_id}/... management routes so the
# reserved literal `active` is never mistaken for a numeric {template_id}.

def _resolve_locale(request: Request, x_locale: str | None) -> str:
    header = x_locale if x_locale is not None else request.headers.get("x-locale")
    return _tp.normalize_locale(header)


@router.get("/projects/{project_id}/templates/active/{type_code}")
def get_active_template(
    project_id: str,
    type_code: str,
    request: Request,
    x_locale: str | None = Header(default=None, alias="x-locale"),
    user=Depends(require_permission("project.document.create", "project_id")),
):
    """G1 — resolve and return the active template body for (type, x-locale)."""
    req_locale = _resolve_locale(request, x_locale)
    try:
        r = _tp.resolve_active_template(project_id, type_code, req_locale)
    except _tp.UnknownDesignType:
        raise HTTPException(status_code=404, detail=f"Unknown document type '{type_code}'")
    return {
        "project_id": project_id,
        "type_code": type_code,
        "requested_locale": req_locale,
        "resolved_locale": r["resolved_locale"],
        "resolution": r["resolution"],
        "scope": r["scope"],
        "is_active": r["is_active"],
        "bytes": r["bytes"],
        "content": r["content"],
    }


@router.get("/projects/{project_id}/templates/active/{type_code}/meta")
def get_active_template_meta(
    project_id: str,
    type_code: str,
    request: Request,
    x_locale: str | None = Header(default=None, alias="x-locale"),
    user=Depends(require_permission("project.document.create", "project_id")),
):
    """G2 — resolution meta (no body): resolved_locale / available_locales / bytes."""
    req_locale = _resolve_locale(request, x_locale)
    try:
        return _tp.resolve_active_meta(project_id, type_code, req_locale)
    except _tp.UnknownDesignType:
        raise HTTPException(status_code=404, detail=f"Unknown document type '{type_code}'")


# ── Template-body management (group 0024 — P0011 E1~E7) ──────────────────────

@router.get("/projects/{project_id}/templates")
def list_tpls(
    project_id: str,
    type_code: str | None = Query(None),
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """E1 — registry rows for the project (+ held locales per row)."""
    return {"templates": list_templates(project_id, type_code)}


class TemplateContentBody(BaseModel):
    type_code: str
    locale: str
    content: str


@router.post("/projects/{project_id}/templates", status_code=201)
def register_tpl(
    project_id: str,
    body: TemplateContentBody,
    user=Depends(require_permission("project.document_type.update", "project_id")),
):
    """E4 — JSON body registration: ensure registry (is_active=0) + first body."""
    try:
        return register_template_content(
            project_id, body.type_code, body.locale, body.content,
            user_id=user.get("user_id"),
        )
    except _tp.TemplateValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/projects/{project_id}/templates/{template_id}/contents")
def list_tpl_contents(
    project_id: str,
    template_id: int,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """E2 — locale body metadata (no content)."""
    rows = list_template_contents(project_id, template_id)
    if rows is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template_id": template_id, "contents": rows}


@router.get("/projects/{project_id}/templates/{template_id}/contents/{locale}")
def get_tpl_content(
    project_id: str,
    template_id: int,
    locale: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """E3 — single locale body (full content)."""
    row = get_template_content(project_id, template_id, locale)
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return row


class TemplateContentPut(BaseModel):
    content: str


@router.put("/projects/{project_id}/templates/{template_id}/contents/{locale}")
def put_tpl_content(
    project_id: str,
    template_id: int,
    locale: str,
    body: TemplateContentPut,
    user=Depends(require_permission("project.document_type.update", "project_id")),
):
    """E5 — register/replace a locale body (no redeploy, idempotent)."""
    try:
        result = put_template_content(
            project_id, template_id, locale, body.content, user_id=user.get("user_id"),
        )
    except _tp.TemplateValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return result


@router.delete("/projects/{project_id}/templates/{template_id}/contents/{locale}")
def delete_tpl_content(
    project_id: str,
    template_id: int,
    locale: str,
    user=Depends(require_permission("project.document_type.delete", "project_id")),
):
    """E6 — delete a locale body (ko-fallback protected)."""
    try:
        result = delete_template_content(project_id, template_id, locale)
    except KoFallbackProtected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if not result:
        raise HTTPException(status_code=404, detail="Template content not found")
    return {"detail": "deleted", "template_id": template_id, "locale": locale}


class TemplatePatch(BaseModel):
    is_active: int | None = None


@router.patch("/projects/{project_id}/templates/{template_id}")
def update_tpl(
    project_id: str,
    template_id: int,
    body: TemplatePatch,
    user=Depends(require_permission("project.document_type.update", "project_id")),
):
    """E7 — activation toggle (existing contract)."""
    row = update_template(project_id, template_id, body.model_dump())
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return row


@router.delete("/projects/{project_id}/templates/{template_id}")
def delete_tpl(
    project_id: str,
    template_id: int,
    user=Depends(require_permission("project.document_type.delete", "project_id")),
):
    if not delete_template(project_id, template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"detail": "deleted"}


@router.get("/projects/{project_id}/settings")
@router.get("/projects/{project_id}/paths")
def get_settings(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    row = get_project_settings(project_id)
    return row or {}


class ProjectSettingsPatch(BaseModel):
    group_structure: int | None = None
    digits_group: int | None = None
    digits_sub_group: int | None = None
    digits_type: int | None = None
    storage_root_override: str | None = None
    source_mode_override: str | None = None


class GlobalSourceModeBody(BaseModel):
    mode: str


class ProjectSourceModeBody(BaseModel):
    override: str | None = None


def _source_mode_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {"code": code, "message": message}},
    )


@router.get("/settings/mode")
def get_global_source_mode(user=Depends(require_permission("system.settings.manage"))):
    return {"ok": True, "scope": "global", "mode": source_mode_service.get_global_mode()}


@router.put("/settings/mode")
def put_global_source_mode(
    body: GlobalSourceModeBody,
    user=Depends(require_permission("system.settings.manage")),
):
    try:
        return source_mode_service.set_global_mode(body.mode, updated_by=user.get("user_id"))
    except ValueError as exc:
        return _source_mode_error(422, "invalid_request", str(exc))


@router.get("/settings/project/{project_id}/mode")
def get_project_source_mode(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    try:
        return source_mode_service.get_project_mode(project_id)
    except LookupError as exc:
        return _source_mode_error(404, "not_found", str(exc))


@router.put("/settings/project/{project_id}/mode")
def put_project_source_mode(
    project_id: str,
    body: ProjectSourceModeBody,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        return source_mode_service.set_project_mode(project_id, body.override)
    except LookupError as exc:
        return _source_mode_error(404, "not_found", str(exc))
    except ValueError as exc:
        return _source_mode_error(422, "invalid_request", str(exc))


@router.patch("/projects/{project_id}/settings")
@router.patch("/projects/{project_id}/paths")
def patch_settings(
    project_id: str,
    body: ProjectSettingsPatch,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    updates = body.model_dump(exclude_unset=True)
    try:
        return update_project_settings(project_id, updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/projects/{project_id}/numbering/impact")
def numbering_impact(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    return get_numbering_impact(project_id)


class NumberingMigrateBody(BaseModel):
    target: str
    from_width: int
    to_width: int


@router.post("/projects/{project_id}/numbering/migrate", status_code=202)
def numbering_migrate(
    project_id: str,
    body: NumberingMigrateBody,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        job = enqueue_numbering_migrate(
            project_id,
            body.target,
            body.from_width,
            body.to_width,
            requested_by=user.get("user_id", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"job": job}


@router.post("/projects/{project_id}/numbering/verify")
def numbering_verify(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    return verify_numbering(project_id)


@router.get("/jobs/{job_id}/status")
def job_status(job_id: int, user=Depends(get_current_user)):
    row = get_numbering_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


class StorageMigrateBody(BaseModel):
    new_root: str
    confirm: bool = False


@router.post("/projects/{project_id}/storage/migrate")
def storage_migrate(
    project_id: str,
    body: StorageMigrateBody,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Change project storage path + migrate old data + verify + permanently delete legacy.

    Synchronous call. Response includes per-stage results.
    On verification failure, legacy data is preserved and ok=False.
    """
    if not body.confirm:
        raise HTTPException(status_code=422, detail="confirm=true is required")
    new_root = (body.new_root or "").strip()
    if not new_root:
        raise HTTPException(status_code=422, detail="new_root is empty")
    try:
        result = apply_storage_change(project_id, new_root)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result

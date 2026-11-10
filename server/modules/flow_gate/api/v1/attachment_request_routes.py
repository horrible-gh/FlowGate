"""User side of AI attachment upload requests (flowgate.chat.0017 CH0002). Mounted under ``/api/v1``.

Session (Console user) routes::

    GET  /attachment-requests?chat_doc_id=                 a chat's requests, grouped by run, anchored
    GET  /attachment-requests?flow_id=&node_key=[&all=1]   one card's requests (open ones by default)
    GET  /attachment-requests/counts?flow_id=              open requests per card of a Flow
    GET  /attachment-requests/limits?project_id=           the effective limits (system ?? project)
    GET  /attachment-requests/{request_id}                 one request
    GET  /attachment-requests/{request_id}/preview         text (<= 1 MiB) / image kind / binary
    GET  /attachment-requests/{request_id}/content         download (``?inline=1``: verified raster image only)
    POST /attachment-requests/{request_id}/decision        {"decision": "approve"|"reject", "expected_doc_id",
                                                            "conflict": "keep_both"|"overwrite",
                                                            "expected_existing_sha256"}
    POST /attachment-requests/decisions                    {"decision", "items": [{"request_id", "expected_doc_id"}]}
                                                           approve all / reject all / the selected ones: one
                                                           result per request (done | skipped | failed)
    GET  /settings/attachment-requests                     system defaults (administrators)
    PUT  /settings/attachment-requests
    GET  /projects/{project_id}/attachment-request-settings  project overrides (administrators)
    PUT  /projects/{project_id}/attachment-request-settings

The AI side (``POST /api/v1/attachment-requests``, Bearer = a live run token) lives in
``document_routes`` next to the other worker attachment routes.

Reads need ``perm_document_read`` and a decision ``perm_document_update`` on the request's
project — any such user, whoever started the run and whatever screen mode they are in. The
decision route is exempt from the group AI-run lease (``mutation_policy``), so it answers while
an AI works in the same group. All handlers are plain ``def`` (blocking store / filesystem work
runs in FastAPI's thread pool).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import FileResponse, JSONResponse

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.documents.attachments import request_settings
from modules.flow_gate.documents.attachments import requests as svc

router = APIRouter(tags=["AttachmentRequests"])


def _error(exc: svc.RequestError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


def _settings_error(exc: request_settings.SettingsError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"ok": False, "error": {
        "code": "attachment_request_settings_invalid", "message": exc.message, "details": {"field": exc.field}}})


def _require_admin(user: dict) -> None:
    if not svc.has_permission(user, "system.settings.manage", None):
        raise svc.RequestError(403, "attachment_request_forbidden", "administrators only")


@router.get("/attachment-requests")
def list_attachment_requests(
    chat_doc_id: Optional[str] = Query(default=None),
    flow_id: Optional[str] = Query(default=None),
    node_key: Optional[str] = Query(default=None),
    all: bool = Query(default=False),  # noqa: A002 - wire name
    user: dict = Depends(get_current_user),
):
    try:
        if chat_doc_id:
            return svc.list_for_chat(chat_doc_id, user)
        if flow_id and node_key:
            return svc.list_for_node(flow_id, node_key, user, include_closed=all)
        raise svc.RequestError(422, "attachment_request_invalid", "chat_doc_id, or flow_id with node_key, is required")
    except svc.RequestError as exc:
        return _error(exc)


@router.get("/attachment-requests/counts")
def attachment_request_counts(flow_id: str = Query(...), user: dict = Depends(get_current_user)):
    try:
        return svc.pending_counts(flow_id, user)
    except svc.RequestError as exc:
        return _error(exc)


@router.get("/attachment-requests/limits")
def attachment_request_limits(project_id: str = Query(...), user: dict = Depends(get_current_user)):
    try:
        svc.require_permission(user, "perm_document_read", project_id)
        data = request_settings.effective_with_sources(project_id)
        return {"ok": True, "project_id": project_id, **data}
    except svc.RequestError as exc:
        return _error(exc)


@router.get("/attachment-requests/{request_id}")
def get_attachment_request(request_id: str, user: dict = Depends(get_current_user)):
    try:
        return {"ok": True, "request": svc.public(svc.readable_row(request_id, user))}
    except svc.RequestError as exc:
        return _error(exc)


@router.get("/attachment-requests/{request_id}/preview")
def preview_attachment_request(request_id: str, user: dict = Depends(get_current_user)):
    try:
        return svc.preview(request_id, user)
    except svc.RequestError as exc:
        return _error(exc)


@router.get("/attachment-requests/{request_id}/content")
def download_attachment_request(request_id: str, inline: bool = Query(default=False),
                                user: dict = Depends(get_current_user)):
    try:
        path, meta = svc.content(request_id, user, inline=inline)
    except svc.RequestError as exc:
        return _error(exc)
    return FileResponse(path=str(path), media_type=meta["media_type"], headers=meta["headers"])


@router.post("/attachment-requests/decisions")
def decide_attachment_requests(body: Any = Body(default=None), user: dict = Depends(get_current_user)):
    try:
        return svc.decide_many(user, body)
    except svc.RequestError as exc:
        return _error(exc)


@router.post("/attachment-requests/{request_id}/decision")
def decide_attachment_request(request_id: str, body: Any = Body(default=None),
                              user: dict = Depends(get_current_user)):
    try:
        updated = svc.decide(request_id, user, body)
        return {"ok": True, "request": svc.public(updated)}
    except svc.RequestError as exc:
        return _error(exc)


@router.get("/settings/attachment-requests")
def get_system_attachment_request_settings(user: dict = Depends(get_current_user)):
    try:
        _require_admin(user)
        return {"ok": True, **request_settings.system_view()}
    except svc.RequestError as exc:
        return _error(exc)


@router.put("/settings/attachment-requests")
def put_system_attachment_request_settings(body: Any = Body(default=None), user: dict = Depends(get_current_user)):
    try:
        _require_admin(user)
        values = (body or {}).get("values") if isinstance(body, dict) else None
        request_settings.save_system(values, user.get("user_id"))
        return {"ok": True, **request_settings.system_view()}
    except request_settings.SettingsError as exc:
        return _settings_error(exc)
    except svc.RequestError as exc:
        return _error(exc)


def _project_exists(project_id: str) -> None:
    from modules.flow_gate.db import projects as db_projects

    if db_projects.get_by_id(project_id) is None:
        raise svc.RequestError(404, "attachment_request_project_not_found", "project not found")


@router.get("/projects/{project_id}/attachment-request-settings")
def get_project_attachment_request_settings(project_id: str, user: dict = Depends(get_current_user)):
    try:
        _require_admin(user)
        _project_exists(project_id)
        return {"ok": True, **request_settings.project_view(project_id)}
    except svc.RequestError as exc:
        return _error(exc)


@router.put("/projects/{project_id}/attachment-request-settings")
def put_project_attachment_request_settings(project_id: str, body: Any = Body(default=None),
                                            user: dict = Depends(get_current_user)):
    try:
        _require_admin(user)
        _project_exists(project_id)
        values = (body or {}).get("overrides") if isinstance(body, dict) else None
        request_settings.save_project(project_id, values, user.get("user_id"))
        return {"ok": True, **request_settings.project_view(project_id)}
    except request_settings.SettingsError as exc:
        return _settings_error(exc)
    except svc.RequestError as exc:
        return _error(exc)

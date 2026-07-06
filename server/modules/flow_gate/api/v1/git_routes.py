"""Git integration API (flowgate.default.0115 — P0005).

GET/PUT/DELETE /api/v1/projects/{project_id}/git/config
POST           /api/v1/projects/{project_id}/git/test-connection
GET/POST       /api/v1/projects/{project_id}/git/provision   (0161 P0004)
GET/POST       /api/v1/groups/{group_id}/git/finalize
GET            /api/v1/groups/{group_id}/git/merge/{merge_id}/conflicts
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/resolve
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/abort

RBAC (P0005 공통): read = project.settings.read, mutate = project.settings.edit.
Group-scoped routes resolve the project from the group_id prefix and check the
same keys manually (require_permission needs a path param named project_id).
Errors follow the source-mode envelope {"ok": false, "error": {code, message}}.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac.decorators import _has_permission, require_permission
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git_service import GitServiceError

router = APIRouter(prefix="/api/v1", tags=["Git"])


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {"code": code, "message": message}},
    )


def _guard(exc: GitServiceError) -> JSONResponse:
    return _error_response(exc.status, exc.code, exc.message)


def _check_group_permission(user: dict, group_id: str, permission: str) -> Optional[JSONResponse]:
    project_id = (group_id or "").split(".", 1)[0]
    if not _has_permission(user, permission, project_id):
        return _error_response(403, "forbidden", "Forbidden")
    return None


# ── Project config (P0005 §1·§2) ─────────────────────────────────────────────

@router.get("/projects/{project_id}/git/config")
def get_git_config(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    try:
        return git_service.get_config_view(project_id)
    except GitServiceError as exc:
        return _guard(exc)


class GitConfigPut(BaseModel):
    repo_url: str
    provider: str | None = None
    username: str | None = None
    # None (or omitted) = keep the stored secret, "" = clear it (P0005 §2-1).
    secret: str | None = None
    base_branch: str | None = None
    default_finalize_action: str | None = None
    enabled: bool = False


@router.put("/projects/{project_id}/git/config")
def put_git_config(
    project_id: str,
    body: GitConfigPut,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        return git_service.save_config(project_id, body.model_dump(exclude_unset=True))
    except GitServiceError as exc:
        return _guard(exc)


@router.delete("/projects/{project_id}/git/config")
def delete_git_config(
    project_id: str,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        return git_service.delete_config(project_id)
    except GitServiceError as exc:
        return _guard(exc)


class GitTestBody(BaseModel):
    repo_url: str | None = None
    username: str | None = None
    secret: str | None = None
    base_branch: str | None = None
    provider: str | None = None


@router.post("/projects/{project_id}/git/test-connection")
def test_git_connection(
    project_id: str,
    body: GitTestBody | None = None,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        override = body.model_dump(exclude_unset=True) if body is not None else {}
        result = git_service.test_connection(project_id, override)
        return {"ok": True, "result": result}
    except GitServiceError as exc:
        return _guard(exc)


# ── Base provisioning status / manual trigger (0161 P0004) ──────────────────

@router.get("/projects/{project_id}/git/provision")
def get_git_provision(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    try:
        return {"ok": True, "provision": git_service.provision_view(project_id)}
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/projects/{project_id}/git/provision")
def post_git_provision(
    project_id: str,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    # Synchronous by design (P0004): clone/fetch runs under GIT_NET_TIMEOUT_SEC;
    # a provisioning failure is a 200 with result.status="failed", not an error.
    try:
        return git_service.provision_manual(project_id)
    except GitServiceError as exc:
        return _guard(exc)


# ── Group finalize (P0005 §5) ────────────────────────────────────────────────

@router.get("/groups/{group_id}/git/finalize")
def get_group_finalize_state(group_id: str, user=Depends(get_current_user)):
    denied = _check_group_permission(user, group_id, "project.settings.read")
    if denied:
        return denied
    try:
        return git_service.get_finalize_state(group_id)
    except GitServiceError as exc:
        return _guard(exc)


class FinalizeBody(BaseModel):
    action: str | None = None  # default = the project's configured default


@router.post("/groups/{group_id}/git/finalize")
def post_group_finalize(
    group_id: str,
    body: FinalizeBody | None = None,
    user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.finalize(group_id, body.action if body else None)
    except GitServiceError as exc:
        return _guard(exc)


# ── Conflict session (P0005 §6) ──────────────────────────────────────────────

@router.get("/groups/{group_id}/git/merge/{merge_id}/conflicts")
def get_merge_conflicts(group_id: str, merge_id: int, user=Depends(get_current_user)):
    denied = _check_group_permission(user, group_id, "project.settings.read")
    if denied:
        return denied
    try:
        return git_service.list_conflicts(group_id, merge_id)
    except GitServiceError as exc:
        return _guard(exc)


class ResolveFile(BaseModel):
    path: str
    content: str


class ResolveBody(BaseModel):
    files: list[ResolveFile] = []
    complete: bool = False


@router.post("/groups/{group_id}/git/merge/{merge_id}/resolve")
def post_merge_resolve(
    group_id: str,
    merge_id: int,
    body: ResolveBody,
    user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.resolve_conflicts(
            group_id, merge_id,
            [f.model_dump() for f in body.files],
            bool(body.complete),
        )
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/groups/{group_id}/git/merge/{merge_id}/abort")
def post_merge_abort(group_id: str, merge_id: int, user=Depends(get_current_user)):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.abort_merge(group_id, merge_id)
    except GitServiceError as exc:
        return _guard(exc)

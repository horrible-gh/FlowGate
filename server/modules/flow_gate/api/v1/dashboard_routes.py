"""Main dashboard summary API."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import JSONResponse

from modules.flow_gate.auth.middleware import get_current_user, verify_token
from modules.flow_gate.db import notification_seen as db_notification_seen
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services.dashboard_service import (
    DashboardDataError,
    get_dashboard_summary,
    get_notification_feed,
)
from modules.flow_gate.utils.help_url import help_url
from modules.flow_gate.utils.id_validators import validate_project_id

router = APIRouter(prefix="/api/v1", tags=["Dashboard"])

_NO_STORE = {"Cache-Control": "no-store"}
_log = logging.getLogger(__name__)
_optional_bearer = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        headers=_NO_STORE,
        content={
            "ok": False,
            "http_status": status,
            "error_message": message,
            "help_url": help_url(),
        },
    )


def _limit(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer between 1 and 50") from exc
    if parsed < 1 or parsed > 50:
        raise ValueError(f"{name} must be between 1 and 50")
    return parsed


def _dashboard_current_user(token: str | None = Depends(_optional_bearer)) -> dict:
    try:
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return get_current_user(verify_token(token))
    except HTTPException as exc:
        headers = dict(exc.headers or {})
        headers.update(_NO_STORE)
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=headers,
        ) from exc


@router.get("/projects/{project_id}/dashboard/summary")
def dashboard_summary(
    request: Request,
    project_id: str,
    activity_limit: str = "10",
    workflow_limit: str = "10",
    current_user: dict = Depends(_dashboard_current_user),
):
    del request
    if project_id == "__SYSTEM__":
        return _fail(404, f"Project {project_id} does not exist")
    try:
        validate_project_id(project_id)
    except ValueError as exc:
        return _fail(422, str(exc))

    project = db_projects.get_by_id(project_id)
    if project is None:
        return _fail(404, f"Project {project_id} does not exist")

    user_id = current_user.get("user_id")
    if not user_id or not has_permission(user_id, project_id, "perm_document_read"):
        return _fail(
            403,
            "Insufficient permissions for this operation "
            "(perm_document_read required)",
        )

    try:
        parsed_activity_limit = _limit(activity_limit, "activity_limit")
        parsed_workflow_limit = _limit(workflow_limit, "workflow_limit")
    except ValueError as exc:
        return _fail(400, str(exc))

    try:
        result = get_dashboard_summary(
            project_id,
            parsed_activity_limit,
            parsed_workflow_limit,
        )
    except DashboardDataError as exc:
        _log.error("Dashboard data integrity failure project=%s error=%s", project_id, exc)
        return _fail(500, "Dashboard data could not be normalized")
    except Exception:
        _log.exception("Dashboard summary failed project=%s", project_id)
        return _fail(500, "Dashboard data lookup failed")

    return JSONResponse(content=result, headers=_NO_STORE)


def _authorize_project(project_id: str, current_user: dict) -> tuple[str | None, JSONResponse | None]:
    """Shared project-existence + read-permission gate for the notification endpoints.

    Returns (user_id, None) when authorized, or (None, failure_response) otherwise — mirroring the
    inline checks in dashboard_summary so the 🔔 feed enforces the exact same access rules.
    """
    if project_id == "__SYSTEM__":
        return None, _fail(404, f"Project {project_id} does not exist")
    try:
        validate_project_id(project_id)
    except ValueError as exc:
        return None, _fail(422, str(exc))
    if db_projects.get_by_id(project_id) is None:
        return None, _fail(404, f"Project {project_id} does not exist")
    user_id = current_user.get("user_id")
    if not user_id or not has_permission(user_id, project_id, "perm_document_read"):
        return None, _fail(
            403,
            "Insufficient permissions for this operation (perm_document_read required)",
        )
    return user_id, None


@router.get("/projects/{project_id}/notifications")
def notifications(
    request: Request,
    project_id: str,
    limit: str = "50",
    current_user: dict = Depends(_dashboard_current_user),
):
    """🔔 notification center feed: persistent document-inflow history + unread count.

    R0001 (group 0045) / NR0003 option A. Reads the same workflow_events the dashboard does, but adds the
    per-user unread watermark so the header bell can show "unread N" without entering the dashboard.
    """
    del request
    user_id, failure = _authorize_project(project_id, current_user)
    if failure is not None:
        return failure

    try:
        parsed_limit = _limit(limit, "limit")
    except ValueError as exc:
        return _fail(400, str(exc))

    try:
        last_seen_at = db_notification_seen.get_last_seen(user_id, project_id)
        result = get_notification_feed(project_id, last_seen_at, parsed_limit)
    except DashboardDataError as exc:
        _log.error("Notification feed integrity failure project=%s error=%s", project_id, exc)
        return _fail(500, "Notification data could not be normalized")
    except Exception:
        _log.exception("Notification feed failed project=%s", project_id)
        return _fail(500, "Notification data lookup failed")

    return JSONResponse(content=result, headers=_NO_STORE)


@router.post("/projects/{project_id}/notifications/seen")
def notifications_seen(
    request: Request,
    project_id: str,
    current_user: dict = Depends(_dashboard_current_user),
):
    """Mark the 🔔 feed as read up to now (clears the unread badge). R0001 group 0045 / NR0003 option A."""
    del request
    user_id, failure = _authorize_project(project_id, current_user)
    if failure is not None:
        return failure

    try:
        last_seen_at = db_notification_seen.mark_seen(user_id, project_id)
    except Exception:
        _log.exception("Notification mark-seen failed project=%s", project_id)
        return _fail(500, "Notification mark-seen failed")

    return JSONResponse(
        content={"ok": True, "project_id": project_id, "last_seen_at": last_seen_at, "unread_count": 0},
        headers=_NO_STORE,
    )

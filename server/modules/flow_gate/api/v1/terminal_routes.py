"""Designer terminal API.

Paths (under ``/flowgate/api/v1/terminal``), fixed with the screen card:

* ``GET /status?flow_id=`` -- anyone signed in; the screen hides its button from it.
* ``POST /sessions`` -- administrators only, ``terminal.enabled`` only; returns a one-time
  30-second ticket for the WebSocket so the login token never appears in a URL.
* ``WS /ws?ticket=`` -- the terminal stream.
* ``DELETE /sessions/{session_id}`` -- ends the session and its whole process tree.
* ``/system/...`` -- administrators only; bundle install, removal, on/off and shell test.

Handlers are plain ``def`` (FastAPI's worker pool) except the WebSocket, whose blocking
steps run through ``anyio.to_thread`` inside ``terminal_sessions.serve_websocket``.
"""
from __future__ import annotations

from functools import wraps
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import terminal_sessions, terminal_service
from modules.flow_gate.services.terminal_sessions import TerminalError

router = APIRouter(prefix="/api/v1/terminal", tags=["Terminal"])


class CreateSessionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    flow_id: str = Field(min_length=1, max_length=255)
    cols: int = Field(default=80, ge=1, le=terminal_sessions.MAX_DIMENSION)
    rows: int = Field(default=24, ge=1, le=terminal_sessions.MAX_DIMENSION)


class EnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def _refused(exc: TerminalError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


@router.get("/status")
def terminal_status(flow_id: Optional[str] = Query(default=None, max_length=255),
                    user: dict = Depends(get_current_user)):
    return terminal_sessions.manager.status(user, flow_id)


@router.post("/sessions", status_code=201)
def create_terminal_session(body: CreateSessionBody, user: dict = Depends(get_current_user)):
    try:
        return terminal_sessions.manager.create(user, body.flow_id, body.cols, body.rows)
    except TerminalError as exc:
        return _refused(exc)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_terminal_session(session_id: str, user: dict = Depends(get_current_user)):
    try:
        terminal_sessions.manager.close(user, session_id)
    except TerminalError as exc:
        return _refused(exc)
    return Response(status_code=204)


@router.websocket("/ws")
async def terminal_socket(websocket: WebSocket, ticket: str = Query(default="", max_length=128)):
    await terminal_sessions.serve_websocket(websocket, ticket)


def admin_only(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        try:
            if not kwargs["user"].get("is_admin"):
                raise TerminalError(403, "terminal_not_admin", "Administrator access required.")
            return handler(*args, **kwargs)
        except TerminalError as exc:
            return _refused(exc)
    return wrapped


@router.get("/system")
@admin_only
def terminal_system(user: dict = Depends(get_current_user)):
    return terminal_service.manager.status()


@router.post("/system/installs", status_code=202)
@admin_only
def install_terminal(user: dict = Depends(get_current_user)):
    return terminal_service.manager.install()


@router.get("/system/installs/{install_id}")
@admin_only
def terminal_install_status(install_id: str, user: dict = Depends(get_current_user)):
    return terminal_service.manager.job(install_id)


@router.delete("/system/installs/{install_id}", status_code=202)
@admin_only
def cancel_terminal_install(install_id: str, user: dict = Depends(get_current_user)):
    return terminal_service.manager.cancel(install_id)


@router.delete("/system/bundle")
@admin_only
def remove_terminal_bundle(user: dict = Depends(get_current_user)):
    return terminal_service.manager.remove()


@router.put("/system/enabled")
@admin_only
def enable_terminal(body: EnabledBody, user: dict = Depends(get_current_user)):
    return terminal_service.manager.enable(body.enabled, user.get("user_id"))


@router.post("/system/test")
@admin_only
def test_terminal_shell(user: dict = Depends(get_current_user)):
    return terminal_service.manager.test()

"""GET/PATCH /api/v1/me/chat-settings — the logged-in user's chat settings (P0009 §0-1).

The address carries no user identifier.  The user comes from the token and only from
the token, so there is no URL that could name somebody else's settings in the first
place — safer than building one and then guarding it (D0008 §5).

Both handlers are plain ``def``.  The store underneath is synchronous, and an ``async``
handler reaching it would block the event loop for every other request (L0010 §5); the
static guard in tests/test_event_loop_blocking_0279.py enforces this.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import chat_settings_service

router = APIRouter(tags=["ChatSettings"])


class ChatSettingsPatch(BaseModel):
    """Whatever the request actually sent, untouched.

    Every field is ``Any`` and extras are kept rather than dropped, because the two
    judgements this endpoint must make are "was this key sent at all" and "is this
    value one we know" — and a typed model answers neither: it would coerce ``"20"``
    into a valid 20 and quietly swallow a misspelled key that L0010 §2-2 wants
    answered with a 422.  ``model_dump(exclude_unset=True)`` therefore yields exactly
    the keys the client wrote, unknown ones included, and the service judges them.
    """

    model_config = ConfigDict(extra="allow")

    send_action: Any = None
    context_mode: Any = None
    context_turns: Any = None


def _invalid_request(exc: chat_settings_service.ChatSettingsError) -> JSONResponse:
    # Same envelope as the neighbouring settings routes (project_settings._source_mode_error),
    # plus the field name so the screen can mark the box that is wrong (P0009 scenario 7).
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "error": {
                "code": "invalid_request",
                "field": exc.field,
                "message": exc.message,
            },
        },
    )


@router.get("/me/chat-settings")
def get_my_chat_settings(user=Depends(get_current_user)):
    return chat_settings_service.settings_response(user["user_id"])


@router.patch("/me/chat-settings")
def patch_my_chat_settings(body: ChatSettingsPatch, user=Depends(get_current_user)):
    patch = body.model_dump(exclude_unset=True)
    try:
        return chat_settings_service.save_chat_settings(user["user_id"], patch)
    except chat_settings_service.ChatSettingsError as exc:
        return _invalid_request(exc)

"""GET/PATCH /api/v1/me/ui-settings — the logged-in user's non-chat UI settings (0452).

The address carries no user identifier.  The user comes from the token and only from the
token, so there is no URL that could name somebody else's settings in the first place —
safer than building one and then guarding it (the same choice 0362 made for
/me/chat-settings).

Both handlers are plain ``def``.  The store underneath is synchronous, and an ``async``
handler reaching it would block the event loop for every other request; the static guard
in tests/test_event_loop_blocking_0279.py enforces this.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import ui_settings_service

router = APIRouter(tags=["UiSettings"])


class UiSettingsPatch(BaseModel):
    """Whatever the request actually sent, untouched.

    Every field is ``Any`` and extras are kept rather than dropped, because the two
    judgements this endpoint must make are "was this key sent at all" and "is this value
    one we know" — and a typed model answers neither: it would coerce ``"30"`` into a
    valid 30 and quietly swallow a misspelled key that L0003 §2-8 wants answered with a
    422.  ``model_dump(exclude_unset=True)`` therefore yields exactly the keys the client
    wrote, unknown ones included, and the service judges them.
    """

    model_config = ConfigDict(extra="allow")

    ai_finished_card_retention_minutes: Any = None


def _invalid_request(exc: ui_settings_service.UiSettingsError) -> JSONResponse:
    # Same envelope as the neighbouring settings routes, plus the field name so the
    # screen can mark the control that is wrong.
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


@router.get("/me/ui-settings")
def get_my_ui_settings(user=Depends(get_current_user)):
    # Safe variant: a store that cannot be read must not take the finished-card feature
    # down with it (L0003 §2-8, §5).
    return ui_settings_service.settings_response_safe(user["user_id"])


@router.patch("/me/ui-settings")
def patch_my_ui_settings(body: UiSettingsPatch, user=Depends(get_current_user)):
    patch = body.model_dump(exclude_unset=True)
    try:
        return ui_settings_service.save_ui_settings(user["user_id"], patch)
    except ui_settings_service.UiSettingsError as exc:
        return _invalid_request(exc)

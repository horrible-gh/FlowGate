"""HTTP adapter of the card run settings (flowgate.v02.0053 T#2). Mounted under the ``/flowgate``
context like the Graph routes; same envelope (P0008 §1.2 / §1.3), user JWT only, RBAC read / write.

    GET   /api/v1/flows/{flow_id}/nodes/{node_key}/settings    the card's settings (value · default · choices)
    PATCH /api/v1/flows/{flow_id}/nodes/{node_key}/settings    {"field": <name>, "value": <value | null>}

A PATCH changes exactly one value and answers the card's settings as they are now (null = default).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import settings

router = APIRouter(prefix="/api/v1", tags=["CardSettings"])

_BODY_KEYS = {"field", "value"}


def parse_change(body: Any) -> tuple[str, Any, list[dict]]:
    """``(field, value, errors)`` of a PATCH body: exactly ``field`` (text) and ``value`` (any JSON)."""
    if not isinstance(body, dict):
        return "", None, [{"field": "body", "reason": "not_an_object"}]
    errors = [{"field": key, "reason": "unknown_field"} for key in sorted(set(body) - _BODY_KEYS)]
    name = body.get("field")
    if not isinstance(name, str) or not name.strip():
        errors.append({"field": "field", "reason": "required"})
    if "value" not in body:
        errors.append({"field": "value", "reason": "required"})
    return (name.strip() if isinstance(name, str) else ""), body.get("value"), errors


@router.get("/flows/{flow_id}/nodes/{node_key}/settings")
def get_card_settings(flow_id: str, node_key: str, request: Request):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "read")
        return settings.read(group, node_key)

    return legacy.run(request, handle)


@router.patch("/flows/{flow_id}/nodes/{node_key}/settings")
def patch_card_settings(flow_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        name, value, errors = parse_change(body)
        if errors:
            raise legacy.invalid(request, errors)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "write")
        return settings.write(group, node_key, name, value, user_id)

    return legacy.run(request, handle)

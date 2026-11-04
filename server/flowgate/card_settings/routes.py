"""HTTP adapter of the card run settings (flowgate.v02.0053 T#2). Mounted under the ``/flowgate``
context like the Graph routes; same envelope (P0008 §1.2 / §1.3), user JWT only, RBAC read / write.

    GET   /api/v1/flows/{flow_id}/nodes/{node_key}/settings    the card's settings (value · default · choices)
    PATCH /api/v1/flows/{flow_id}/nodes/{node_key}/settings    {"field": <name>, "value": <value | null>}

A PATCH changes exactly one value and answers the card's settings as they are now (null = default).
0061 T#11: ``{"field": "hold", "value": true | false | null}`` sets / releases the card's Hold (``settings.write``).
flowgate.v02.0098 T#1: ``GET ...?refresh_merge_targets=true`` judges a check card's merge target candidates again instead
of reading their cache (a read option only: nothing is written).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Body, Request

from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import settings, context as settings_context

router = APIRouter(prefix="/api/v1", tags=["CardSettings"])

_BODY_KEYS = {"field", "value", "expected_settings_version", "expected_context"}
_TRUE = {"1", "true", "yes"}


def refresh_requested(request: Request) -> bool:
    """flowgate.v02.0098 T#1: the GET's ``refresh_merge_targets`` read option (true / 1 / yes; anything else: off)."""
    return str(request.query_params.get("refresh_merge_targets") or "").strip().lower() in _TRUE


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


def parse_expected_version(body: Any) -> tuple[Optional[int], list[dict]]:
    """flowgate.v02.0085 T#1: the optional ``expected_settings_version`` of a PATCH body - a whole number, never a
    boolean. ``(version | None, errors)``."""
    if not isinstance(body, dict) or "expected_settings_version" not in body:
        return None, []
    version = body["expected_settings_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        return None, [{"field": "expected_settings_version", "reason": "not_a_version"}]
    return version, []


@router.get("/flows/{flow_id}/nodes/{node_key}/settings")
def get_card_settings(flow_id: str, node_key: str, request: Request):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        expected_context = None
        if "context" in request.query_params:
            try:
                expected_context = json.loads(request.query_params["context"])
            except (ValueError, TypeError):
                raise legacy.invalid(request, [{"field": "context", "reason": "invalid_context"}])
            if not settings_context.parse(expected_context):
                raise legacy.invalid(request, [{"field": "context", "reason": "invalid_context"}])
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "read")
        answer = settings.read(group, node_key, refresh_merge_targets=refresh_requested(request), viewer=user_id, expected_context=expected_context)
        try:
            legacy.authorize(user_id, group, "write")
        except legacy.GraphApiError:
            answer["locked"] = True
            answer["settings_capability"] = {"editable": False, "reason": "forbidden"}
        return answer

    return legacy.run(request, handle)


@router.patch("/flows/{flow_id}/nodes/{node_key}/settings")
def patch_card_settings(flow_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        name, value, errors = parse_change(body)
        expected_version, version_errors = parse_expected_version(body)
        errors = errors + version_errors
        expected_context = body.get("expected_context") if isinstance(body, dict) else None
        if isinstance(body, dict) and "expected_context" in body and not settings_context.parse(expected_context):
            errors.append({"field": "expected_context", "reason": "invalid_context"})
        if errors:
            raise legacy.invalid(request, errors)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "write")
        return settings.write(group, node_key, name, value, user_id, legacy.locale_of(request),
                              expected_version=expected_version, expected_context=expected_context)

    return legacy.run(request, handle)

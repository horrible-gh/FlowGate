"""HTTP adapter of the card approval (flowgate.v02.0056 T#1). Mounted under the ``/flowgate`` context next to
the Graph run routes, whose path rule it follows (``/runs/{run_id}/nodes/{node_key}/gate-decision``); same
envelope (P0008 §1.2 / §1.3), user JWT only, RBAC write.

    POST /api/v1/runs/{run_id}/nodes/{node_key}/approval    {"result_doc_id": <the result shown> | null}

The body is optional; ``result_doc_id``, when given, must still be the node's result (409 result_changed).
There is no card rejection route (0002-CH): a gate card keeps ``gate-decision``.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Request

from flowgate.card_approval import approval
from flowgate.card_approval import legacy_adapter as legacy

router = APIRouter(prefix="/api/v1", tags=["CardApproval"])

_BODY_KEYS = {"result_doc_id"}


def parse_approval(body: Any) -> tuple[Optional[str], list[dict]]:
    """``(result_doc_id, errors)``: no body, ``{}`` or ``{"result_doc_id": text | null}``."""
    if body is None:
        return None, []
    if not isinstance(body, dict):
        return None, [{"field": "body", "reason": "not_an_object"}]
    errors = [{"field": key, "reason": "unknown_field"} for key in sorted(set(body) - _BODY_KEYS)]
    value = body.get("result_doc_id")
    if value is not None and (not isinstance(value, str) or not value.strip()):
        errors.append({"field": "result_doc_id", "reason": "not_a_text"})
    return (value.strip() if isinstance(value, str) and not errors else None), errors


@router.post("/runs/{run_id}/nodes/{node_key}/approval")
def post_card_approval(run_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        legacy.client_id_of(request)
        expected, errors = parse_approval(body)
        if errors:
            raise legacy.invalid(request, errors)
        group = legacy.load_flow(legacy.run_flow_id(run_id))
        legacy.authorize(user_id, group, "write")
        return approval.approve(group, run_id, node_key, expected, user_id, legacy.locale_of(request))

    return legacy.run(request, handle)

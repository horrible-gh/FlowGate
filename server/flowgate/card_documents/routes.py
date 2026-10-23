"""HTTP adapter of the card documents (flowgate.v02.0056 T#3). Mounted under the ``/flowgate`` context like the
Graph routes; same envelope (P0008 §1.2 / §1.3), user JWT only, the v0.1 document create permission.

    POST /api/v1/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document    {"content": <text> | null}

A person writes a card line that has no document (``compose.write_line``): 201 with the document made and the
line as the card read shows it now. The line reads themselves stay in the draft save and Canvas responses
(``card_documents``).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from flowgate.card_documents import compose
from flowgate.card_documents import legacy_adapter as legacy

router = APIRouter(prefix="/api/v1", tags=["CardDocuments"])


@router.post("/flows/{flow_id}/nodes/{node_key}/slots/{slot}/document")
def write_card_line(flow_id: str, node_key: str, slot: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        content, errors = compose.parse_body(body)
        if errors:
            raise legacy.invalid(request, errors)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group)
        return compose.write_line(group, node_key, slot, content, user_id, legacy.locale_of(request))

    return legacy.run(request, handle, 201)

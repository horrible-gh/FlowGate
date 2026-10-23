"""HTTP adapter of the gate decision history (flowgate.v02.0085 T#2, D0007 §7.2). Mounted under the ``/flowgate``
context next to the Graph run routes, whose path rule it follows (``/runs/{run_id}/nodes/{node_key}/gate-decision``);
same envelope (P0008 §1.2 / §1.3), user JWT only, RBAC read - checked on every call, so a user whose permission was
withdrawn sees nothing of the history any more.

    GET /api/v1/runs/{run_id}/nodes/{node_key}/gate-decisions?cursor=<opaque>&limit=<1..200>

``{"flow_id", "run_id", "node_key", "items": [...], "next_cursor": str | null, "latest": entry | null}``: the gate's
decisions in decision order (``history.history``: every round of the gate in the Flow, the old ``gate_confirmed`` /
``gate_rejected`` events as legacy entries with unknown Git / archive facts), one stable cursor page; ``latest`` is the
current result whatever the page. 404 ``run_not_found`` / ``gate_node_not_found`` (no gate of that key in the run's
revision), 422 for a bad cursor or limit.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Request

from flowgate.gate_decisions import history
from flowgate.gate_decisions import legacy_adapter as legacy

router = APIRouter(prefix="/api/v1", tags=["GateDecisions"])
# ASCII digits only, at most as long as the largest limit: ``str.isdigit`` takes ``²`` and other digits ``int`` refuses,
# and an unbounded run of digits is no limit either - both are 422, never a 500.
LIMIT_TEXT = re.compile(r"[0-9]{1,%d}" % len(str(history.MAX_LIMIT)))


def parse_history_page(cursor: Optional[str], limit: Optional[str]) -> tuple[tuple[Optional[str], int], list[dict]]:
    errors: list[dict] = []
    size = history.DEFAULT_LIMIT
    if limit is not None:
        text = str(limit).strip()
        value = int(text) if LIMIT_TEXT.fullmatch(text) else None
        if value is None or not (1 <= value <= history.MAX_LIMIT):
            errors.append({"field": "limit", "reason": f"not_in_range:1,{history.MAX_LIMIT}"})
        else:
            size = value
    if cursor is not None:
        try:
            history.decode_cursor(cursor)
        except ValueError:
            errors.append({"field": "cursor", "reason": "invalid_cursor"})
    return (cursor or None, size), errors


@router.get("/runs/{run_id}/nodes/{node_key}/gate-decisions")
def get_gate_decisions(run_id: str, node_key: str, request: Request, cursor: Optional[str] = None,
                       limit: Optional[str] = None):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        legacy.client_id_of(request)
        (page_cursor, size), errors = parse_history_page(cursor, limit)
        if errors:
            raise legacy.invalid(request, errors)
        flow_id = legacy.run_flow_id(run_id)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "read")
        run = legacy.run_store.run_by_id(run_id)
        if run is None:
            raise legacy.GraphApiError(404, "run_not_found", {"run_id": run_id})
        if legacy.node_types(flow_id, int(run["revision_no"])).get(node_key) != "gate":
            raise legacy.GraphApiError(404, "gate_node_not_found", {"run_id": run_id, "node_key": node_key})
        page = history.history(flow_id, node_key, cursor=page_cursor, limit=size)
        return {"flow_id": flow_id, "run_id": run_id, "node_key": node_key, **page}

    return legacy.run(request, handle)

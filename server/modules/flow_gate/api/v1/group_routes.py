"""Group single-item retrieval endpoint (D021 §4-5).

GET /api/v1/group/{gid}/next-action
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.utils.id_validators import validate_group_id

router = APIRouter(prefix="/api/v1", tags=["OutboundGroup"])

_HELP_URL = "https://example.com/api/v1/help"

# D017 Next-action candidate mapping based on the workflow pipeline (decided by operational AI)
_TYPE_NEXT: dict[str, list[dict]] = {
    "R": [
        {"action_code": "create_ds", "doc_type": "DS", "description": "Draft design instruction"},
        {"action_code": "create_n", "doc_type": "N", "description": "Draft investigation instruction"},
        {"action_code": "create_t", "doc_type": "T", "description": "Draft work order"},
    ],
    "NR": [
        {"action_code": "create_ds", "doc_type": "DS", "description": "Draft design instruction"},
        {"action_code": "create_d", "doc_type": "D", "description": "Draft design document"},
    ],
    "DS": [
        {"action_code": "create_d", "doc_type": "D", "description": "Draft design document"},
        {"action_code": "create_db", "doc_type": "DB", "description": "Draft DB design document"},
        {"action_code": "create_l", "doc_type": "L", "description": "Draft logic design document"},
        {"action_code": "create_p", "doc_type": "P", "description": "Draft protocol"},
    ],
    "T": [
        {"action_code": "create_tr", "doc_type": "TR", "description": "Draft work report"},
    ],
    "N": [
        {"action_code": "create_nr", "doc_type": "NR", "description": "Draft investigation report"},
    ],
    "D": [
        {"action_code": "create_ar", "doc_type": "AR", "description": "Create approval request"},
        {"action_code": "create_tr", "doc_type": "TR", "description": "Draft work report"},
    ],
    "DB": [
        {"action_code": "create_ar", "doc_type": "AR", "description": "Create approval request"},
    ],
    "TR": [
        {"action_code": "create_ar", "doc_type": "AR", "description": "Create approval request"},
    ],
    "AR": [
        {"action_code": "create_ac", "doc_type": "AC", "description": "Approve"},
        {"action_code": "create_rj", "doc_type": "RJ", "description": "Reject"},
    ],
}


def get_next_action_candidates(group_id: str) -> list[dict]:
    """Return next action candidates based on the group's last document type."""
    store = get_store()
    last_doc = store._fetch_one(
        "SELECT doc_id, type_code, title FROM documents WHERE group_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        [group_id],
    )
    if last_doc is None:
        return []
    type_code = last_doc.get("type_code", "")
    candidates_base = _TYPE_NEXT.get(type_code, [])
    # inject prev_doc_id
    return [
        {**c, "prev_doc_id": last_doc["doc_id"]}
        for c in candidates_base
    ]


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "http_status": status,
                 "error_message": message, "help_url": _HELP_URL},
    )


@router.get("/group/{gid}/next-action")
def get_group_next_action(request: Request, gid: str):
    """Retrieve the group's last action and expected next action candidates (D021 §4-5)."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        validate_group_id(gid)
    except ValueError as exc:
        return _fail(422, str(exc))

    group = db_groups.get_by_id(gid)
    if group is None:
        return _fail(404, f"Group {gid} does not exist")

    store = get_store()
    # last workflow event for this group — exclude token lifecycle events
    # (token_issued/token_consumed/token_revoked are not workflow actions)
    last_event_row = store._fetch_one(
        "SELECT * FROM workflow_events WHERE group_id = ? "
        "AND event_type NOT IN ('token_issued', 'token_consumed', 'token_revoked') "
        "ORDER BY id DESC LIMIT 1",
        [gid],
    )

    last_action = None
    if last_event_row:
        metadata = {}
        if last_event_row.get("metadata"):
            try:
                metadata = json.loads(last_event_row["metadata"])
            except Exception:
                pass
        last_action = {
            "event_type": last_event_row.get("event_type"),
            "action_code": metadata.get("action_code"),
            "doc_id": metadata.get("doc_id"),
            "actor_id": last_event_row.get("actor_user_id"),
            "timestamp": last_event_row.get("created_at"),
        }

    candidates = get_next_action_candidates(gid)

    return JSONResponse(content={
        "ok": True,
        "group_id": gid,
        "last_action": last_action,
        "candidates": candidates,
    })

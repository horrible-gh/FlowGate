"""Silent, authenticated reports of tree transport failures. No URL or response text is accepted."""
import hashlib
from datetime import datetime
from typing import Literal, Union
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, constr
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import server_logs
from util.credential_redaction import redact_text

router = APIRouter()

class TreeReadFailure(BaseModel):
    id: constr(pattern=r"^[A-Za-z0-9.:-]{8,200}$")
    project_id: constr(min_length=1, max_length=200)
    screen: Literal["full_tree", "designer_tree"]
    status: Union[StrictInt, Literal["no_response"]]
    elapsed_ms: StrictInt = Field(ge=0, le=86400000)
    occurred_at: datetime
    request_id: constr(min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid")

@router.post("/api/v1/client-reports/tree-read-failed")
def tree_read_failed(body: TreeReadFailure, user=Depends(get_current_user)):
    if isinstance(body.status, int) and not 400 <= body.status <= 599:
        raise HTTPException(422, "status must be 400..599 or no_response")
    extra = body.model_dump()
    extra["occurred_at"] = body.occurred_at.isoformat()
    extra["user_id"] = str(user.get("user_id") or "")
    # The fingerprint contains the user and client id, never mutable diagnostics.
    key = hashlib.sha256((extra["user_id"] + ":" + body.id).encode()).hexdigest()
    if not server_logs.record("browser", "WARNING", "tree-read-failed:" + key,
                              request_id=redact_text(body.request_id, 64), extra=extra,
                              logger_name="browser.tree_read", location="tree"):
        raise HTTPException(503, "report queue unavailable")
    # Acknowledgement means durable storage, not merely entry into the best-effort log queue.
    if not server_logs.flush(5.0):
        raise HTTPException(503, "report persistence pending")
    try:
        stored = get_store()._fetch_one(
            "SELECT id FROM server_logs WHERE source = ? AND logger_name = ? AND message = ? LIMIT 1",
            ["browser", "browser.tree_read", "tree-read-failed:" + key],
        )
    except Exception:
        raise HTTPException(503, "report persistence unavailable")
    if not stored:
        raise HTTPException(503, "report persistence unavailable")
    return {"ok": True}

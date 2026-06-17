"""Project-control remote tool API — thin router (P0005 §3.1).

  POST /flowgate/api/v1/remote/{operation}   operation ∈ read|write|grep|glob|remove

Authentication, permission scope, path validation, execution, op-logging and the
completion ment all live in remote_tool_service (L0006). This router only
extracts the Bearer token + JSON body, delegates, and returns the P0005 envelope
with the pipeline's HTTP status.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.requests import Request

from modules.flow_gate.services import remote_tool_service

router = APIRouter(prefix="/api/v1", tags=["RemoteTool"])


def _extract_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


@router.post("/remote/{operation}", response_class=JSONResponse)
async def remote_tool(request: Request, operation: str):
    raw_token = _extract_bearer(request)
    try:
        body = await request.json()
    except Exception:
        # Malformed/absent JSON body. Auth (①) runs first regardless; if the
        # request is authenticated, the missing fields surface as 422 (④).
        body = None

    status, payload = remote_tool_service.handle(operation, raw_token, body)
    return JSONResponse(status_code=status, content=payload)

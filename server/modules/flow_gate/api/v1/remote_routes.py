"""Project-control remote tool API — thin router (P0005 §3.1).

  POST /flowgate/api/v1/remote/{operation}   operation ∈ read|write|grep|glob|remove|patch|stat

Authentication, permission scope, path validation, execution, op-logging and the
completion ment all live in remote_tool_service (L0006). This router only
extracts the Bearer token + JSON body, delegates, and returns the P0005 envelope
with the pipeline's HTTP status.
"""
from __future__ import annotations

from typing import Optional

import anyio.to_thread
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

    # 0279 T0005 (NR0003 원인 1): remote_tool_service.handle() is a plain `def`
    # that walks the entire source tree (grep/glob) and does blocking file I/O.
    # Calling it directly from this `async def` ran it ON the event loop, so one
    # remote/grep froze every other in-flight request — measured at 40s, during
    # which an unrelated 0.15s DB read took 40.2s and returned only once the grep
    # finished. This handler must stay `async def` because it awaits
    # request.json(), so push the sync pipeline through the threadpool instead.
    # (Same bug class as 0275 T0005, whose fix covered tree/list/document/git
    # routes but missed this router.)
    status, payload = await anyio.to_thread.run_sync(
        remote_tool_service.handle, operation, raw_token, body
    )
    return JSONResponse(status_code=status, content=payload)

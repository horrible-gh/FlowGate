"""Single project retrieval endpoint (D021 §4-4).

GET /api/v1/project/{p}/source-path
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.utils.id_validators import validate_project_id

router = APIRouter(prefix="/api/v1", tags=["OutboundProject"])

_HELP_URL = "https://example.com/api/v1/help"


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "http_status": status,
                 "error_message": message, "help_url": _HELP_URL},
    )


@router.get("/project/{p}/source-path")
def get_project_source_path(request: Request, p: str):
    """Retrieve project source code path (D021 §4-4)."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        validate_project_id(p)
    except ValueError as exc:
        return _fail(422, str(exc))

    project = db_projects.get_by_id(p)
    if project is None:
        return _fail(404, f"Project {p} does not exist")

    # source_path: from projects.source_path column (added by migration 014)
    # Falls back to None if not set
    source_path = project.get("source_path")

    return JSONResponse(content={
        "ok": True,
        "project": p,
        "source_path": source_path,
    })

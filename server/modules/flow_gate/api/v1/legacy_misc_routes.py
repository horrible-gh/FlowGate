"""T373 — Unify LegacyMisc API v1 track (P006 §3-10).

Remaining routes (both used by the console file explorer):
  POST /flowgate/api/v1/storage/folder
  POST /flowgate/api/v1/storage/file

Both require a signed-in user and a document-update grant on the target project.
The project is checked as named in the body and, when a group is named, as stored
on that group, so a request cannot pair another project's group with its own.

Routes removed because nothing called them any more: /brief, /queue, /draft,
/draft/{doc_id}, /detail, /detail/{doc_id}, /projects (already served first by
the project settings router), GET /groups/{group_id}, /clipboard, /outbox/create
and /project-settings/pick-folder (a server-side folder dialog).

The following are claimed by other v1 routers:
  GET  /flowgate/api/v1/modules        → modules/flow_gate/api/v1/module_routes.py
  GET  /flowgate/api/v1/groups         → workflow/routers/workflow.py
  POST /flowgate/api/v1/groups         → workflow/routers/workflow.py
  PUT  /flowgate/api/v1/groups/{id}    → workflow/routers/workflow.py
"""
from __future__ import annotations

import anyio.to_thread
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from starlette.requests import Request

from modules.flow_gate import process_service
from modules.flow_gate.auth.middleware import get_current_user

router = APIRouter(prefix="/api/v1", tags=["LegacyMisc"])


def _require_storage_target(user: dict, body: dict) -> None:
    """403 unless the user may update every project the body names."""
    from modules.flow_gate.rbac import access

    project_id = body.get("project_id") or None
    group_project = access.project_of_group(body.get("group_id") or None)
    targets = {p for p in (project_id, group_project) if p}
    if not targets and not access.is_admin(user):
        access.require(user, None, "project.document.update")
    for target in targets:
        access.require(user, target, "project.document.update")


# The handlers `await request.json()` on the loop and hand every DB/file step,
# including the permission check, to anyio.to_thread.run_sync.
@router.post("/storage/folder", response_class=JSONResponse)
async def api_create_folder(request: Request, current_user: dict = Depends(get_current_user)):
    """Create folder. Body: {project_id, parent_path, name, group_id?}

    0327 T0004 (B0001): the optional group_id creates inside that group's worktree
    so the explorer's "new folder" works while a group branch is selected.
    """
    body = await request.json()

    def _create():
        _require_storage_target(current_user, body)
        return process_service.create_storage_folder(
            project_id=body.get("project_id", ""),
            parent_path=body.get("parent_path", ""),
            name=body.get("name", ""),
            group_id=body.get("group_id") or None,
        )

    result = await anyio.to_thread.run_sync(_create)
    if result.get("status") == "error":
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(content=result)


@router.post("/storage/file", response_class=JSONResponse)
async def api_create_file(request: Request, current_user: dict = Depends(get_current_user)):
    """Create file. Body: {project_id, parent_path, name, group_id?}

    0327 T0004 (B0001): the optional group_id creates inside that group's worktree
    so the explorer's "new file" works while a group branch is selected.
    """
    body = await request.json()

    def _create():
        _require_storage_target(current_user, body)
        return process_service.create_storage_file(
            project_id=body.get("project_id", ""),
            parent_path=body.get("parent_path", ""),
            name=body.get("name", ""),
            group_id=body.get("group_id") or None,
        )

    result = await anyio.to_thread.run_sync(_create)
    if result.get("status") == "error":
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(content=result)

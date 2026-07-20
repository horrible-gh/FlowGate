"""T373 — Unify LegacyMisc API v1 track (P006 §3-10).

Migration targets (9 items):
  GET  /flowgate/api/v1/brief
  GET  /flowgate/api/v1/queue
  GET  /flowgate/api/v1/draft/{doc_id}
  GET  /flowgate/api/v1/detail/{doc_id}
  GET  /flowgate/api/v1/projects
  GET  /flowgate/api/v1/groups/{group_id}
  POST /flowgate/api/v1/storage/folder
  POST /flowgate/api/v1/storage/file
  POST /flowgate/api/v1/clipboard

T394 — Transfer 2 remaining /flow_gate/ items:
  POST /flowgate/api/v1/outbox/create
  POST /flowgate/api/v1/project-settings/pick-folder

The following 4 are claimed by existing v1 routers:
  GET  /flowgate/api/v1/modules        → modules/flow_gate/api/v1/module_routes.py
  GET  /flowgate/api/v1/groups         → workflow/routers/workflow.py
  POST /flowgate/api/v1/groups         → workflow/routers/workflow.py
  PUT  /flowgate/api/v1/groups/{id}    → workflow/routers/workflow.py
"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Form, Query
from fastapi.responses import JSONResponse
from starlette.requests import Request

from modules.flow_gate import db as _db
from modules.flow_gate import service, process_service

router = APIRouter(prefix="/api/v1", tags=["LegacyMisc"])


# 0275 T0005 (NR0003 원인 2): handlers doing sync DB work are plain `def` so
# FastAPI runs them in the threadpool instead of blocking the event loop. The
# ones that `await request.json()` stay async.
@router.get("/brief", response_class=JSONResponse)
def api_brief():
    return service.envelope("brief", service.get_brief())


@router.get("/queue", response_class=JSONResponse)
def api_queue():
    return service.envelope("queue", service.build_action_queue())


@router.get("/draft", response_class=JSONResponse)
def api_draft_rpc(doc_id: str = Query(...)):
    return api_draft(doc_id)


@router.get("/draft/{doc_id:path}", response_class=JSONResponse)
def api_draft(doc_id: str):
    draft = service.build_worker_draft(doc_id)
    if draft is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "kind": "draft", "detail": f"Document not found: {doc_id}"},
        )
    return service.envelope("draft", draft)


@router.get("/detail", response_class=JSONResponse)
def api_detail_rpc(doc_id: str = Query(...)):
    return api_detail(doc_id)


@router.get("/detail/{doc_id:path}", response_class=JSONResponse)
def api_detail(doc_id: str):
    detail = service.get_document_detail(doc_id)
    if detail is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "kind": "detail", "detail": f"Document not found: {doc_id}"},
        )
    return service.envelope("detail", detail)


@router.get("/projects", response_class=JSONResponse)
def api_projects():
    """Return projects and modules as JSON."""
    projects = process_service.get_projects_with_modules()
    return {"projects": projects}


@router.get("/groups/{group_id}", response_class=JSONResponse)
def api_get_group(group_id: str):
    """Retrieve single group (JSON)."""
    group = _db.get_group(group_id)
    if group is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"Group not found: {group_id}"},
        )
    return JSONResponse(content={"status": "success", "group": group})


@router.post("/storage/folder", response_class=JSONResponse)
async def api_create_folder(request: Request):
    """Create folder. Body: {project_id, parent_path, name}"""
    body = await request.json()
    result = process_service.create_storage_folder(
        project_id=body.get("project_id", ""),
        parent_path=body.get("parent_path", ""),
        name=body.get("name", ""),
    )
    if result.get("status") == "error":
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(content=result)


@router.post("/storage/file", response_class=JSONResponse)
async def api_create_file(request: Request):
    """Create file. Body: {project_id, parent_path, name}"""
    body = await request.json()
    result = process_service.create_storage_file(
        project_id=body.get("project_id", ""),
        parent_path=body.get("parent_path", ""),
        name=body.get("name", ""),
    )
    if result.get("status") == "error":
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(content=result)


@router.post("/clipboard", response_class=JSONResponse)
async def api_clipboard(request: Request):
    data = await request.json()
    file_path = (data.get("file_path") or "").strip()
    if not file_path:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "file_path is required"},
        )
    return process_service.build_clipboard_text(file_path)


# ── T394: Transfer remaining /flow_gate/ items ──────────────────────────────────────────────

@router.post("/outbox/create", response_class=JSONResponse)
def api_outbox_create(
    request: Request,
    project: str = Form(...),
    module: str = Form(""),
    title: str = Form(...),
    slug: str = Form(""),
    priority: str = Form("medium"),
    body: str = Form(""),
    owner: str = Form("admin"),
    group_id: str = Form(""),
    new_group_name: str = Form(""),
    doc_type: str = Form("R"),
    template: str = Form("default"),
):
    """Create an R/B workflow-root document. Form-encoded POST."""
    result = process_service.create_requirement(
        project=project.strip(),
        module=module.strip(),
        title=title.strip(),
        slug=slug.strip(),
        priority=priority.strip(),
        body=body,
        owner=owner.strip(),
        group_id=group_id.strip(),
        new_group_name=new_group_name.strip(),
        doc_type=doc_type.strip(),
        template=template.strip(),
        locale=request.headers.get("x-locale") or "ko",
    )
    if result.get("errors"):
        return JSONResponse(status_code=400, content={"ok": False, "errors": result["errors"]})
    return JSONResponse(content={"ok": True, "result": result})


def _pick_folder_dialog(initial_dir: str = "") -> str:
    """Server-side Windows folder selection dialog. Returns selected path or empty string."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        result = filedialog.askdirectory(
            parent=root,
            initialdir=initial_dir if (initial_dir and os.path.isdir(initial_dir)) else "/",
        )
        root.destroy()
        return os.path.normpath(result) if result else ""
    except Exception:
        return ""


@router.post("/project-settings/pick-folder", response_class=JSONResponse)
async def api_pick_folder(initial_dir: str = Form("")):
    """Invoke server-side folder selection dialog."""
    loop = asyncio.get_running_loop()
    path = await loop.run_in_executor(None, _pick_folder_dialog, initial_dir.strip())
    if path:
        return JSONResponse(content={"cancelled": False, "path": path})
    return JSONResponse(content={"cancelled": True, "path": ""})

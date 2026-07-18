"""T378 — Added tree & disposal API v1 track (R021 T-A).
T533 — Added single-file download + directory ZIP streaming download.

New endpoints:
  GET  /api/v1/projects/{project_id}/files/tree
  GET  /api/v1/projects/{project_id}/groups/tree
  GET  /api/v1/projects/{project_id}/files/src-content
  POST /api/v1/groups/{group_id}/dispose
  GET  /api/v1/projects/{project_id}/files/download
  GET  /api/v1/projects/{project_id}/files/download-zip
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from starlette.requests import Request

from modules.flow_gate import process_service
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services.auth_outbound import verify_bearer

router = APIRouter(prefix="/api/v1", tags=["Tree"])


class SrcContentUpdate(BaseModel):
    content: str


class SrcDeleteRequest(BaseModel):
    path: str
    type: str
    # NR0003 권장 5: the client sends its current group-branch context here so the
    # server can refuse a group-scoped delete outright. Absent (base-checkout edit) → None.
    group_id: str | None = None


@router.get("/projects/{project_id}/files/tree", response_class=JSONResponse)
async def get_files_tree(project_id: str, branch: str = Query("main", description="branch (currently unused, kept for interface compatibility)")):
    """Return the file tree for the project."""
    tree = process_service.get_file_tree(project_id)
    return {"data": tree}


def _resolve_src_path(project_id: str, path: str):
    from modules.flow_gate.storage.paths import src_root
    from modules.flow_gate.db import projects as _proj

    row = _proj.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    settings = _proj.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"

    if not project_name:
        raise HTTPException(status_code=404, detail="Not found")

    docs_root = src_root(project_name, branch).resolve()
    try:
        full_path = (docs_root / path).resolve()
        full_path.relative_to(docs_root)  # prevent path traversal
    except (ValueError, OSError):
        raise HTTPException(status_code=403, detail="Forbidden")
    return full_path


def _resolve_delete_path(project_id: str, path: str):
    """Resolve without following symlinks and reject any symlink component."""
    from modules.flow_gate.storage.paths import src_root
    from modules.flow_gate.db import projects as _proj
    row = _proj.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    settings = _proj.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"
    if not project_name:
        raise HTTPException(status_code=404, detail="Not found")
    root = src_root(project_name, branch).resolve()
    # NR0003 finding: normalize a SINGLE backslash to '/', matching _validate_path_param.
    # Replacing only a double backslash left 'foo\bar' as one literal component, so its
    # per-component symlink check was bypassed and resolution disagreed with validation.
    candidate = root.joinpath(*path.replace("\\", "/").split("/"))
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(status_code=400, detail="Symbolic links are not allowed")
    return candidate


@router.api_route("/projects/{project_id}/files/src-content", methods=["GET", "HEAD"], response_class=PlainTextResponse)
async def get_src_file_content(request: Request, project_id: str, path: str = Query(..., description="relative path from docs_root")):
    """Return the content of a src-tree file as UTF-8 text.
    For HEAD requests, return only the Content-Length header (for file size checks).
    """
    full_path = _resolve_src_path(project_id, path)
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    file_size = full_path.stat().st_size
    if request.method == "HEAD":
        return PlainTextResponse("", headers={"Content-Length": str(file_size)})

    with open(full_path, encoding="utf-8", errors="replace") as f:
        return f.read()


@router.patch("/projects/{project_id}/files/src-content", response_class=JSONResponse)
async def update_src_file_content(
    project_id: str,
    body: SrcContentUpdate,
    path: str = Query(..., description="relative path from docs_root"),
):
    """Save the content of a src-tree file as UTF-8 text."""
    full_path = _resolve_src_path(project_id, path)
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    full_path.write_text(body.content, encoding="utf-8")

    # flowgate.default.0176 T0010 §a: this write lands directly in the project's
    # base checkout (an intended admin edit — the write path is NOT changed). That
    # leaves the base dirty, which blocks merge finalize for EVERY group of this
    # project via the E3 guard (NR flowgate.default.0176.0009). Return the base git
    # status so the editor can warn the operator immediately, rather than the
    # contamination staying invisible until a later finalize returns a bare 500.
    from modules.flow_gate.services import git_service
    base_git = git_service.base_checkout_dirty_status(project_id)
    return {"path": path, "content_length": len(body.content), "base_git": base_git}



@router.delete("/projects/{project_id}/files", response_class=JSONResponse)
async def delete_src_path(request: Request, project_id: str, body: SrcDeleteRequest):
    """Delete one file or directory from the editable base checkout."""
    auth = _check_project_auth(request, project_id)
    if isinstance(auth, JSONResponse): return auth
    # NR0003 권장 4: deletion requires a WRITE permission (perm_document_delete), not read.
    # Enforce it for EVERY caller — user JWTs and worker/outbound tokens alike. verify_bearer
    # only guarantees perm_document_read, so gating this check on _is_user_jwt let any worker
    # token hard-delete base-checkout files with mere read access. Checking the token's
    # issued_to against the request project can only restrict access, never widen it.
    if not has_permission(auth["issued_to"], project_id, "perm_document_delete"):
        return _err(403, "FORBIDDEN", "insufficient permission for this operation")
    # NR0003 권장 5: this endpoint manages ONLY the editable base checkout. A group-branch
    # (read-only) context must never delete through here — the client hides the menu, but the
    # server refuses group-scoped deletes outright rather than trusting the UI guard.
    if (body.group_id or "").strip():
        return _err(403, "FORBIDDEN", "deletion is not allowed on a group branch")
    try: _validate_path_param(body.path)
    except HTTPException: return _err(400, "INVALID_PATH", "path is invalid")
    if body.path.replace("\\", "/").strip("/") in ("", "."): return _err(400, "INVALID_PATH", "project root cannot be deleted")
    if body.type not in ("file", "folder"): return _err(400, "TYPE_MISMATCH", "type must be file or folder")
    try: full_path = _resolve_delete_path(project_id, body.path)
    except HTTPException: return _err(400, "INVALID_PATH", "symbolic links are not allowed")
    if not full_path.exists(): return _err(404, "NOT_FOUND", "path does not exist")
    actual_type = "folder" if full_path.is_dir() else "file" if full_path.is_file() else ""
    if actual_type != body.type: return _err(409, "TYPE_MISMATCH", "path type does not match request")
    try: shutil.rmtree(full_path) if actual_type == "folder" else full_path.unlink()
    except OSError: return _err(500, "DELETE_FAILED", "failed to delete path")
    # NR0003 권장 8: like the src-content PATCH, this delete lands in the base checkout and
    # leaves it dirty (blocking merge finalize for every group of this project). Return the
    # base git status so the explorer can refresh its base-dirty markers and Git finalize
    # warning immediately, instead of the contamination staying invisible until a later finalize.
    from modules.flow_gate.services import git_service
    base_git = git_service.base_checkout_dirty_status(project_id)
    return {"deleted": body.path, "type": actual_type, "base_git": base_git}


@router.get("/projects/{project_id}/groups/tree", response_class=JSONResponse)
async def get_groups_tree(project_id: str, branch: str = Query("main", description="branch (currently unused, kept for interface compatibility)")):
    """Return the group tree for the project."""
    tree = process_service.get_group_tree(project_id)
    return {"data": tree}


@router.post("/groups/{group_id}/dispose", response_class=JSONResponse)
async def group_dispose(request: Request, group_id: str):
    """Process group disposal."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason_option = str(body.get("reason_option", "")).strip()
    reason_detail = str(body.get("reason_detail", "")).strip()
    result = process_service.dispose_group(
        group_id,
        reason_option=reason_option,
        reason_detail=reason_detail,
    )
    # TR0079.0003 (rework): a group disposal must propagate over SSE so already-open
    # clients react immediately — without it the only refresh was the disposing user's
    # local explorer reload, leaving open document tabs (and other users' views) stale:
    # the action bar kept offering approve/reject/workflow actions on a now-discarded
    # group ("the action bar still lets you act on documents in a disposed group"), and nothing
    # changed until F5 ("SSE isn't applied immediately, so the action bar doesn't update"). Reuse the
    # existing GROUP_VIEW_REFRESH event: its FE handler invalidates the dashboard +
    # explorer and signals open tabs to refetch, which now surfaces group_disposed and
    # collapses the action bar. Best-effort; never fail the request on a delivery error.
    if isinstance(result, dict) and result.get("status") == "success":
        # 0192 T0005 §3: dispose_group only writes the file-less DC marker — it never
        # touched git, so the discarded group's worktree directory, unmerged local
        # work branch and ledger row all leaked (and the stale ledger row lingered in
        # the file-explorer group dropdown). Tear the slot down now, before the SSE
        # refresh below, so clients re-fetching the slot list see it gone. Best-effort
        # by contract: disposal has already succeeded and a git failure must not undo it.
        try:
            from modules.flow_gate.services import git_service
            git_service.cleanup_disposed_group(result.get("project") or "", group_id)
        except Exception:
            pass
        try:
            from modules.flow_gate.api.v1.events import publisher as _events_pub
            from modules.flow_gate.api.v1.events.event_types import EventType

            await _events_pub.broadcast_event(_events_pub.FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": group_id, "reason": "group_disposed"},
                audience="*",
                project=result.get("project"),
                group_id=group_id,
            ))
        except Exception:
            pass
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# T533 — Download endpoint helpers
# ---------------------------------------------------------------------------

def _err(status: int, code: str, message: str) -> JSONResponse:
    """P007 §3 error response format."""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _validate_path_param(path: str) -> None:
    """Pre-validate the path parameter.

    Absolute paths, drive prefixes, and '..' segments are rejected immediately with 400.
    Actual path traversal prevention is handled with 403 in _resolve_src_path().
    """
    if not path:
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_PATH", "message": "path parameter is required"}})
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_PATH", "message": "absolute paths are not allowed"}})
    if len(normalized) >= 2 and normalized[1] == ":":
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_PATH", "message": "drive prefix is not allowed"}})
    if ".." in normalized.split("/"):
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_PATH", "message": "'..' path segments are not allowed"}})


def _rfc5987_encode(name: str) -> str:
    """RFC 5987 UTF-8 percent-encoding — Content-Disposition filename* value."""
    return "UTF-8''" + quote(name, safe="")


def _check_project_auth(request: Request, project_id: str):
    """Verify Bearer token and check project_id permission.

    - Worker token: perm_document_read is checked inside verify_bearer() against the token's project.
    - User JWT: after verify_bearer() validation, perm_document_read is additionally checked
      against the requested project_id.

    Success: returns token_rec dict.
    Failure: returns JSONResponse (caller must return immediately).
    """
    token_rec = verify_bearer(request)
    if isinstance(token_rec, JSONResponse):
        return token_rec

    if token_rec.get("_is_user_jwt"):
        user_id: str = token_rec["issued_to"]
        if not has_permission(user_id, project_id, "perm_document_read"):
            return _err(403, "FORBIDDEN", "insufficient permission for this operation")

    return token_rec


# ---------------------------------------------------------------------------
# T533 — Single-file download
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/files/download")
async def download_file(
    request: Request,
    project_id: str,
    path: str = Query(..., description="relative path from project root (POSIX '/' separator)"),
):
    """Single-file download.

    Complies with P007 §2-1 spec.
    - Response: application/octet-stream, Content-Disposition RFC 5987 UTF-8 encoding.
    - Auth: verify_bearer() + perm_document_read.
    - Path guard: _validate_path_param (400) → _resolve_src_path (403).
    """
    auth = _check_project_auth(request, project_id)
    if isinstance(auth, JSONResponse):
        return auth

    _validate_path_param(path)

    full_path = _resolve_src_path(project_id, path)
    if not full_path.is_file():
        return _err(404, "FILE_NOT_FOUND", "file does not exist")

    filename = full_path.name
    # RFC 5987: filename* required; include filename fallback if name is ASCII
    encoded = _rfc5987_encode(filename)
    try:
        ascii_name = filename.encode("ascii").decode("ascii")
        disposition = f'attachment; filename="{ascii_name}"; filename*={encoded}'
    except UnicodeEncodeError:
        disposition = f"attachment; filename*={encoded}"

    return FileResponse(
        path=str(full_path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


# ---------------------------------------------------------------------------
# T533 — Directory ZIP streaming download
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/files/download-zip")
async def download_zip(
    request: Request,
    project_id: str,
    path: str = Query(..., description="relative path from project root to the directory to compress"),
):
    """Directory ZIP streaming download.

    Complies with P007 §2-2 spec.
    - Reads the ZIP from SpooledTemporaryFile in chunks and sends via StreamingResponse.
    - Preserves directory structure; empty folders are not included.
    - Content-Disposition RFC 5987 UTF-8 encoding.
    - Auth: verify_bearer() + perm_document_read.
    - Path guard: _validate_path_param (400) → _resolve_src_path (403).
    """
    auth = _check_project_auth(request, project_id)
    if isinstance(auth, JSONResponse):
        return auth

    _validate_path_param(path)

    full_path = _resolve_src_path(project_id, path)
    if not full_path.is_dir():
        return _err(404, "DIRECTORY_NOT_FOUND", "directory does not exist")

    dir_name = full_path.name
    zip_filename = f"{dir_name}.zip"

    def _generate_zip():
        # SpooledTemporaryFile: kept in memory if below max_size, spooled to disk if exceeded.
        buf = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
        try:
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for file_path in sorted(full_path.rglob("*")):
                    if file_path.is_file():
                        arcname = file_path.relative_to(full_path)
                        zf.write(str(file_path), arcname=str(arcname))
            buf.seek(0)
            while True:
                chunk = buf.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            buf.close()

    encoded_zip = _rfc5987_encode(zip_filename)
    try:
        ascii_zip = zip_filename.encode("ascii").decode("ascii")
        disposition = f'attachment; filename="{ascii_zip}"; filename*={encoded_zip}'
    except UnicodeEncodeError:
        disposition = f"attachment; filename*={encoded_zip}"

    return StreamingResponse(
        _generate_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )

"""T534 — File upload endpoint (multipart + directory structure restoration).

Endpoint:
  POST /api/v1/projects/{project_id}/files/upload

Note:
  T533 download endpoint is implemented in tree_routes.py.
"""
from __future__ import annotations

import os
import re

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services.auth_outbound import verify_bearer

router = APIRouter(prefix="/api/v1", tags=["FileTransfer"])

_MAX_FILE_BYTES = 100 * 1024 * 1024   # 100 MB
_MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500 MB


# ── Internal Utilities ───────────────────────────────────────────────────────

def _get_src_root(project_id: str, group_id: str | None = None):
    """Returns normalized absolute src_root path for the given project_id.

    0115: delegates to the shared resolver (storage.paths) so an optional
    group_id routes to the group's git worktree when one is registered; the
    group-less call keeps the pre-0115 project-branch behavior unchanged.

    0327 T0004 (B0001): when a group_id IS supplied the resolution is fail-closed.
    The shared resolver falls back to the base checkout whenever a worktree cannot
    be resolved — harmless for the internal callers it was built for, but now that
    the file explorer uploads into a selected group, that fallback would drop the
    user's files into the base checkout under a group-branch tab and dirty it for
    every other group's finalize. Same contract as the src-content editor: resolve
    the group's worktree exactly, or refuse (409).
    """
    from modules.flow_gate.storage.paths import resolve_project_src_root

    if group_id:
        from modules.flow_gate.db import groups as _groups
        from modules.flow_gate.services import git_service

        group = _groups.get_by_id(group_id)
        if group is None or group.get("project_id") != project_id:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "GROUP_NOT_FOUND", "message": "Group not found in this project"}},
            )
        root, reason = git_service.effective_src_root_ex(project_id, group_id)
        if root is None:
            raise HTTPException(
                status_code=409,
                detail={"error": {
                    "code": "WORKTREE_UNAVAILABLE",
                    "message": f"Group worktree is unavailable ({reason}); base checkout was not used",
                }},
            )
        return root.resolve()

    root = resolve_project_src_root(project_id)
    if root is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PROJECT_NOT_FOUND", "message": "Project not found"}},
        )
    return root


def _is_valid_relative_path(path: str) -> bool:
    """Validates a relative path.

    - Empty string (project root) is allowed.
    - Rejects ``..`` segments, absolute paths, and drive prefixes.
    """
    if not path:
        return True
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", normalized):
        return False
    for seg in normalized.split("/"):
        if seg == "..":
            return False
    return True


def _is_safe_filename_path(relative_filename: str) -> bool:
    """Checks path segment safety within a filename.

    Rejects empty segments, ``.``, ``..``, and drive prefixes.
    """
    for part in relative_filename.split("/"):
        if part in ("", ".", ".."):
            return False
        if re.match(r"^[A-Za-z]:", part):
            return False
    return True


def _under_root(full_path: str, root: str) -> bool:
    """Returns True if full_path is under root (prefix check including os.sep)."""
    root_prefix = root if root.endswith(os.sep) else root + os.sep
    return full_path.startswith(root_prefix) or full_path == root


def _save_file(full_path_str: str, data: bytes) -> None:
    """Create intermediate directories and write one uploaded file (overwrite).

    0279 T0005 (NR0003 cause 1): split out of upload_files so the blocking mkdir +
    write can be pushed through anyio.to_thread. Run on the event loop it froze
    every other request for the duration of the write — up to 100 MB per file and
    500 MB per request.
    """
    os.makedirs(os.path.dirname(full_path_str), exist_ok=True)
    with open(full_path_str, "wb") as fh:
        fh.write(data)


def _err(status: int, code: str, message: str) -> JSONResponse:
    """P007 §3 error response format."""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _check_project_auth(request: Request, project_id: str):
    """Verifies Bearer token and checks project_id permissions.

    - Worker token: perm_document_read is verified inside verify_bearer() against the token's project.
    - User JWT: after verify_bearer() verification, additionally checks perm_document_read for the requested project_id.

    On success: returns token_rec dict.
    On failure: returns JSONResponse (caller must return immediately).
    """
    token_rec = verify_bearer(request)
    if isinstance(token_rec, JSONResponse):
        return token_rec

    if token_rec.get("_is_user_jwt"):
        user_id: str = token_rec["issued_to"]
        if not has_permission(user_id, project_id, "perm_document_read"):
            return _err(403, "FORBIDDEN", "Insufficient permissions for this action")

    return token_rec


# ── Endpoint ─────────────────────────────────────────────────────

@router.post("/projects/{project_id}/files/upload", response_class=JSONResponse)
async def upload_files(request: Request, project_id: str):
    """Uploads files via multipart/form-data — restores directory structure.

    Form fields:
      - target_path: target upload directory (relative path, empty string = root)
      - files[]: list of files to upload. Filename may include a webkitRelativePath-style prefix.

    Returns:
      { "uploaded": [{ "path": str, "size": int }],
        "skipped":  [{ "path": str, "reason": str }] }

    skip reason codes:
      - "PATH_TRAVERSAL" : path traversal or invalid path
    """
    # Authenticate and verify project permissions
    auth_result = _check_project_auth(request, project_id)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    # Parse form data
    # Starlette's multipart parser loads the entire body into memory.
    # Pre-reject requests over 500 MB using the Content-Length header.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_TOTAL_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Request size exceeds 500 MB"}},
                )
        except ValueError:
            pass

    form = await request.form()
    target_path = str(form.get("target_path") or "").strip()
    file_fields = form.getlist("files[]")

    # files[] absent or empty → 400
    if not file_fields:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_PARAM", "message": "files[] field is empty"}},
        )

    # Validate target_path
    if not _is_valid_relative_path(target_path):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_PARAM", "message": "target_path is invalid"}},
        )

    # Resolve project src_root (0115: an optional group_id form field targets the
    # group's git worktree; absent → unchanged project-branch upload behavior).
    # 0327 T0004: the group branch of _get_src_root hits the git ledger and stats the
    # worktree, so the resolution runs off the event loop (0279 T0005 rule) — this is
    # an async handler and that work would freeze every other in-flight request.
    group_id = str(form.get("group_id") or "").strip() or None
    src_root_path = await anyio.to_thread.run_sync(_get_src_root, project_id, group_id)
    root_str = str(src_root_path)

    uploaded = []
    skipped = []
    total_bytes = 0

    for upload in file_fields:
        # Ignore scalar values that are not UploadFile
        if not hasattr(upload, "filename"):
            continue

        raw_filename = upload.filename or ""
        # Normalize path separators (Windows \ → POSIX /)
        relative_filename = raw_filename.replace("\\", "/").lstrip("/")

        # Validate path segment safety in filename
        if not relative_filename or not _is_safe_filename_path(relative_filename):
            skipped.append({"path": raw_filename, "reason": "PATH_TRAVERSAL"})
            continue

        # Final relative path = target_path + relative_filename
        if target_path:
            combined_rel = target_path.replace("\\", "/").rstrip("/") + "/" + relative_filename
        else:
            combined_rel = relative_filename

        # Normalize with os.path.realpath and check for root escape
        full_path_str = os.path.realpath(os.path.join(root_str, combined_rel))
        if not _under_root(full_path_str, root_str):
            skipped.append({"path": combined_rel, "reason": "PATH_TRAVERSAL"})
            continue

        # Read file data
        data = await upload.read()
        file_size = len(data)

        # Single file size limit — P007 §decision rule 1: return 413 if exceeded
        if file_size > _MAX_FILE_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Single file exceeds 100 MB"}},
            )

        # Cumulative size limit
        total_bytes += file_size
        if total_bytes > _MAX_TOTAL_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Total request size exceeds 500 MB"}},
            )

        # Create intermediate directories (if needed) and save the file
        # (overwrite) — off the event loop, see _save_file.
        await anyio.to_thread.run_sync(_save_file, full_path_str, data)

        # Return POSIX relative path from project root in the response
        saved_rel = os.path.relpath(full_path_str, root_str).replace("\\", "/")
        uploaded.append({"path": saved_rel, "size": file_size})

    return {"uploaded": uploaded, "skipped": skipped}

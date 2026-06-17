"""T534 — File upload endpoint (multipart + directory structure restoration).

Endpoint:
  POST /api/v1/projects/{project_id}/files/upload

Note:
  T533 download endpoint is implemented in tree_routes.py.
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services.auth_outbound import verify_bearer

router = APIRouter(prefix="/api/v1", tags=["FileTransfer"])

_MAX_FILE_BYTES = 100 * 1024 * 1024   # 100 MB
_MAX_TOTAL_BYTES = 500 * 1024 * 1024  # 500 MB


# ── Internal Utilities ───────────────────────────────────────────────────────

def _get_src_root(project_id: str):
    """Returns normalized absolute src_root path for the given project_id."""
    from modules.flow_gate.storage.paths import src_root
    from modules.flow_gate.db import projects as _proj

    row = _proj.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    settings = _proj.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"

    if not project_name:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PROJECT_NOT_FOUND", "message": "Project not found"}},
        )

    return src_root(project_name, branch).resolve()


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

    # Resolve project src_root
    src_root_path = _get_src_root(project_id)
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

        # Create intermediate directories (if needed)
        os.makedirs(os.path.dirname(full_path_str), exist_ok=True)

        # Save file (overwrite)
        with open(full_path_str, "wb") as fh:
            fh.write(data)

        # Return POSIX relative path from project root in the response
        saved_rel = os.path.relpath(full_path_str, root_str).replace("\\", "/")
        uploaded.append({"path": saved_rel, "size": file_size})

    return {"uploaded": uploaded, "skipped": skipped}

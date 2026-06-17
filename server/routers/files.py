"""File content retrieval API router.

Endpoints:
  GET /api/files/content?path=<basename|absolute_path>
    - Absolute path: if the file exists, return it directly.
    - Basename: search in order outbox → inbox → processed → accept → reject → cancelled
    - Paths outside allowed directories: 404
    - File not found: 404
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from modules.flow_gate import db

router = APIRouter(tags=["Files"])

_SEARCH_DIRS: list[str] = [
    db.OUTBOX_DIR,
    db.INBOX_DIR,
    db.PROCESSED_DIR,
    db.ACCEPT_DIR,
    db.REJECT_DIR,
    db.CANCELLED_DIR,
]

_ALLOWED_DIRS: frozenset[str] = frozenset(
    os.path.normpath(d) for d in _SEARCH_DIRS
)


def _is_allowed_path(abs_path: str) -> bool:
    """Check whether a path is inside an allowed directory (prevents path traversal)."""
    norm = os.path.normpath(abs_path)
    parent = os.path.normpath(os.path.dirname(norm))
    return parent in _ALLOWED_DIRS


@router.get("/files/content", response_class=PlainTextResponse)
async def get_file_content(path: str = Query(..., description="Filename (basename) or absolute path")):
    """Return file contents as UTF-8 text."""
    if os.path.isabs(path):
        abs_path = os.path.normpath(path)
        if not _is_allowed_path(abs_path):
            raise HTTPException(status_code=404, detail="Not found")
        if not os.path.isfile(abs_path):
            raise HTTPException(status_code=404, detail="Not found")
        with open(abs_path, encoding="utf-8") as f:
            return f.read()

    basename = os.path.basename(path)
    if not basename or basename != path.replace("\\", "/").split("/")[-1]:
        raise HTTPException(status_code=404, detail="Not found")

    for directory in _SEARCH_DIRS:
        candidate = os.path.join(directory, basename)
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as f:
                return f.read()

    raise HTTPException(status_code=404, detail="Not found")

"""Document type query and extension service (D009 r3 — based on 21 document_types).

Global default types (project_id=NULL, is_system=1) cannot be deleted.
Project-specific extension types (project_id=<id>, is_system=0) can be added/deleted.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from modules.flow_gate.db.templates import (
    list_document_types,
    get_document_type_by_code,
    create_document_type,
    delete_document_type,
)


def list_types(
    project_id: str | None = None,
    series: str | None = None,
) -> list[dict]:
    """Return the list of document types.

    When project_id is specified, returns global (NULL) types plus the project's own types.
    """
    return list_document_types(project_id=project_id, series=series)


def get_type(
    type_code: str,
    series: str,
    project_id: str | None = None,
) -> Optional[dict]:
    """Look up a type by type_code + series. Returns None if not found."""
    return get_document_type_by_code(type_code, series, project_id)


def extend_type(data: dict[str, Any]) -> dict:
    """Add a custom document type for a specific project.

    Even if is_system=1 is passed, it is forced to 0 (to protect system types).
    """
    if not data.get("project_id"):
        raise HTTPException(
            status_code=422,
            detail="extend_type requires project_id (global types are reserved for the system).",
        )
    data = dict(data)
    data["is_system"] = 0  # extension types are always non-system

    existing = get_document_type_by_code(
        data["type_code"], data["series"], data["project_id"]
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Type already exists: {data['type_code']} / {data['series']}",
        )
    return create_document_type(data)


def delete_type(type_id: int) -> None:
    """Delete a document type (ValueError for is_system=1 types is converted to 403)."""
    try:
        delete_document_type(type_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

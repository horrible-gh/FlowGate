"""Template upload / linking / render service (aligned with D009 r3 / D013 r1).

Template files are in Jinja2 format, computed via storage/paths.py
and safely written with filesystem.safe_write.

Render output is stored at the path pointed to by document_path().
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from modules.flow_gate.db.templates import (
    get_template_by_type,
    create_template,
    delete_template,
    get_template,
)
from modules.flow_gate.storage import paths as storage_paths


# ── Internal helpers ─────────────────────────────────────────────────────────────────

def _template_storage_path(type_code: str, project_id: str | None) -> Path:
    """Compute the storage path for a template file.

    {storage_root}/templates/{project_id or '_global'}/{type_code}.j2
    """
    root = storage_paths.get_storage_root()
    proj_dir = project_id if project_id else "_global"
    return root / "templates" / proj_dir / f"{type_code}.j2"


def _safe_write(path: Path, content: bytes | str) -> None:
    """Safely write content to path (auto-creating parent directories)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    encoding = None if isinstance(content, bytes) else "utf-8"
    with open(path, mode, encoding=encoding) as f:
        f.write(content)


# ── Public API ──────────────────────────────────────────────────────────────────

def save_template(
    content: bytes | str,
    type_code: str,
    project_id: str | None,
    uploaded_by: str,
) -> dict:
    """Save a template file to storage and register it in the DB.

    If a template is already registered, only overwrite the file and reuse the
    DB record (template_path is deterministic, so no duplicate INSERT or UPDATE).
    """
    tmpl_path = _template_storage_path(type_code, project_id)
    _safe_write(tmpl_path, content)

    existing = get_template_by_type(type_code, project_id)
    if existing:
        return existing

    return create_template({
        "project_id": project_id,
        "type_code": type_code,
        "template_path": str(tmpl_path),
        "is_active": 1,
        "uploaded_by": uploaded_by,
    })


def get_template(type_code: str, project_id: str | None = None) -> Optional[dict]:
    """Return registered template info. Returns None if not found."""
    return get_template_by_type(type_code, project_id)


def render_template(
    doc_id: str,
    type_code: str,
    project_id: str,
    context: dict[str, Any],
    group_code: str,
    doc_code: str,
    filename: str,
    subgroup_code: str | None = None,
    module: str = "none",
) -> Path:
    """Render a template and save the output file to the document storage path.

    Raises 404 if no template is found. Raises 500 on Jinja2 rendering failure.

    Returns: Path of the saved output file
    """
    tmpl_record = get_template_by_type(type_code, project_id)
    if tmpl_record is None:
        # global fallback
        tmpl_record = get_template_by_type(type_code, None)
    if tmpl_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active template found for type '{type_code}'.",
        )

    tmpl_path = Path(tmpl_record["template_path"])
    if not tmpl_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Template file not found: {tmpl_path}",
        )

    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
        env = Environment(
            loader=FileSystemLoader(str(tmpl_path.parent)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        tmpl = env.get_template(tmpl_path.name)
        rendered = tmpl.render(**context)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Template rendering failed: {exc}",
        ) from exc

    out_path = storage_paths.document_path(
        project_id=project_id,
        group_code=group_code,
        doc_code=doc_code,
        filename=filename,
        subgroup_code=subgroup_code,
        module=module,
    )
    _safe_write(out_path, rendered)
    return out_path

"""Project settings service (D018 r1 §C).

- Document type CRUD
- Template upload/management
- Path (project_settings) read/write
- Enqueue numbering-digit changes (using numbering_jobs)
"""
from __future__ import annotations

import os
import uuid

from modules.flow_gate.db import numbering_jobs as _nj
from modules.flow_gate.db import projects as _proj
from modules.flow_gate.db import templates as _tpl
from modules.flow_gate.db.connection import get_store
from modules.flow_gate import template_provision as _tp



def list_document_types(project_id: str, locale: str = "ko") -> list[dict]:
    return _tpl.list_document_types(project_id=project_id, locale=locale)



def create_document_type(project_id: str, data: dict) -> dict:
    return _tpl.create_document_type({**data, "project_id": project_id})



def update_document_type(project_id: str, type_id: int, data: dict) -> dict | None:
    row = _tpl.get_document_type(type_id)
    if not row or (row.get("project_id") != project_id and row.get("project_id") is not None):
        return None
    return _tpl.update_document_type(type_id, data)



def delete_document_type(project_id: str, type_id: int) -> bool:
    row = _tpl.get_document_type(type_id)
    if not row:
        return False
    if row.get("project_id") not in (None, project_id):
        return False
    if row.get("is_system"):
        raise ValueError("System-reserved document types cannot be deleted.")
    _tpl.delete_document_type(type_id)
    return True


_TEMPLATE_STORE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "templates", "document_type_templates"
)
_MAX_TEMPLATE_SIZE = 500 * 1024



def list_templates(project_id: str, type_code: str | None = None) -> list[dict]:
    store = get_store()
    sql = (
        "SELECT id, project_id, type_code, is_active, uploaded_by, uploaded_at "
        "FROM document_type_templates WHERE project_id = ?"
    )
    params: list = [project_id]
    if type_code:
        sql += " AND type_code = ?"
        params.append(type_code)
    sql += " ORDER BY uploaded_at DESC, id DESC"
    rows = store._fetch_all(sql, params)
    # E1: held locales per row; template_path is omitted (AC-1 — no path in mgmt UI).
    for row in rows:
        row["locales"] = _tpl.available_locales(row["id"])
    return rows



def upload_template(
    project_id: str,
    type_code: str,
    filename: str,
    content: bytes,
    uploaded_by: str | None = None,
) -> dict:
    if len(content) > _MAX_TEMPLATE_SIZE:
        raise ValueError("Template file size must not exceed 500KB.")

    safe_filename = os.path.basename(filename or "template.md")
    if not safe_filename.lower().endswith(".md"):
        raise ValueError("Template files must use the .md extension.")

    os.makedirs(_TEMPLATE_STORE_DIR, exist_ok=True)
    safe_name = f"{project_id}_{type_code}_{uuid.uuid4().hex[:8]}_{safe_filename}"
    dest = os.path.join(_TEMPLATE_STORE_DIR, safe_name)
    with open(dest, "wb") as file_handle:
        file_handle.write(content)

    store = get_store()
    store._execute(
        "INSERT INTO document_type_templates (project_id, type_code, template_path, is_active, uploaded_by, uploaded_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'))"
        " ON CONFLICT(project_id, type_code) DO UPDATE SET"
        " template_path=excluded.template_path, is_active=excluded.is_active,"
        " uploaded_by=excluded.uploaded_by, uploaded_at=excluded.uploaded_at",
        [project_id, type_code, dest, 0, uploaded_by],
    )
    row = store._fetch_one(
        "SELECT * FROM document_type_templates WHERE project_id = ? AND type_code = ?",
        [project_id, type_code],
    )
    return row



def update_template(project_id: str, template_id: int, data: dict) -> dict | None:
    row = _tpl.get_template(template_id)
    if not row or row.get("project_id") != project_id:
        return None

    allowed = {"is_active"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if "is_active" in updates and updates["is_active"]:
        store = get_store()
        store._execute(
            "UPDATE document_type_templates SET is_active = 0"
            " WHERE project_id = ? AND type_code = ? AND id != ?",
            [project_id, row["type_code"], template_id],
        )
    if updates:
        store = get_store()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        store._execute(
            f"UPDATE document_type_templates SET {set_clause} WHERE id = ?",
            [*updates.values(), template_id],
        )
    return _tpl.get_template(template_id)



def delete_template(project_id: str, template_id: int) -> bool:
    row = _tpl.get_template(template_id)
    if not row or row.get("project_id") != project_id:
        return False
    template_path = row.get("template_path")
    _tpl.delete_template(template_id)
    if template_path and os.path.exists(template_path):
        try:
            os.remove(template_path)
        except OSError:
            pass
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Template-body content management (group 0024 — P0011 E1~E7).
# Bodies live in document_type_template_contents (option D). Ownership-fail → None
# (router → 404); validation-fail → TemplateValidationError (422);
# ko-fallback delete guard → KoFallbackProtected (409).
# ─────────────────────────────────────────────────────────────────────────────


class KoFallbackProtected(Exception):
    """Last 'ko' body cannot be deleted while other locales exist (P0011 §4-6 → 409)."""


def get_template_registry(project_id: str, type_code: str) -> dict | None:
    """E1 — registry row for (project, type) + held locales. None if absent."""
    return _tpl.registry_with_locales(project_id, type_code)


def list_template_contents(project_id: str, template_id: int) -> list[dict] | None:
    """E2 — locale body metadata (no content). None if template not owned."""
    if not _tpl.template_owned_by(template_id, project_id):
        return None
    return _tpl.list_content_meta(template_id)


def get_template_content(
    project_id: str, template_id: int, locale: str
) -> dict | None:
    """E3 — single locale body (full content). None if not owned / not present."""
    if not _tpl.template_owned_by(template_id, project_id):
        return None
    return _tpl.get_content_row(template_id, locale)


def register_template_content(
    project_id: str, type_code: str, locale: str, content: str, user_id: str | None
) -> dict:
    """E4 — ensure registry (is_active preserved) + first/replacement body.

    Raises TemplateValidationError (422) on invalid locale/content.
    Returns the E1-shaped registry summary (id, type_code, is_active, locales, …).
    """
    _tp.validate_locale(locale)
    _tp.validate_content(content)
    with get_store().transaction():
        template_id = _tpl.ensure_registry(project_id, type_code, user_id)
        _tpl.upsert_content(template_id, locale, content, user_id)
    return _tpl.registry_with_locales(project_id, type_code)  # type: ignore[return-value]


def put_template_content(
    project_id: str, template_id: int, locale: str, content: str, user_id: str | None
) -> dict | None:
    """E5 — register/replace a locale body (no redeploy). None if not owned.

    Raises TemplateValidationError (422). Returns {locale, bytes, updated_*, created}.
    """
    _tp.validate_locale(locale)
    _tp.validate_content(content)
    if not _tpl.template_owned_by(template_id, project_id):
        return None
    with get_store().transaction():
        created = _tpl.upsert_content(template_id, locale, content, user_id)
    row = _tpl.get_content_row(template_id, locale)
    return {
        "template_id": template_id,
        "locale": locale,
        "bytes": _tp.bytelen(row["content"]),  # type: ignore[index]
        "updated_by": row["updated_by"],  # type: ignore[index]
        "updated_at": row["updated_at"],  # type: ignore[index]
        "created": created,
    }


def delete_template_content(
    project_id: str, template_id: int, locale: str
) -> bool | None:
    """E6 — delete a locale body. None if not owned; raises KoFallbackProtected
    (409) if deleting the last 'ko' while other locales remain. True if deleted,
    False if the locale had no body.
    """
    if not _tpl.template_owned_by(template_id, project_id):
        return None
    if locale == _tp.FALLBACK_LOCALE and _tpl.count_other_locales(template_id, locale) > 0:
        raise KoFallbackProtected(
            "Cannot delete the 'ko' fallback content while other locales exist."
        )
    with get_store().transaction():
        return _tpl.delete_content(template_id, locale)



def get_project_settings(project_id: str) -> dict | None:
    return _proj.get_settings(project_id)



def update_project_settings(project_id: str, data: dict) -> dict:
    allowed = {
        "group_structure",
        "digits_group",
        "digits_sub_group",
        "digits_type",
        "storage_root_override",
        "source_mode_override",
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    mode = updates.get("source_mode_override")
    if mode is not None and mode not in {"local", "remote"}:
        raise ValueError("source_mode_override must be one of: local, remote, null")
    current = _proj.get_settings(project_id) or {}
    merged = {**current, **updates, "project_id": project_id}
    return _proj.upsert_settings(project_id, merged)



def get_numbering_impact(project_id: str) -> dict:
    """Calculate the number of items affected by reformatting."""
    store = get_store()
    docs = store._fetch_one("SELECT COUNT(*) as cnt FROM documents WHERE project_id = ?", [project_id])
    groups = store._fetch_one("SELECT COUNT(*) as cnt FROM groups WHERE project_id = ?", [project_id])
    subs = store._fetch_one(
        "SELECT COUNT(*) as cnt FROM sub_groups sg"
        " INNER JOIN groups g ON g.group_id = sg.group_id"
        " WHERE g.project_id = ?",
        [project_id],
    )
    return {
        "documents": docs["cnt"] if docs else 0,
        "groups": groups["cnt"] if groups else 0,
        "sub_groups": subs["cnt"] if subs else 0,
    }



def enqueue_numbering_migrate(
    project_id: str,
    target: str,
    from_width: int,
    to_width: int,
    requested_by: str,
) -> dict:
    """Enqueue a numbering-digit change job. Raise 409 when another job is already running."""
    running = _nj.list_by_project(project_id, status="running")
    queued = _nj.list_by_project(project_id, status="queued")
    if running or queued:
        raise ValueError(f"Project {project_id} already has a numbering job in progress.")
    return _nj.create(
        {
            "project_id": project_id,
            "requested_by": requested_by,
            "target": target,
            "from_width": from_width,
            "to_width": to_width,
            "status": "queued",
        }
    )



def get_numbering_job(job_id: int) -> dict | None:
    return _nj.get_by_id(job_id)



def verify_numbering(project_id: str) -> dict:
    """Run verify_id_widths."""
    try:
        from modules.flow_gate.numbering.verify import verify_id_widths

        report = verify_id_widths(project_id)
        if hasattr(report, "to_dict"):
            return report.to_dict()
        return report.__dict__ if hasattr(report, "__dict__") else report
    except Exception as exc:
        return {"error": str(exc), "project_id": project_id}

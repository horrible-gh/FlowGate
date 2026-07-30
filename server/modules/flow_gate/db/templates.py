"""Document type / template CRUD."""
from __future__ import annotations
from typing import Optional, Any
from .connection import get_store, now_iso


def get_document_type(id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM document_types WHERE id = ?", [id]
    )


def get_document_type_by_code(
    type_code: str, series: str, project_id: str | None = None
) -> Optional[dict]:
    store = get_store()
    if project_id is None:
        return store._fetch_one(
            "SELECT * FROM document_types "
            "WHERE type_code = ? AND series = ? AND project_id IS NULL",
            [type_code, series],
        )
    return store._fetch_one(
        "SELECT * FROM document_types "
        "WHERE type_code = ? AND series = ? AND project_id = ?",
        [type_code, series, project_id],
    )


def list_document_types(
    project_id: str | None = None,
    series: str | None = None,
    locale: str = "ko",
) -> list[dict]:
    store = get_store()
    # Build WHERE conditions for the outer query
    cond_parts = ["1=1"]
    cond_params: list = []
    if project_id is None:
        cond_parts.append("dt.project_id IS NULL")
    else:
        cond_parts.append("(dt.project_id IS NULL OR dt.project_id = ?)")
        cond_params.append(project_id)
    if series:
        cond_parts.append("dt.series = ?")
        cond_params.append(series)
    cond = " AND ".join(cond_parts)

    # locale param goes first (once for type_name, once for description). project_id
    # params drive the active template lookup.
    params: list = [locale, locale, project_id, project_id, project_id, project_id] + cond_params
    sql = f"""
        SELECT dt.id,
               dt.project_id,
               dt.type_code,
               dt.series,
               dt.color,
               dt.is_system,
               dt.is_active,
               dt.sort_order,
               dt.created_at,
               dt.updated_at,
               COALESCE(
                   (SELECT dtn.type_name FROM document_type_names dtn
                    WHERE  dtn.document_type_id = dt.id AND dtn.locale = ?),
                   (SELECT dtn.type_name FROM document_type_names dtn
                    WHERE  dtn.document_type_id = dt.id AND dtn.locale = 'ko'),
                   dt.type_code
               ) AS type_name,
               COALESCE(
                   (SELECT dtd.description FROM document_type_descriptions dtd
                    WHERE  dtd.document_type_id = dt.id AND dtd.locale = ?),
                   (SELECT dtd.description FROM document_type_descriptions dtd
                    WHERE  dtd.document_type_id = dt.id AND dtd.locale = 'ko'),
                   dt.description
               ) AS description,
               (
                   SELECT dtt.template_path
                   FROM   document_type_templates dtt
                   WHERE  dtt.type_code = dt.type_code
                   AND    dtt.is_active = 1
                   AND    (
                              (? IS NOT NULL AND dtt.project_id = ?)
                              OR (? IS NULL AND dtt.project_id IS NULL)
                              OR dtt.project_id IS NULL
                          )
                   ORDER  BY CASE WHEN dtt.project_id = ? THEN 0 ELSE 1 END, dtt.id DESC
                   LIMIT  1
               ) AS template_path
        FROM   document_types dt
        WHERE  {cond}
        ORDER  BY dt.series, dt.sort_order, dt.type_code
    """
    try:
        return store._fetch_all(sql, params)
    except Exception:
        # Fallback for environments where document_type_names table does not yet exist
        fallback_sql = "SELECT * FROM document_types WHERE 1=1"
        fallback_params: list = []
        if project_id is None:
            fallback_sql += " AND project_id IS NULL"
        else:
            fallback_sql += " AND (project_id IS NULL OR project_id = ?)"
            fallback_params.append(project_id)
        if series:
            fallback_sql += " AND series = ?"
            fallback_params.append(series)
        fallback_sql += " ORDER BY series, sort_order, type_code"
        return store._fetch_all(fallback_sql, fallback_params)


def create_document_type(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO document_types "
        "(project_id, type_code, series, color, is_system, is_active, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            data.get("project_id"), data["type_code"], data["series"],
            data.get("color"),
            data.get("is_system", 0), data.get("is_active", 1), data.get("sort_order", 0),
            now, now,
        ],
    )
    row = get_document_type_by_code(
        data["type_code"], data["series"], data.get("project_id")
    )
    # Store localized name in document_type_names if provided
    type_name = data.get("type_name")
    locale = data.get("locale", "ko")
    if type_name and row:
        try:
            store._execute(
                "INSERT INTO document_type_names (document_type_id, locale, type_name)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(document_type_id, locale) DO UPDATE SET"
                " type_name = excluded.type_name",
                [row["id"], locale, type_name],
            )
        except Exception:
            pass  # document_type_names may not exist in legacy environments
    # Store localized description in document_type_descriptions if provided
    description = data.get("description")
    if description and row:
        try:
            store._execute(
                "INSERT INTO document_type_descriptions (document_type_id, locale, description)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(document_type_id, locale) DO UPDATE SET"
                " description = excluded.description",
                [row["id"], locale, description],
            )
        except Exception:
            pass  # document_type_descriptions may not exist in legacy environments
    return row  # type: ignore[return-value]


def update_document_type(id: int, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    # Extract type_name/description before building the SQL to avoid updating removed columns
    type_name = updates.pop("type_name", None)
    description = updates.pop("description", None)
    locale = updates.pop("locale", "ko")
    updates = {k: v for k, v in updates.items() if k not in ("id", "created_at")}
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE document_types SET {set_clause} WHERE id = ?",
        [*updates.values(), id],
    )
    if type_name:
        try:
            store._execute(
                "INSERT INTO document_type_names (document_type_id, locale, type_name)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(document_type_id, locale) DO UPDATE SET"
                " type_name = excluded.type_name",
                [id, locale, type_name],
            )
        except Exception:
            pass  # document_type_names may not exist in legacy environments
    if description:
        try:
            store._execute(
                "INSERT INTO document_type_descriptions (document_type_id, locale, description)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(document_type_id, locale) DO UPDATE SET"
                " description = excluded.description",
                [id, locale, description],
            )
        except Exception:
            pass  # document_type_descriptions may not exist in legacy environments
    return get_document_type(id)


def delete_document_type(id: int) -> None:
    store = get_store()
    row = get_document_type(id)
    if row and row.get("is_system"):
        raise ValueError(f"System reserved document type id={id} cannot be deleted.")
    store._execute("DELETE FROM document_types WHERE id = ?", [id])


def get_template(id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM document_type_templates WHERE id = ?", [id]
    )


def get_template_by_type(
    type_code: str, project_id: str | None = None
) -> Optional[dict]:
    store = get_store()
    if project_id is None:
        return store._fetch_one(
            "SELECT * FROM document_type_templates "
            "WHERE type_code = ? AND project_id IS NULL AND is_active = 1",
            [type_code],
        )
    return store._fetch_one(
        "SELECT * FROM document_type_templates "
        "WHERE type_code = ? AND project_id = ? AND is_active = 1",
        [type_code, project_id],
    )


def create_template(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO document_type_templates "
        "(project_id, type_code, template_path, is_active, uploaded_by, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            # template_path is nullable since migration 044 (option D keeps the body in
            # document_type_template_contents, not on disk).
            data.get("project_id"), data["type_code"], data.get("template_path"),
            data.get("is_active", 1), data.get("uploaded_by"), data.get("uploaded_at", now),
        ],
    )
    return get_template_by_type(  # type: ignore[return-value]
        data["type_code"], data.get("project_id")
    )


def delete_template(id: int) -> None:
    get_store()._execute("DELETE FROM document_type_templates WHERE id = ?", [id])


# ─────────────────────────────────────────────────────────────────────────────
# Template-body provision / management (group 0024 — D0010/P0011/P0012/L0013/DB0014)
# document_type_template_contents holds one markdown body per (template_id, locale).
# These functions are the SQL realisation of DB0014 §4 (the templates_db.* / types_db.*
# semantic contracts L0013 §2 declared). Path/usable() checks live in the app layer.
# ─────────────────────────────────────────────────────────────────────────────

# SUPPORTED_LOCALES order (ko, ja, en) — used for deterministic enumeration ordering.
_LOCALE_ORDER_SQL = (
    "CASE locale WHEN 'ko' THEN 0 WHEN 'ja' THEN 1 WHEN 'en' THEN 2 ELSE 3 END"
)


def is_design_type(type_code: str) -> bool:
    """DB0014 §4-1 — single source of truth for 'is this a valid design type?'.

    True iff document_types has (type_code, series='design'). Shared by the
    Next-Step provision gate and the resolver (L0013 §5-6 equivalence invariant).
    """
    row = get_store()._fetch_one(
        "SELECT 1 FROM document_types "
        "WHERE type_code = ? AND series = 'design' LIMIT 1",
        [type_code],
    )
    return row is not None


def active_registry_rows(type_code: str, project_id: str | None) -> list[dict]:
    """DB0014 §4-2 — active registry rows for a type, project-first then global.

    Returns ≤2 rows ordered (project scope, then global), id DESC tiebreak. Only
    is_active=1 rows; the project row and/or the global (project_id IS NULL) row.
    """
    return get_store()._fetch_all(
        "SELECT t.id, t.project_id, t.type_code, t.is_active "
        "FROM document_type_templates t "
        "WHERE t.type_code = ? "
        "  AND t.is_active = 1 "
        "  AND (t.project_id = ? OR t.project_id IS NULL) "
        "ORDER BY (CASE WHEN t.project_id = ? THEN 0 ELSE 1 END) ASC, t.id DESC",
        [type_code, project_id, project_id],
    )


def content_for(template_id: int, locale: str) -> Optional[str]:
    """DB0014 §4-3 — body for (template_id, locale), or None. PK exact match."""
    row = get_store()._fetch_one(
        "SELECT content FROM document_type_template_contents "
        "WHERE template_id = ? AND locale = ?",
        [template_id, locale],
    )
    return row["content"] if row else None


def available_locales(template_id: int) -> list[str]:
    """DB0014 §4-4 — locales a template row actually holds, SUPPORTED_LOCALES order."""
    rows = get_store()._fetch_all(
        "SELECT locale FROM document_type_template_contents "
        f"WHERE template_id = ? ORDER BY {_LOCALE_ORDER_SQL}",
        [template_id],
    )
    return [r["locale"] for r in rows]


def template_owned_by(template_id: int, project_id: str) -> bool:
    """P0011 §5 ownership gate — template_id belongs to project_id (else 404).

    Global rows (project_id IS NULL) are not project-scoped management targets.
    """
    row = get_store()._fetch_one(
        "SELECT 1 FROM document_type_templates "
        "WHERE id = ? AND project_id = ?",
        [template_id, project_id],
    )
    return row is not None


def registry_with_locales(project_id: str, type_code: str) -> Optional[dict]:
    """P0011 E1 — registry row for (project, type) + held locales (ko,ja,en order)."""
    row = get_store()._fetch_one(
        "SELECT id, project_id, type_code, is_active, uploaded_by, uploaded_at "
        "FROM document_type_templates "
        "WHERE project_id = ? AND type_code = ?",
        [project_id, type_code],
    )
    if not row:
        return None
    row = dict(row)
    row["locales"] = available_locales(row["id"])
    return row


def list_content_meta(template_id: int) -> list[dict]:
    """P0011 E2 — locale bodies (excluding raw content), byte-accurate, ko/ja/en order."""
    # Byte size is computed in Python: SQLite's LENGTH(CAST(content AS BLOB)) is not
    # portable (MySQL/PostgreSQL lack a BLOB cast); the locale rows are few (ko/ja/en).
    rows = get_store()._fetch_all(
        "SELECT locale, content, updated_by, updated_at "
        "FROM document_type_template_contents "
        f"WHERE template_id = ? ORDER BY {_LOCALE_ORDER_SQL}",
        [template_id],
    )
    for r in rows:
        content = r.pop("content", None) or ""
        r["bytes"] = len(content.encode("utf-8"))
    return rows


def get_content_row(template_id: int, locale: str) -> Optional[dict]:
    """P0011 E3 — single locale body, full content row."""
    return get_store()._fetch_one(
        "SELECT template_id, locale, content, updated_by, updated_at "
        "FROM document_type_template_contents "
        "WHERE template_id = ? AND locale = ?",
        [template_id, locale],
    )


def ensure_registry(project_id: str, type_code: str, user_id: str | None) -> int:
    """P0011 E4 (a) — ensure a registry row exists (is_active=0 on create),
    preserving is_active/uploaded_* if it already exists. Returns the row id.
    """
    store = get_store()
    store._execute(
        "INSERT INTO document_type_templates "
        "(project_id, type_code, template_path, is_active, uploaded_by, uploaded_at) "
        "VALUES (?, ?, NULL, 0, ?, ?) "
        "ON CONFLICT(project_id, type_code) DO NOTHING",
        [project_id, type_code, user_id, now_iso()],
    )
    row = store._fetch_one(
        "SELECT id FROM document_type_templates "
        "WHERE project_id = ? AND type_code = ?",
        [project_id, type_code],
    )
    return row["id"]  # type: ignore[index]


def upsert_content(
    template_id: int, locale: str, content: str, user_id: str | None
) -> bool:
    """P0011 E5 — register/replace a locale body (no redeploy, idempotent timestamp).

    Returns True if a new row was inserted (created), False if it updated/no-op.
    DB0014 §4-6 E5: unchanged content re-PUT keeps updated_by/at (idempotent).
    """
    store = get_store()
    existed = store._fetch_one(
        "SELECT 1 FROM document_type_template_contents "
        "WHERE template_id = ? AND locale = ?",
        [template_id, locale],
    )
    store._execute(
        "INSERT INTO document_type_template_contents "
        "(template_id, locale, content, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(template_id, locale) DO UPDATE SET "
        "  content    = excluded.content, "
        "  updated_by = CASE WHEN document_type_template_contents.content = excluded.content "
        "                    THEN document_type_template_contents.updated_by ELSE excluded.updated_by END, "
        "  updated_at = CASE WHEN document_type_template_contents.content = excluded.content "
        "                    THEN document_type_template_contents.updated_at ELSE excluded.updated_at END",
        [template_id, locale, content, user_id, now_iso()],
    )
    return existed is None


def count_other_locales(template_id: int, locale: str) -> int:
    """P0011 E6 (a) — number of bodies in OTHER locales (ko-fallback delete guard)."""
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS others FROM document_type_template_contents "
        "WHERE template_id = ? AND locale <> ?",
        [template_id, locale],
    )
    return row["others"] if row else 0


def delete_content(template_id: int, locale: str) -> bool:
    """P0011 E6 (b) — delete a locale body. Returns True if a row existed."""
    store = get_store()
    existed = store._fetch_one(
        "SELECT 1 FROM document_type_template_contents "
        "WHERE template_id = ? AND locale = ?",
        [template_id, locale],
    )
    store._execute(
        "DELETE FROM document_type_template_contents "
        "WHERE template_id = ? AND locale = ?",
        [template_id, locale],
    )
    return existed is not None


def set_template_active(template_id: int, project_id: str, is_active: int) -> bool:
    """P0011 E7 — activation toggle (project-scoped). Returns True if a row matched."""
    store = get_store()
    matched = store._fetch_one(
        "SELECT 1 FROM document_type_templates WHERE id = ? AND project_id = ?",
        [template_id, project_id],
    )
    if not matched:
        return False
    store._execute(
        "UPDATE document_type_templates SET is_active = ? "
        "WHERE id = ? AND project_id = ?",
        [is_active, template_id, project_id],
    )
    return True

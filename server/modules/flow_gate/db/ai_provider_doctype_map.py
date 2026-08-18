"""Per-document-type AI provider assignment CRUD (flowgate.default.0317 D0004 implementation).

Stores the project-scoped "document type -> provider" assignment rules the continuous chain's hop
provider decider reads at each step boundary. One row = one (project_id, doc_type)
assignment pointing at an ai_providers.provider_id.

The whole map for a project is replaced in ONE transaction (delete-all -> insert) so a
failed save never leaves a partial map, mirroring ai_providers.replace_scope. Reads return
rows in doc_type order for a stable UI; the resolver validates the provider against the
project's effective enabled chain, so this layer stays a plain store.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso

_COLUMNS = "id, project_id, doc_type, provider_id, created_at, updated_at"


def list_for_project(project_id: str) -> list[dict]:
    """Every assignment for one project in doc_type order."""
    return get_store()._fetch_all(
        f"SELECT {_COLUMNS} FROM ai_provider_doctype_map "
        "WHERE project_id = ? ORDER BY doc_type",
        [project_id],
    )


def get_provider_for_type(project_id: str, doc_type: str) -> Optional[str]:
    """The assigned provider_id for one doc type, or None when unmapped.

    Pure lookup — callers (ai_settings_service.resolve_doctype_provider) still confirm the
    provider is in the project's effective enabled chain before pinning it."""
    row = get_store()._fetch_one(
        "SELECT provider_id FROM ai_provider_doctype_map "
        "WHERE project_id = ? AND doc_type = ?",
        [project_id, doc_type],
    )
    return row.get("provider_id") if row else None


def replace_for_project(project_id: str, assignments: list[dict]) -> None:
    """Replace one project's whole assignment map atomically (full-replace merge).

    `assignments` are dicts with {"doc_type", "provider_id"} — both already validated by the
    service. An empty list clears the project's map (back to default-provider behavior).
    """
    store = get_store()
    now = now_iso()
    with store.transaction() as s:
        s._execute("DELETE FROM ai_provider_doctype_map WHERE project_id = ?", [project_id])
        for a in assignments:
            s._execute(
                "INSERT INTO ai_provider_doctype_map "
                "(project_id, doc_type, provider_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [project_id, a["doc_type"], a["provider_id"], now, now],
            )

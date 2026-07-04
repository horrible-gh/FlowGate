"""Project CRUD."""
from __future__ import annotations
import os
from datetime import datetime
from typing import Optional, Any
from .connection import get_store, now_iso


def get_by_name(project_name: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM projects WHERE project_name = ?", [project_name]
    )


def get_by_id(project_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM projects WHERE project_id = ?", [project_id]
    )


def list_projects(is_active: int | None = None) -> list[dict]:
    store = get_store()
    if is_active is not None:
        return store._fetch_all(
            "SELECT * FROM projects WHERE is_active = ? ORDER BY project_id", [is_active]
        )
    return store._fetch_all("SELECT * FROM projects ORDER BY project_id")


def list_modules(project_id: str) -> list[dict]:
    """Modules for a project = union of groups.module and project_modules,
    mirroring get_group_tree (process_service). The 'none' bucket is titled
    "All" (TR556 convention). Returns [{name, title}] with 'none'/All first. (M036)
    """
    store = get_store()
    # project_modules titles (first-class; authoritative title when present)
    pm_titles: dict[str, str] = {}
    has_pm = store.table_exists("project_modules")
    if has_pm:
        for r in store._fetch_all(
            "SELECT name, title FROM project_modules WHERE project_id = ?", [project_id]
        ):
            name = (r.get("name") or "").strip() or "none"
            pm_titles[name] = (r.get("title") or "").strip() or name
    # union of module labels from both sources
    labels: list[str] = []
    seen: set[str] = set()

    def _add(raw) -> None:
        label = (raw or "").strip() or "none"
        if label not in seen:
            seen.add(label)
            labels.append(label)

    for r in store._fetch_all(
        "SELECT DISTINCT module FROM groups WHERE project_id = ?", [project_id]
    ):
        _add(r.get("module"))
    for name in pm_titles:
        _add(name)
    # 'none'/All first, then the rest alphabetically
    labels.sort(key=lambda l: (l != "none", l))

    def _title(label: str) -> str:
        return "All" if label == "none" else (pm_titles.get(label) or label)

    return [{"name": label, "title": _title(label)} for label in labels]


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO projects (project_id, project_name, description, color, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            data["project_id"], data["project_name"], data.get("description"),
            data.get("color"), data.get("is_active", 1),
            data.get("created_at", now), data.get("updated_at", now),
        ],
    )
    return get_by_id(data["project_id"])  # type: ignore[return-value]


def update(project_id: str, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k not in ("project_id", "created_at")}
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE projects SET {set_clause} WHERE project_id = ?",
        [*updates.values(), project_id],
    )
    return get_by_id(project_id)


def delete(project_id: str) -> None:
    get_store()._execute("DELETE FROM projects WHERE project_id = ?", [project_id])


def get_settings(project_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM project_settings WHERE project_id = ?", [project_id]
    )


def upsert_settings(project_id: str, data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO project_settings "
        "(project_id, group_structure, digits_group, digits_sub_group, digits_type, "
        "storage_root_override, branch, source_mode_override, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET "
        "group_structure=excluded.group_structure, digits_group=excluded.digits_group, "
        "digits_sub_group=excluded.digits_sub_group, digits_type=excluded.digits_type, "
        "storage_root_override=excluded.storage_root_override, branch=excluded.branch, "
        "source_mode_override=excluded.source_mode_override, "
        "updated_at=excluded.updated_at",
        [
            project_id, data.get("group_structure", 2), data.get("digits_group", 4),
            data.get("digits_sub_group", 3), data.get("digits_type", 4),
            data.get("storage_root_override"), data.get("branch", "main"),
            data.get("source_mode_override"), now,
        ],
    )
    return get_settings(project_id)  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# Legacy compatibility API (migrated from store.py, phase 1).
# Ports the store.FlowGateStore methods previously called by the legacy shim in db/__init__.py.
# Preserves signatures and compatibility return keys such as project, docs_root, and module.
# ─────────────────────────────────────────────────────────────────────────────

# project_root is deprecated and absent from D009; keep it in an in-process cache to match store.py.
_LEGACY_PROJECT_ROOTS: dict[str, str] = {}


def get_project_modules(project_id: str) -> list[dict]:
    """Return project_modules entries for get_group_tree, or [] if the table does not exist."""
    store = get_store()
    if not store.table_exists("project_modules"):
        return []
    return store._fetch_all(
        "SELECT * FROM project_modules WHERE project_id = ? ORDER BY name", [project_id]
    )


def get_project_settings() -> list[dict]:
    """Return all project_settings with D009 fields mapped to compatibility keys.

    Maps project_id and storage_root_override to project, docs_root, and project_root.
    project_root is populated from the environment or cache.
    """
    rows = get_store()._fetch_all(
        "SELECT project_id AS project, storage_root_override AS docs_root,"
        " '' AS project_root, updated_at"
        " FROM project_settings ORDER BY project_id"
    )
    for item in rows:
        env_key = f"FLOWGATE_PROJECT_ROOT_OVERRIDE_{item['project']}"
        item["project_root"] = os.environ.get(
            env_key, _LEGACY_PROJECT_ROOTS.get(item["project"], "")
        )
    return rows


def get_project_settings_by_project(project: str) -> Optional[dict]:
    """Return settings for a project using compatibility keys."""
    item = get_store()._fetch_one(
        "SELECT project_id AS project, storage_root_override AS docs_root,"
        " '' AS project_root, updated_at"
        " FROM project_settings WHERE project_id = ?",
        [project],
    )
    if item is not None:
        env_key = f"FLOWGATE_PROJECT_ROOT_OVERRIDE_{project}"
        item["project_root"] = os.environ.get(
            env_key, _LEGACY_PROJECT_ROOTS.get(project, "")
        )
    return item


def upsert_project_settings(project: str, docs_root: str, project_root: str = "") -> None:
    """Insert or update project_settings; keep deprecated project_root only in the environment/cache."""
    env_key = f"FLOWGATE_PROJECT_ROOT_OVERRIDE_{project}"
    if project_root:
        _LEGACY_PROJECT_ROOTS[project] = project_root
        os.environ[env_key] = project_root
    else:
        _LEGACY_PROJECT_ROOTS.pop(project, None)
        os.environ.pop(env_key, None)
    now = datetime.now().isoformat()
    storage_root_override = docs_root.strip() if docs_root else ""
    get_store()._execute(
        "INSERT INTO project_settings"
        " (project_id, storage_root_override, updated_at)"
        " VALUES (?, ?, ?)"
        " ON CONFLICT(project_id) DO UPDATE SET"
        "     storage_root_override = excluded.storage_root_override,"
        "     updated_at = excluded.updated_at",
        [project, storage_root_override, now],
    )


def remove_project_settings(project: str) -> None:
    """Delete project_settings."""
    _LEGACY_PROJECT_ROOTS.pop(project, None)
    os.environ.pop(f"FLOWGATE_PROJECT_ROOT_OVERRIDE_{project}", None)
    get_store()._execute(
        "DELETE FROM project_settings WHERE project_id = ?", [project]
    )


def get_allowed_projects() -> list[dict]:
    """Return active projects with compatibility keys project and module (empty string)."""
    return get_store()._fetch_all(
        "SELECT project_id AS project, project_name, '' AS module"
        " FROM projects WHERE is_active = 1 ORDER BY project_id"
    )


def get_allowed_project_names() -> set:
    """Return the set of active project IDs."""
    rows = get_store()._fetch_all(
        "SELECT project_id AS project FROM projects WHERE is_active = 1"
    )
    return {r["project"] for r in rows}


def add_allowed_project(project: str, module: str = "") -> None:
    """Register or activate a project; ignore module because D009 does not include it."""
    now = now_iso()
    get_store()._execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)"
        " ON CONFLICT(project_id) DO UPDATE SET"
        "   is_active = 1,"
        "   updated_at = excluded.updated_at"
        " WHERE projects.is_active = 0",
        [project, project, now, now],
    )


def remove_allowed_project(project: str, module: str = "") -> None:
    """Deactivate a project (soft delete); ignore module."""
    get_store()._execute(
        "UPDATE projects SET is_active = 0, updated_at = ?"
        " WHERE project_id = ?",
        [now_iso(), project],
    )

"""The one access judgment: ``can(user, target, permission_id)`` (design 0004-D §2.2).

The scope of a permission ID is its first word:

* ``system.*``    — system administrators (``users.is_admin``) only.
* ``workspace.*`` — judged on a workspace with ``can_workspace``: the caller's member role
  (owner/admin/member) through ``workspace_role_permissions``, plus the workspace's project
  creation policy and state. Project roles are not used.

Until the one-time grant migration has completed (``permission_service.migration_ready``) only
system administrators pass, in both the project and the workspace scope (design 7.1).
* ``project.*``   — judged on a project with ``can``: a system administrator may do
  everything; anyone else needs the permission in their one role on that project
  (``permission_service``: the highest of direct, team and workspace value). No role means
  not a member, and a role on ``__SYSTEM__`` never counts.

The project is always taken from the stored target (document, group, document type) and never
from a project/group ID the client wrote next to it. When a request names several targets,
every one of them must pass.

List endpoints use ``visible_project_clause`` / ``member_permission_clause``: the projects the
caller may see are computed from their grants and applied as an ``IN (...)`` condition in SQL,
instead of fetching every row and filtering in Python.
"""
from __future__ import annotations

import functools
import inspect
import re
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from modules.flow_gate.db import connection as _connection
from modules.flow_gate.rbac import permission_service as _permission_service

SYSTEM_PROJECT = "__SYSTEM__"

# Request fields that name an already stored document (path, query or body).
_DOC_FIELDS = ("doc_id", "prev_doc_id", "parent_doc_id")
# Request fields that name an already stored group.
_GROUP_FIELDS = ("group_id", "gid")
# Request fields that name a project directly. Used only when nothing stored is
# named, or in addition to stored targets so a mismatched pair cannot pass.
_PROJECT_FIELDS = ("project_id",)
# Endpoint parameters holding the signed-in user, never a target.
_USER_KEYS = ("current_user", "user")

# The read permission a request without a stored target may still use.
READ_PERMISSION = "project.document.read"


def get_store():
    """The store, looked up at call time (a test may swap it after this module is imported)."""
    return _connection.get_store()


def is_admin(user: Optional[dict]) -> bool:
    return bool(user and user.get("is_admin"))


def scope_of(permission_id: str) -> str:
    """'system' / 'workspace' / 'project' (anything else is treated as project scope)."""
    head = (permission_id or "").split(".", 1)[0]
    return head if head in ("system", "workspace") else "project"


def is_member(user_id: Optional[str], project_id: Optional[str]) -> bool:
    """True when the user holds a role on exactly this project (direct, team or workspace)."""
    if not user_id or not project_id or project_id == SYSTEM_PROJECT:
        return False
    return _permission_service.effective_role(user_id, project_id) is not None


def can(user: Optional[dict], project_id: Optional[str], permission_id: str) -> bool:
    """The one judgment entry: may ``user`` use ``permission_id`` on ``project_id``."""
    if not user:
        return False
    if is_admin(user):
        return True
    if scope_of(permission_id) != "project":
        # system.* is for system administrators; workspace.* is judged on a workspace.
        return False
    user_id = user.get("user_id")
    if not user_id or not project_id or project_id == SYSTEM_PROJECT:
        return False
    return _permission_service.has_permission(user_id, project_id, permission_id)


def require(user: Optional[dict], project_id: Optional[str], permission_id: str) -> None:
    """Raise 403 unless ``can`` allows the request."""
    if not can(user, project_id, permission_id):
        if user and not is_admin(user) and not _permission_service.migration_ready():
            # the grant migration has not completed: only administrators are served (design 7.1)
            raise HTTPException(status_code=403, detail="rbac_migration_incomplete")
        raise HTTPException(status_code=403, detail="Forbidden")


# ── workspace scope (design 2.2-B) ───────────────────────────────────────────

def workspace_member_role(workspace_id: Optional[str], user_id: Optional[str]) -> Optional[str]:
    if not workspace_id or not user_id:
        return None
    row = get_store()._fetch_one(
        "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
        [workspace_id, user_id],
    )
    return (row or {}).get("role")


# The deploy SQL mapping (design 2.4), used only by a schema that predates it.
_WORKSPACE_ROLE_FALLBACK = {
    "owner": {"workspace.view", "workspace.member.manage", "workspace.team.manage", "workspace.admin",
              "workspace.project.create"},
    "admin": {"workspace.view", "workspace.member.manage", "workspace.team.manage", "workspace.project.create"},
    "member": {"workspace.view", "workspace.project.create"},
}


def _workspace_role_permissions(role: str) -> set[str]:
    store = get_store()
    if not _permission_service._table_exists(store, "workspace_role_permissions"):
        return set(_WORKSPACE_ROLE_FALLBACK.get(role, ()))
    rows = store._fetch_all(
        "SELECT permission_id FROM workspace_role_permissions WHERE workspace_role = ?", [role]
    )
    return {row["permission_id"] for row in rows}


_PROJECT_CREATE_ROLES = {
    "owner": ("owner",),
    "owner_admin": ("owner", "admin"),
    "all": ("owner", "admin", "member"),
}


def can_workspace(user: Optional[dict], workspace: Any, permission_id: str) -> bool:
    """May ``user`` use the ``workspace.*`` permission on ``workspace`` (a row or an id).

    The judgment every workspace request goes through. Until the grant migration has
    completed only system administrators pass (design 7.1, the same gate as ``can``); after
    that ``workspace_rule`` decides.
    """
    if not user:
        return False
    if not is_admin(user) and not _permission_service.migration_ready():
        return False
    return workspace_rule(user, workspace, permission_id)


def workspace_rule(user: Optional[dict], workspace: Any, permission_id: str) -> bool:
    """The workspace rule of design 2.2-B on the stored data, without the migration gate.

    A system administrator always may. Anyone else needs a member role whose mapping holds
    the permission. ``workspace.project.create`` also follows the workspace's creation policy
    and needs an active workspace; in a disabled workspace only viewing and the owner's
    administration (re-enabling) remain (2.2-B 4).

    Requests use ``can_workspace``. This function is called directly only by table ③ of the
    grant migration, which is computed while the migration record still says 'running'.
    """
    if not user:
        return False
    ws = workspace if isinstance(workspace, dict) else None
    if ws is None and workspace:
        ws = get_store()._fetch_one("SELECT * FROM workspaces WHERE workspace_id = ?", [workspace])
    if not ws:
        return False
    active = (ws.get("status") or "active") == "active"
    if permission_id == "workspace.project.create" and not active:
        return False
    if is_admin(user):
        return True
    if scope_of(permission_id) != "workspace":
        return False
    role = workspace_member_role(ws.get("workspace_id"), user.get("user_id"))
    if role is None or permission_id not in _workspace_role_permissions(role):
        return False
    if not active and permission_id not in ("workspace.view", "workspace.admin"):
        return False
    if permission_id == "workspace.project.create":
        policy = ws.get("project_create_policy") or "owner_admin"
        return role in _PROJECT_CREATE_ROLES.get(policy, ("owner",))
    return True


# ── list conditions ──────────────────────────────────────────────────────────

def _in_clause(column: str, project_ids: Iterable[str]) -> tuple[str, list]:
    ids = sorted({pid for pid in project_ids if pid and pid != SYSTEM_PROJECT})
    if not ids:
        return " AND 1 = 0", []
    marks = ", ".join("?" for _ in ids)
    return f" AND {column} IN ({marks})", ids


def visible_project_clause(user: Optional[dict], column: str = "project_id") -> tuple[str, list]:
    """SQL fragment limiting ``column`` to projects the user may see.

    Returns ``("", [])`` for a system administrator. The fragment starts with
    ``AND`` so it can be appended to an existing WHERE clause.
    """
    if is_admin(user):
        return "", []
    return _in_clause(column, _permission_service.permitted_projects((user or {}).get("user_id")))


# Aliases reserved for the access subqueries; a listed row's column must not reuse them.
_INNER_ALIASES = frozenset({"acl_member", "acl_role", "acl_perm"})
_QUALIFIED_COLUMN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*$")


def outer_column(column: str) -> str:
    """Validate that ``column`` names the listed row's project column as ``alias.column``.

    List queries join several tables, so a bare ``project_id`` may bind to a different table
    than the listed row. The column must be qualified (for example ``g.project_id``) and must
    not use an alias reserved for the access subqueries.
    """
    match = _QUALIFIED_COLUMN.match(column or "")
    if not match or match.group(1) in _INNER_ALIASES:
        raise ValueError(f"project column must be qualified with the outer table alias: {column!r}")
    return column


def member_permission_clause(
    user: Optional[dict], permission_id: str, column: str
) -> tuple[str, list]:
    """SQL fragment limiting ``column`` to projects where ``can`` allows ``permission_id``.

    The same rule as ``can``: an administrator gets no condition, anyone else gets the projects
    on which their one role holds the permission. ``column`` must be qualified (see
    ``outer_column``). The fragment starts with ``AND``.
    """
    column = outer_column(column)
    if is_admin(user):
        return "", []
    user_id = (user or {}).get("user_id") or ""
    if not user_id:
        return " AND 1 = 0", []
    return _in_clause(column, _permission_service.permitted_projects(user_id, permission_id))


def permitted_project_clause(user_id: Optional[str], permission_id: str, column: str) -> tuple[str, list]:
    """``member_permission_clause`` for the owner of a worker token (judged by the stored
    user row: an administrator owner sees everything, a disabled owner nothing)."""
    column = outer_column(column)
    if not user_id:
        return " AND 1 = 0", []
    row = get_store()._fetch_one("SELECT is_admin, is_active FROM users WHERE user_id = ?", [user_id])
    if not row or not row.get("is_active"):
        return " AND 1 = 0", []
    if row.get("is_admin"):
        return "", []
    return _in_clause(column, _permission_service.permitted_projects(user_id, permission_id))


def project_of_document(doc_id: Optional[str]) -> Optional[str]:
    if not doc_id:
        return None
    row = get_store()._fetch_one(
        "SELECT project_id FROM documents WHERE doc_id = ?", [doc_id]
    )
    return row["project_id"] if row else None


def project_of_group(group_id: Optional[str]) -> Optional[str]:
    if not group_id:
        return None
    row = get_store()._fetch_one(
        "SELECT project_id FROM groups WHERE group_id = ?", [group_id]
    )
    return row["project_id"] if row else None


def project_of_document_type(type_id: Any) -> Optional[str]:
    """The project of a project-specific document type (None for a global type)."""
    if type_id in (None, ""):
        return None
    row = get_store()._fetch_one(
        "SELECT project_id FROM document_types WHERE id = ?", [type_id]
    )
    return (row or {}).get("project_id") or None


def _field_of(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    if isinstance(value, BaseModel):
        return getattr(value, field, None)
    return None


def normalize_id(value: Any) -> Any:
    """An identifier as the services look it up (surrounding blanks removed).

    The check must resolve the same target the endpoint later reads, so an ID
    padded with blanks is judged as the stored target it names.
    """
    return value.strip() if isinstance(value, str) else value


def _values(kwargs: dict, field: str) -> Iterable[Any]:
    """The value of ``field`` taken from plain kwargs and from body models/dicts.

    One level of nesting is followed, so a body holding a list of selections
    (each naming a document) is judged per selection. Values are normalised
    with ``normalize_id``; blank values are skipped.
    """
    direct = normalize_id(kwargs.get(field))
    if isinstance(direct, (str, int)) and not isinstance(direct, bool) and direct != "":
        yield direct
    for key, value in kwargs.items():
        if key in _USER_KEYS:
            continue
        items = [value]
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                nested = getattr(value, name, None)
                if isinstance(nested, list):
                    items.extend(nested)
        elif isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, list):
                    items.extend(nested)
        for item in items:
            found = normalize_id(_field_of(item, field))
            if isinstance(found, (str, int)) and not isinstance(found, bool) and found != "":
                yield found


def target_projects(kwargs: dict) -> tuple[set[str], bool]:
    """Projects named by a request, and whether any stored target was found.

    Stored documents and groups decide their own project. An explicit project ID
    is added as a further target so a request cannot pair someone else's
    document with its own project.
    """
    projects: set[str] = set()
    stored = False
    for field in _DOC_FIELDS:
        for value in _values(kwargs, field):
            project = project_of_document(str(value))
            if project:
                projects.add(project)
                stored = True
    for field in _GROUP_FIELDS:
        for value in _values(kwargs, field):
            project = project_of_group(str(value))
            if project:
                projects.add(project)
                stored = True
    type_id = kwargs.get("type_id")
    if isinstance(type_id, int):
        project = project_of_document_type(type_id)
        if project:
            projects.add(project)
            stored = True
    for field in _PROJECT_FIELDS:
        for value in _values(kwargs, field):
            projects.add(str(value))
    return projects, stored


def _current_user(kwargs: dict) -> Optional[dict]:
    for name in _USER_KEYS:
        value = kwargs.get(name)
        if isinstance(value, dict):
            return value
    return None


def check_target(kwargs: dict, permission_id: str) -> None:
    """Judge one endpoint call from its resolved arguments."""
    user = _current_user(kwargs)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if is_admin(user):
        return
    projects, _stored = target_projects(kwargs)
    if not projects:
        # Nothing project-scoped is named (for example a missing document, which
        # the endpoint answers with 404, or a global type list). Reads stay open
        # to signed-in users; a change without a target is for system administrators.
        if permission_id == READ_PERMISSION:
            return
        raise HTTPException(status_code=403, detail="Forbidden")
    for project in projects:
        require(user, project, permission_id)


def require_target_permission(permission_id: str):
    """Endpoint decorator: check ``permission_id`` on the request's real target.

    Keeps the wrapped function's signature so FastAPI still resolves its
    parameters (including ``current_user``) exactly as before.
    """

    def decorator(func):
        signature = inspect.signature(func)
        # FastAPI resolves string annotations against the endpoint's __globals__,
        # which would be this module's for the wrapper. Publish the signature with
        # the annotations already evaluated in the endpoint's own module.
        try:
            resolved = inspect.signature(func, eval_str=True)
        except Exception:  # noqa: BLE001 -- keep the raw signature if a name is unresolvable
            resolved = signature

        def _bound(args, kwargs) -> dict:
            try:
                bound = signature.bind_partial(*args, **kwargs)
            except TypeError:
                return dict(kwargs)
            return dict(bound.arguments)

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                import anyio.to_thread

                values = _bound(args, kwargs)
                await anyio.to_thread.run_sync(lambda: check_target(values, permission_id))
                return await func(*args, **kwargs)

            async_wrapper.__signature__ = resolved
            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            check_target(_bound(args, kwargs), permission_id)
            return func(*args, **kwargs)

        wrapper.__signature__ = resolved
        return wrapper

    return decorator

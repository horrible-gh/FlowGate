"""Single project-access judgment for human (session JWT) requests.

Every project-scoped check goes through ``can(user, project_id, permission_id)``:

* A system administrator (``users.is_admin``) may do everything.
* Anyone else must be a member of the target project, i.e. hold a row in
  ``user_project_roles`` for that exact project. Workspace access is already
  materialised into that table, so it is the only membership source. Holding a
  role on ``__SYSTEM__`` does not make a user a member of any real project.
* A member must also hold the permission through ``permission_service``.

The project is always taken from the stored target (document, group, document
type) and never from a project/group ID the client wrote next to it. When a
request names several targets, every one of them must pass.

List endpoints use ``visible_project_clause`` so the filter is applied in SQL
instead of fetching every row and filtering in Python.
"""
from __future__ import annotations

import functools
import inspect
import re
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from modules.flow_gate.db.connection import get_store
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


def is_admin(user: Optional[dict]) -> bool:
    return bool(user and user.get("is_admin"))


def is_member(user_id: Optional[str], project_id: Optional[str]) -> bool:
    """True when the user holds a role on exactly this project."""
    if not user_id or not project_id or project_id == SYSTEM_PROJECT:
        return False
    row = get_store()._fetch_one(
        "SELECT 1 AS ok FROM user_project_roles WHERE user_id = ? AND project_id = ?",
        [user_id, project_id],
    )
    return row is not None


def can(user: Optional[dict], project_id: Optional[str], permission_id: str) -> bool:
    """The one judgment entry: may ``user`` use ``permission_id`` on ``project_id``."""
    if not user:
        return False
    if is_admin(user):
        return True
    user_id = user.get("user_id")
    if not is_member(user_id, project_id):
        return False
    return _permission_service.has_permission(user_id, project_id, permission_id)


def require(user: Optional[dict], project_id: Optional[str], permission_id: str) -> None:
    """Raise 403 unless ``can`` allows the request."""
    if not can(user, project_id, permission_id):
        raise HTTPException(status_code=403, detail="Forbidden")


def visible_project_clause(user: Optional[dict], column: str = "project_id") -> tuple[str, list]:
    """SQL fragment limiting ``column`` to projects the user may see.

    Returns ``("", [])`` for a system administrator. The fragment starts with
    ``AND`` so it can be appended to an existing WHERE clause.
    """
    if is_admin(user):
        return "", []
    user_id = (user or {}).get("user_id") or ""
    return (
        f" AND {column} IN (SELECT upr.project_id FROM user_project_roles upr"
        " WHERE upr.user_id = ? AND upr.project_id <> ?)",
        [user_id, SYSTEM_PROJECT],
    )


# Aliases used inside the correlated subqueries below. An outer column must not
# reuse them, or it would bind to the inner row instead of the listed row.
_INNER_ALIASES = frozenset({"acl_member", "acl_role", "acl_perm"})
_QUALIFIED_COLUMN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_][A-Za-z0-9_]*$")


def outer_column(column: str) -> str:
    """Validate that ``column`` names the listed row's project column.

    Used inside an ``EXISTS`` subquery, a bare ``project_id`` resolves to the
    inner ``user_project_roles`` row, so the permission would no longer be tied
    to the listed row's project. The column must therefore be qualified with
    the outer table alias (for example ``g.project_id``).
    """
    match = _QUALIFIED_COLUMN.match(column or "")
    if not match or match.group(1) in _INNER_ALIASES:
        raise ValueError(f"project column must be qualified with the outer table alias: {column!r}")
    return column


def role_permission_exists(column: str) -> str:
    """``EXISTS`` fragment: the user holds the permission for the row's project.

    The same role join and ``__SYSTEM__`` aggregation as
    ``permission_service.get_user_permissions``, correlated to ``column`` of
    the outer row. Parameters: ``user_id``, ``SYSTEM_PROJECT``, ``permission_id``.
    """
    column = outer_column(column)
    return (
        " EXISTS (SELECT 1 FROM user_project_roles acl_role"
        " JOIN role_permissions acl_perm ON acl_role.role_id = acl_perm.role_id"
        f" WHERE acl_role.user_id = ? AND acl_role.project_id IN ({column}, ?)"
        " AND acl_perm.permission_id = ?)"
    )


def member_permission_clause(
    user: Optional[dict], permission_id: str, column: str
) -> tuple[str, list]:
    """SQL fragment limiting ``column`` to projects where ``can`` allows ``permission_id``.

    The same rule as ``can`` expressed in SQL, so a list never loads every
    project row to judge it in Python: an administrator gets no condition,
    anyone else must hold a role on the project itself (``__SYSTEM__`` never
    counts as membership) and hold the permission through the same role join
    ``permission_service`` uses. ``column`` must be the outer row's qualified
    project column (see ``outer_column``). The fragment starts with ``AND``.
    """
    column = outer_column(column)
    if is_admin(user):
        return "", []
    user_id = (user or {}).get("user_id") or ""
    if not user_id:
        return " AND 1 = 0", []
    return (
        f" AND {column} IN (SELECT acl_member.project_id FROM user_project_roles acl_member"
        " WHERE acl_member.user_id = ? AND acl_member.project_id <> ?)"
        " AND" + role_permission_exists(column),
        [user_id, SYSTEM_PROJECT, user_id, SYSTEM_PROJECT, permission_id],
    )


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
        # to signed-in users; changes need the permission on __SYSTEM__.
        if permission_id == "perm_document_read":
            return
        if not _permission_service.has_permission(user.get("user_id"), SYSTEM_PROJECT, permission_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return
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

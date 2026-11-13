"""Group (module) identifier ↔ permanent key mapping (flowgate.v02gui.0012 0001-B item B).

A module's creation-time name is its **permanent key**. It lives on in ``groups.module``,
``documents.module``, the module part of every ``group_id``, the ``id_counter`` keys and the
branch/worktree names, and none of those are ever rewritten. Changing a Group identifier only
records a new **exposed identifier** in ``project_module_identifiers`` (migration 187):

- the exposed identifier is what the tree, the module list and the rename dialog show, and what
  the client sends back as ``moduleName`` (PATCH path, Flow creation);
- every server entry point that receives a module translates it back to the permanent key with
  :func:`resolve_module_key` (or :func:`canonical_group_module` when a group is known), so numbering,
  storage paths and stored metadata only ever see the permanent key;
- an identifier a module used before stays reserved for that module and keeps resolving to it.

Without the table (a database before migration 187) every function degrades to the identity
mapping, which is exactly the behaviour before this change.
"""
from __future__ import annotations

import re
from typing import Optional

from .connection import get_store, now_iso

IDENTIFIER_MAX_LENGTH = 40
_IDENTIFIER_RE = re.compile(r"^[a-z0-9_\-]+$")
# "none" is the module-less "All" bucket (db/projects.py list_modules_bulk); "all" reads the same.
RESERVED_IDENTIFIERS = frozenset({"none", "all"})

_TABLE = "project_module_identifiers"
_TABLE_SEEN = False


class ModuleIdentifierError(ValueError):
    """A rejected identifier change. ``code`` is stable for clients; ``status`` is the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _table_exists(store) -> bool:
    # Same reasoning as db/projects.py _project_modules_table_exists: migrations only CREATE it.
    global _TABLE_SEEN
    if not _TABLE_SEEN and store.table_exists(_TABLE):
        _TABLE_SEEN = True
    return _TABLE_SEEN


def _label(value: Optional[str]) -> str:
    return (value or "").strip() or "none"


def current_identifiers(project_ids: list[str], store=None) -> dict[str, dict[str, str]]:
    """``{project_id: {module_key: exposed identifier}}`` for keys whose identifier was changed."""
    result: dict[str, dict[str, str]] = {pid: {} for pid in project_ids}
    if not project_ids:
        return result
    store = store or get_store()
    if not _table_exists(store):
        return result
    ids = list(dict.fromkeys(project_ids))
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        placeholders = ",".join(["?"] * len(chunk))
        for row in store._fetch_all(
            f"SELECT project_id, identifier, module_key FROM {_TABLE}"
            f" WHERE is_current = 1 AND project_id IN ({placeholders})",
            chunk,
        ):
            pid = row.get("project_id")
            if pid in result:
                result[pid][row.get("module_key")] = row.get("identifier")
    return result


def exposed_identifier(project_id: str, module_key: Optional[str], store=None) -> str:
    """Identifier shown for a permanent key (the key itself when it was never changed)."""
    key = _label(module_key)
    return current_identifiers([project_id], store).get(project_id, {}).get(key, key)


def _known_keys(store, project_id: str) -> set[str]:
    keys: set[str] = set()
    if store.table_exists("project_modules"):
        for row in store._fetch_all(
            "SELECT name FROM project_modules WHERE project_id = ?", [project_id]
        ):
            keys.add(_label(row.get("name")))
    for row in store._fetch_all(
        "SELECT DISTINCT module FROM groups WHERE project_id = ?", [project_id]
    ):
        keys.add(_label(row.get("module")))
    return keys


def resolve_module_key(project_id: str, identifier: Optional[str], store=None) -> str:
    """Translate an identifier received from a client to the permanent module key.

    A permanent key always resolves to itself, so callers that already hold a key are unaffected.
    An identifier the table does not know (a brand-new module) is returned unchanged.
    """
    value = _label(identifier)
    if value == "none" or not project_id:
        return value
    store = store or get_store()
    if not _table_exists(store):
        return value
    row = store._fetch_one(
        f"SELECT module_key FROM {_TABLE} WHERE project_id = ? AND identifier = ?",
        [project_id, value],
    )
    if row is None:
        return value
    key = _label(row.get("module_key"))
    if key != value and value in _known_keys(store, project_id):
        # The identifier is itself some module's permanent key. Keys win; rename_identifier
        # never lets this happen, so it only guards rows written outside it.
        return value
    return key


def canonical_group_module(project_id: str, group: Optional[dict], requested: Optional[str]) -> Optional[str]:
    """Module to number, store and record a new document of ``group`` under.

    The group row's module (its permanent key) always wins over the module a request carried —
    the rule process_service.submit_document already applies — so a request that sends the new
    exposed identifier (or any other value) can never split one Flow's documents across two module
    directories or two id_counter rows. Without a group row the request value is only translated.
    """
    if group:
        group_module = (group.get("module") or "").strip()
        if group_module:
            return group_module
    if requested is None or not str(requested).strip():
        return requested
    return resolve_module_key(project_id, str(requested))


def validate_identifier_format(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ModuleIdentifierError("module_identifier_required", "Identifier is required.")
    if value != value.lower() and _IDENTIFIER_RE.match(value.lower()):
        raise ModuleIdentifierError(
            "module_identifier_uppercase", "Identifier must be lowercase."
        )
    if not _IDENTIFIER_RE.match(value):
        raise ModuleIdentifierError(
            "module_identifier_invalid",
            "Identifier may only contain lowercase letters, digits, '-' and '_'.",
        )
    if len(value) > IDENTIFIER_MAX_LENGTH:
        raise ModuleIdentifierError(
            "module_identifier_too_long",
            f"Identifier must be {IDENTIFIER_MAX_LENGTH} characters or fewer.",
        )
    if value in RESERVED_IDENTIFIERS:
        raise ModuleIdentifierError(
            "module_identifier_reserved", f"Identifier '{value}' is reserved."
        )
    return value


def rename_identifier(project_id: str, module_key: str, new_identifier: str, store=None) -> str:
    """Make ``new_identifier`` the exposed identifier of ``module_key``. Returns it.

    Must run inside the caller's transaction. Only ``project_module_identifiers`` rows change;
    the PRIMARY KEY (project_id, identifier) turns a concurrent claim of the same identifier by
    another module into a failed insert instead of a second owner.
    """
    store = store or get_store()
    if not _table_exists(store):
        raise ModuleIdentifierError(
            "module_identifier_unsupported",
            "This server's database does not support identifier changes yet (migration 187).",
            status=409,
        )
    key = _label(module_key)
    value = validate_identifier_format(new_identifier)
    if key == "none":
        raise ModuleIdentifierError(
            "module_identifier_reserved", "The 'All' group's identifier cannot be changed."
        )
    if value != key and value in _known_keys(store, project_id):
        raise ModuleIdentifierError(
            "module_identifier_taken", f"Identifier '{value}' already exists (another group uses it).", 409
        )
    owner = store._fetch_one(
        f"SELECT module_key FROM {_TABLE} WHERE project_id = ? AND identifier = ?",
        [project_id, value],
    )
    if owner is not None and _label(owner.get("module_key")) != key:
        raise ModuleIdentifierError(
            "module_identifier_taken",
            f"Identifier '{value}' already exists (another group used it and keeps it reserved).",
            409,
        )
    now = now_iso()
    # Retire whatever is current now: it keeps resolving to this key and stays reserved.
    store._execute(
        f"UPDATE {_TABLE} SET is_current = 0, updated_at = ?"
        f" WHERE project_id = ? AND module_key = ? AND is_current = 1",
        [now, project_id, key],
    )
    if value == key:
        # Back to the permanent key: no current row means "the key is the identifier".
        return value
    if owner is not None:
        store._execute(
            f"UPDATE {_TABLE} SET is_current = 1, updated_at = ?"
            f" WHERE project_id = ? AND identifier = ?",
            [now, project_id, value],
        )
    else:
        store._execute(
            f"INSERT INTO {_TABLE} (project_id, identifier, module_key, is_current, created_at, updated_at)"
            f" VALUES (?, ?, ?, 1, ?, ?)",
            [project_id, value, key, now, now],
        )
    return value

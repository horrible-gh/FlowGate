"""Group (module) identifier rules (flowgate.v02gui.0012 0001-B item B).

A module's name *is* its key: ``groups.module``, ``documents.module``, the module part of every
``group_id`` / ``doc_id``, the ``id_counter`` keys, the storage directory and the branch/worktree
names all carry it. Changing a Group identifier renames that key everywhere
(``modules.flow_gate.services.module_rename``), so there is no second name to translate and an old
name is free for any group once it was renamed away. (The alias table of migration 187 was the
earlier design; migration 188 drops it.)

This module keeps the two rules every entry point shares:

- :func:`validate_identifier_format` — what an identifier may look like;
- :func:`canonical_group_module` — a new document of a group is numbered, stored and recorded under
  the group row's module, never under whatever module a request carried.
"""
from __future__ import annotations

import re
from typing import Optional

IDENTIFIER_MAX_LENGTH = 40
_IDENTIFIER_RE = re.compile(r"^[a-z0-9_\-]+$")
# "none" is the module-less "All" bucket (db/projects.py list_modules_bulk); "all" reads the same.
RESERVED_IDENTIFIERS = frozenset({"none", "all"})


class ModuleIdentifierError(ValueError):
    """A rejected identifier. ``code`` is stable for clients; ``status`` is the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def canonical_group_module(project_id: str, group: Optional[dict], requested: Optional[str]) -> Optional[str]:
    """Module to number, store and record a new document of ``group`` under.

    The group row's module always wins over the module a request carried — the rule
    process_service.submit_document already applies — so a stale or wrong module in a request can
    never split one Flow's documents across two module directories or two id_counter rows.
    Without a group row the request value is used as it is.
    """
    if group:
        group_module = (group.get("module") or "").strip()
        if group_module:
            return group_module
    return requested


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

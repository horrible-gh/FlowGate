"""remote_tool_grant / remote_tool_grant_scope CRUD (DB0007 §4·§5).

Persistent form of the permission selection (R0001 "the permissions chosen when the user delegates a task").
A grant is one delegated token; its scope set is the selected permissions.
Follows the get_store()-based access pattern used by db/tokens.py.
"""
from __future__ import annotations

from typing import Any, Optional

from .connection import get_store, now_iso

# Scope enum (DB0007 §7.2). glob operations share the grep scope, so it is not listed here.
VALID_SCOPES = ("read", "write", "grep", "remove")


def get_by_token_hash(token_hash: str) -> Optional[dict]:
    """Look up a grant by its token hash (authentication ① lookup, DB0007 §4)."""
    return get_store()._fetch_one(
        "SELECT * FROM remote_tool_grant WHERE token_hash = ?", [token_hash]
    )


def get_by_id(grant_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM remote_tool_grant WHERE grant_id = ?", [grant_id]
    )


def get_scopes(grant_id: str) -> set[str]:
    """Return the set of permission scopes held by a grant (permission check ②, DB0007 §5)."""
    rows = get_store()._fetch_all(
        "SELECT scope FROM remote_tool_grant_scope WHERE grant_id = ?", [grant_id]
    )
    return {r["scope"] for r in rows}


def create(data: dict[str, Any], scopes: list[str]) -> dict:
    """Insert a grant and its scope set atomically.

    The grant row + scope rows are written in one transaction so a grant never
    exists without its selected scopes. `scopes` is deduplicated and validated
    against VALID_SCOPES (the (grant_id, scope) PK also blocks duplicates).
    """
    clean_scopes = []
    for s in scopes:
        if s not in VALID_SCOPES:
            raise ValueError(f"invalid scope: {s!r} (allowed: {VALID_SCOPES})")
        if s not in clean_scopes:
            clean_scopes.append(s)

    now = now_iso()
    store = get_store()
    with store.transaction():
        store._execute(
            "INSERT INTO remote_tool_grant "
            "(grant_id, token_hash, project, module, group_id, report_doc_id, session_id, "
            "status, issued_at, expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                data["grant_id"], data["token_hash"], data["project"],
                data["module"], data.get("group_id"),
                data.get("report_doc_id"), data.get("session_id"),
                data.get("status", "active"),
                data.get("issued_at", now), data.get("expires_at"),
                now, now,
            ],
        )
        for scope in clean_scopes:
            store._execute(
                "INSERT INTO remote_tool_grant_scope (grant_id, scope) VALUES (?, ?)",
                [data["grant_id"], scope],
            )
    return get_by_id(data["grant_id"])  # type: ignore[return-value]


def replace_scopes(grant_id: str, scopes: list[str]) -> None:
    """Replace a grant's scope set atomically."""
    clean_scopes = []
    for s in scopes:
        if s not in VALID_SCOPES:
            raise ValueError(f"invalid scope: {s!r} (allowed: {VALID_SCOPES})")
        if s not in clean_scopes:
            clean_scopes.append(s)

    store = get_store()
    with store.transaction():
        store._execute("DELETE FROM remote_tool_grant_scope WHERE grant_id = ?", [grant_id])
        for scope in clean_scopes:
            store._execute(
                "INSERT INTO remote_tool_grant_scope (grant_id, scope) VALUES (?, ?)",
                [grant_id, scope],
            )
        store._execute(
            "UPDATE remote_tool_grant SET updated_at = ? WHERE grant_id = ?",
            [now_iso(), grant_id],
        )

def revoke(grant_id: str) -> None:
    """Mark a grant revoked (flip status instead of deleting the row to preserve the audit trail, DB0007 §8)."""
    store = get_store()
    store._execute(
        "UPDATE remote_tool_grant SET status = 'revoked', updated_at = ? WHERE grant_id = ?",
        [now_iso(), grant_id],
    )

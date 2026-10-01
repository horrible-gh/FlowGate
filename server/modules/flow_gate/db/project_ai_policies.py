"""Project AI usage policy (flowgate.v02.0017 TR0003 rev7, migration 131).

A project owns no AI definition. It only decides which AI it may use:

    ai_enabled           AI on/off for the whole project
    system_ai_scope      'all'      = every shared system AI (project_access='all') plus the
                                      system AI this project lists
                         'selected' = only the listed system AI, in the listed order
    allow_user_ai        members may use their own AI in this project (each member only their
                         own; running it needs the owner's Agent — follow-up execution layer)
    default_provider_id  default AI, NULL = the system default

Row absence reads as the defaults above (enabled, 'all', no user AI, system default), so a
project created after the migration needs no row until someone changes its policy.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso

SCOPES = ("all", "selected")

DEFAULTS = {
    "ai_enabled": True,
    "system_ai_scope": "all",
    "allow_user_ai": False,
    "default_provider_id": None,
    "updated_at": None,
    "updated_by": None,
}


def get(project_id: str) -> dict:
    row = get_store()._fetch_one(
        "SELECT ai_enabled, system_ai_scope, allow_user_ai, default_provider_id, updated_by, "
        "updated_at FROM project_ai_policies WHERE project_id = ?",
        [project_id],
    )
    if row is None:
        return dict(DEFAULTS)
    return {
        "ai_enabled": bool(row["ai_enabled"]),
        "system_ai_scope": row["system_ai_scope"] if row["system_ai_scope"] in SCOPES else "all",
        "allow_user_ai": bool(row["allow_user_ai"]),
        "default_provider_id": row.get("default_provider_id"),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


def listed_provider_ids(project_id: str) -> list[str]:
    """System AI this project lists explicitly, in its order."""
    return [r["provider_id"] for r in get_store()._fetch_all(
        "SELECT provider_id FROM project_ai_policy_providers WHERE project_id = ? "
        "ORDER BY sort_order, provider_id",
        [project_id],
    )]


def save(project_id: str, policy: dict, listed: Optional[list[str]],
         updated_by: Optional[str]) -> None:
    """Upsert the policy row and, when `listed` is given, replace the listed AI in the same
    transaction (a failed save never leaves half a policy)."""
    store = get_store()
    now = now_iso()
    with store.transaction() as s:
        s._execute(
            "INSERT INTO project_ai_policies (project_id, ai_enabled, system_ai_scope, "
            "allow_user_ai, default_provider_id, updated_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "ai_enabled = excluded.ai_enabled, "
            "system_ai_scope = excluded.system_ai_scope, "
            "allow_user_ai = excluded.allow_user_ai, "
            "default_provider_id = excluded.default_provider_id, "
            "updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            [project_id, 1 if policy["ai_enabled"] else 0, policy["system_ai_scope"],
             1 if policy["allow_user_ai"] else 0, policy.get("default_provider_id"),
             updated_by, now, now],
        )
        if listed is not None:
            s._execute("DELETE FROM project_ai_policy_providers WHERE project_id = ?", [project_id])
            for index, provider_id in enumerate(listed):
                s._execute(
                    "INSERT INTO project_ai_policy_providers "
                    "(project_id, provider_id, sort_order, created_at) VALUES (?, ?, ?, ?)",
                    [project_id, provider_id, index, now],
                )

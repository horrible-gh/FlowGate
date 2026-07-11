"""Git integration storage (flowgate.default.0115 DB0007, migration 056).

project_git_config / group_git_state / git_merge_session(+_file) / git_project_lock
CRUD. Follows the dominant inline-SQL pattern (get_store()._fetch_one/_fetch_all/
_execute) used by db/remote_tool_grants.py.

Secret handling invariant (DB0007 I5): ``secret_enc`` only ever receives values
produced by git_service.encrypt_secret() — this module stores what it is given
and never logs it.
"""
from __future__ import annotations

from typing import Any, Optional

from .connection import get_store, now_iso

PROVIDER_VALUES = ("github", "gitlab", "gitea", "gitbucket", "generic")
ACTION_VALUES = ("merge", "push", "wait")
STATE_VALUES = (
    "none", "awaiting_choice", "merging", "conflict", "merged", "pushed", "waiting",
)


# ── project_git_config ────────────────────────────────────────────────────────

def get_config(project_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM project_git_config WHERE project_id = ?", [project_id]
    )


def upsert_config(project_id: str, data: dict[str, Any]) -> dict:
    """Insert or update the project's git config.

    ``data['secret_enc']`` semantics: the key must always be present and carry
    the FINAL value to store (the caller resolves the null=keep / ""=clear /
    value=replace protocol before reaching storage).
    """
    now = now_iso()
    existing = get_config(project_id)
    store = get_store()
    if existing is None:
        store._execute(
            "INSERT INTO project_git_config "
            "(project_id, repo_url, provider, username, secret_enc, base_branch, "
            "default_finalize_action, enabled, translate_url, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                project_id, data["repo_url"], data.get("provider") or "generic",
                data.get("username"), data.get("secret_enc"),
                data.get("base_branch") or "main",
                data.get("default_finalize_action") or "wait",
                1 if data.get("enabled") else 0, data.get("translate_url"), now, now,
            ],
        )
    else:
        store._execute(
            "UPDATE project_git_config SET repo_url = ?, provider = ?, username = ?, "
            "secret_enc = ?, base_branch = ?, default_finalize_action = ?, enabled = ?, "
            "translate_url = ?, updated_at = ? WHERE project_id = ?",
            [
                data["repo_url"], data.get("provider") or "generic",
                data.get("username"), data.get("secret_enc"),
                data.get("base_branch") or "main",
                data.get("default_finalize_action") or "wait",
                1 if data.get("enabled") else 0, data.get("translate_url"), now, project_id,
            ],
        )
    return get_config(project_id)  # type: ignore[return-value]


def delete_config(project_id: str) -> bool:
    if get_config(project_id) is None:
        return False
    get_store()._execute(
        "DELETE FROM project_git_config WHERE project_id = ?", [project_id]
    )
    return True


# ── group_git_state ───────────────────────────────────────────────────────────

def get_state(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [group_id]
    )


def register_worktree(group_id: str, project_id: str, branch: str) -> dict:
    """Record a group's worktree in the ledger (idempotent upsert)."""
    now = now_iso()
    store = get_store()
    existing = get_state(group_id)
    if existing is None:
        store._execute(
            "INSERT INTO group_git_state "
            "(group_id, project_id, branch, worktree_registered, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, 'none', ?, ?)",
            [group_id, project_id, branch, now, now],
        )
    else:
        store._execute(
            "UPDATE group_git_state SET branch = ?, worktree_registered = 1, updated_at = ? "
            "WHERE group_id = ?",
            [branch, now, group_id],
        )
    return get_state(group_id)  # type: ignore[return-value]


def unregister_worktree(group_id: str) -> None:
    """Drop the worktree registration after slot cleanup (flowgate.default.0182
    NR0003 §5). status / merge_commit remain untouched as group history; the row
    simply stops counting as an active slot (list_states_of_project filters on
    worktree_registered = 1)."""
    get_store()._execute(
        "UPDATE group_git_state SET worktree_registered = 0, updated_at = ? "
        "WHERE group_id = ?",
        [now_iso(), group_id],
    )


def set_status(
    group_id: str,
    status: str,
    *,
    merge_id: Optional[int] = None,
    merge_commit: Optional[str] = None,
) -> None:
    if status not in STATE_VALUES:
        raise ValueError(f"invalid git state: {status!r}")
    get_store()._execute(
        "UPDATE group_git_state SET status = ?, merge_id = ?, merge_commit = ?, "
        "updated_at = ? WHERE group_id = ?",
        [status, merge_id, merge_commit, now_iso(), group_id],
    )


def list_states_by_status(statuses: list[str]) -> list[dict]:
    if not statuses:
        return []
    placeholders = ", ".join("?" for _ in statuses)
    return get_store()._fetch_all(
        f"SELECT * FROM group_git_state WHERE status IN ({placeholders})", list(statuses)
    )


def list_states_of_project(project_id: str) -> list[dict]:
    """Registered worktree ledger rows for one project (flowgate.default.0162 L §2.2).

    Scoped by project_id (``list_states_by_status`` spans every project and is
    unsuitable for per-project aggregation / pending_count). Covered by
    idx_group_git_state_project(project_id, status) — no schema change (DB0005).
    """
    return get_store()._fetch_all(
        "SELECT * FROM group_git_state "
        "WHERE project_id = ? AND worktree_registered = 1",
        [project_id],
    )


# ── git_merge_session (+ files) ───────────────────────────────────────────────

def create_session(group_id: str, files: list[str]) -> int:
    """Create an open merge session with its conflict file set (one transaction)."""
    now = now_iso()
    store = get_store()
    with store.transaction():
        store._execute(
            "INSERT INTO git_merge_session (group_id, status, created_at, touched_at) "
            "VALUES (?, 'open', ?, ?)",
            [group_id, now, now],
        )
        row = store._fetch_one(
            "SELECT merge_id FROM git_merge_session "
            "WHERE group_id = ? AND status = 'open' ORDER BY merge_id DESC",
            [group_id],
        )
        merge_id = int(row["merge_id"])
        for path in files:
            store._execute(
                "INSERT INTO git_merge_session_file (merge_id, path, resolved) "
                "VALUES (?, ?, 0)",
                [merge_id, path],
            )
    return merge_id


def get_session(merge_id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM git_merge_session WHERE merge_id = ?", [merge_id]
    )


def get_open_session_by_group(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM git_merge_session WHERE group_id = ? AND status = 'open'",
        [group_id],
    )


def session_files(merge_id: int) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM git_merge_session_file WHERE merge_id = ? ORDER BY path",
        [merge_id],
    )


def mark_file_resolved(merge_id: int, path: str) -> None:
    get_store()._execute(
        "UPDATE git_merge_session_file SET resolved = 1, resolved_at = ? "
        "WHERE merge_id = ? AND path = ?",
        [now_iso(), merge_id, path],
    )


def remaining_conflicts(merge_id: int) -> list[str]:
    rows = get_store()._fetch_all(
        "SELECT path FROM git_merge_session_file "
        "WHERE merge_id = ? AND resolved = 0 ORDER BY path",
        [merge_id],
    )
    return [r["path"] for r in rows]


def close_session(merge_id: int, status: str) -> None:
    if status not in ("done", "aborted"):
        raise ValueError(f"invalid session close status: {status!r}")
    get_store()._execute(
        "UPDATE git_merge_session SET status = ?, closed_at = ? WHERE merge_id = ?",
        [status, now_iso(), merge_id],
    )


def list_open_sessions() -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM git_merge_session WHERE status = 'open'", []
    )


def touch_session(merge_id: int) -> None:
    """Bump a session's activity timestamp — the sweep TTL basis (0205 L §1).

    Called on session creation (via create_session's touched_at), conflict-list
    fetch, and resolve submission. A quiet session (no touch for the TTL window)
    is what the auto-recovery sweep reclaims."""
    get_store()._execute(
        "UPDATE git_merge_session SET touched_at = ? WHERE merge_id = ?",
        [now_iso(), merge_id],
    )


# ── group_git_state provisioning-failure ledger (0205 L §2.4 / DB0005) ────────

def upsert_provision_failure(
    group_id: str, project_id: str, branch: str, error: str
) -> None:
    """Persist a worktree provisioning failure so it survives the one-shot SSE
    (0205 P scenario 4). Creates a minimal ledger row (worktree_registered=0,
    status='none') when the group has none yet, else records the error on the
    existing row. ``provision_error`` / ``provision_failed_at`` are always written
    as a pair. ``branch`` may be "" when the failure preceded branch-name
    resolution (E9) — the column is NOT NULL, so an empty string is stored."""
    now = now_iso()
    store = get_store()
    with store.transaction():
        existing = get_state(group_id)
        if existing is None:
            store._execute(
                "INSERT INTO group_git_state "
                "(group_id, project_id, branch, worktree_registered, status, "
                "provision_error, provision_failed_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, 'none', ?, ?, ?, ?)",
                [group_id, project_id, branch or "", error, now, now, now],
            )
        else:
            store._execute(
                "UPDATE group_git_state SET provision_error = ?, "
                "provision_failed_at = ?, updated_at = ? WHERE group_id = ?",
                [error, now, now, group_id],
            )


def clear_provision_failure(group_id: str) -> None:
    """Clear the provisioning-failure marker after a successful provision (0205
    L §2.4). No-op when the group has no row. Cleared as a pair."""
    get_store()._execute(
        "UPDATE group_git_state SET provision_error = NULL, "
        "provision_failed_at = NULL, updated_at = ? WHERE group_id = ?",
        [now_iso(), group_id],
    )


def list_states_of_project_any(project_id: str) -> list[dict]:
    """Every ledger row for a project, registered or not (0205 L §2.8).

    ``list_states_of_project`` filters worktree_registered=1; the provision-failure
    surface needs the UNregistered failure rows too. Covered by
    idx_group_git_state_project(project_id, status) left prefix (DB0005 §4)."""
    return get_store()._fetch_all(
        "SELECT * FROM group_git_state WHERE project_id = ?", [project_id]
    )


# ── git_project_lock (INSERT = acquire, DELETE = release; DB0007 §2.5) ───────

def try_acquire_lock(project_id: str, holder: str) -> bool:
    """Attempt to take the project mutex. False when another holder owns it."""
    try:
        get_store()._execute(
            "INSERT INTO git_project_lock (project_id, holder, acquired_at) "
            "VALUES (?, ?, ?)",
            [project_id, holder, now_iso()],
        )
    except Exception:
        return False
    # Verify the row is ours: some drivers swallow duplicate-key errors differently.
    row = get_lock(project_id)
    return bool(row and row.get("holder") == holder)


def get_lock(project_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM git_project_lock WHERE project_id = ?", [project_id]
    )


def release_lock(project_id: str, holder: str) -> None:
    get_store()._execute(
        "DELETE FROM git_project_lock WHERE project_id = ? AND holder = ?",
        [project_id, holder],
    )


def transfer_lock(project_id: str, old_holder: str, new_holder: str) -> None:
    """Hand the mutex to a merge session (L0006 §2.8 persistent inheritance)."""
    store = get_store()
    with store.transaction():
        store._execute(
            "DELETE FROM git_project_lock WHERE project_id = ? AND holder = ?",
            [project_id, old_holder],
        )
        store._execute(
            "INSERT INTO git_project_lock (project_id, holder, acquired_at) "
            "VALUES (?, ?, ?)",
            [project_id, new_holder, now_iso()],
        )


def list_locks() -> list[dict]:
    return get_store()._fetch_all("SELECT * FROM git_project_lock", [])


def force_release_lock(project_id: str) -> None:
    get_store()._execute(
        "DELETE FROM git_project_lock WHERE project_id = ?", [project_id]
    )

"""TR Self-check runs repository (flowgate.default.0650 T0002 / 0634 0007-DB).

Advisory self-check execution audit & one-active invariant store.
Does NOT store test222 verdicts (pass/fail/approved), raw secrets, or unbounded stdout.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Optional

from .connection import get_store, now_iso

_MAX_TAIL_BYTES = 64 * 1024  # 64 KiB UTF-8 encoded byte limit
_MAX_DIAGNOSTIC_CHARS = 2000


class SelfCheckAlreadyRunningError(Exception):
    """Raised when a pending/running self-check already exists for (project_id, group_id)."""

    def __init__(self, message: str, existing_run_id: str | None = None):
        super().__init__(message)
        self.existing_run_id = existing_run_id


def compute_active_key(project_id: str, group_id: str) -> str:
    """Canonical active key: SHA-256(UTF-8(project_id) + NUL + UTF-8(group_id)) in lowercase hex."""
    payload = f"{project_id}\0{group_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest().lower()


def _validate_hash64(value: str | None, field: str) -> None:
    """Validate DB0007 SHA-256 lowercase hexadecimal contract before writes."""
    if value is not None and (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")


def _is_active_key_unique_violation(exc: Exception) -> bool:
    """Identify only the active_key UNIQUE constraint across supported drivers."""
    diagnostic = getattr(exc, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    if constraint:
        return "active_key" in constraint.lower() and (getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)) == "23505"
    args = getattr(exc, "args", ())
    message = str(exc).lower()
    if args and args[0] == 1062:
        return "active_key" in message
    return (
        "unique constraint failed: tr_self_check_runs.active_key" in message
        and exc.__class__.__name__ == "IntegrityError"
    )


def _truncate_tail(text: str | None) -> str | None:
    """Limit stored output tail to 64 KiB of UTF-8 bytes without breaking UTF-8 characters."""
    if text is None:
        return None
    raw = text.encode("utf-8")
    if len(raw) <= _MAX_TAIL_BYTES:
        return text
    tail = raw[-_MAX_TAIL_BYTES:]
    return tail.decode("utf-8", errors="ignore")


def _truncate_diagnostic(text: str | None) -> str | None:
    if text is None:
        return None
    if len(text) > _MAX_DIAGNOSTIC_CHARS:
        return text[:_MAX_DIAGNOSTIC_CHARS]
    return text


def _normalize_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    res = dict(row)
    for b_col in ("timed_out", "cancel_requested", "source_changed_during_run",
                  "worktree_state_changed", "cleanup_pending"):
        if b_col in res and res[b_col] is not None:
            res[b_col] = bool(res[b_col])
    for l_col in ("index_lock_before", "index_lock_after"):
        if l_col in res and res[l_col] is not None:
            res[l_col] = bool(res[l_col])
    if "args_json" in res and isinstance(res["args_json"], str):
        try:
            res["args"] = json.loads(res["args_json"])
        except Exception:
            res["args"] = []
    if "env_keys_json" in res and isinstance(res["env_keys_json"], str):
        try:
            res["env_keys"] = json.loads(res["env_keys_json"])
        except Exception:
            res["env_keys"] = []
    return res


def create_pending(
    project_id: str,
    group_id: str,
    tr_doc_id: str,
    requested_by: str | None,
    policy_version: str,
    program: str,
    args: list[str],
    resolved_executable_path: str,
    resolved_executable_name: str,
    executable_origin: str,
    cwd_relative: str,
    timeout_seconds: int,
    env_keys: list[str],
    run_id: str | None = None,
    source_lock_holder: str | None = None,
) -> dict:
    """Atomically insert a pending self-check run row with active_key set."""
    store = get_store()
    active_key = compute_active_key(project_id, group_id)
    _validate_hash64(active_key, "active_key")
    rid = run_id or f"scr_{uuid.uuid4().hex[:24]}"
    now = now_iso()

    # Pre-check active to return existing run_id cleanly
    existing = get_active(project_id, group_id)
    if existing:
        raise SelfCheckAlreadyRunningError(
            f"A self-check run is already active for {project_id}/{group_id}",
            existing_run_id=existing["self_check_run_id"],
        )

    args_json = json.dumps(list(args), ensure_ascii=False)
    sorted_env_keys = sorted(list(set(env_keys)))
    env_keys_json = json.dumps(sorted_env_keys, ensure_ascii=False)

    try:
        with store.transaction():
            store._execute(
            "INSERT INTO tr_self_check_runs ("
            " self_check_run_id, project_id, group_id, tr_doc_id, requested_by,"
            " policy_version, program, args_json, resolved_executable_path,"
            " resolved_executable_name, executable_origin, cwd_relative, timeout_seconds,"
            " env_keys_json, status, active_key, exit_code, timed_out, cancel_requested,"
            " source_changed_during_run, worktree_state_changed, recovery_state,"
            " cleanup_pending, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, 0, 0, 0, 0, 'none', 0, ?, ?)",
            [
                rid, project_id, group_id, tr_doc_id, requested_by,
                policy_version, program, args_json, resolved_executable_path,
                resolved_executable_name, executable_origin, cwd_relative, timeout_seconds,
                env_keys_json, active_key, now, now,
            ],
        )
            if source_lock_holder is not None:
                store._execute(
                    "UPDATE tr_self_check_runs SET source_lock_holder = ? WHERE self_check_run_id = ?",
                    [source_lock_holder, rid],
                )
    except Exception as exc:
        if _is_active_key_unique_violation(exc):
            conflict = get_active(project_id, group_id)
            if conflict is not None:
                raise SelfCheckAlreadyRunningError(
                    f"A self-check run is already active for {project_id}/{group_id}",
                    existing_run_id=conflict["self_check_run_id"],
                ) from exc
        raise

    return get_run(rid)  # type: ignore[return-value]


def get_run(run_id: str) -> dict | None:
    """Lookup run by primary key."""
    row = get_store()._fetch_one(
        "SELECT * FROM tr_self_check_runs WHERE self_check_run_id = ?", [run_id]
    )
    return _normalize_row(row)


def get_run_for_doc(run_id: str, tr_doc_id: str) -> dict | None:
    """Lookup run ensuring it belongs to the given TR document."""
    row = get_store()._fetch_one(
        "SELECT * FROM tr_self_check_runs WHERE self_check_run_id = ? AND tr_doc_id = ?",
        [run_id, tr_doc_id],
    )
    return _normalize_row(row)


def list_by_doc(
    tr_doc_id: str,
    limit: int = 20,
    cursor: tuple[str, str] | None = None,
) -> list[dict]:
    """List runs for a document ordered by created_at DESC, self_check_run_id DESC."""
    store = get_store()
    params: list[Any] = [tr_doc_id]
    query = "SELECT * FROM tr_self_check_runs WHERE tr_doc_id = ?"

    if cursor is not None:
        cursor_created_at, cursor_run_id = cursor
        query += " AND (created_at < ? OR (created_at = ? AND self_check_run_id < ?))"
        params.extend([cursor_created_at, cursor_created_at, cursor_run_id])

    query += " ORDER BY created_at DESC, self_check_run_id DESC LIMIT ?"
    params.append(limit)

    rows = store._fetch_all(query, params)
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]


def get_active(project_id: str, group_id: str) -> dict | None:
    """Return currently active run for (project_id, group_id) if any."""
    key = compute_active_key(project_id, group_id)
    row = get_store()._fetch_one(
        "SELECT * FROM tr_self_check_runs WHERE active_key = ? AND status IN ('pending', 'running')",
        [key],
    )
    return _normalize_row(row)


def mark_running(
    run_id: str,
    *,
    source_lock_holder: str | None = None,
    process_owner_kind: str | None = None,
    target_pid: int | None = None,
    target_start_identity: str | None = None,
    supervisor_pid: int | None = None,
    supervisor_start_identity: str | None = None,
    source_head_before: str | None = None,
    source_branch_before: str | None = None,
    source_refs_hash_before: str | None = None,
    source_index_hash_before: str | None = None,
    source_status_hash_before: str | None = None,
    index_lock_before: int | None = None,
) -> dict | None:
    """Transition run from pending to running and persist process ownership metadata."""
    store = get_store()
    now = now_iso()
    _validate_hash64(source_refs_hash_before, "source_refs_hash_before")
    _validate_hash64(source_index_hash_before, "source_index_hash_before")
    _validate_hash64(source_status_hash_before, "source_status_hash_before")
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET"
        " status = 'running',"
        " started_at = COALESCE(started_at, ?),"
        " updated_at = ?,"
        " source_lock_holder = ?,"
        " process_owner_kind = ?,"
        " target_pid = ?,"
        " target_start_identity = ?,"
        " supervisor_pid = ?,"
        " supervisor_start_identity = ?,"
        " source_head_before = ?,"
        " source_branch_before = ?,"
        " source_refs_hash_before = ?,"
        " source_index_hash_before = ?,"
        " source_status_hash_before = ?,"
        " index_lock_before = ?"
        " WHERE self_check_run_id = ? AND status = 'pending' AND recovery_state = 'none'",
        [
            now, now, source_lock_holder, process_owner_kind,
            target_pid, target_start_identity, supervisor_pid, supervisor_start_identity,
            source_head_before, source_branch_before, source_refs_hash_before,
            source_index_hash_before, source_status_hash_before, index_lock_before,
            run_id,
        ],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def request_cancel(run_id: str) -> bool:
    """CAS 0 -> 1 for cancel_requested on active run."""
    store = get_store()
    now = now_iso()
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET cancel_requested = 1, updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND cancel_requested = 0",
        [now, run_id],
    )
    return affected > 0


def finish_completed(
    run_id: str,
    *,
    exit_code: int | None = None,
    timed_out: bool = False,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
    source_head_after: str | None = None,
    source_branch_after: str | None = None,
    source_refs_hash_after: str | None = None,
    source_index_hash_after: str | None = None,
    source_status_hash_after: str | None = None,
    index_lock_after: int | None = None,
    source_changed_during_run: bool = False,
    worktree_state_changed: bool = False,
    cleanup_pending: bool = False,
    cleanup_error: str | None = None,
) -> dict | None:
    """Atomic terminal transition to completed, clearing active_key."""
    store = get_store()
    now = now_iso()
    _validate_hash64(source_refs_hash_after, "source_refs_hash_after")
    _validate_hash64(source_index_hash_after, "source_index_hash_after")
    _validate_hash64(source_status_hash_after, "source_status_hash_after")
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET"
        " status = 'completed',"
        " active_key = NULL,"
        " exit_code = ?,"
        " timed_out = ?,"
        " stdout_tail = ?,"
        " stderr_tail = ?,"
        " source_head_after = ?,"
        " source_branch_after = ?,"
        " source_refs_hash_after = ?,"
        " source_index_hash_after = ?,"
        " source_status_hash_after = ?,"
        " index_lock_after = ?,"
        " source_changed_during_run = ?,"
        " worktree_state_changed = ?,"
        " cleanup_pending = ?,"
        " cleanup_error = ?,"
        " finished_at = ?,"
        " updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'",
        [
            exit_code, 1 if timed_out else 0,
            _truncate_tail(stdout_tail), _truncate_tail(stderr_tail),
            source_head_after, source_branch_after, source_refs_hash_after,
            source_index_hash_after, source_status_hash_after, index_lock_after,
            1 if source_changed_during_run else 0, 1 if worktree_state_changed else 0,
            1 if cleanup_pending else 0, _truncate_diagnostic(cleanup_error),
            now, now, run_id,
        ],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def finish_failed(
    run_id: str,
    *,
    error_code: str | None = None,
    exit_code: int | None = None,
    timed_out: bool = False,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
    source_head_after: str | None = None,
    source_branch_after: str | None = None,
    source_refs_hash_after: str | None = None,
    source_index_hash_after: str | None = None,
    source_status_hash_after: str | None = None,
    index_lock_after: int | None = None,
    source_changed_during_run: bool = False,
    worktree_state_changed: bool = False,
    cleanup_pending: bool = False,
    cleanup_error: str | None = None,
) -> dict | None:
    """Atomic terminal transition to failed, clearing active_key."""
    store = get_store()
    now = now_iso()
    _validate_hash64(source_refs_hash_after, "source_refs_hash_after")
    _validate_hash64(source_index_hash_after, "source_index_hash_after")
    _validate_hash64(source_status_hash_after, "source_status_hash_after")
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET"
        " status = 'failed',"
        " active_key = NULL,"
        " error_code = ?,"
        " exit_code = ?,"
        " timed_out = ?,"
        " stdout_tail = ?,"
        " stderr_tail = ?,"
        " source_head_after = ?,"
        " source_branch_after = ?,"
        " source_refs_hash_after = ?,"
        " source_index_hash_after = ?,"
        " source_status_hash_after = ?,"
        " index_lock_after = ?,"
        " source_changed_during_run = ?,"
        " worktree_state_changed = ?,"
        " cleanup_pending = ?,"
        " cleanup_error = ?,"
        " finished_at = ?,"
        " updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'",
        [
            error_code, exit_code, 1 if timed_out else 0,
            _truncate_tail(stdout_tail), _truncate_tail(stderr_tail),
            source_head_after, source_branch_after, source_refs_hash_after,
            source_index_hash_after, source_status_hash_after, index_lock_after,
            1 if source_changed_during_run else 0, 1 if worktree_state_changed else 0,
            1 if cleanup_pending else 0, _truncate_diagnostic(cleanup_error),
            now, now, run_id,
        ],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def finish_cancelled(
    run_id: str,
    *,
    exit_code: int | None = None,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
    source_head_after: str | None = None,
    source_branch_after: str | None = None,
    source_refs_hash_after: str | None = None,
    source_index_hash_after: str | None = None,
    source_status_hash_after: str | None = None,
    index_lock_after: int | None = None,
    source_changed_during_run: bool = False,
    worktree_state_changed: bool = False,
    cleanup_pending: bool = False,
    cleanup_error: str | None = None,
) -> dict | None:
    """Atomic terminal transition to cancelled with the post-run source identity."""
    store = get_store()
    now = now_iso()
    _validate_hash64(source_refs_hash_after, "source_refs_hash_after")
    _validate_hash64(source_index_hash_after, "source_index_hash_after")
    _validate_hash64(source_status_hash_after, "source_status_hash_after")
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET"
        " status = 'cancelled',"
        " active_key = NULL,"
        " exit_code = ?,"
        " stdout_tail = ?,"
        " stderr_tail = ?,"
        " source_head_after = ?,"
        " source_branch_after = ?,"
        " source_refs_hash_after = ?,"
        " source_index_hash_after = ?,"
        " source_status_hash_after = ?,"
        " index_lock_after = ?,"
        " source_changed_during_run = ?,"
        " worktree_state_changed = ?,"
        " cleanup_pending = ?,"
        " cleanup_error = ?,"
        " finished_at = ?,"
        " updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'",
        [
            exit_code, _truncate_tail(stdout_tail), _truncate_tail(stderr_tail),
            source_head_after, source_branch_after, source_refs_hash_after,
            source_index_hash_after, source_status_hash_after, index_lock_after,
            1 if source_changed_during_run else 0, 1 if worktree_state_changed else 0,
            1 if cleanup_pending else 0, _truncate_diagnostic(cleanup_error),
            now, now, run_id,
        ],
    )
    if affected == 0:
        return None
    return get_run(run_id)

def mark_recovering(run_id: str) -> dict | None:
    """CAS none -> recovering on orphan active run."""
    store = get_store()
    now = now_iso()
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET recovery_state = 'recovering', updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'",
        [now, run_id],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def mark_recovery_incomplete(run_id: str, reason: str) -> dict | None:
    """Mark recovery incomplete, retaining active status and active_key."""
    store = get_store()
    now = now_iso()
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET"
        " recovery_state = 'incomplete',"
        " recovery_reason = ?,"
        " updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'recovering'",
        [_truncate_diagnostic(reason), now, run_id],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def retry_recovery_incomplete(run_id: str) -> dict | None:
    """CAS incomplete -> recovering for an explicit recovery retry."""
    store = get_store()
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET recovery_state = 'recovering', updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running')"
        " AND recovery_state = 'incomplete'",
        [now_iso(), run_id],
    )
    return get_run(run_id) if affected else None


def finish_recovered_interrupted(
    run_id: str,
    *,
    cleanup_pending: bool = False,
    cleanup_error: str | None = None,
) -> dict | None:
    """Mark recovered after interruption by restart, clearing active_key."""
    store = get_store()
    now = now_iso()
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET"
        " status = 'failed',"
        " error_code = 'interrupted_by_restart',"
        " recovery_state = 'recovered',"
        " active_key = NULL,"
        " cleanup_pending = ?,"
        " cleanup_error = ?,"
        " finished_at = ?,"
        " updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'recovering'",
        [
            1 if cleanup_pending else 0, _truncate_diagnostic(cleanup_error),
            now, now, run_id,
        ],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def list_orphan_active_runs() -> list[dict]:
    """Return pending/running rows that may need startup recovery."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE status IN ('pending', 'running') AND recovery_state IN ('none', 'recovering', 'incomplete') ORDER BY created_at ASC"
    )
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]


def list_recovery_incomplete_project_ids() -> list[str]:
    """Return project IDs that have active runs in recovery_incomplete state."""
    rows = get_store()._fetch_all(
        "SELECT DISTINCT project_id FROM tr_self_check_runs WHERE recovery_state = 'incomplete' AND status IN ('pending', 'running')"
    )
    return [r["project_id"] for r in rows if r and "project_id" in r]


def has_group_recovery_incomplete(project_id: str, group_id: str) -> bool:
    """Active recovery_incomplete run of this Group (0666 L 2.24.3; idx on project_id, group_id)."""
    row = get_store()._fetch_one(
        "SELECT 1 FROM tr_self_check_runs WHERE project_id = ? AND group_id = ? AND recovery_state = 'incomplete' AND status IN ('pending', 'running') LIMIT 1",
        [project_id, group_id],
    )
    return row is not None


def set_cleanup_result(
    run_id: str,
    *,
    cleanup_pending: bool,
    cleanup_error: str | None = None,
) -> dict | None:
    """Update cleanup outcome without affecting status or exit_code."""
    store = get_store()
    now = now_iso()
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET cleanup_pending = ?, cleanup_error = ?, updated_at = ? WHERE self_check_run_id = ?",
        [1 if cleanup_pending else 0, _truncate_diagnostic(cleanup_error), now, run_id],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def purge_old_terminal(cutoff_iso: str, batch_size: int = 500) -> int:
    """Purge terminal rows older than cutoff. Protects active/recovering/incomplete rows."""
    store = get_store()
    candidates = store._fetch_all(
        "SELECT self_check_run_id FROM tr_self_check_runs"
        " WHERE status IN ('completed', 'failed', 'cancelled')"
        " AND finished_at < ?"
        " AND cleanup_pending = 0"
        " AND recovery_state NOT IN ('recovering', 'incomplete')"
        " AND active_key IS NULL"
        " ORDER BY finished_at ASC LIMIT ?",
        [cutoff_iso, batch_size],
    )
    if not candidates:
        return 0

    ids = [r["self_check_run_id"] for r in candidates]
    placeholders = ", ".join("?" for _ in ids)
    return store._execute_affected(
        f"DELETE FROM tr_self_check_runs WHERE self_check_run_id IN ({placeholders})"
        " AND status IN ('completed', 'failed', 'cancelled')"
        " AND finished_at < ?"
        " AND cleanup_pending = 0"
        " AND recovery_state NOT IN ('recovering', 'incomplete')"
        " AND active_key IS NULL",
        [*ids, cutoff_iso],
    )

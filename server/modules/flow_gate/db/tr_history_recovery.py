"""Durable journal for non-atomic TR/Time-Machine reapply recovery (0648)."""
from __future__ import annotations

import uuid

from .connection import get_store, now_iso

_UNRESOLVED = ("prepared", "git_applied", "recovery_required")
_PHASES = frozenset((*_UNRESOLVED, "resolved"))
_RESOLUTIONS = frozenset(("ledger_completed", "compensated"))


def by_recovery_id(recovery_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM tr_history_recovery WHERE recovery_id = ?", [recovery_id]
    )


def create_prepared(
    *, project_id: str, group_id: str, doc_id: str,
    source_ledger_row_id: int, pre_git_head_sha: str,
) -> dict:
    recovery_id = uuid.uuid4().hex
    now = now_iso()
    get_store()._execute(
        "INSERT INTO tr_history_recovery "
        "(recovery_id, project_id, group_id, doc_id, operation, source_ledger_row_id, "
        " phase, pre_git_head_sha, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'reapply', ?, 'prepared', ?, ?, ?)",
        [recovery_id, project_id, group_id, doc_id, int(source_ledger_row_id),
         pre_git_head_sha, now, now],
    )
    row = by_recovery_id(recovery_id)
    if row is None:
        raise RuntimeError("reapply recovery journal insert was not durable")
    return row


def mark_git_applied(recovery_id: str, git_commit_sha: str) -> dict:
    now = now_iso()
    get_store()._execute(
        "UPDATE tr_history_recovery "
        "SET phase = 'git_applied', git_commit_sha = ?, error_detail = NULL, updated_at = ? "
        "WHERE recovery_id = ? AND phase = 'prepared'",
        [git_commit_sha, now, recovery_id],
    )
    row = by_recovery_id(recovery_id)
    if row is None or row.get("phase") != "git_applied" or row.get("git_commit_sha") != git_commit_sha:
        raise RuntimeError("reapply recovery journal did not enter git_applied")
    return row


def mark_recovery_required(recovery_id: str, error_detail: str) -> dict:
    now = now_iso()
    get_store()._execute(
        "UPDATE tr_history_recovery "
        "SET phase = 'recovery_required', error_detail = ?, updated_at = ? "
        "WHERE recovery_id = ? AND phase <> 'resolved'",
        [str(error_detail)[-4000:], now, recovery_id],
    )
    row = by_recovery_id(recovery_id)
    if row is None:
        raise RuntimeError("reapply recovery journal disappeared")
    return row


def resolve(
    recovery_id: str, *, resolution: str | None, error_detail: str | None = None,
) -> dict:
    if resolution is not None and resolution not in _RESOLUTIONS:
        raise ValueError(resolution)
    now = now_iso()
    get_store()._execute(
        "UPDATE tr_history_recovery "
        "SET phase = 'resolved', resolution = ?, error_detail = ?, updated_at = ? "
        "WHERE recovery_id = ? AND phase <> 'resolved'",
        [resolution, str(error_detail)[-4000:] if error_detail else None, now, recovery_id],
    )
    row = by_recovery_id(recovery_id)
    if row is None or row.get("phase") != "resolved":
        raise RuntimeError("reapply recovery journal did not resolve")
    return row


def unresolved_by_group(group_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM tr_history_recovery "
        "WHERE group_id = ? AND phase IN ('prepared','git_applied','recovery_required') "
        "ORDER BY id ASC",
        [group_id],
    )


def has_unresolved(group_id: str) -> bool:
    row = get_store()._fetch_one(
        "SELECT id FROM tr_history_recovery "
        "WHERE group_id = ? AND phase IN ('prepared','git_applied','recovery_required') "
        "ORDER BY id ASC LIMIT 1",
        [group_id],
    )
    return row is not None

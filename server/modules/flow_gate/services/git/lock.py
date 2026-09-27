"""Project-level Git mutex and admission guards.

Extracted from git_service.py (flowgate.default.0550 T0015, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from . import merge_target
from .credentials import GitServiceError

_log = logging.getLogger(__name__)

LOCK_WAIT_SEC = 5

def _acquire_lock(project_id: str, holder: str, wait_sec: float = LOCK_WAIT_SEC) -> bool:
    from modules.flow_gate.services import git_service as _gs
    deadline = time.monotonic() + wait_sec
    while True:
        if _gs.db_git.try_acquire_lock(project_id, holder):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def open_merge_session_of_project(project_id: str) -> Optional[dict]:
    """The one open merge session belonging to this project, or None.

    Open sessions are always few (≤ one per group by the DB invariant, and a
    project rarely has more than a couple in flight), so a full scan is fine.
    Should several somehow exist (past bug / manual edit), the newest merge_id
    wins (L §5 boundary condition)."""
    from modules.flow_gate.services import git_service as _gs
    best: Optional[dict] = None
    for session in _gs.db_git.list_open_sessions():
        try:
            if _gs._project_of_group(session["group_id"]) != project_id:
                continue
            # A group_update merge lives exclusively in that group's worktree.
            # It does not hold the shared base checkout and must not block other
            # groups' base-mutating operations.
            if _gs.db_git.session_kind(session) == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
                continue
            # 0594 T0012: a finalize attempt record now exists from BEFORE the merge
            # runs, and a non-base target merges in its own managed workspace. Only
            # a session that actually owns the base checkout's merge state (legacy,
            # TR kinds, or a base-target attempt that reached a conflict) blocks it.
            if not merge_target.holds_base_checkout(session):
                continue
        except Exception:
            continue
        if best is None or int(session["merge_id"]) > int(best["merge_id"]):
            best = session
    return best


def base_merge_in_progress(project_id: str) -> Optional[dict]:
    """The merge that currently OWNS the base checkout's dirty files, or None.

    flowgate.default.0481 T0010 #1. While `git merge` is stopped on a conflict, the base
    checkout's `git status --porcelain` reports every unmerged path AND every side that
    merged cleanly — so `base_dirty` fills up with the merge itself. The Git panel then
    offers that pile as an uncommitted-base-changes summary with an AI-delegation button,
    a per-file revert and a commit: three actions that are all wrong for a half-finished
    merge, and the AI one could never even start (see PROJECT_SCOPED_ACTION_SCOPES in
    ai_invoke's admission). Read the fact straight off the tree — MERGE_HEAD is git's own
    "a merge is stopped here" flag — and carry the session ids so the caller can point at
    the resolver instead of at a cleanup that must not happen.
    """
    from modules.flow_gate.services import git_service as _gs
    base_root = _gs._base_root_of(project_id)
    if base_root is None:
        return None
    try:
        if not (base_root / ".git" / "MERGE_HEAD").exists():
            return None
    except OSError:
        return None
    session = open_merge_session_of_project(project_id)
    return {
        "merge_id": int(session["merge_id"]) if session else None,
        "group_id": session.get("group_id") if session else None,
    }


def guard_base_free(project_id: str) -> None:
    """Reject a base-mutating op while any unresolved merge session holds the base
    checkout (P scenario 3 / L §2.2). State-based, not lock-based: it survives
    restarts and never depends on a long-held mutex. The blocking session always
    belongs to a DIFFERENT group — a group in 'conflict' cannot itself reach a
    base-mutating entry (its own state guard rejects it first)."""
    session = open_merge_session_of_project(project_id)
    if session is None:
        return
    raise GitServiceError(
        409, "merge_conflict_open",
        f"unresolved merge of group '{session['group_id']}' holds the base checkout "
        "— resolve or abort it first",
        details={
            "blocking_group_id": session["group_id"],
            "merge_id": session.get("merge_id"),
            "conflict_since": session.get("created_at"),
        },
    )

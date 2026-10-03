"""Git integration storage (flowgate.default.0115 DB0007, migration 056).

project_git_config / group_git_state / git_merge_session(+_file) / git_project_lock
CRUD. Follows the dominant inline-SQL pattern (get_store()._fetch_one/_fetch_all/
_execute) used by db/remote_tool_grants.py.

Secret handling invariant (DB0007 I5): ``secret_enc`` only ever receives values
produced by git_service.encrypt_secret() — this module stores what it is given
and never logs it.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import meta_cache
from .connection import get_store, now_iso

PROVIDER_VALUES = ("github", "gitlab", "gitea", "gitbucket", "generic")
ACTION_VALUES = ("merge", "merge_only", "push", "wait")
STATE_VALUES = (
    "none", "awaiting_choice", "merging", "conflict", "merged", "pushed", "waiting",
)
# TR work-scope check enforcement stages (0299 D0004 §3.6, migration 071). The order IS the
# strength, and tr_scope_service uses it for the "observe < warn < enforce" comparison.
TR_SCOPE_STAGE_VALUES = ("observe", "warn", "enforce")


# ── project_git_config ────────────────────────────────────────────────────────

def _get_config_db(project_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM project_git_config WHERE project_id = ?", [project_id]
    )


def get_config(project_id: str) -> Optional[dict]:
    """TTL-cached read (0282 NR0003 finding 2) — several layers each re-fetch the
    config on one screen load. Returns a copy so a caller mutating its row
    cannot poison the cache; upsert/delete below invalidate explicitly (they
    also read via _get_config_db so their existence checks are never stale)."""
    row = meta_cache.git_config_cache().get_or_load(
        project_id, lambda: _get_config_db(project_id)
    )
    return dict(row) if isinstance(row, dict) else row


def upsert_config(project_id: str, data: dict[str, Any]) -> dict:
    """Insert or update the project's git config.

    ``data['secret_enc']`` semantics: the key must always be present and carry
    the FINAL value to store (the caller resolves the null=keep / ""=clear /
    value=replace protocol before reaching storage).
    """
    now = now_iso()
    existing = _get_config_db(project_id)
    store = get_store()
    if existing is None:
        store._execute(
            "INSERT INTO project_git_config "
            "(project_id, repo_url, provider, username, secret_enc, base_branch, "
            "default_finalize_action, enabled, translate_url, author_name, author_email, "
            "tr_scope_stage, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                project_id, data["repo_url"], data.get("provider") or "generic",
                data.get("username"), data.get("secret_enc"),
                data.get("base_branch") or "main",
                data.get("default_finalize_action") or "wait",
                1 if data.get("enabled") else 0, data.get("translate_url"),
                data.get("author_name"), data.get("author_email"),
                data.get("tr_scope_stage") or "observe", now, now,
            ],
        )
    else:
        store._execute(
            "UPDATE project_git_config SET repo_url = ?, provider = ?, username = ?, "
            "secret_enc = ?, base_branch = ?, default_finalize_action = ?, enabled = ?, "
            "translate_url = ?, author_name = ?, author_email = ?, tr_scope_stage = ?, "
            "updated_at = ? WHERE project_id = ?",
            [
                data["repo_url"], data.get("provider") or "generic",
                data.get("username"), data.get("secret_enc"),
                data.get("base_branch") or "main",
                data.get("default_finalize_action") or "wait",
                1 if data.get("enabled") else 0, data.get("translate_url"),
                data.get("author_name"), data.get("author_email"),
                data.get("tr_scope_stage") or "observe", now, project_id,
            ],
        )
    meta_cache.invalidate_git_config(project_id)
    return get_config(project_id)  # type: ignore[return-value]


def set_default_merge_target(project_id: str, branch: Optional[str]) -> None:
    """Persist the project's suggested finalize target (T0016 §2.2) without
    disturbing any other git config field. A non-base integration branch that a
    finalize actually merged into becomes this project's suggested default for
    the NEXT group's finalize dialog, instead of resetting to base_branch every
    time. ``None``/blank clears the suggestion back to "none"."""
    if _get_config_db(project_id) is None:
        return
    get_store()._execute(
        "UPDATE project_git_config SET default_merge_target = ? WHERE project_id = ?",
        [branch or None, project_id],
    )
    meta_cache.invalidate_git_config(project_id)


def delete_config(project_id: str) -> bool:
    if _get_config_db(project_id) is None:
        return False
    get_store()._execute(
        "DELETE FROM project_git_config WHERE project_id = ?", [project_id]
    )
    meta_cache.invalidate_git_config(project_id)
    return True


# ── group_git_state ───────────────────────────────────────────────────────────

def get_state(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [group_id]
    )


def get_state_by_branch(project_id: str, branch: str) -> Optional[dict]:
    """Return any durable FlowGate owner of a group worktree branch.

    Unlike ``list_states_of_project``, this intentionally includes unregistered
    historical/failure rows: a stale internal branch must never become another
    group's work base merely because its worktree is currently absent.
    """
    return get_store()._fetch_one(
        "SELECT * FROM group_git_state WHERE project_id = ? AND branch = ?",
        [project_id, branch],
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


def set_initial_source_sync(group_id: str, sha: Optional[str]) -> None:
    """Persist the flowgate.default.0511 T0004 initial-source-sync marker.

    Called exactly once per group, inside the project git lock, right after the
    ONE forced reset+clean lands (or, for a legacy group with prior
    tr_commit_ledger history, as a safe non-destructive backfill — see
    git_service.ensure_initial_group_source_sync). No CAS/rowcount check: the
    caller already re-read the row inside the same lock immediately before
    calling this (marker recheck), so a second writer cannot be racing it.
    """
    get_store()._execute(
        "UPDATE group_git_state SET initial_source_sync_at = ?, "
        "initial_source_sync_sha = ?, updated_at = ? WHERE group_id = ?",
        [now_iso(), sha, now_iso(), group_id],
    )


# ── group work-base commit record (flowgate.default.0665 T0004, migration 129) ──

WORK_BASE_VERIFIED = "verified"
WORK_BASE_UNVERIFIED = "unverified"
WORK_BASE_CONFIRMED = "confirmed"
WORK_BASE_STATES = (WORK_BASE_VERIFIED, WORK_BASE_UNVERIFIED, WORK_BASE_CONFIRMED)
WORK_BASE_LOG_KINDS = ("fork", "reopen_fork", "update", "backfill", "manual_confirm")


def work_base_evidence(state: Optional[dict]) -> dict:
    """Decoded ``work_base_evidence`` JSON; ``{}`` for NULL or an unreadable value."""
    raw = (state or {}).get("work_base_evidence")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def set_work_base_record(
    group_id: str,
    *,
    work_base_sha: Optional[str],
    work_base_sync_sha: Optional[str],
    state: str,
    evidence: dict,
) -> None:
    """Replace the whole recorded floor of one group (fork, reopen, backfill, confirm)."""
    if state not in WORK_BASE_STATES:
        raise ValueError(f"invalid work base state: {state!r}")
    now = now_iso()
    get_store()._execute(
        "UPDATE group_git_state SET work_base_sha = ?, work_base_sync_sha = ?, "
        "work_base_state = ?, work_base_evidence = ?, work_base_recorded_at = ?, "
        "updated_at = ? WHERE group_id = ?",
        [work_base_sha, work_base_sync_sha, state,
         json.dumps(evidence, ensure_ascii=False, sort_keys=True), now, now, group_id],
    )


def set_work_base_sync(group_id: str, sync_sha: str, expected_floor: Optional[str]) -> bool:
    """Advance ``work_base_sync_sha`` after a completed update-from-base.

    Conditional on the floor the caller validated, so a concurrent confirm or
    reopen cannot be overwritten with a value derived from a stale floor. The
    caller holds the project Git lock (every floor writer does), so reading the
    row back is an exact answer to "did the expected row move".
    """
    now = now_iso()
    floor = expected_floor or ""
    get_store()._execute(
        "UPDATE group_git_state SET work_base_sync_sha = ?, work_base_recorded_at = ?, "
        "updated_at = ? WHERE group_id = ? "
        "AND COALESCE(work_base_sync_sha, work_base_sha, '') = ?",
        [sync_sha, now, now, group_id, floor],
    )
    row = get_state(group_id) or {}
    return row.get("work_base_sync_sha") == sync_sha


def advance_work_base_sync(
    group_id: str,
    project_id: str,
    sync_sha: str,
    expected_floor: Optional[str],
    *,
    source_ref: Optional[str] = None,
    result_head: Optional[str] = None,
    merge_id: Optional[int] = None,
    evidence: Optional[dict] = None,
) -> bool:
    """Advance the floor and append its ``update`` audit row as ONE record.

    Returns False (nothing written) when the compare-and-set lost. When anything
    fails after the floor moved -- the audit insert included -- the previous floor
    is kept: the transaction rolls back, and because some adapters commit per
    statement the previous value is also restored explicitly (only while the row
    still holds ``sync_sha``) before the error propagates. The caller then rolls
    the Git merge back, so the floor never names a commit HEAD does not contain.
    """
    previous = get_state(group_id) or {}
    advanced = False
    try:
        with get_store().transaction():
            if not set_work_base_sync(group_id, sync_sha, expected_floor):
                return False
            advanced = True
            append_work_base_log(
                group_id, project_id, "update", source_ref=source_ref, source_sha=sync_sha,
                result_head=result_head, merge_id=merge_id, evidence=evidence,
            )
    except BaseException:
        if advanced:
            _restore_work_base_sync(group_id, sync_sha, previous)
        raise
    return True


def _restore_work_base_sync(group_id: str, written_sha: str, previous: dict) -> None:
    try:
        get_store()._execute(
            "UPDATE group_git_state SET work_base_sync_sha = ?, work_base_recorded_at = ?, "
            "updated_at = ? WHERE group_id = ? AND work_base_sync_sha = ?",
            [previous.get("work_base_sync_sha"), previous.get("work_base_recorded_at"),
             now_iso(), group_id, written_sha],
        )
    except Exception:  # noqa: BLE001 -- the original failure is what propagates
        import logging
        logging.getLogger(__name__).error(
            "work base floor restore failed for %s", group_id, exc_info=True)


def append_work_base_log(
    group_id: str,
    project_id: str,
    kind: str,
    *,
    source_ref: Optional[str] = None,
    source_sha: Optional[str] = None,
    result_head: Optional[str] = None,
    merge_id: Optional[int] = None,
    actor: Optional[str] = None,
    evidence: Optional[dict] = None,
) -> str:
    if kind not in WORK_BASE_LOG_KINDS:
        raise ValueError(f"invalid work base log kind: {kind!r}")
    import uuid

    log_id = uuid.uuid4().hex
    get_store()._execute(
        "INSERT INTO group_work_base_sync_log "
        "(log_id, group_id, project_id, kind, source_ref, source_sha, result_head, "
        "merge_id, actor, evidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [log_id, group_id, project_id, kind, source_ref, source_sha, result_head,
         merge_id, actor,
         json.dumps(evidence, ensure_ascii=False, sort_keys=True) if evidence else None,
         now_iso()],
    )
    return log_id


def list_work_base_log(group_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM group_work_base_sync_log WHERE group_id = ? "
        "ORDER BY created_at ASC, log_id ASC",
        [group_id],
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


def final_approval_retry_context(state: Optional[dict]) -> Optional[dict]:
    """Decode the durable clean-finalize retry snapshot (0555 A11).

    Invalid or legacy values fail closed: callers see no retry capability and
    therefore cannot approve an AC by guessing from terminal Git state alone.
    """
    raw = (state or {}).get("final_approval_retry")
    if not raw:
        return None
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
    intent = parsed.get("intent") if isinstance(parsed, dict) else None
    if not isinstance(intent, dict) or not intent.get("approval_intent_id"):
        return None
    return parsed


def set_final_approval_retry(group_id: str, retry: dict) -> None:
    """Persist a retry snapshot without changing the ledger status.

    Used by the no-work/discarded terminal result, whose public label is not a
    value accepted by group_git_state.status.
    """
    get_store()._execute(
        "UPDATE group_git_state SET final_approval_retry = ?, updated_at = ? "
        "WHERE group_id = ?",
        [json.dumps(retry, ensure_ascii=False), now_iso(), group_id],
    )


def set_status_with_final_approval_retry(
    group_id: str,
    status: str,
    retry: dict,
    *,
    merge_id: Optional[int] = None,
    merge_commit: Optional[str] = None,
) -> None:
    """Record terminal Git and its approval retry evidence in one DB write."""
    if status not in STATE_VALUES:
        raise ValueError(f"invalid git state: {status!r}")
    get_store()._execute(
        "UPDATE group_git_state SET status = ?, merge_id = ?, merge_commit = ?, "
        "final_approval_retry = ?, updated_at = ? WHERE group_id = ?",
        [
            status, merge_id, merge_commit,
            json.dumps(retry, ensure_ascii=False), now_iso(), group_id,
        ],
    )


def consume_final_approval_retry(group_id: str, approval_intent_id: str) -> bool:
    """CAS-clear exactly one retry snapshot inside the caller's transaction."""
    state = get_state(group_id)
    retry = final_approval_retry_context(state)
    intent = (retry or {}).get("intent") or {}
    if intent.get("approval_intent_id") != approval_intent_id:
        return False
    raw = (state or {}).get("final_approval_retry")
    affected = get_store()._execute_affected(
        "UPDATE group_git_state SET final_approval_retry = NULL, updated_at = ? "
        "WHERE group_id = ? AND final_approval_retry = ?",
        [now_iso(), group_id, raw],
    )
    return affected == 1


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

# git_merge_session.kind (088). 'merge' is the finalize merge — the only kind that existed
# before flowgate.default.0332 — and the two tr_* kinds are a rewind's cancel and a forward
# restore's reapply hitting a conflict. A NULL column reads as 'merge': rows written before
# 088 landed are all finalize merges, and nothing should have to backfill to be correct.
SESSION_KIND_MERGE = "merge"
SESSION_KIND_TR_REVERT = "tr_revert"
SESSION_KIND_TR_REAPPLY = "tr_reapply"
SESSION_KIND_GROUP_UPDATE = "group_update"
# flowgate.default.0630 T0005: an ordinary Branch Manager merge (local source → local target)
# that stopped on a conflict. Unlike every kind above it has no group: it is owned by the
# project (owner_type='branch_merge', migration 123). It walks the same resolver and the same
# merge review as a finalize `merge` — never the commit-on-resolve path of `group_update`.
SESSION_KIND_BRANCH_MERGE = "branch_merge"
SESSION_KINDS = (
    SESSION_KIND_MERGE, SESSION_KIND_TR_REVERT, SESSION_KIND_TR_REAPPLY,
    SESSION_KIND_GROUP_UPDATE, SESSION_KIND_BRANCH_MERGE,
)
TR_SESSION_KINDS = (SESSION_KIND_TR_REVERT, SESSION_KIND_TR_REAPPLY)
WORKTREE_SESSION_KINDS = (*TR_SESSION_KINDS, SESSION_KIND_GROUP_UPDATE)
# Kinds that carry a pinned merge target and end in the human merge review gate.
MERGE_REVIEW_SESSION_KINDS = (SESSION_KIND_MERGE, SESSION_KIND_BRANCH_MERGE)

# git_merge_session.owner_type (123). NULL reads as 'group': every row written before 123
# was a group's session.
OWNER_GROUP = "group"
OWNER_BRANCH_MERGE = "branch_merge"


def session_kind(session: Optional[dict]) -> str:
    """The session's kind, with the pre-088 NULL read as 'merge'."""
    return str((session or {}).get("kind") or SESSION_KIND_MERGE)


def session_owner_type(session: Optional[dict]) -> str:
    """The session's owner, with the pre-123 NULL read as 'group'."""
    owner = (session or {}).get("owner_type")
    if owner:
        return str(owner)
    if session_kind(session) == SESSION_KIND_BRANCH_MERGE:
        return OWNER_BRANCH_MERGE
    return OWNER_GROUP


def is_branch_merge_session(session: Optional[dict]) -> bool:
    return bool(session) and session_owner_type(session) == OWNER_BRANCH_MERGE


def session_project_id(session: Optional[dict]) -> Optional[str]:
    """The owning project: the stored column for a branch merge, the group prefix otherwise
    (the same derivation git_service._project_of_group has always used for group rows)."""
    if not session:
        return None
    if session.get("project_id"):
        return str(session["project_id"])
    group_id = session.get("group_id")
    if group_id:
        return str(group_id).split(".", 1)[0]
    return None


def session_context(session: Optional[dict]) -> dict:
    """The session's `context` JSON as a dict; {} for a merge session or unreadable text."""
    raw = (session or {}).get("context")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def create_session(
    group_id: str,
    files: list[str],
    finalize_action: str | None = None,
    *,
    kind: str = SESSION_KIND_MERGE,
    context: Optional[dict] = None,
) -> int:
    """Create an open conflict session with its conflict file set (one transaction).

    `kind`/`context` (088) are what let a TR revert conflict live in this table beside the
    finalize merge it has nothing else in common with: same row shape, same file list, same
    merge_id the panel and the AI conflict token are already keyed on — and everything that
    genuinely differs (which worktree, which ledger row, what commit ends it) inside `context`.
    """
    if finalize_action is not None and finalize_action not in ACTION_VALUES:
        raise ValueError(f"invalid finalize action: {finalize_action!r}")
    if kind not in SESSION_KINDS:
        raise ValueError(f"invalid session kind: {kind!r}")
    now = now_iso()
    store = get_store()
    context_json = json.dumps(context or {}, ensure_ascii=False)
    with store.transaction():
        store._execute(
            "INSERT INTO git_merge_session "
            "(group_id, status, finalize_action, kind, context, created_at, touched_at) "
            "VALUES (?, 'open', ?, ?, ?, ?, ?)",
            [group_id, finalize_action, kind, context_json, now, now],
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


def create_branch_merge_session(
    project_id: str,
    *,
    finalize_action: str | None = None,
    context: Optional[dict] = None,
) -> int:
    """Open a group-less ``branch_merge`` session owned by ``project_id`` (0630 T0005).

    Written BEFORE ``git merge`` runs (the attempt record), with no conflict files yet —
    ``add_session_files`` attaches them if the merge stops. ``group_id`` stays NULL: there is
    no group, and inventing one is exactly what D0004 §2.1 forbids. Callers hold the project
    Git lock, so the newest open branch_merge row of this project is the one just inserted.
    """
    if finalize_action is not None and finalize_action not in ACTION_VALUES:
        raise ValueError(f"invalid finalize action: {finalize_action!r}")
    now = now_iso()
    store = get_store()
    context_json = json.dumps(context or {}, ensure_ascii=False)
    with store.transaction():
        store._execute(
            "INSERT INTO git_merge_session "
            "(group_id, status, finalize_action, kind, context, created_at, touched_at, "
            "owner_type, project_id) "
            "VALUES (NULL, 'open', ?, ?, ?, ?, ?, ?, ?)",
            [finalize_action, SESSION_KIND_BRANCH_MERGE, context_json, now, now,
             OWNER_BRANCH_MERGE, project_id],
        )
        row = store._fetch_one(
            "SELECT merge_id FROM git_merge_session "
            "WHERE project_id = ? AND owner_type = ? AND status = 'open' "
            "ORDER BY merge_id DESC",
            [project_id, OWNER_BRANCH_MERGE],
        )
    return int(row["merge_id"])


def list_branch_merge_sessions(project_id: str, *, open_only: bool = True) -> list[dict]:
    """A project's branch_merge sessions, newest first (0630 T0005)."""
    sql = (
        "SELECT * FROM git_merge_session WHERE project_id = ? AND owner_type = ?"
        + (" AND status = 'open'" if open_only else "")
        + " ORDER BY merge_id DESC"
    )
    return get_store()._fetch_all(sql, [project_id, OWNER_BRANCH_MERGE])


def add_session_files(merge_id: int, files: list[str]) -> None:
    """Attach conflict files to an already-open session (flowgate.default.0594 T0012).

    A finalize attempt record is now written BEFORE the merge runs, so its conflict
    file set is only known afterwards. Same rows ``create_session`` writes."""
    store = get_store()
    with store.transaction():
        for path in files:
            store._execute(
                "INSERT INTO git_merge_session_file (merge_id, path, resolved) "
                "VALUES (?, ?, 0)",
                [merge_id, path],
            )


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


CHECKPOINT_STATES = ("active", "consumed", "invalidated")


def create_resolution_checkpoint(data: dict) -> dict:
    """Persist one held finalize resolution independently of its merge session."""
    import uuid

    checkpoint_id = str(uuid.uuid4())
    now = now_iso()
    store = get_store()
    with store.transaction():
        store._execute(
            "UPDATE git_resolution_checkpoint SET state = 'invalidated', updated_at = ? "
            "WHERE project_id = ? AND group_id = ? AND state = 'active'",
            [now, data["project_id"], data["group_id"]],
        )
        store._execute(
            "INSERT INTO git_resolution_checkpoint "
            "(checkpoint_id, project_id, group_id, source_merge_id, target_branch, "
            "source_branch, base_head, merge_head, expected_remote_head, resolved_paths, "
            "conflict_origins, provenance, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            [checkpoint_id, data["project_id"], data["group_id"], data["source_merge_id"],
             data["target_branch"], data["source_branch"], data["base_head"],
             data["merge_head"], data.get("expected_remote_head"),
             json.dumps(data["resolved_paths"], ensure_ascii=False),
             json.dumps(data.get("conflict_origins") or [], ensure_ascii=False),
             json.dumps(data["provenance"], ensure_ascii=False), now, now],
        )
    return get_resolution_checkpoint(checkpoint_id)


def rollback_resolution_checkpoint(checkpoint_id: str, previous_id: str | None) -> None:
    """Undo the committed hold ledger transition when merge --abort failed."""
    store = get_store()
    with store.transaction():
        store._execute(
            "UPDATE git_resolution_checkpoint SET state = 'invalidated', updated_at = ? "
            "WHERE checkpoint_id = ? AND state = 'active'",
            [now_iso(), checkpoint_id],
        )
        if previous_id:
            store._execute(
                "UPDATE git_resolution_checkpoint SET state = 'active', updated_at = ? "
                "WHERE checkpoint_id = ? AND state = 'invalidated'",
                [now_iso(), previous_id],
            )


def get_resolution_checkpoint(checkpoint_id: str) -> Optional[dict]:
    row = get_store()._fetch_one(
        "SELECT * FROM git_resolution_checkpoint WHERE checkpoint_id = ?", [checkpoint_id]
    )
    return _decode_resolution_checkpoint(row)


def _decode_resolution_checkpoint(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    for key in ("resolved_paths", "conflict_origins", "provenance", "replay_result"):
        try:
            result[key] = json.loads(result[key]) if result.get(key) else ([] if key != "provenance" else {})
        except (TypeError, ValueError):
            result[key] = [] if key != "provenance" else {}
    return result


def active_resolution_checkpoint(project_id: str, group_id: str) -> Optional[dict]:
    row = get_store()._fetch_one(
        "SELECT * FROM git_resolution_checkpoint WHERE project_id = ? AND group_id = ? "
        "AND state = 'active' ORDER BY created_at DESC LIMIT 1",
        [project_id, group_id],
    )
    return _decode_resolution_checkpoint(row)


def set_resolution_checkpoint_replay(checkpoint_id: str, merge_id: int, result: dict) -> None:
    get_store()._execute(
        "UPDATE git_resolution_checkpoint SET replay_merge_id = ?, replay_result = ?, "
        "updated_at = ? WHERE checkpoint_id = ? AND state = 'active'",
        [merge_id, json.dumps(result, ensure_ascii=False), now_iso(), checkpoint_id],
    )


def invalidate_resolution_checkpoint(checkpoint_id: str) -> None:
    get_store()._execute(
        "UPDATE git_resolution_checkpoint SET state = 'invalidated', updated_at = ? "
        "WHERE checkpoint_id = ? AND state = 'active'",
        [now_iso(), checkpoint_id],
    )


def invalidate_resolution_checkpoint_for_merge(merge_id: int) -> None:
    """Discard any hold or replayed checkpoint abandoned by explicit [abort]."""
    get_store()._execute(
        "UPDATE git_resolution_checkpoint SET state = 'invalidated', updated_at = ? "
        "WHERE state = 'active' AND (source_merge_id = ? OR replay_merge_id = ?)",
        [now_iso(), merge_id, merge_id],
    )


def consume_resolution_checkpoint(merge_id: int) -> None:
    get_store()._execute(
        "UPDATE git_resolution_checkpoint SET state = 'consumed', updated_at = ? "
        "WHERE replay_merge_id = ? AND state = 'active'",
        [now_iso(), merge_id],
    )


def consume_active_resolution_checkpoint(project_id: str, group_id: str) -> None:
    """Finish a held checkpoint when retry merges cleanly without a conflict session."""
    get_store()._execute(
        "UPDATE git_resolution_checkpoint SET state = 'consumed', updated_at = ? "
        "WHERE project_id = ? AND group_id = ? AND state = 'active'",
        [now_iso(), project_id, group_id],
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


def sessions_by_group(group_id: str) -> list[dict]:
    """Every session ever opened for a group, newest first.

    The open-session accessors above answer "what is this group doing now".  A
    deferred final approval also has to find the session it was parked on AFTER
    that session closed (0555 D0005 §3.9 re-approval), so the closed rows have to
    be reachable too.
    """
    return get_store()._fetch_all(
        "SELECT * FROM git_merge_session WHERE group_id = ? ORDER BY merge_id DESC",
        [group_id],
    )


def cas_session_context(merge_id: int, expected_raw: Any, context: dict) -> bool:
    """Replace a session's `context` only if the stored text is still `expected_raw`.

    :func:`set_session_context` is a blind write and `_execute` reports no rowcount
    (see [[store-execute-has-no-rowcount]]).  A final approval consuming its intent
    has to know whether THIS call was the one that consumed it, so it goes through
    the affected-row boundary with the previous text as the CAS condition.  Run
    inside a transaction the caller owns and the consume shares that unit of work.
    """
    affected = get_store()._execute_affected(
        "UPDATE git_merge_session SET context = ? WHERE merge_id = ? AND context = ?",
        [json.dumps(context or {}, ensure_ascii=False), merge_id, expected_raw],
    )
    return affected == 1


def set_session_context(merge_id: int, context: dict) -> None:
    """Replace a session's `context` JSON (088).

    Read-modify-write is the caller's job. `_execute` reports no rowcount
    (see [[store-execute-has-no-rowcount]]), so a caller that has to know the write landed
    reads the row back instead of trusting a return value that does not exist.
    """
    get_store()._execute(
        "UPDATE git_merge_session SET context = ? WHERE merge_id = ?",
        [json.dumps(context or {}, ensure_ascii=False), merge_id],
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

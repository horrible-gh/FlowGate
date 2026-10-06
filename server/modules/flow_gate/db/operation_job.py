"""Operation job storage (flowgate.default.0669 units 5a/5b; 0666 0010-DB §2.3/§4 (g)~(l), 0009-L §2.13~2.19.1).

``operation_job`` (migration 131) CRUD and CAS writes. Every write that moves a job
follows the contract of ``git_concurrency``: the WHERE carries the expected state
(status set / lease_token / cancel flag) and success is the affected-row count from
``_execute_affected`` — never a re-read. Writes whose SET could equal the stored value
bump ``touch_seq`` so MySQL/MariaDB (changed rows) agrees with SQLite/PostgreSQL
(matched rows), DB §2.0.

MySQL evaluates a multi-column SET left to right with already-updated values, so any
assignment that reads a column (``claimed_from = status``) comes before the assignment
that overwrites that column. DB §4(h) lists them the other way round; the order here is
the one that means the same thing on all three engines.

Time columns hold ``now_iso()`` strings and callers bind every threshold already
computed in that format. No DB clock function, RETURNING, upsert or SKIP LOCKED.

Unit 5b added the reconcile claim (DB §4(k)), the DOMAIN_RELEASED targets (l), the
recovery_required retry claim (L 2.18) and a kind filter on the candidate reads.

Unit 6a added the freeze/pin column writes (F1~F5, ``FREEZE_COLUMNS`` through
``job_write(allow_freeze=True)``) and the Group freeze claim on ``group_git_state``
(DB §4(q), migration 134).
"""
from __future__ import annotations

from typing import Iterable, Optional

from .connection import get_store

WAITING_STATUSES = ("freeze_wait", "pending", "blocked", "retry_wait")
EXECUTING_STATUSES = ("running", "freezing")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")

_INSERT_COLUMNS = (
    "job_id", "kind", "project_id", "group_id", "target_key", "request_key",
    "request_key_hash", "request_fingerprint", "has_freeze_phase", "status", "phase",
    "payload", "available_at", "lease_owner", "lease_token", "lease_until",
    "requested_by", "created_at", "updated_at",
)

# Columns an executing context may write through job_write (L 2.15). Freeze/pin columns
# belong to the F1~F5 transactions and need allow_freeze=True (approval_freeze only).
WRITABLE_COLUMNS = frozenset((
    "status", "phase", "payload", "evidence", "attempt_count", "available_at",
    "lease_owner", "lease_token", "lease_until", "blocked_domain", "blocked_lock_key",
    "blocked_holder", "blocked_operation", "wait_started_at", "last_error_code",
    "retryable", "release_intent", "recovery_since", "phase_note", "result", "finished_at",
))
FREEZE_COLUMNS = frozenset(("freeze_record", "freeze_completed", "frozen_sha", "frozen_tree", "pin_ref"))

# DB §4(i): publish / freeze / release claimable, as one predicate. Bind order: now x3.
_CLAIMABLE = (
    "((status IN ('pending','blocked','retry_wait') AND available_at <= ? AND cancel_requested = 0 "
    "AND (has_freeze_phase = 0 OR freeze_completed = 1)) "
    "OR (status = 'freeze_wait' AND available_at <= ? AND cancel_requested = 0 AND release_intent IS NULL) "
    "OR (has_freeze_phase = 1 AND status IN ('freeze_wait','pending','blocked','retry_wait') "
    "AND available_at <= ? AND (cancel_requested = 1 OR release_intent IS NOT NULL)))"
)


def _in(values: Iterable[str]) -> tuple[str, list]:
    values = list(values)
    if not values:
        raise ValueError("empty status set")
    return "(" + ", ".join("?" for _ in values) + ")", values


# ── create / read (DB §4(g), L 2.13.2) ───────────────────────────────────────

def insert_job(row: dict) -> None:
    """Insert one job. Raises on a (project_id, kind, request_key_hash) collision; the
    caller re-reads with get_job_by_key and never retries the insert blindly.

    Under PostgreSQL an expected unique violation aborts its transaction, so the caller
    runs this in a transaction of its own (DB §4, D 8.4).
    """
    unknown = set(row) - set(_INSERT_COLUMNS)
    if unknown:
        raise ValueError(f"unknown operation_job columns: {sorted(unknown)}")
    cols = [c for c in _INSERT_COLUMNS if c in row]
    get_store()._execute(
        "INSERT INTO operation_job (" + ", ".join(cols) + ", attempt_count, touch_seq) "
        "VALUES (" + ", ".join("?" for _ in cols) + ", 0, 0)",
        [row[c] for c in cols],
    )


def get_job(job_id: str) -> Optional[dict]:
    return get_store()._fetch_one("SELECT * FROM operation_job WHERE job_id = ?", [job_id])


def get_job_by_key(project_id: str, kind: str, request_key_hash: str) -> Optional[dict]:
    """(g) Idempotent lookup (idx_job_request)."""
    return get_store()._fetch_one(
        "SELECT * FROM operation_job WHERE project_id = ? AND kind = ? AND request_key_hash = ?",
        [project_id, kind, request_key_hash],
    )


def count_failed_or_cancelled_by_key_prefix(project_id: str, kind: str, key_prefix: str) -> int:
    """final_approval_publish re-approval seq (L 2.13.1): failed/cancelled jobs of one doc.

    The doc is identified by the request_key prefix 'fap:{doc_id}:r'; LIKE metacharacters
    in the prefix are escaped with '!' (portable — no backslash, which MySQL treats as a
    string escape).
    """
    escaped = key_prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS n FROM operation_job "
        "WHERE project_id = ? AND kind = ? AND status IN ('failed','cancelled') "
        "AND request_key LIKE ? ESCAPE '!'",
        [project_id, kind, escaped + "%"],
    )
    return int((row or {}).get("n") or 0)


def count_terminal_by_key_prefix(project_id: str, kind: str, key_prefix: str) -> int:
    """Jobs of one request-key prefix that already ended, whatever way (succeeded included).

    branch_merge_publish: a merge that ended (handed off to a conflict that was then aborted,
    or merged) must not hand its result to the next identical request; only a job still in
    flight is "the same click". '!' escapes LIKE metacharacters as above."""
    escaped = key_prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS n FROM operation_job "
        "WHERE project_id = ? AND kind = ? "
        "AND status IN ('succeeded','failed','cancelled') "
        "AND request_key LIKE ? ESCAPE '!'",
        [project_id, kind, escaped + "%"],
    )
    return int((row or {}).get("n") or 0)


# ── claim (DB §4(h), L 2.14) — one job per CAS ───────────────────────────────

def claim_publish(job_id: str, instance_id: str, lease_token: str, lease_until: str, now: str) -> int:
    """pending/blocked/retry_wait -> running. 1 = won, 0 = LOST."""
    return get_store()._execute_affected(
        "UPDATE operation_job "
        "SET claimed_from = status, status = 'running', lease_owner = ?, lease_token = ?, "
        "lease_until = ?, updated_at = ?, touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND status IN ('pending','blocked','retry_wait') "
        "AND available_at <= ? AND cancel_requested = 0 "
        "AND (has_freeze_phase = 0 OR (freeze_completed = 1 AND frozen_sha IS NOT NULL "
        "AND frozen_tree IS NOT NULL AND pin_ref IS NOT NULL))",
        [instance_id, lease_token, lease_until, now, job_id, now],
    )


def claim_freeze(job_id: str, instance_id: str, lease_token: str, lease_until: str, now: str) -> int:
    """freeze_wait -> freezing (re-freeze). 1 = won, 0 = LOST."""
    return get_store()._execute_affected(
        "UPDATE operation_job "
        "SET claimed_from = status, status = 'freezing', lease_owner = ?, lease_token = ?, "
        "lease_until = ?, updated_at = ?, touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND status = 'freeze_wait' AND available_at <= ? "
        "AND cancel_requested = 0 AND release_intent IS NULL",
        [instance_id, lease_token, lease_until, now, job_id, now],
    )


def claim_release(job_id: str, instance_id: str, lease_token: str, lease_until: str, now: str) -> int:
    """Waiting job with a freeze record and a cancel/release intent -> freezing (release)."""
    return get_store()._execute_affected(
        "UPDATE operation_job "
        "SET claimed_from = status, status = 'freezing', lease_owner = ?, lease_token = ?, "
        "lease_until = ?, updated_at = ?, touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND has_freeze_phase = 1 "
        "AND status IN ('freeze_wait','pending','blocked','retry_wait') "
        "AND (cancel_requested = 1 OR release_intent IS NOT NULL) AND available_at <= ?",
        [instance_id, lease_token, lease_until, now, job_id, now],
    )


# ── lease (L 2.15) ───────────────────────────────────────────────────────────

def renew_lease(job_id: str, lease_token: str, now: str, lease_until: str) -> int:
    """Extend a lease that is still unexpired by the holder's own clock. 0 = lost.

    touch_seq: renew_now and the loop can renew twice in one second with the same
    lease_until, which MySQL would otherwise report as 0 rows — a false LeaseLost.
    """
    return get_store()._execute_affected(
        "UPDATE operation_job SET lease_until = ?, updated_at = ?, touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND lease_token = ? AND status IN ('running','freezing') "
        "AND lease_until > ?",
        [lease_until, now, job_id, lease_token, now],
    )


def job_write(job_id: str, lease_token: str, expected_statuses: Iterable[str],
              changes: dict, now: str, *, allow_freeze: bool = False) -> int:
    """Every phase/evidence/status write of an executing context. 0 = LeaseLost.

    The lease_token + status set condition is what makes a late writer's record vanish
    (D 3.6.4); touch_seq keeps a no-op SET at 1 affected row on MySQL.
    """
    allowed = WRITABLE_COLUMNS | FREEZE_COLUMNS if allow_freeze else WRITABLE_COLUMNS
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"job_write cannot set: {sorted(unknown)}")
    status_sql, statuses = _in(expected_statuses)
    cols = sorted(changes)
    sets = "".join(f"{c} = ?, " for c in cols)
    return get_store()._execute_affected(
        "UPDATE operation_job SET " + sets + "updated_at = ?, touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND lease_token = ? AND status IN " + status_sql,
        [changes[c] for c in cols] + [now, job_id, lease_token] + statuses,
    )


# ── candidates (DB §4(i), §4(j)) ─────────────────────────────────────────────

def _kind_filter(kinds: Optional[Iterable[str]]) -> tuple[str, list]:
    """Optional 'AND kind IN (...)': the Runner only picks kinds it has an executor for,
    so a kind whose executor is not installed yet never eats a project's candidate cap."""
    if kinds is None:
        return "", []
    sql, values = _in(kinds)
    return " AND kind IN " + sql, values


def claimable_projects(now: str, kinds: Optional[Iterable[str]] = None) -> list[dict]:
    """(i)-1 Projects with a claimable job and their oldest created_at."""
    kind_sql, kind_values = _kind_filter(kinds)
    return get_store()._fetch_all(
        "SELECT project_id, MIN(created_at) AS oldest FROM operation_job "
        "WHERE " + _CLAIMABLE + kind_sql + " GROUP BY project_id",
        [now, now, now] + kind_values,
    )


def claimable_jobs(project_id: str, now: str, limit: int,
                   kinds: Optional[Iterable[str]] = None) -> list[dict]:
    """(i)-2 Oldest claimable jobs of one project. The round-robin cursor across projects
    is application memory (the caller), not stored."""
    kind_sql, kind_values = _kind_filter(kinds)
    return get_store()._fetch_all(
        "SELECT job_id, kind, status, has_freeze_phase, cancel_requested, release_intent "
        "FROM operation_job WHERE project_id = ? AND " + _CLAIMABLE + kind_sql + " "
        "ORDER BY created_at, job_id LIMIT ?",
        [project_id, now, now, now] + kind_values + [int(limit)],
    )


def list_expired_leases(threshold: str, limit: int) -> list[dict]:
    """(j) running/freezing jobs whose lease lapsed or whose owner is not alive.

    threshold = now - CLOCK_SKEW_MARGIN_SEC. Read only — moving these jobs is the
    Sweeper's reconcile (reconcile_claim below); nothing re-claims running/freezing
    for execution (invariant I3).
    """
    return get_store()._fetch_all(
        "SELECT job_id, lease_token, lease_owner, lease_until, status FROM operation_job j "
        "WHERE j.status IN ('running','freezing') AND ("
        "(j.lease_until IS NOT NULL AND j.lease_until < ?) "
        "OR j.lease_owner IS NULL "
        "OR NOT EXISTS (SELECT 1 FROM server_instance si "
        "WHERE si.instance_id = j.lease_owner AND si.status = 'alive')) "
        "ORDER BY j.lease_until LIMIT ?",
        [threshold, int(limit)],
    )


# (k) Same owner_dead / lease_expired predicate as (j). Bind order: threshold.
_RECLAIMABLE = (
    "((lease_until IS NOT NULL AND lease_until < ?) "
    "OR lease_owner IS NULL "
    "OR NOT EXISTS (SELECT 1 FROM server_instance si "
    "WHERE si.instance_id = operation_job.lease_owner AND si.status = 'alive'))"
)


def reconcile_claim(job_id: str, status: str, seen_token: Optional[str], seen_owner: Optional[str],
                    threshold: str, instance_id: str, lease_token: str, lease_until: str,
                    reason: str, now: str) -> int:
    """(k) Take an expired/orphaned running|freezing job for reconcile — one Sweeper wins.

    The token/owner read by (j) are re-checked NULL-safely (each bound twice) and the
    death predicate is the same as (j), with the same threshold.
    """
    return get_store()._execute_affected(
        "UPDATE operation_job "
        "SET lease_owner = ?, lease_token = ?, lease_until = ?, reconcile_claimed = 1, "
        "reconcile_reason = ?, updated_at = ?, touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND status = ? "
        "AND (lease_token = ? OR (lease_token IS NULL AND ? IS NULL)) "
        "AND (lease_owner = ? OR (lease_owner IS NULL AND ? IS NULL)) "
        "AND " + _RECLAIMABLE,
        [instance_id, lease_token, lease_until, reason, now, job_id, status,
         seen_token, seen_token, seen_owner, seen_owner, threshold],
    )


def recovery_retry_candidates(now: str, since_after: str, limit: int) -> list[dict]:
    """L 2.18: recovery_required jobs due for an automatic reconcile, inside the window."""
    return get_store()._fetch_all(
        "SELECT job_id FROM operation_job "
        "WHERE status = 'recovery_required' AND available_at <= ? AND recovery_since > ? "
        "ORDER BY available_at, job_id LIMIT ?",
        [now, since_after, int(limit)],
    )


def claim_recovery(job_id: str, instance_id: str, lease_token: str, lease_until: str,
                   now: str, since_after: str, threshold: str) -> int:
    """L 2.18 claim_for_reconcile: lease on a recovery_required job, status unchanged.

    A lease another Sweeper still holds (lease_until >= threshold) loses the CAS, so a
    retry runs on one instance at a time.
    """
    return get_store()._execute_affected(
        "UPDATE operation_job "
        "SET lease_owner = ?, lease_token = ?, lease_until = ?, updated_at = ?, "
        "touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND status = 'recovery_required' AND available_at <= ? "
        "AND recovery_since > ? AND (lease_until IS NULL OR lease_until < ?)",
        [instance_id, lease_token, lease_until, now, job_id, now, since_after, threshold],
    )


# ── wake-up targets (DB §4(l), L 2.17.1 targets_of) ──────────────────────────

def blocked_job_ids(lock_key: str, limit: int) -> list[str]:
    rows = get_store()._fetch_all(
        "SELECT job_id FROM operation_job "
        "WHERE blocked_lock_key = ? AND status IN ('blocked','freeze_wait') "
        "ORDER BY created_at, job_id LIMIT ?",
        [lock_key, int(limit)],
    )
    return [r["job_id"] for r in rows]


def blocked_job_ids_of_project(project_id: str, limit: int) -> list[str]:
    """domain '*' release (a whole project freed): any blocked/freeze_wait job there."""
    rows = get_store()._fetch_all(
        "SELECT job_id FROM operation_job "
        "WHERE project_id = ? AND status IN ('blocked','freeze_wait') "
        "ORDER BY created_at, job_id LIMIT ?",
        [project_id, int(limit)],
    )
    return [r["job_id"] for r in rows]


# ── cancel (L 2.19.1) ────────────────────────────────────────────────────────

def cancel_unfrozen_waiting(job_id: str, now: str, result: str) -> int:
    """(a) Waiting job without a freeze record -> cancelled at once."""
    return get_store()._execute_affected(
        "UPDATE operation_job "
        "SET status = 'cancelled', cancel_requested = 1, finished_at = ?, result = ?, "
        "lease_owner = NULL, lease_token = NULL, lease_until = NULL, updated_at = ?, "
        "touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND status IN ('freeze_wait','pending','blocked','retry_wait') "
        "AND (has_freeze_phase = 0 OR freeze_record IS NULL)",
        [now, result, now, job_id],
    )


def mark_cancel_for_release(job_id: str, release_intent: str, now: str) -> int:
    """(b) Waiting job with a freeze record: flag it so only a release claim picks it up."""
    return get_store()._execute_affected(
        "UPDATE operation_job "
        "SET cancel_requested = 1, release_intent = ?, available_at = ?, updated_at = ?, "
        "touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND status IN ('freeze_wait','pending','blocked','retry_wait') "
        "AND has_freeze_phase = 1 AND freeze_record IS NOT NULL "
        "AND (status = 'freeze_wait' OR phase = 'publish_pending')",
        [release_intent, now, now, job_id],
    )


def mark_cancel_requested(job_id: str, now: str) -> int:
    """(c) Executing job: flag only; the executor checks it at a phase boundary."""
    return get_store()._execute_affected(
        "UPDATE operation_job SET cancel_requested = 1, updated_at = ?, touch_seq = touch_seq + 1 "
        "WHERE job_id = ? AND status IN ('freezing','running')",
        [now, job_id],
    )


# ── Group freeze claim (DB §4(q), L 2.11; migration 134) ─────────────────────

def touch_job_lease(job_id: str, lease_token: str, expected_statuses: Iterable[str], now: str) -> int:
    """(q) first UPDATE: the job lease still ours, inside the claim's transaction."""
    status_sql, statuses = _in(expected_statuses)
    return get_store()._execute_affected(
        "UPDATE operation_job SET touch_seq = touch_seq + 1, updated_at = ? "
        "WHERE job_id = ? AND lease_token = ? AND status IN " + status_sql,
        [now, job_id, lease_token] + statuses,
    )


def set_group_freeze_claim(group_id: str, job_id: str, state: str, now: str) -> int:
    """(q) second UPDATE. 1 = the claim is this job's (first time or same job again),
    0 = another job holds it (or the Group has no group_git_state row)."""
    return get_store()._execute_affected(
        "UPDATE group_git_state "
        "SET approval_freeze_job_id = ?, approval_freeze_state = ?, approval_freeze_at = ?, "
        "touch_seq = touch_seq + 1 "
        "WHERE group_id = ? AND (approval_freeze_job_id IS NULL OR approval_freeze_job_id = ?)",
        [job_id, state, now, group_id, job_id],
    )


def clear_group_freeze_claim(group_id: str, job_id: str) -> int:
    """L 2.11 clear_freeze_claim: only the owning job's claim. 0 = not (or no longer) ours."""
    return get_store()._execute_affected(
        "UPDATE group_git_state "
        "SET approval_freeze_job_id = NULL, approval_freeze_state = NULL, approval_freeze_at = NULL, "
        "touch_seq = touch_seq + 1 "
        "WHERE group_id = ? AND approval_freeze_job_id = ?",
        [group_id, job_id],
    )


def get_group_freeze_claim(group_id: str) -> Optional[dict]:
    """{'job_id', 'state', 'at'} or None. ``SELECT *`` so a store still at migration 133
    reads as "no claim" instead of failing every G admission."""
    row = get_store()._fetch_one("SELECT * FROM group_git_state WHERE group_id = ?", [group_id])
    if not row or not row.get("approval_freeze_job_id"):
        return None
    return {"job_id": row["approval_freeze_job_id"], "state": row.get("approval_freeze_state"),
            "at": row.get("approval_freeze_at")}


def group_state_exists(group_id: str) -> bool:
    return get_store()._fetch_one(
        "SELECT group_id FROM group_git_state WHERE group_id = ?", [group_id]) is not None


def projects_with_kind(kind: str) -> list[str]:
    """Orphan pin sweep (L 2.25): only projects that ever had a job of this kind can hold
    a pin the sweep may delete."""
    rows = get_store()._fetch_all(
        "SELECT DISTINCT project_id FROM operation_job WHERE kind = ? ORDER BY project_id", [kind])
    return [r["project_id"] for r in rows]


def active_jobs_of_group(group_id: str, kind: str) -> list[dict]:
    """Non-terminal jobs of one Group and kind, oldest first (unit 6b: the approval
    in-flight probe and the approval-retry route; idx_job_group, migration 135)."""
    status_sql, statuses = _in(TERMINAL_STATUSES)
    return get_store()._fetch_all(
        "SELECT * FROM operation_job WHERE group_id = ? AND kind = ? "
        "AND status NOT IN " + status_sql + " ORDER BY created_at, job_id",
        [group_id, kind] + statuses,
    )


def jobs_of_group(group_id: str, kind: str, statuses: Iterable[str]) -> list[dict]:
    """Jobs of one Group and kind in the given statuses, oldest first (unit 7a: the
    cleanup job's pin record and the orphan pin sweep's cleanup_terminal)."""
    status_sql, values = _in(statuses)
    return get_store()._fetch_all(
        "SELECT * FROM operation_job WHERE group_id = ? AND kind = ? "
        "AND status IN " + status_sql + " ORDER BY created_at, job_id",
        [group_id, kind] + values,
    )

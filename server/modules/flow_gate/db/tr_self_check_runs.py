"""TR Self-check runs repository (flowgate.default.0650 T0002 / 0634 0007-DB).

Advisory self-check execution audit & one-active invariant store.
Does NOT store test222 verdicts (pass/fail/approved), raw secrets, or unbounded stdout.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Optional

from . import dialect as _dialect
from .connection import get_store, now_iso

_MAX_TAIL_BYTES = 64 * 1024  # 64 KiB UTF-8 encoded byte limit
_MAX_DIAGNOSTIC_CHARS = 2000


class SelfCheckAlreadyRunningError(Exception):
    """Raised when a pending/running self-check already exists for (project_id, group_id)."""

    def __init__(self, message: str, existing_run_id: str | None = None):
        super().__init__(message)
        self.existing_run_id = existing_run_id


class DraftOwnerClosedError(Exception):
    """A draft run's owner token is consumed, revoked or gone, so it can no longer own a run."""


class SelfCheckHostCapacityError(Exception):
    """0114 T#4 (D 0006 3.5 (4)): the host has no processor left to reserve. Raised inside the
    insert's transaction before the row exists, so nothing is written."""

    def __init__(self, limit: int, reserved_total: int, reserved_protected: int, requested: int,
                 desired: int):
        super().__init__(f"host capacity exhausted: {reserved_total}/{limit} reserved")
        self.limit = limit
        self.reserved_total = reserved_total
        self.reserved_protected = reserved_protected
        self.requested = requested
        self.desired = desired


#: The one row every reserving admission locks first (migration 164).
CAPACITY_LOCK_ID = "host_capacity"


def _lock_host_capacity(store) -> None:
    """Serialize reserving admissions across every instance sharing the store: the first
    statement of the insert's transaction. SQLite: a no-op UPDATE takes the database write
    lock. PostgreSQL / MySQL: a locking read of the row waits for a holder, then reads the
    committed state. A missing row locks nothing, so the admission fails closed."""
    if store.dialect == _dialect.SQLITE:
        locked = store._execute_affected(
            "UPDATE tr_self_check_capacity_lock SET updated_at = updated_at WHERE lock_id = ?",
            [CAPACITY_LOCK_ID])
    else:
        locked = store._fetch_one(
            "SELECT lock_id FROM tr_self_check_capacity_lock WHERE lock_id = ? FOR UPDATE",
            [CAPACITY_LOCK_ID]) is not None
    if not locked:
        raise RuntimeError("tr_self_check_capacity_lock row is missing")


def host_reservations(host_node_key: str | None, limit: int) -> tuple[int, int]:
    """``(total, protected)`` of the host's active (pending/running) runs' reservations.

    D 3.5 (5): a run of any project, group or instance counts; a run whose host is unknown
    counts for the requesting host (and, when the requesting host is unknown itself, every
    active run counts); a run without a stored reservation counts as the whole ``limit``.
    ``protected`` is the part held by runs in recovery (recovering / incomplete)."""
    store = get_store()
    sql = ("SELECT cpu_reservation, recovery_state FROM tr_self_check_runs"
           " WHERE status IN ('pending', 'running')")
    params: list[Any] = []
    if host_node_key is not None:
        sql += " AND (host_node_key = ? OR host_node_key IS NULL)"
        params.append(host_node_key)
    total = protected = 0
    for row in store._fetch_all(sql, params) or []:
        value = row.get("cpu_reservation")
        reserved = value if isinstance(value, int) and value >= 1 else limit
        total += reserved
        if row.get("recovery_state") not in (None, "none"):
            protected += reserved
    return total, protected


def decide_reservation(limit: int, serial_reserve: int, reserved_total: int, desired: int) -> int | None:
    """R for a request wishing ``desired`` processors, or None when nothing is free (D 3.5 (4)).

    The first processor is granted whenever one is free; the part above 1 only from what is
    left after the serial reserve, so serial runs are not easily refused because of parallel
    ones. The reserve is a rule inside the cap, never a way over it."""
    free = limit - reserved_total
    if free < 1:
        return None
    extra = max(0, min(desired - 1, free - 1 - serial_reserve))
    return 1 + extra


def _require_open_owner(store, owner_token_id: str) -> None:
    """Fail the draft insert unless its TR(new) owner token is still unconsumed and unrevoked.

    0638 T#1 rev1: runs inside the insert's transaction. On MySQL/PostgreSQL the row lock
    (FOR UPDATE) is taken BEFORE the insert, the same order token consumption takes it
    before link_owner_runs, so a registration either waits for this insert to commit (and
    its link then sees the row) or has already consumed the token (and this insert rolls
    back). SQLite has no FOR UPDATE; there the insert runs first and its write lock
    serializes the two the same way.
    """
    sql = "SELECT consumed_at, revoked_at FROM tokens WHERE token_id = ?"
    if store.dialect != _dialect.SQLITE:
        sql += " FOR UPDATE"
    row = store._fetch_one(sql, [owner_token_id])
    if row is None or row.get("consumed_at") or row.get("revoked_at"):
        raise DraftOwnerClosedError(f"draft owner token {owner_token_id} is no longer open")


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
    tr_doc_id: str | None,
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
    owner_token_id: str | None = None,
    draft_doc_ref: str | None = None,
    owner_instance_id: str | None = None,
    owner_marker: str | None = None,
    attempt_id: str | None = None,
    input_commit: str | None = None,
    host_node_key: str | None = None,
    host_capacity: tuple[int, int] | None = None,
    cpu_share_requested: int | None = None,
    progress_owner: dict | None = None,
) -> dict:
    """Atomically insert a pending self-check run row with active_key set.

    0080 T#1: ``owner_instance_id`` (the server whose worker runs it) and ``owner_marker``
    (the run-unit process ownership record, tr_self_check_proof.owner_marker) land with the
    row, i.e. before anything is spawned, so recovery can judge even a run that never got
    to ``mark_running``.

    0638 T#1: ``tr_doc_id=None`` is a draft run owned by a TR(new) token; it then needs both
    ``owner_token_id`` and ``draft_doc_ref`` (ck_selfcheck_owner, enforced here for MySQL).
    The owner token must still be open when the row commits (:func:`_require_open_owner`);
    otherwise :class:`DraftOwnerClosedError` and nothing is written, so no draft row can
    land after its registration's link and stay unlinked.

    0110 T#3: ``attempt_id`` / ``input_commit`` (migration 162) name the Graph attempt tree a
    run executes in and the commit that tree started from; both land in the insert's
    transaction. A group worktree run writes neither column.

    0114 T#3: ``host_node_key`` (migration 163) is the admitting server's host identifier
    (``instance_registry.node_key``), copied into the row in the insert's transaction so a
    baseline comparison never depends on instance registration rows that may be gone.

    0114 T#4: ``host_capacity=(cap, serial reserve)`` makes the insert reserve processors for
    the run (D 3.5 (4)-(6)). In the same transaction, after the capacity lock: the Group's
    active run is checked again (the "already running" refusal stays first and distinct), the
    host's active and protected reservations are summed (:func:`host_reservations`), R is
    decided for ``cpu_share_requested`` and stored with the row as ``cpu_reservation``. When
    nothing is free, :class:`SelfCheckHostCapacityError` and nothing is written. Without
    ``host_capacity`` the row has no reservation (callers other than the admission path).
    """
    if tr_doc_id is None and not (owner_token_id and draft_doc_ref):
        raise ValueError("a draft self-check run needs owner_token_id and draft_doc_ref")
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
            reservation = None
            # One short SQL fence for scope closure and admission, before G-run insert.
            if host_capacity is not None or progress_owner is not None:
                _lock_host_capacity(store)
            if progress_owner is not None:
                _require_progress_owner(store, progress_owner, project_id, group_id)
            if host_capacity is not None:
                limit, serial_reserve = host_capacity
                desired = min(max(1, int(cpu_share_requested or 1)), limit)
                existing = get_active(project_id, group_id)
                if existing:
                    raise SelfCheckAlreadyRunningError(
                        f"A self-check run is already active for {project_id}/{group_id}",
                        existing_run_id=existing["self_check_run_id"],
                    )
                total, protected = host_reservations(host_node_key, limit)
                reservation = decide_reservation(limit, serial_reserve, total, desired)
                if reservation is None:
                    raise SelfCheckHostCapacityError(limit, total, protected, 1, desired)
            draft = tr_doc_id is None
            lock_first = store.dialect != _dialect.SQLITE
            if draft and lock_first:
                _require_open_owner(store, owner_token_id)
            store._execute(
            "INSERT INTO tr_self_check_runs ("
            " self_check_run_id, project_id, group_id, tr_doc_id, owner_token_id, draft_doc_ref, requested_by,"
            " policy_version, program, args_json, resolved_executable_path,"
            " resolved_executable_name, executable_origin, cwd_relative, timeout_seconds,"
            " env_keys_json, status, active_key, exit_code, timed_out, cancel_requested,"
            " source_changed_during_run, worktree_state_changed, recovery_state,"
            " cleanup_pending, created_at, updated_at, owner_instance_id, owner_marker"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, 0, 0, 0, 0, 'none', 0, ?, ?, ?, ?)",
            [
                rid, project_id, group_id, tr_doc_id, owner_token_id, draft_doc_ref, requested_by,
                policy_version, program, args_json, resolved_executable_path,
                resolved_executable_name, executable_origin, cwd_relative, timeout_seconds,
                env_keys_json, active_key, now, now, owner_instance_id, owner_marker,
            ],
        )
            if progress_owner is not None:
                keys = ("scope_id", "ai_run_id", "hop_id", "provider_attempt_id",
                        "owner_token_id", "project_id", "group_id")
                if progress_owner["project_id"] != project_id or progress_owner["group_id"] != group_id:
                    raise ValueError("self-check progress owner leaves project/group")
                store._execute(
                    "INSERT INTO tr_self_check_run_progress (self_check_run_id, "
                    + ", ".join(keys) + ", progress_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [rid, *(progress_owner[k] for k in keys), _progress_stamp()],
                )
            if draft and not lock_first:
                _require_open_owner(store, owner_token_id)
            if source_lock_holder is not None:
                store._execute(
                    "UPDATE tr_self_check_runs SET source_lock_holder = ? WHERE self_check_run_id = ?",
                    [source_lock_holder, rid],
                )
            if attempt_id is not None:
                store._execute(
                    "UPDATE tr_self_check_runs SET attempt_id = ?, input_commit = ? WHERE self_check_run_id = ?",
                    [attempt_id, input_commit, rid],
                )
            if host_node_key is not None:
                store._execute(
                    "UPDATE tr_self_check_runs SET host_node_key = ? WHERE self_check_run_id = ?",
                    [host_node_key, rid],
                )
            if reservation is not None:
                store._execute(
                    "UPDATE tr_self_check_runs SET cpu_reservation = ?, cpu_share_requested = ?"
                    " WHERE self_check_run_id = ?",
                    [reservation, cpu_share_requested, rid],
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


def get_run_for_owner(run_id: str, owner_token_id: str) -> dict | None:
    """Lookup a draft run ensuring it belongs to the given TR(new) token and is still unlinked."""
    row = get_store()._fetch_one(
        "SELECT * FROM tr_self_check_runs WHERE self_check_run_id = ? AND owner_token_id = ?"
        " AND tr_doc_id IS NULL",
        [run_id, owner_token_id],
    )
    return _normalize_row(row)


def list_by_owner(owner_token_id: str, limit: int = 20) -> list[dict]:
    """List a TR(new) token's unlinked draft runs, created_at DESC, self_check_run_id DESC."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE owner_token_id = ? AND tr_doc_id IS NULL"
        " ORDER BY created_at DESC, self_check_run_id DESC LIMIT ?",
        [owner_token_id, limit],
    )
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]


def list_drafts_by_group(project_id: str, group_id: str, limit: int = 20) -> list[dict]:
    """List a group's unlinked draft runs, created_at DESC, self_check_run_id DESC."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE project_id = ? AND group_id = ? AND tr_doc_id IS NULL"
        " ORDER BY created_at DESC, self_check_run_id DESC LIMIT ?",
        [project_id, group_id, limit],
    )
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]


def link_owner_runs(owner_token_id: str, tr_doc_id: str, project_id: str, group_id: str) -> int:
    """Attach a TR(new) token's draft runs to the TR it registered (0638 T#1).

    Only rows of the same project/group that are still unlinked move; a linked row is never
    re-pointed. Active rows move too: their worker thread addresses them by run id only.
    """
    now = now_iso()
    return get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET tr_doc_id = ?, linked_at = ?, updated_at = ?"
        " WHERE owner_token_id = ? AND tr_doc_id IS NULL AND project_id = ? AND group_id = ?",
        [tr_doc_id, now, now, owner_token_id, project_id, group_id],
    )


def get_active(project_id: str, group_id: str) -> dict | None:
    """Return currently active run for (project_id, group_id) if any."""
    key = compute_active_key(project_id, group_id)
    row = get_store()._fetch_one(
        "SELECT * FROM tr_self_check_runs WHERE active_key = ? AND status IN ('pending', 'running')",
        [key],
    )
    return _normalize_row(row)


def _claim_fence(claim: str | None) -> tuple[str, list[Any]]:
    """0053 1-B rev2: the WHERE fragment that fences a queue worker's write to its own claim.

    ``claim`` is the token ``claim_next_queued`` stored in ``queue_lease_owner`` for one claim of
    one worker. A lease that lapsed before any spawn hands the run back to the queue
    (``release_expired_claims`` clears the token) and a later claim stores a new one, so a
    worker that resumes after losing its claim matches nothing and writes nothing. None = no
    fence (a pre-queue run, recovery, cancel, the reaper)."""
    if claim is None:
        return "", []
    return " AND queue_lease_owner = ?", [claim]


def claim_held(run_id: str, claim: str) -> bool:
    """Whether ``claim`` is still the run's current queue claim (``queue_lease_owner``)."""
    row = get_store()._fetch_one(
        "SELECT self_check_run_id FROM tr_self_check_runs WHERE self_check_run_id = ?"
        " AND queue_lease_owner = ? AND status IN ('pending', 'running')", [run_id, claim])
    return row is not None


def mark_running(
    run_id: str,
    *,
    claim: str | None = None,
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
    """Transition run from pending to running and persist process ownership metadata.
    ``claim``: a queue worker's claim fence (``_claim_fence``)."""
    store = get_store()
    now = now_iso()
    _validate_hash64(source_refs_hash_before, "source_refs_hash_before")
    _validate_hash64(source_index_hash_before, "source_index_hash_before")
    _validate_hash64(source_status_hash_before, "source_status_hash_before")
    fence, fence_params = _claim_fence(claim)
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
        " WHERE self_check_run_id = ? AND status = 'pending' AND recovery_state = 'none'" + fence,
        [
            now, now, source_lock_holder, process_owner_kind,
            target_pid, target_start_identity, supervisor_pid, supervisor_start_identity,
            source_head_before, source_branch_before, source_refs_hash_before,
            source_index_hash_before, source_status_hash_before, index_lock_before,
            run_id, *fence_params,
        ],
    )
    if affected == 0:
        return None
    return get_run(run_id)


class SelfCheckQuotaExhausted(Exception):
    code = "selfcheck_quota_exhausted"


def verify_used(stage_key: str, store=None) -> int:
    row = (store or get_store())._fetch_one(
        "SELECT COALESCE(SUM(verify_started_count), 0) AS used FROM tr_self_check_runs WHERE verify_stage_key = ?",
        [stage_key])
    return int(row["used"])


def mark_spawn_started(run_id: str, process_owner_kind: str, claim: str | None = None) -> bool:
    """0080 T#3: the stamp a spawn writes before its first Popen -- ``process_owner_kind``
    on a pending row with no process recorded yet. From then on a missing target record
    means "a process may exist that no record names" (``tr_self_check_proof.tree_state``),
    not "nothing was spawned". CAS: pending, recovery_state none, no spawn stamped yet;
    False = do not spawn.

    0053 1-B rev2: a queue worker passes its ``claim``; the stamp is written only while that
    claim is the run's current one. The same row is what ``release_expired_claims`` needs
    unstamped, so the stamp and a lease hand-over exclude each other: either this worker
    stamps (and the run is never handed over) or the run was handed over (and this worker
    never spawns)."""
    fence, fence_params = _claim_fence(claim)
    store = get_store()
    with store.transaction():
        _lock_host_capacity(store)
        row = store._fetch_one("SELECT * FROM tr_self_check_runs WHERE self_check_run_id = ?", [run_id])
        if (not row or row["status"] != "pending" or row["cancel_requested"]
                or row["process_owner_kind"] is not None
                or (claim is not None and row.get("queue_lease_owner") != claim)):
            return False
        if row.get("verify_stage_key") and verify_used(row["verify_stage_key"], store) >= row["verify_max_runs"]:
            raise SelfCheckQuotaExhausted()
        return store._execute_affected(
            "UPDATE tr_self_check_runs SET process_owner_kind = ?,"
            " verify_started_count = verify_started_count + CASE WHEN verify_stage_key IS NULL THEN 0 ELSE 1 END"
            " WHERE self_check_run_id = ? AND status = 'pending' AND recovery_state = 'none' AND cancel_requested = 0"
            " AND process_owner_kind IS NULL AND target_pid IS NULL AND supervisor_pid IS NULL" + fence,
            [process_owner_kind, run_id, *fence_params],
        ) > 0


def record_spawn_ownership(
    run_id: str,
    *,
    process_owner_kind: str | None = None,
    target_pid: int | None = None,
    target_start_identity: str | None = None,
    supervisor_pid: int | None = None,
    supervisor_start_identity: str | None = None,
    claim: str | None = None,
) -> bool:
    """0080 T#3: what a spawn learnt about the run's processes, stored as soon as it is
    known (right after each Popen, and again when a failed spawn is held) so a later
    reaper proves the tree from it. Only adds: a recorded value is never cleared.
    ``claim``: a queue worker's claim fence (``_claim_fence``)."""
    fence, fence_params = _claim_fence(claim)
    return get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET"
        " process_owner_kind = COALESCE(?, process_owner_kind),"
        " target_pid = COALESCE(?, target_pid),"
        " target_start_identity = COALESCE(?, target_start_identity),"
        " supervisor_pid = COALESCE(?, supervisor_pid),"
        " supervisor_start_identity = COALESCE(?, supervisor_start_identity)"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running')" + fence,
        [process_owner_kind, target_pid, target_start_identity, supervisor_pid,
         supervisor_start_identity, run_id, *fence_params],
    ) > 0


def clear_spawn_started(run_id: str, claim: str | None = None) -> bool:
    """Undo ``mark_spawn_started`` when the spawn knows it created no process (Popen
    raised). Only while no process is recorded. ``claim``: a queue worker's claim fence -- a
    worker that lost its claim never clears the stamp of the worker that took the run over."""
    fence, fence_params = _claim_fence(claim)
    return get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET process_owner_kind = NULL,"
        " verify_started_count = CASE WHEN verify_started_count > 0 THEN verify_started_count - 1 ELSE 0 END"
        " WHERE self_check_run_id = ? AND status = 'pending' AND process_owner_kind IS NOT NULL"
        " AND target_pid IS NULL AND supervisor_pid IS NULL" + fence,
        [run_id, *fence_params],
    ) > 0


def request_cancel(run_id: str) -> bool:
    """CAS 0 -> 1 for cancel_requested on an active or queued run."""
    store = get_store()
    now = now_iso()
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET cancel_requested = 1, updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('queued', 'pending', 'running') AND cancel_requested = 0",
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
    claim: str | None = None,
) -> dict | None:
    """Atomic terminal transition to completed, clearing active_key. ``claim``: a queue
    worker's claim fence (``_claim_fence``)."""
    store = get_store()
    now = now_iso()
    _validate_hash64(source_refs_hash_after, "source_refs_hash_after")
    _validate_hash64(source_index_hash_after, "source_index_hash_after")
    _validate_hash64(source_status_hash_after, "source_status_hash_after")
    fence, fence_params = _claim_fence(claim)
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
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'" + fence,
        [
            exit_code, 1 if timed_out else 0,
            _truncate_tail(stdout_tail), _truncate_tail(stderr_tail),
            source_head_after, source_branch_after, source_refs_hash_after,
            source_index_hash_after, source_status_hash_after, index_lock_after,
            1 if source_changed_during_run else 0, 1 if worktree_state_changed else 0,
            1 if cleanup_pending else 0, _truncate_diagnostic(cleanup_error),
            now, now, run_id, *fence_params,
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
    claim: str | None = None,
) -> dict | None:
    """Atomic terminal transition to failed, clearing active_key. ``claim``: a queue worker's
    claim fence (``_claim_fence``)."""
    store = get_store()
    now = now_iso()
    _validate_hash64(source_refs_hash_after, "source_refs_hash_after")
    _validate_hash64(source_index_hash_after, "source_index_hash_after")
    _validate_hash64(source_status_hash_after, "source_status_hash_after")
    fence, fence_params = _claim_fence(claim)
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
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'" + fence,
        [
            error_code, exit_code, 1 if timed_out else 0,
            _truncate_tail(stdout_tail), _truncate_tail(stderr_tail),
            source_head_after, source_branch_after, source_refs_hash_after,
            source_index_hash_after, source_status_hash_after, index_lock_after,
            1 if source_changed_during_run else 0, 1 if worktree_state_changed else 0,
            1 if cleanup_pending else 0, _truncate_diagnostic(cleanup_error),
            now, now, run_id, *fence_params,
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
    claim: str | None = None,
) -> dict | None:
    """Atomic terminal transition to cancelled with the post-run source identity. ``claim``: a
    queue worker's claim fence (``_claim_fence``)."""
    store = get_store()
    now = now_iso()
    _validate_hash64(source_refs_hash_after, "source_refs_hash_after")
    _validate_hash64(source_index_hash_after, "source_index_hash_after")
    _validate_hash64(source_status_hash_after, "source_status_hash_after")
    fence, fence_params = _claim_fence(claim)
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
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'" + fence,
        [
            exit_code, _truncate_tail(stdout_tail), _truncate_tail(stderr_tail),
            source_head_after, source_branch_after, source_refs_hash_after,
            source_index_hash_after, source_status_hash_after, index_lock_after,
            1 if source_changed_during_run else 0, 1 if worktree_state_changed else 0,
            1 if cleanup_pending else 0, _truncate_diagnostic(cleanup_error),
            now, now, run_id, *fence_params,
        ],
    )
    if affected == 0:
        return None
    return get_run(run_id)

def record_result_summary(run_id: str, result_summary: str | None, environment_evidence: str | None) -> dict | None:
    """0114 T#3 (D 3.2): the result block summary of a run that already completed normally.

    Written after, and apart from, the terminal write: only a ``completed`` row that did not
    time out and has no summary yet moves, and no status, exit code or source field is
    touched. None = nothing written."""
    affected = get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET result_summary = ?, environment_evidence = ?"
        " WHERE self_check_run_id = ? AND status = 'completed' AND timed_out = 0"
        " AND recovery_state = 'none' AND result_summary IS NULL",
        [result_summary, environment_evidence, run_id],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def mark_recovering(run_id: str, claim: str | None = None) -> dict | None:
    """CAS none -> recovering on orphan active run. ``claim``: a queue worker's claim fence
    (``_claim_fence``) -- a worker that lost its claim never holds the new claimant's run."""
    store = get_store()
    now = now_iso()
    fence, fence_params = _claim_fence(claim)
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET recovery_state = 'recovering', updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'" + fence,
        [now, run_id, *fence_params],
    )
    if affected == 0:
        return None
    return get_run(run_id)


def claim_recovering(run_id: str) -> str | None:
    """The CAS of ``mark_recovering`` without its read back (0080 T#2 reclaim gate).

    Returns the ``updated_at`` the CAS wrote -- the caller's handle for
    ``abandon_recovering`` -- or None when the row was not an active ``none`` row. Nothing
    runs between the commit and the return, so a caller that got no stamp back never
    holds a fence it does not know of unless the store itself failed after committing."""
    now = now_iso()
    affected = get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET recovery_state = 'recovering', updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'none'",
        [now, run_id],
    )
    return now if affected else None


def abandon_recovering(run_id: str, claimed_at: str) -> int:
    """Undo a ``claim_recovering`` whose caller failed before writing any outcome:
    recovering -> none, only while the row is still active, still ``recovering`` and not
    written since the claim (``updated_at`` as the claim left it). 0 = not undone."""
    return get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET recovery_state = 'none', updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running')"
        " AND recovery_state = 'recovering' AND updated_at = ?",
        [now_iso(), run_id, claimed_at],
    )


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


def finish_recovered_cancelled(run_id: str) -> dict | None:
    """A cancel of a run no worker of this process drives, after the release proof
    (0080 T#2, NR0007 §5 item 3-a): cancelled, recovered, active_key cleared.

    Same CAS as ``finish_recovered_interrupted``: only a row this caller took into
    ``recovering`` moves."""
    store = get_store()
    now = now_iso()
    affected = store._execute_affected(
        "UPDATE tr_self_check_runs SET"
        " status = 'cancelled',"
        " recovery_state = 'recovered',"
        " active_key = NULL,"
        " finished_at = ?,"
        " updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running') AND recovery_state = 'recovering'",
        [now, now, run_id],
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
    """Active recovery_incomplete run of this Group (0666 L 2.24.3; idx on project_id, group_id).

    0053 1-B: only a run admitted before the queue (``enqueued_at IS NULL``) ran in the Group's
    worktree under its G, so only such a run keeps the Group closed. A queue run executes in its
    own snapshot and holds no G: an unproved one keeps its execution slot (its reservation stays
    in :func:`host_reservations`), never the Group."""
    row = get_store()._fetch_one(
        "SELECT 1 FROM tr_self_check_runs WHERE project_id = ? AND group_id = ? AND recovery_state = 'incomplete' AND status IN ('pending', 'running') AND enqueued_at IS NULL LIMIT 1",
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


# 0146: immutable parent scope is written in create_pending's transaction. Reads do
# not join tokens/documents, so consuming a token or linking a draft cannot replay it.
_PROGRESS_RANK = {"queued": 0, "pending": 0, "running": 1, "completed": 2, "failed": 2, "cancelled": 2}


def record_effective_progress(run_id: str, *, status: str | None = None,
                              output_bytes: int | None = None) -> None:
    store = get_store()
    for _ in range(3):
        row = store._fetch_one("SELECT * FROM tr_self_check_run_progress WHERE self_check_run_id = ?", [run_id])
        if not row or row["state_rank"] >= 2:
            return
        rank = _PROGRESS_RANK.get(status, -1)
        advanced = rank > row["state_rank"]
        count = max(int(output_bytes or 0), int(row["output_bytes"]))
        if not advanced and count == row["output_bytes"]:
            return
        changed = store._execute_affected(
            "UPDATE tr_self_check_run_progress SET progress_seq = progress_seq + 1, "
            "output_bytes = ?, state_rank = ?, status = ?, event_kind = ?, progress_at = ? "
            "WHERE self_check_run_id = ? AND progress_seq = ?",
            [count, rank if advanced else row["state_rank"], status if advanced else row["status"],
             "state:" + status if advanced else "output", _progress_stamp(), run_id, row["progress_seq"]],
        )
        if changed:
            return


def observe_effective_progress(scope_id: str) -> dict:
    store = get_store()
    total = store._fetch_one(
        "SELECT COALESCE(SUM(progress_seq), 0) AS seq FROM tr_self_check_run_progress WHERE scope_id = ?", [scope_id])
    last = store._fetch_one(
        "SELECT self_check_run_id, status, progress_seq, output_bytes, event_kind, progress_at "
        "FROM tr_self_check_run_progress WHERE scope_id = ? ORDER BY progress_at DESC, self_check_run_id DESC LIMIT 1",
        [scope_id])
    return {"seq": int(total["seq"]), "last": last}


def _progress_stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# 0146 T#3. All scope writes share the capacity row with create_pending.
# No registry lock is held for this transaction, and no process operation is done here.
_OWNER_KEYS = ("scope_id", "ai_run_id", "hop_id", "provider_attempt_id",
               "owner_token_id", "project_id", "group_id")


class ProgressOwnerClosedError(Exception):
    """A managed provider scope ended; it must never become an independent run."""


def _ensure_owner_scope(store, owner: dict) -> dict:
    row = store._fetch_one("SELECT * FROM tr_self_check_owner_scopes WHERE scope_id = ?",
                           [owner["scope_id"]])
    if row is None:
        store._execute(
            "INSERT INTO tr_self_check_owner_scopes (" + ", ".join(_OWNER_KEYS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?)", [owner[k] for k in _OWNER_KEYS])
        row = dict(owner)
    if any(row[k] != owner[k] for k in _OWNER_KEYS):
        raise ValueError("self-check scope ownership mismatch")
    return row


def _require_progress_owner(store, owner: dict, project_id: str, group_id: str) -> None:
    if owner["project_id"] != project_id or owner["group_id"] != group_id:
        raise ValueError("self-check progress owner leaves project/group")
    if _ensure_owner_scope(store, owner).get("closed_at"):
        raise ProgressOwnerClosedError("self-check owner scope is closed")


def managed_owner_token(token_id: str, project_id: str, group_id: str) -> bool:
    return get_store()._fetch_one(
        "SELECT scope_id FROM tr_self_check_owner_scopes"
        " WHERE owner_token_id = ? AND project_id = ? AND group_id = ? LIMIT 1",
        [token_id, project_id, group_id]) is not None


def close_owner_scopes(scopes: list[dict], *, boundary: str, reason: str,
                       cancel: bool) -> list[str]:
    if not scopes:
        return []
    owner = scopes[-1]
    if boundary not in ("scope", "attempt", "hop", "run"):
        raise ValueError("unknown owner boundary")
    keys = ["project_id", "group_id", "ai_run_id"]
    if boundary == "hop":
        keys.append("hop_id")
    if boundary == "attempt":
        keys.append("provider_attempt_id")
    if boundary == "scope":
        keys.append("scope_id")
    where = " AND ".join(k + " = ?" for k in keys)
    store = get_store()
    with store.transaction():
        _lock_host_capacity(store)
        # Include scopes with no children: a copied owner cannot insert after this commit.
        for scope in scopes:
            _ensure_owner_scope(store, scope)
        store._execute(
            "UPDATE tr_self_check_owner_scopes SET closed_at = COALESCE(closed_at, ?),"
            " close_reason = CASE WHEN cancel_requested = 1 THEN close_reason ELSE ? END,"
            " cancel_requested = CASE WHEN cancel_requested = 1 THEN 1 ELSE ? END"
            " WHERE " + where, [now_iso(), reason, int(cancel), *(owner[k] for k in keys)])
        return [r["scope_id"] for r in store._fetch_all(
            "SELECT scope_id FROM tr_self_check_owner_scopes WHERE " + where,
            [owner[k] for k in keys])]


def owned_cancel_runs(scope_ids: list[str] | None = None) -> list[dict]:
    sql = ("SELECT r.*, p.scope_id AS parent_scope_id, s.ai_run_id, s.hop_id,"
           " s.provider_attempt_id, s.close_reason"
           " FROM tr_self_check_runs r"
           " JOIN tr_self_check_run_progress p ON p.self_check_run_id = r.self_check_run_id"
           " JOIN tr_self_check_owner_scopes s ON s.scope_id = p.scope_id"
           " AND s.ai_run_id = p.ai_run_id AND s.hop_id = p.hop_id"
           " AND s.provider_attempt_id = p.provider_attempt_id"
           " AND s.owner_token_id = p.owner_token_id"
           " AND s.project_id = r.project_id AND s.group_id = r.group_id"
           " WHERE s.cancel_requested = 1 AND r.status IN ('queued', 'pending', 'running')")
    params = []
    if scope_ids is not None:
        if not scope_ids:
            return []
        sql += " AND s.scope_id IN (" + ", ".join("?" for _ in scope_ids) + ")"
        params.extend(scope_ids)
    return [_normalize_row(row) for row in get_store()._fetch_all(sql, params)]


def owned_cancel_run(run_id: str, scope_id: str) -> dict | None:
    return next((r for r in owned_cancel_runs([scope_id])
                 if r["self_check_run_id"] == run_id), None)


def register_owner_scope(owner: dict) -> None:
    store = get_store()
    with store.transaction():
        _lock_host_capacity(store)
        _require_progress_owner(store, owner, owner["project_id"], owner["group_id"])


# ── 0053 1-B: the Self-check queue (migration 201) ──────────────────────────────
#
# A queue run is inserted as ``queued`` (no active_key, no reservation, no G). A queue worker
# claims it -- ``queued -> pending`` with a lease and its processor reservation, decided under
# the capacity lock like an admission -- and from then on the run follows the ordinary
# pending -> running -> terminal path, recovery and reaper included. Order: oldest request
# first (``queue_order``, microseconds, set once), projects alternating (the project whose last
# claim, ``queue_claimed_at``, is the oldest goes next). ``enqueued_at`` is the wait clock (the
# store's ``now_iso`` format); a system re-queue restarts it and keeps the order.

QUEUED = "queued"
QUEUE_TERMINAL = ("completed", "failed", "cancelled")


def create_queued(
    project_id: str,
    group_id: str,
    tr_doc_id: str | None,
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
    *,
    run_id: str,
    snapshot_path: str,
    queue_key: str,
    owner_token_id: str | None = None,
    draft_doc_ref: str | None = None,
    attempt_id: str | None = None,
    input_commit: str | None = None,
    cpu_share_requested: int | None = None,
    owner_marker: str | None = None,
    source_before: dict | None = None,
    progress_owner: dict | None = None,
    supersede_code: str | None = None,
    verify_limits: dict | None = None,
) -> dict:
    """Insert a ``queued`` run (0053 1-B). No active_key: a Group may queue any number of runs.

    The draft-owner rule (``_require_open_owner``) and the managed provider scope fence
    (``_require_progress_owner`` under the capacity lock) are the same as ``create_pending``'s,
    in the insert's transaction. ``source_before`` is the worktree identity the snapshot was
    taken from (``tr_self_check_service._probe``); it is stored as the run's *_before fields.

    rev2 -- the same card queuing again: with ``supersede_code`` the card's (``queue_key``)
    waiting requests OLDER than this one are cancelled with that code in the insert's own
    transaction. Every enqueue takes the capacity lock first, so enqueues of one card are
    serialized: the new row's ``queue_order`` is set under the lock above every order the card
    already has, and only rows below it are cancelled. Two requests of one card racing each
    other therefore end with exactly the later-committed one queued -- never both cancelled.
    The cancelled rows are returned in the result's ``superseded_runs`` (for snapshot removal
    and events; not a stored column)."""
    if tr_doc_id is None and not (owner_token_id and draft_doc_ref):
        raise ValueError("a draft self-check run needs owner_token_id and draft_doc_ref")
    before = source_before or {}
    for key in ("refs_hash", "index_hash", "status_hash"):
        _validate_hash64(before.get(key), "source_%s_before" % key)
    store = get_store()
    now = now_iso()
    args_json = json.dumps(list(args), ensure_ascii=False)
    env_keys_json = json.dumps(sorted(set(env_keys)), ensure_ascii=False)
    superseded: list[dict] = []
    with store.transaction():
        # rev2: every enqueue serializes on the capacity row (the claim takes it too), so the
        # card's order below and its supersede are decided against committed rows only.
        _lock_host_capacity(store)
        if progress_owner is not None:
            _require_progress_owner(store, progress_owner, project_id, group_id)
        if verify_limits is not None and verify_used(verify_limits["stage_key"], store) >= verify_limits["max_runs"]:
            raise SelfCheckQuotaExhausted()
        order = _card_next_order(store, queue_key)
        draft = tr_doc_id is None
        lock_first = store.dialect != _dialect.SQLITE
        if draft and lock_first:
            _require_open_owner(store, owner_token_id)
        store._execute(
            "INSERT INTO tr_self_check_runs ("
            " self_check_run_id, project_id, group_id, tr_doc_id, owner_token_id, draft_doc_ref, requested_by,"
            " policy_version, program, args_json, resolved_executable_path,"
            " resolved_executable_name, executable_origin, cwd_relative, timeout_seconds,"
            " env_keys_json, status, active_key, exit_code, timed_out, cancel_requested,"
            " source_changed_during_run, worktree_state_changed, recovery_state,"
            " cleanup_pending, created_at, updated_at, owner_marker, attempt_id, input_commit,"
            " cpu_share_requested, enqueued_at, snapshot_path, queue_key, queue_requeue_count, queue_order,"
            " source_head_before, source_branch_before, source_refs_hash_before,"
            " source_index_hash_before, source_status_hash_before, index_lock_before"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, 0, 0, 0, 0,"
            " 'none', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id, project_id, group_id, tr_doc_id, owner_token_id, draft_doc_ref, requested_by,
                policy_version, program, args_json, resolved_executable_path,
                resolved_executable_name, executable_origin, cwd_relative, timeout_seconds,
                env_keys_json, now, now, owner_marker, attempt_id, input_commit,
                cpu_share_requested, now, snapshot_path, queue_key, order,
                before.get("head"), before.get("branch"), before.get("refs_hash"),
                before.get("index_hash"), before.get("status_hash"),
                None if before.get("index_lock") is None else int(bool(before.get("index_lock"))),
            ],
        )
        if progress_owner is not None:
            keys = ("scope_id", "ai_run_id", "hop_id", "provider_attempt_id",
                    "owner_token_id", "project_id", "group_id")
            store._execute(
                "INSERT INTO tr_self_check_run_progress (self_check_run_id, "
                + ", ".join(keys) + ", progress_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, *(progress_owner[k] for k in keys), _progress_stamp()],
            )
        if draft and not lock_first:
            _require_open_owner(store, owner_token_id)
        if verify_limits is not None:
            store._execute(
                "UPDATE tr_self_check_runs SET verify_stage_key = ?, verify_max_runs = ?, verify_timeout_sec = ? WHERE self_check_run_id = ?",
                [verify_limits["stage_key"], verify_limits["max_runs"], verify_limits["run_timeout_sec"], run_id])
        if supersede_code is not None:
            older = store._fetch_all(
                "SELECT self_check_run_id FROM tr_self_check_runs WHERE queue_key = ?"
                " AND status = 'queued' AND self_check_run_id <> ? AND queue_order < ?",
                [queue_key, run_id, order]) or []
            for item in older:
                if store._execute_affected(
                        "UPDATE tr_self_check_runs SET status = 'cancelled', error_code = ?,"
                        " cancel_requested = 1, finished_at = ?, updated_at = ?"
                        " WHERE self_check_run_id = ? AND status = 'queued' AND queue_key = ?"
                        " AND queue_order < ?",
                        [supersede_code, now, now, item["self_check_run_id"], queue_key, order]):
                    superseded.append(item["self_check_run_id"])
    row = get_run(run_id)
    if row is not None:
        row["superseded_runs"] = [r for r in (get_run(rid) for rid in superseded) if r is not None]
    return row  # type: ignore[return-value]


def _card_next_order(store, queue_key: str) -> str:
    """The ``queue_order`` of a card's new request, under the capacity lock: now (UTC,
    microseconds), raised above the card's highest order when the clock did not move past it,
    so a card's requests are strictly ordered by the order they were stored in."""
    from datetime import datetime, timedelta
    order = _progress_stamp()
    top = store._fetch_one(
        "SELECT MAX(queue_order) AS top FROM tr_self_check_runs WHERE queue_key = ?", [queue_key])
    highest = (top or {}).get("top")
    if highest and highest >= order:
        order = (datetime.fromisoformat(highest) + timedelta(microseconds=1)).isoformat(timespec="microseconds")
    return order


def _end_queued(run_id: str, status: str, error_code: str | None, cancel: bool,
                detail: str | None = None) -> dict | None:
    now = now_iso()
    affected = get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET status = ?, error_code = ?,"
        " cancel_requested = CASE WHEN ? = 1 THEN 1 ELSE cancel_requested END,"
        " stderr_tail = COALESCE(?, stderr_tail), finished_at = ?, updated_at = ?"
        " WHERE self_check_run_id = ? AND status = 'queued'",
        [status, error_code, 1 if cancel else 0, _truncate_diagnostic(detail), now, now, run_id],
    )
    return get_run(run_id) if affected else None


def cancel_queued(run_id: str, error_code: str | None = None) -> dict | None:
    """CAS queued -> cancelled (a waiting request that never started). None = not queued."""
    return _end_queued(run_id, "cancelled", error_code, True)


def fail_queued(run_id: str, error_code: str, detail: str | None = None) -> dict | None:
    """CAS queued -> failed with ``error_code`` (wait limit, snapshot failure); ``detail`` (the
    reason) goes to ``stderr_tail``. None = not queued."""
    return _end_queued(run_id, "failed", error_code, False, detail)


def queued_of_card(queue_key: str, *, exclude_run_id: str | None = None) -> list[dict]:
    """The card's queued (not started) runs, oldest first."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE queue_key = ? AND status = 'queued'"
        " ORDER BY queue_order ASC, self_check_run_id ASC", [queue_key])
    return [_normalize_row(r) for r in rows if r and r["self_check_run_id"] != exclude_run_id]


def card_runs(queue_key: str, limit: int = 20) -> list[dict]:
    """The card's queue runs, newest first."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE queue_key = ?"
        " ORDER BY queue_order DESC, self_check_run_id DESC LIMIT ?", [queue_key, limit])
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]


def list_queued(limit: int = 500) -> list[dict]:
    """Every queued run in queue order (oldest request first)."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE status = 'queued'"
        " ORDER BY queue_order ASC, self_check_run_id ASC LIMIT ?", [limit])
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]


def queue_position(run_id: str) -> int | None:
    """1-based place of a queued run in the whole queue's order; None when it is not queued."""
    row = get_run(run_id)
    if not row or row["status"] != QUEUED:
        return None
    ahead = get_store()._fetch_one(
        "SELECT COUNT(*) AS n FROM tr_self_check_runs WHERE status = 'queued'"
        " AND (queue_order < ? OR (queue_order = ? AND self_check_run_id < ?))",
        [row["queue_order"], row["queue_order"], run_id])
    return int(ahead["n"]) + 1


def _next_queued(store) -> dict | None:
    """The queue's next run: projects alternate (the project whose last queue claim is the
    oldest, or that never had one, goes first; ties by its oldest request), oldest request
    first inside a project. Cancel-requested rows are skipped (their cancel ends them).

    rev2: every project with a waiting request is a candidate however long the queue is --
    each project's oldest request is taken over the WHOLE queue (one row per project) before
    the alternation picks a project; nothing cuts the queue to its first N rows."""
    firsts = store._fetch_all(
        "SELECT project_id, MIN(queue_order) AS first_order FROM tr_self_check_runs"
        " WHERE status = 'queued' AND cancel_requested = 0 GROUP BY project_id") or []
    if not firsts:
        return None
    first = {r["project_id"]: r["first_order"] or "" for r in firsts}
    projects = sorted(first)
    last: dict[str, str] = {}
    for at in range(0, len(projects), 500):
        chunk = projects[at:at + 500]
        placeholders = ", ".join("?" for _ in chunk)
        for r in store._fetch_all(
                "SELECT project_id, MAX(queue_claimed_at) AS last_claim FROM tr_self_check_runs"
                f" WHERE queue_claimed_at IS NOT NULL AND project_id IN ({placeholders})"
                " GROUP BY project_id", chunk) or []:
            last[r["project_id"]] = r["last_claim"]
    chosen = min(projects, key=lambda p: (last.get(p) is not None, last.get(p) or "", first[p], p))
    return store._fetch_one(
        "SELECT * FROM tr_self_check_runs WHERE status = 'queued' AND cancel_requested = 0"
        " AND project_id = ? ORDER BY queue_order ASC, self_check_run_id ASC LIMIT 1", [chosen])


def claim_next_queued(instance_id: str, lease_until: str, host_node_key: str | None,
                      host_capacity: tuple[int, int]) -> dict | None:
    """Claim the queue's next run for a worker of ``instance_id``: ``queued -> pending`` with the
    lease, the owning instance, the host and the processor reservation (decided exactly as an
    admission does, under the capacity lock, from the host's active and protected runs). None =
    nothing queued or no processor free -- the request waits; it is never refused.

    rev2: ``queue_lease_owner`` is a token new for every claim (``<instance>/<random>``), not the
    instance id, so two claims of one run -- even by workers of the same instance after a lease
    hand-over -- never match each other. The worker passes it as ``claim`` to every write it
    makes on the run (``_claim_fence``)."""
    store = get_store()
    limit, serial_reserve = host_capacity
    with store.transaction():
        _lock_host_capacity(store)
        total, _protected = host_reservations(host_node_key, limit)
        if limit - total < 1:
            return None
        row = _next_queued(store)
        if row is None:
            return None
        desired = min(max(1, int(row.get("cpu_share_requested") or 1)), limit)
        reservation = decide_reservation(limit, serial_reserve, total, desired)
        if reservation is None:
            return None
        now = now_iso()
        claim = f"{instance_id}/{uuid.uuid4().hex}"
        affected = store._execute_affected(
            "UPDATE tr_self_check_runs SET status = 'pending', queue_lease_owner = ?,"
            " queue_lease_until = ?, owner_instance_id = ?, host_node_key = ?,"
            " cpu_reservation = ?, started_at = ?, queue_claimed_at = ?, updated_at = ?"
            " WHERE self_check_run_id = ? AND status = 'queued' AND cancel_requested = 0",
            [claim, lease_until, instance_id, host_node_key, reservation, now,
             _progress_stamp(), now, row["self_check_run_id"]])
    return get_run(row["self_check_run_id"]) if affected else None


def renew_lease(run_id: str, claim: str, lease_until: str) -> bool:
    """The claiming worker keeps its lease while it drives the run. False = ``claim`` is no
    longer the run's claim (handed over, ended): the worker must stop driving the run."""
    return get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET queue_lease_until = ?"
        " WHERE self_check_run_id = ? AND queue_lease_owner = ? AND status IN ('pending', 'running')",
        [lease_until, run_id, claim]) > 0


_UNSPAWNED = (" status = 'pending' AND enqueued_at IS NOT NULL AND recovery_state = 'none'"
              " AND process_owner_kind IS NULL AND target_pid IS NULL AND supervisor_pid IS NULL")


def release_expired_claims(now: str) -> list[str]:
    """Claims whose lease lapsed before anything was spawned go back to the queue, keeping their
    order, so another worker takes them over. A run that may have a process is never touched
    here: it stays for the recovery / reaper proof (``tr_self_check_service``)."""
    store = get_store()
    rows = store._fetch_all(
        "SELECT self_check_run_id FROM tr_self_check_runs WHERE" + _UNSPAWNED
        + " AND queue_lease_until IS NOT NULL AND queue_lease_until < ?", [now]) or []
    released = []
    for row in rows:
        if store._execute_affected(
                "UPDATE tr_self_check_runs SET status = 'queued', queue_lease_owner = NULL,"
                " queue_lease_until = NULL, owner_instance_id = NULL, cpu_reservation = NULL,"
                " started_at = NULL, updated_at = ? WHERE self_check_run_id = ? AND" + _UNSPAWNED
                + " AND queue_lease_until < ?", [now_iso(), row["self_check_run_id"], now]):
            released.append(row["self_check_run_id"])
    return released


def queued_waiting_since(cutoff: str) -> list[dict]:
    """Queued runs whose wait clock (``enqueued_at``) started before ``cutoff``."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE status = 'queued' AND enqueued_at < ?"
        " ORDER BY queue_order ASC", [cutoff])
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]


def requeue_waited(run_id: str, expected_enqueued_at: str) -> dict | None:
    """The system's one re-queue of a run that waited past the limit: the wait clock restarts,
    the order (``queue_order``) is kept, the count goes up. CAS on the clock it read."""
    now = now_iso()
    affected = get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET enqueued_at = ?, queue_requeue_count = queue_requeue_count + 1,"
        " updated_at = ? WHERE self_check_run_id = ? AND status = 'queued' AND enqueued_at = ?",
        [now, now, run_id, expected_enqueued_at])
    return get_run(run_id) if affected else None


def requeue_recovered(run_id: str, owner_marker: str | None = None) -> dict | None:
    """A queue run interrupted by a restart, after the recovery proof (owner dead, tree empty)
    took it into ``recovering``: back to ``queued`` with its snapshot, order and request, the
    process records cleared and the wait clock restarted. Same CAS as
    ``finish_recovered_interrupted``; only queue runs (``enqueued_at`` set) move."""
    now = now_iso()
    affected = get_store()._execute_affected(
        "UPDATE tr_self_check_runs SET status = 'queued', recovery_state = 'none', recovery_reason = NULL,"
        " enqueued_at = ?, queue_lease_owner = NULL, queue_lease_until = NULL, owner_instance_id = NULL,"
        " cpu_reservation = NULL, started_at = NULL, process_owner_kind = NULL, target_pid = NULL,"
        " target_start_identity = NULL, supervisor_pid = NULL, supervisor_start_identity = NULL,"
        " cancel_requested = 0, owner_marker = COALESCE(?, owner_marker), updated_at = ?"
        " WHERE self_check_run_id = ? AND status IN ('pending', 'running')"
        " AND recovery_state = 'recovering' AND enqueued_at IS NOT NULL AND cancel_requested = 0",
        [now, owner_marker, now, run_id])
    return get_run(run_id) if affected else None


def group_open_runs(project_id: str, group_id: str) -> list[dict]:
    """The Group's queued, claimed and running runs (legacy G run included), oldest first."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE project_id = ? AND group_id = ?"
        " AND status IN ('queued', 'pending', 'running')"
        " ORDER BY created_at ASC, queue_order ASC, self_check_run_id ASC",
        [project_id, group_id])
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]


def snapshot_cleanup_pending(limit: int = 50) -> list[dict]:
    """Ended queue runs whose snapshot removal failed, for the next pass to retry."""
    rows = get_store()._fetch_all(
        "SELECT * FROM tr_self_check_runs WHERE cleanup_pending = 1 AND snapshot_path IS NOT NULL"
        " AND status IN ('completed', 'failed', 'cancelled') ORDER BY finished_at ASC LIMIT ?", [limit])
    return [_normalize_row(r) for r in rows if r is not None]  # type: ignore[misc]

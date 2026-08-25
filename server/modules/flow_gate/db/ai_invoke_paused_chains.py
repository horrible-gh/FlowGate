"""ai_invoke_paused_chains CRUD (group 0252 DB0010).

Single source of truth for the miniplayer pause/resume state (L0009 paused_store).
One row = one user-paused continuous chain. UNIQUE(group_id) enforces "at most one
paused row per group" and is the upsert conflict key; pending_q_doc_ids is NEVER
stored here — it is derived live from the group's open Q documents (DB0010 §4).
"""
from __future__ import annotations

import json
from typing import Optional

from . import dialect as _dialect
from .connection import get_store, now_iso


def load_json_map(value) -> Optional[dict]:
    """Read one of the JSON-text selection columns back, defensively (0365 DB0004 §2-2).

    Missing, corrupt, or non-object text degrades to None: a single damaged row must never
    block a resume — it only loses that row's per-step selections. Accepts a dict as-is so
    callers can pass either a stored column or an in-memory map.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value) or None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    return parsed


def dump_json_map(value) -> Optional[str]:
    """Serialize a selection map for storage (0365 DB0004 §2-2).

    ensure_ascii=False keeps the Korean handoff-note text readable in the column. "No
    selection" has exactly ONE representation — NULL — so an empty or unusable map
    normalizes to None (invariant I4).
    """
    parsed = load_json_map(value)
    if parsed is None:
        return None
    try:
        return json.dumps(parsed, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def load_json_list(value) -> list:
    """Read the N/T auto-approve item_seq selection back (0352 T0004 §2 / migration 078).

    Same defensive contract as load_json_map: missing/corrupt/non-list text degrades to an
    empty list ("no selection") rather than raising, so one damaged row cannot block a resume.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def dump_json_list(value) -> Optional[str]:
    """Serialize the N/T auto-approve item_seq selection for storage (0352 T0004 §2).

    An empty/None list normalizes to NULL — "no selection" has exactly one representation,
    mirroring dump_json_map's invariant I4 for the provider/note maps.
    """
    parsed = load_json_list(value)
    if not parsed:
        return None
    try:
        return json.dumps(parsed)
    except (TypeError, ValueError):
        return None


def _clean_text(value) -> Optional[str]:
    """Blank text is "no selection" too — normalize it to NULL like the maps above."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def upsert(
    *,
    group_id: str,
    doc_ref: str,
    paused_by: str,
    paused_at: str,
    continuation_target_seq: Optional[int],
    docs_target: Optional[int],
    docs_reached: int,
    chain_id: Optional[str] = None,
    chain_docs_target: Optional[int] = None,
    chain_docs_reached: int = 0,
    stop_kind: str = "user",
    stop_code: Optional[str] = None,
    stop_run_id: Optional[str] = None,
    stop_last_message_excerpt: Optional[str] = None,
    # 0365 DB0004: the provider / handoff-note selections the run was started with. Every
    # caller MUST pass them (invariant I3) — this upsert overwrites every column, so a
    # call that omits them wipes the stored selections and the resume falls back to the
    # project default chain again, which is the exact bug B0001 reported.
    continuation_base_provider_id: Optional[str] = None,
    continuation_provider_pinned: Optional[bool] = None,
    continuation_provider_overrides=None,
    continuation_default_note: Optional[str] = None,
    continuation_note_overrides=None,
    # 0352 T0004 §3.6: the N/T authoring mode (auto_approved/ai_direct) and its per-item_seq
    # auto-approve selection. Every caller (pause_run, the user/system stop-row writers, the
    # restore-on-failed-resume path) MUST pass them too — omitting them is the exact
    # pause->resume mode-loss bug this migration/TR fixes.
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs=None,
    # flowgate.default.0400 M0005: the per-hop wall-clock budget (seconds) the chain was
    # started with. Same "every caller MUST pass it" contract as the columns above — this
    # upsert overwrites every column, so a caller that omits it wipes the pick and a resumed
    # hop silently falls back to HOP_TIMEOUT_SEC.
    continuation_step_timeout_sec: Optional[int] = None,
    # flowgate.default.0443 T0002 (R0001): the "재시작 횟수" pick the chain was started
    # with. Same "every caller MUST pass it" contract as the column above.
    continuation_restart_max_attempts: Optional[int] = None,
    # 0414 DB0009 §4-1: the [검수] selections (per-step review count and reviewer) the run
    # was started with. Same invariant I3 as every column above — this upsert overwrites
    # EVERY column, so a caller that omits them wipes the stored selections and the chain
    # resumes with no review gate at all. DB0009 §5-3 calls that out as strictly worse than
    # the 0365 provider loss: losing a provider means "resumed on a pricier provider",
    # losing these means "approved with nobody reviewing it" (L0008 invariant R1).
    continuation_review_count_overrides=None,
    continuation_reviewer_overrides=None,
) -> None:
    """Record (or refresh) the paused row for a group — idempotent on repeat pause.

    chain_id/chain_docs_target/chain_docs_reached (group 0357 T0004) carry the
    CHAIN-lifetime progress across the per-hop runs an unmanned continuous chain is
    made of, so a resumed chain keeps counting from where the chain — not the last
    hop — left off.

    stop_kind/stop_code/stop_run_id/stop_last_message_excerpt (group 0359 DB0008 Q7)
    mark a chain that a *system* stop (not the user) parked here so the miniplayer
    can surface it with the same [resume] affordance. Existing call sites (user
    pauses) omit these and get stop_kind='user' with the other three NULL, matching
    how a legacy row (NULL stop_kind) is read back as 'user' (DB0008 §2.3).
    """
    now = now_iso()
    get_store()._execute(
        "INSERT INTO ai_invoke_paused_chains"
        "(group_id, doc_ref, mode, paused_by, paused_at,"
        " continuation_target_seq, docs_target, docs_reached,"
        " chain_id, chain_docs_target, chain_docs_reached,"
        " stop_kind, stop_code, stop_run_id, stop_last_message_excerpt,"
        " continuation_base_provider_id, continuation_provider_pinned,"
        " continuation_provider_overrides,"
        " continuation_default_note, continuation_note_overrides,"
        " continuation_instruction_mode, continuation_auto_approve_item_seqs,"
        " continuation_step_timeout_sec, continuation_restart_max_attempts,"
        " continuation_review_count_overrides, continuation_reviewer_overrides,"
        " created_at, updated_at) "
        "VALUES (?, ?, 'continuous', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(group_id) DO UPDATE SET "
        "doc_ref = excluded.doc_ref, "
        "paused_by = excluded.paused_by, "
        "paused_at = excluded.paused_at, "
        "continuation_target_seq = excluded.continuation_target_seq, "
        "docs_target = excluded.docs_target, "
        "docs_reached = excluded.docs_reached, "
        "chain_id = excluded.chain_id, "
        "chain_docs_target = excluded.chain_docs_target, "
        "chain_docs_reached = excluded.chain_docs_reached, "
        "stop_kind = excluded.stop_kind, "
        "stop_code = excluded.stop_code, "
        "stop_run_id = excluded.stop_run_id, "
        "stop_last_message_excerpt = excluded.stop_last_message_excerpt, "
        "continuation_base_provider_id = excluded.continuation_base_provider_id, "
        "continuation_provider_pinned = excluded.continuation_provider_pinned, "
        "continuation_provider_overrides = excluded.continuation_provider_overrides, "
        "continuation_default_note = excluded.continuation_default_note, "
        "continuation_note_overrides = excluded.continuation_note_overrides, "
        "continuation_instruction_mode = excluded.continuation_instruction_mode, "
        "continuation_auto_approve_item_seqs = excluded.continuation_auto_approve_item_seqs, "
        "continuation_step_timeout_sec = excluded.continuation_step_timeout_sec, "
        "continuation_restart_max_attempts = excluded.continuation_restart_max_attempts, "
        # DB0009 §4-1: excluded wins, deliberately NOT COALESCE(excluded.x, table.x). A new
        # run started WITHOUT review selections must clear an older row's — otherwise the
        # user turns review off and the gate keeps firing from a stale row.
        "continuation_review_count_overrides = excluded.continuation_review_count_overrides, "
        "continuation_reviewer_overrides = excluded.continuation_reviewer_overrides, "
        "updated_at = excluded.updated_at",
        [group_id, doc_ref, paused_by, paused_at,
         continuation_target_seq, docs_target, docs_reached,
         chain_id, chain_docs_target, chain_docs_reached,
         stop_kind, stop_code, stop_run_id, stop_last_message_excerpt,
         _clean_text(continuation_base_provider_id),
         1 if continuation_provider_pinned else 0,
         dump_json_map(continuation_provider_overrides),
         _clean_text(continuation_default_note),
         dump_json_map(continuation_note_overrides),
         _clean_text(continuation_instruction_mode),
         dump_json_list(continuation_auto_approve_item_seqs),
         continuation_step_timeout_sec,
         continuation_restart_max_attempts,
         # Both maps ride the existing generic (de)serializers — dump_json_map does not care
         # whether the values are ints (count map) or strings (reviewer map), so DB0009 §2-2
         # needs no new helper. Empty maps normalize to NULL (invariant I4).
         dump_json_map(continuation_review_count_overrides),
         dump_json_map(continuation_reviewer_overrides),
         now, now],
    )


def get_by_group(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM ai_invoke_paused_chains WHERE group_id = ?",
        [group_id],
    )


def exists(group_id: str) -> bool:
    return get_by_group(group_id) is not None


def delete_and_return(group_id: str) -> Optional[dict]:
    """Atomically consume the paused row (L0009 2.4 step 3).

    Returns the row when THIS call removed it, None when there was nothing to
    consume (another resume path already took it → resume_conflict). SELECT +
    DELETE run in one transaction; the group lock in ai_invoke_service is the
    first-line serializer, this is the second line of defense (DB0010 §4).
    """
    store = get_store()
    with store.transaction():
        row = store._fetch_one(
            "SELECT * FROM ai_invoke_paused_chains WHERE group_id = ?",
            [group_id],
        )
        if row is None:
            return None
        store._execute(
            "DELETE FROM ai_invoke_paused_chains WHERE group_id = ?",
            [group_id],
        )
        return row


def delete_by_group(group_id: str) -> None:
    """Chain-termination cleanup (L0009 §3 transition table): a chain that ends for
    any reason other than the user pause must not leave a ghost paused card."""
    get_store()._execute(
        "DELETE FROM ai_invoke_paused_chains WHERE group_id = ?",
        [group_id],
    )


def delete_system_stop(group_id: str, stop_run_id: Optional[str]) -> None:
    """Delete the inspected stale system row without racing a newer group pause."""
    if not stop_run_id:
        return
    get_store()._execute(
        "DELETE FROM ai_invoke_paused_chains "
        "WHERE group_id = ? AND COALESCE(stop_kind, 'user') = 'system' "
        "AND stop_run_id = ?",
        [group_id, stop_run_id],
    )


def _is_mariadb_snapshot_isolation_checkread(exc: Exception) -> bool:
    """MySQL/MariaDB server error 1020 (``ER_CHECKREAD``), and nothing else.

    Checked by raw error code, not by importing pymysql/MySQLdb: both drivers
    surface this as ``(1020, "Record has changed since last read in table '...'")``
    on ``exc.args`` because it is the wire-protocol error code the server sends, not
    a driver-specific one -- SQLite and PostgreSQL never produce this code, so the
    check is safe even when this process has no MySQL driver installed at all.
    """
    args = getattr(exc, "args", None)
    return bool(args) and args[0] == 1020


class ReleaseSuperseded:
    """Sentinel distinct from ``None`` (0459 TR0008 rev2): the group's paused row
    still EXISTS but no longer matches the caller's inspected snapshot -- a newer
    user pause or system stop won the read/delete race, either before the pre-DELETE
    compare ran or strictly between that compare and the DELETE itself. Wraps the
    CURRENT surviving row so a caller can reconcile against it.

    This must never be collapsed into the same outcome as a truly absent row: T0007
    §5 permits the idempotent ``already_released`` success ONLY when nothing exists
    for the group_id at all. A superseded row is still a live pause somebody else
    owns -- treating it as "already gone" tells the UI to delete a card for a chain
    that never left the table (the exact rev1 rejection).
    """
    __slots__ = ("row",)

    def __init__(self, row: dict) -> None:
        self.row = row


def release_owned(
    group_id: str,
    *,
    paused_by: Optional[str],
    paused_at: Optional[str],
    stop_kind: Optional[str],
    stop_run_id: Optional[str],
):
    """Compare-and-swap delete for the explicit cancel/release API (0459 T0007).

    Also reused by ``resume_chain()`` (0459 TR0008 rev1) to consume the exact row its
    own ownership check just read, instead of a group-only ``delete_and_return`` that
    would blindly take whatever row is live at consume time.

    Deletes the row only when it still matches the caller's inspected snapshot --
    ``paused_by``, ``paused_at``, ``stop_kind`` (NULL-safe via the same
    ``COALESCE(..., 'user')`` reading as :func:`delete_system_stop`), ``stop_run_id``
    (NULL-safe) -- via a WHERE predicate built from plain equality/IS NULL clauses, so
    no SQLite/MySQL/PostgreSQL-specific null-safe operator is needed. A newer user
    pause or system stop upserted between the caller's read and this call changes at
    least one of those columns; the predicate then matches nothing and the newer row
    survives untouched -- unlike a bare ``delete_by_group(group_id)``.

    Returns a TRI-STATE result (0459 TR0008 rev2), and the two failure shapes are NOT
    interchangeable:
      * the deleted row (dict) on success;
      * ``None`` when nothing exists for this group_id at all -- the only shape a
        caller may report as an idempotent "already released" success;
      * :class:`ReleaseSuperseded` wrapping the CURRENT row when a row exists but no
        longer matches the snapshot -- a real conflict the caller must surface as
        such, never as success.
    """
    store = get_store()
    with store.transaction():
        row = store._fetch_one(
            "SELECT * FROM ai_invoke_paused_chains WHERE group_id = ?",
            [group_id],
        )
        if row is None:
            return None
        normalized_stop_kind = stop_kind or "user"
        clauses = ["group_id = ?", "COALESCE(stop_kind, 'user') = ?"]
        params = [group_id, normalized_stop_kind]
        for column, value in (("paused_by", paused_by), ("paused_at", paused_at),
                              ("stop_run_id", stop_run_id)):
            if value is None:
                clauses.append(f"{column} IS NULL")
            else:
                clauses.append(f"{column} = ?")
                params.append(value)
        # This pre-DELETE compare is a fast-path short-circuit, NOT the atomicity
        # guard (0459 TR0008 rev1 fix): it only rules out the case where the row was
        # already stale when we read it. It cannot see a write that lands *between*
        # this compare and the DELETE below. Either way the row it saw is a REAL,
        # currently-existing row -- report it as superseded, not absent.
        if (
            row.get("paused_by") != paused_by
            or row.get("paused_at") != paused_at
            or (row.get("stop_kind") or "user") != normalized_stop_kind
            or row.get("stop_run_id") != stop_run_id
        ):
            return ReleaseSuperseded(row)
        try:
            affected = store._execute_affected(
                "DELETE FROM ai_invoke_paused_chains WHERE " + " AND ".join(clauses),
                params,
            )
        except Exception as exc:
            if not _is_mariadb_snapshot_isolation_checkread(exc):
                raise
            # MariaDB's default `innodb_snapshot_isolation=ON` (0459 TR0008 rev5,
            # reproduced against a live MariaDB 12.2 instance) makes this DELETE do
            # something Oracle MySQL and PostgreSQL never do: when its WHERE clause
            # matched the row under this transaction's REPEATABLE READ snapshot, but a
            # DIFFERENT, already-committed transaction changed that exact row first,
            # MariaDB refuses to silently perform a current-read delete -- it raises
            # ER_CHECKREAD (1020, "Record has changed since last read") instead of
            # returning affected=0. That is not a real failure: it is MariaDB's own
            # proof that this call lost the identical race the affected==0 branch
            # below already handles, so treat it exactly the same way. (With
            # `innodb_snapshot_isolation=OFF` the same race instead returns affected=0
            # silently, which is why the branch below still exists and is still
            # exercised on its own.)
            affected = 0
        if affected == 1:
            # Only the transaction that physically deleted the inspected row may
            # return it and become ``released=true``. This affected-row proof is what
            # distinguishes the winner from a second process that read the same
            # snapshot but reached the DELETE after the winner committed.
            return row
        if affected != 0:
            raise RuntimeError(
                f"paused-chain CAS deleted an impossible number of rows: {affected}"
            )

        # A zero-row CAS has two distinct meanings. If the key is absent, another
        # release/resume transaction deleted the SAME snapshot first and this caller
        # is the idempotent loser (None). If a row remains, an upsert replaced the
        # inspected snapshot and the live row must be preserved and surfaced as a
        # conflict. A post-DELETE absence by itself cannot identify the winner; the
        # affected-row count above provides that missing ownership proof.
        #
        # This survivor lookup must be a LOCKING read (FOR UPDATE), not the plain
        # consistent read every other query in this module uses. Under MySQL/InnoDB's
        # default REPEATABLE READ, a plain SELECT reuses the snapshot this same
        # transaction's very first read established -- the one that saw ``row`` above
        # -- so if a DIFFERENT transaction deleted and committed that exact row in the
        # meantime, this plain SELECT would still show it as present (stale-snapshot
        # false positive), and we would wrongly report ReleaseSuperseded for a row
        # that is actually gone instead of the idempotent None. FOR UPDATE forces
        # InnoDB to perform a current read against the latest committed data
        # regardless of the transaction's snapshot. SQLite has no FOR UPDATE syntax
        # and needs none: it serializes writers, so by the time this transaction's own
        # DELETE has run, no concurrent writer could still be racing it.
        survivor_sql = "SELECT * FROM ai_invoke_paused_chains WHERE group_id = ?"
        if getattr(store, "dialect", _dialect.SQLITE) != _dialect.SQLITE:
            survivor_sql += " FOR UPDATE"
        survivor = store._fetch_one(survivor_sql, [group_id])
        if survivor is None:
            return None
        return ReleaseSuperseded(survivor)


def list_all_system_stops() -> list[dict]:
    """Every system-parked row, whoever owns it (0406 T0022 item 4).

    Read at process start, where there is no user to scope by: a handoff row left behind by
    the process that died belongs to whoever was running that chain, and all of them have to
    be looked at exactly once.
    """
    return get_store()._fetch_all(
        "SELECT * FROM ai_invoke_paused_chains "
        "WHERE COALESCE(stop_kind, 'user') = 'system' ORDER BY paused_at DESC",
        [],
    )


def mark_stop_code(group_id: str, stop_code: str, *, stop_run_id: Optional[str] = None) -> None:
    """Re-label a system row without touching anything else (0406 T0022 item 4).

    Deliberately NOT the full ``upsert``: that one overwrites every column, and a startup
    recovery that only wants to say "this handoff never landed" must not risk wiping the
    provider / note / mode selections the row is being kept for. Scoped to system rows so a
    user pause written in between survives, and to ``stop_run_id`` when one is known.
    """
    sql = (
        "UPDATE ai_invoke_paused_chains SET stop_code = ?, updated_at = ? "
        "WHERE group_id = ? AND COALESCE(stop_kind, 'user') = 'system'"
    )
    params = [stop_code, now_iso(), group_id]
    if stop_run_id:
        sql += " AND stop_run_id = ?"
        params.append(stop_run_id)
    get_store()._execute(sql, params)


def list_by_user(user_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM ai_invoke_paused_chains WHERE paused_by = ? ORDER BY paused_at DESC",
        [user_id],
    )

"""Stage assignments (flowgate.v02.0113 T#1, DB-1 §2.4-§2.5, §4).

Migration 164: ``graph_stage_assignments`` (one row per ``(attempt_id, stage, seq)``, L-2 ``StageAssignment``) and
the ``assignment_stage`` / ``assignment_seq`` columns of the execution ledger ``graph_attempt_runs``.

- First writer wins (W-7): read, else insert under a savepoint; a loser gets the winner's row and writes no
  ledger request. Writes run inside the caller's Flow guard, which already serializes a Flow's assignments, so the
  savepoint is the line of defence for a write that skipped the guard (DB-1 §4.3).
- After the INSERT only ``stage_state``, ``fail_cause``, ``verdict``, ``intent_kind`` and ``intent_exec_id`` change,
  through exactly two UPDATE statements (W-8 state CAS, W-9 intent once). A test pins that list (DB-1 I-9).
- An invalidated attempt keeps its rows; they go only with the attempt row itself (run / Flow delete: FK cascade,
  and on SQLite the trigger of migration 164).
- ``seq`` is 0 for the instruction and result stages and 1, 2, … per submission for the review stages.
  L-2 ``is_legacy_attempt`` = ``not available() or not has_any(attempt_id)``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Optional

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.db.dialect import MYSQL

TABLE = "graph_stage_assignments"
LEDGER_TABLE = "graph_attempt_runs"

INSTRUCTION, RESULT, RESULT_REVIEW, INSTRUCTION_REVIEW = "instruction", "result", "result_review", "instruction_review"
STAGES = (INSTRUCTION, RESULT, RESULT_REVIEW, INSTRUCTION_REVIEW)
REVIEW_STAGES = (RESULT_REVIEW, INSTRUCTION_REVIEW)
ACTORS = ("ai", "person", "no_review", "wait_setup", "wait_unavailable")
STAGE_STATES = ("active", "waiting", "done", "failed")
INTENT_EXECUTION, INTENT_APPROVAL = "execution", "approval"

# Fixed at the INSERT (L-2 invariant 3) …
IMMUTABLE_COLUMNS = (
    "attempt_id", "stage", "seq", "run_id", "group_id", "node_key", "role", "actor", "actor_reason",
    "provider_id", "provider_name", "role_value_kind", "role_value_provider_id", "source", "cause",
    "plan_instruction_type", "plan_result_type", "reused_doc_id", "review_rounds", "rework_provider_id",
    "author", "author_provider_id", "basis_doc_id", "basis_revision_no", "basis_rejection_count", "decided_at",
)
# … and the only columns W-8 / W-9 set (with updated_at).
MUTABLE_COLUMNS = ("stage_state", "fail_cause", "verdict", "intent_kind", "intent_exec_id")
COLUMNS = (*IMMUTABLE_COLUMNS, *MUTABLE_COLUMNS, "updated_at")

_INSERT = (
    "INSERT INTO graph_stage_assignments (" + ", ".join(COLUMNS) + ") VALUES ("
    + ", ".join("?" for _ in COLUMNS) + ")"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def available() -> bool:
    """False until migration 164 is applied: every attempt is a pre-feature attempt (L-2 E-02)."""
    return get_store().table_exists(TABLE)


def read(attempt_id: str, stage: str, seq: int = 0) -> Optional[dict]:
    """R-4 (R-7 = ``read(attempt_id, 'result', 0)``). Read errors propagate (L-2 E-03)."""
    return get_store()._fetch_one(
        "SELECT * FROM graph_stage_assignments WHERE attempt_id = ? AND stage = ? AND seq = ?",
        [attempt_id, stage, seq])


def _read_committed(attempt_id: str, stage: str, seq: int) -> Optional[dict]:
    """R-4L: the winner's row after a lost insert. MySQL REPEATABLE READ would replay the first snapshot, so it
    takes a locking (current) read; PostgreSQL READ COMMITTED and SQLite see the committed row plainly."""
    store = get_store()
    sql = "SELECT * FROM graph_stage_assignments WHERE attempt_id = ? AND stage = ? AND seq = ?"
    if store.dialect == MYSQL:
        sql += " FOR UPDATE"
    return store._fetch_one(sql, [attempt_id, stage, seq])


def latest(attempt_id: str, stage: str) -> Optional[dict]:
    """R-5: the stage's assignment with the largest ``seq`` (``current_review_assignment``). None = none."""
    return get_store()._fetch_one(
        "SELECT * FROM graph_stage_assignments WHERE attempt_id = ? AND stage = ? ORDER BY seq DESC LIMIT 1",
        [attempt_id, stage])


def has_any(attempt_id: str) -> bool:
    """R-6: the attempt has at least one assignment (False = pre-feature attempt, L-2 §2.13)."""
    return get_store()._fetch_one(
        "SELECT 1 AS present FROM graph_stage_assignments WHERE attempt_id = ? LIMIT 1", [attempt_id]) is not None


def for_node(run_id: str, node_key: str, attempt_id: str) -> list[dict]:
    """R-8: the node's assignments in one attempt (run read: wait reason, reconcile)."""
    return list(get_store()._fetch_all(
        "SELECT * FROM graph_stage_assignments WHERE run_id = ? AND node_key = ? AND attempt_id = ? "
        "ORDER BY stage, seq", [run_id, node_key, attempt_id]) or [])


def ledger_columns_available() -> bool:
    """The ledger's assignment columns exist (migration 164). Before it W-11 binds nothing and R-9 finds nothing."""
    store = get_store()
    return store.column_exists(LEDGER_TABLE, "assignment_stage") and store.column_exists(LEDGER_TABLE, "assignment_seq")


def ledger_exec_for(attempt_id: str, stage: str, seq: int) -> Optional[str]:
    """R-9: the newest ledger request made for this assignment (L-2 §2.2.2 recovery). None = none."""
    if not ledger_columns_available():
        return None
    row = get_store()._fetch_one(
        "SELECT exec_id FROM graph_attempt_runs WHERE attempt_id = ? AND assignment_stage = ? AND assignment_seq = ? "
        "ORDER BY created_at DESC LIMIT 1", [attempt_id, stage, seq])
    return row["exec_id"] if row else None


def insert_if_absent(row: Mapping) -> tuple[dict, bool]:
    """W-7 ①②④: record the assignment unless its key already has one. Returns ``(stored row, inserted)``; a
    loser (``inserted`` False) gets the row that won and must not write a ledger request (W-11).

    Inside the caller's Flow guard transaction (the savepoint needs it). ``row`` holds the columns by name;
    ``updated_at`` defaults to ``decided_at``. A CHECK violation is an error, never "already there".
    """
    unknown = set(row) - set(COLUMNS)
    if unknown:
        raise ValueError(f"not a stage assignment column: {sorted(unknown)}")
    key = (row["attempt_id"], row["stage"], row["seq"])
    found = read(*key)                                                     # ①
    if found is not None:
        return found, False
    values = {column: row.get(column) for column in COLUMNS}
    if values["updated_at"] is None:
        values["updated_at"] = values["decided_at"]
    if get_store().insert_isolated(_INSERT, [values[column] for column in COLUMNS], "fg_gsa_ins"):   # ②
        return read(*key) or values, True
    winner = _read_committed(*key)                                          # ④
    if winner is None:
        raise RuntimeError("stage assignment conflict without a committed row")
    return winner, False


def update_state(attempt_id: str, stage: str, seq: int, expected_state: str, stage_state: str,
                 fail_cause: Optional[str] = None, verdict: Optional[str] = None, now: Optional[str] = None) -> bool:
    """W-8: move ``stage_state`` (with ``fail_cause`` / ``verdict``) only from ``expected_state`` (CAS). True when
    the row moved. Which moves are allowed is L-2 §3.2; ``done`` / ``failed`` have no way out."""
    return get_store()._execute_affected(
        "UPDATE graph_stage_assignments SET stage_state = ?, fail_cause = ?, verdict = ?, updated_at = ? "
        "WHERE attempt_id = ? AND stage = ? AND seq = ? AND stage_state = ?",
        [stage_state, fail_cause, verdict, now or _now(), attempt_id, stage, seq, expected_state]) == 1


def set_intent(attempt_id: str, stage: str, seq: int, intent_kind: str, intent_exec_id: Optional[str] = None,
               now: Optional[str] = None) -> bool:
    """W-9: fill the execution intent once (``execution`` + ledger ``exec_id``, or ``approval``). False = it was
    already filled; read it back with ``read``."""
    return get_store()._execute_affected(
        "UPDATE graph_stage_assignments SET intent_kind = ?, intent_exec_id = ?, updated_at = ? "
        "WHERE attempt_id = ? AND stage = ? AND seq = ? AND intent_kind IS NULL",
        [intent_kind, intent_exec_id, now or _now(), attempt_id, stage, seq]) == 1

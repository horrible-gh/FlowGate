"""Gate decision store (flowgate.v02.0085 T#1; migration 158_gate_decision_persistence.sql; D0007 §7.2).

Tables: ``gate_decisions``, ``gate_decision_effects``, ``gate_archive_checkpoints``, ``gate_cleanup_records``.
Every write here runs inside the caller's Flow guard or the decision's own short DB transaction; this module takes
no lock and does no Git. What it keeps for the later cards:

- A decision is found by its request key (run, node, actor, request id) and is unique per attempt except a canceled
  one (``valid_attempt_key``: the attempt id while the decision counts, NULL once it is canceled). An abandoned
  decision keeps counting: nothing new can be decided in its attempt.
- A decision moves along ``TRANSITIONS`` only, each move compared with the decision version it was read at (a
  conditional update; 0 rows means somebody else moved it first). A terminal decision never changes its status
  again; only its links and the round-split flag (``ANNOTATIONS``) can still be set or cleared, by the Time Machine.
  The version moves with every change.
- Effects are one row per (decision, phase) and are updated in place by a retry, never added a second time.
- A checkpoint belongs to one decision and one archive id; the group id is not a key.
- Nothing is deleted. The tables have no REFERENCES to groups, runs or attempts, so a deleted run or Flow leaves the
  history in place.
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Optional

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store.layout_store import utc_now_ms

DECISION_TABLE = "gate_decisions"
EFFECT_TABLE = "gate_decision_effects"
CHECKPOINT_TABLE = "gate_archive_checkpoints"
CLEANUP_TABLE = "gate_cleanup_records"

CONFIRM, REJECT = "confirm", "reject"
(ACCEPTED, FAILED, RECOVERY_REQUIRED, SUCCEEDED, REJECTED, CANCELED, ABANDONED) = (
    "accepted", "failed", "recovery_required", "succeeded", "rejected", "canceled", "abandoned")
STATUSES = (ACCEPTED, FAILED, RECOVERY_REQUIRED, SUCCEEDED, REJECTED, CANCELED, ABANDONED)
# The decision holds its gate: no other decision, no settings change, no follow-up opening (D0007 §3.3, §7.3).
RESERVING = (ACCEPTED, FAILED, RECOVERY_REQUIRED)
TERMINAL = (SUCCEEDED, REJECTED, CANCELED, ABANDONED)
# A decision is born accepted (its job is queued), or already finished when no Git / archive work is needed
# (succeeded) or when it is a reject (rejected, at once).
BORN = (ACCEPTED, SUCCEEDED, REJECTED)
TRANSITIONS = {
    ACCEPTED: (FAILED, RECOVERY_REQUIRED, SUCCEEDED, CANCELED),
    FAILED: (ACCEPTED, RECOVERY_REQUIRED, SUCCEEDED, CANCELED),
    RECOVERY_REQUIRED: (ACCEPTED, FAILED, SUCCEEDED, ABANDONED),
}
CLOSE_EFFECTS_CONFIRMED, CLOSE_ABANDON = "effects_confirmed", "abandon"

PENDING, DONE, EFFECT_FAILED, UNKNOWN, SKIPPED = "pending", "succeeded", "failed", "unknown", "skipped"

_DECISION_INSERT = (
    "decision_id", "group_id", "run_id", "node_key", "attempt_id", "revision_no", "policy_version", "actor_user_id",
    "request_id", "request_fingerprint", "decision", "decision_comment", "status", "settings_version", "settings_json",
    "frozen_sha", "input_attempt_ref", "target_remote", "target_branch", "target_sha", "job_id",
    "previous_decision_id", "time_machine_ref", "accepted_at", "completed_at")
_DECISION_UPDATABLE = frozenset({
    "job_id", "last_error_code", "frozen_sha", "target_sha", "target_remote", "target_branch", "settings_json",
    "settings_version", "close_kind", "closed_by", "close_reason", "closed_at", "recovery_reset_required",
    "completed_at", "superseded_by_decision_id", "time_machine_ref"})
# What can still be written to a decision in a final status: the link to the decision that replaced it, the Time
# Machine reference and the round-split flag (cleared when the Time Machine has split the round).
ANNOTATIONS = frozenset({"superseded_by_decision_id", "time_machine_ref", "recovery_reset_required"})
_CHECKPOINT_COLUMNS = (
    "archive_id", "decision_id", "group_id", "run_id", "node_key", "attempt_id", "source_sha", "applied_sha",
    "ref_name", "applied_ref", "restore_info", "preserved_at")
_CLEANUP_COLUMNS = (
    "cleanup_id", "group_id", "run_id", "job_id", "branch_name", "end_head_sha", "basis_kind", "basis_ref",
    "basis_sha", "reachable", "keep_requested", "result", "result_reason", "evidence_json", "created_at")
_EFFECT_UPDATABLE = ("status", "target_ref", "sha_before", "sha_after", "evidence_json", "archive_id", "error_code",
                     "recovery_state", "recovery_ref", "reviewed_evidence_version")


class DecisionConflict(Exception):
    """An insert hit a unique key. ``reason``: ``request_key`` (the same request id again; ``existing`` is that
    decision) or ``attempt`` (another valid decision already holds the attempt; ``existing`` is it)."""

    def __init__(self, reason: str, existing: Optional[dict]):
        super().__init__(reason)
        self.reason = reason
        self.existing = existing


def available() -> bool:
    """False until migration 158 is applied."""
    return get_store().table_exists(DECISION_TABLE)


def new_decision_id() -> str:
    return "gdc_" + secrets.token_hex(13)             # 30 chars


def new_archive_id() -> str:
    return "gar_" + secrets.token_hex(13)


def new_cleanup_id() -> str:
    return "gcl_" + secrets.token_hex(13)


def _one(sql: str, params: list) -> Optional[dict]:
    row = get_store()._fetch_one(sql, params)
    return dict(row) if row else None


def _all(sql: str, params: list) -> list[dict]:
    return [dict(r) for r in get_store()._fetch_all(sql, params) or []]


def _json(value: Any) -> Optional[str]:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decoded(row: Optional[dict], *names: str) -> Optional[dict]:
    if row is None:
        return None
    for name in names:
        raw = row.get(name)
        try:
            row[name.replace("_json", "")] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            row[name.replace("_json", "")] = None
    return row


# ── decisions ────────────────────────────────────────────────────────────────

def _decision(row: Optional[dict]) -> Optional[dict]:
    row = _decoded(row, "settings_json")
    if row is not None:
        row["recovery_reset_required"] = bool(row.get("recovery_reset_required"))
    return row


def get(decision_id: str) -> Optional[dict]:
    if not available():
        return None
    return _decision(_one("SELECT * FROM gate_decisions WHERE decision_id = ?", [decision_id]))


def by_request(run_id: str, node_key: str, actor_user_id: str, request_id: str) -> Optional[dict]:
    """The decision a request key made (D0007 §7.2: the key is bound to the actor, run, node and request id)."""
    if not available():
        return None
    return _decision(_one("SELECT * FROM gate_decisions WHERE run_id = ? AND node_key = ? AND actor_user_id = ? "
                          "AND request_id = ?", [run_id, node_key, actor_user_id, request_id]))


def valid_for_attempt(attempt_id: str) -> Optional[dict]:
    """The one decision that counts in an attempt (every status but canceled), or None."""
    if not available():
        return None
    return _decision(_one("SELECT * FROM gate_decisions WHERE valid_attempt_key = ?", [attempt_id]))


def reservation(run_id: str, node_key: str) -> Optional[dict]:
    """The decision that holds the gate right now (accepted, failed or recovery_required), or None."""
    if not available():
        return None
    marks = ", ".join("?" for _ in RESERVING)
    return _decision(_one(
        f"SELECT * FROM gate_decisions WHERE run_id = ? AND node_key = ? AND status IN ({marks}) "
        "ORDER BY accepted_at DESC, decision_id DESC LIMIT 1", [run_id, node_key, *RESERVING]))


def reset_required(group_id: str, node_key: str) -> Optional[dict]:
    """The abandoned decision that still asks for a Time Machine round split of this gate, or None."""
    if not available():
        return None
    return _decision(_one(
        "SELECT * FROM gate_decisions WHERE group_id = ? AND node_key = ? AND recovery_reset_required = 1 "
        "ORDER BY accepted_at DESC, decision_id DESC LIMIT 1", [group_id, node_key]))


def of_node(group_id: str, node_key: str, *, after: Optional[tuple] = None, limit: Optional[int] = None) -> list[dict]:
    """The gate's decisions in decision order (accepted time, then id). ``after`` is the (accepted_at, decision_id)
    the page starts behind."""
    if not available():
        return []
    sql, params = "SELECT * FROM gate_decisions WHERE group_id = ? AND node_key = ?", [group_id, node_key]
    if after is not None:
        sql += " AND (accepted_at > ? OR (accepted_at = ? AND decision_id > ?))"
        params += [after[0], after[0], after[1]]
    sql += " ORDER BY accepted_at, decision_id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [_decision(r) for r in _all(sql, params)]


def insert_decision(row: dict) -> dict:
    """Store a new decision (``row``: the columns of ``_DECISION_INSERT``; ``decision_id`` and ``accepted_at`` are
    made when missing; ``settings`` may be a dict for ``settings_json``). Raises ``DecisionConflict`` when the
    request key was used before or another valid decision already holds the attempt (both are checked first; the
    unique indexes still decide a race)."""
    data = dict(row)
    if "settings" in data:
        data["settings_json"] = _json(data.pop("settings"))
    data.setdefault("decision_id", new_decision_id())
    data.setdefault("accepted_at", utc_now_ms())
    data.setdefault("status", ACCEPTED)
    if data["status"] not in BORN:
        raise ValueError(f"a decision is not born {data['status']}")
    if data["decision"] not in (CONFIRM, REJECT):
        raise ValueError("decision must be confirm or reject")
    if data["status"] in (SUCCEEDED, REJECTED):
        data.setdefault("completed_at", data["accepted_at"])
    same_request = by_request(data["run_id"], data["node_key"], data["actor_user_id"], data["request_id"])
    if same_request is not None:
        raise DecisionConflict("request_key", same_request)
    holder = valid_for_attempt(data["attempt_id"])
    if holder is not None:
        raise DecisionConflict("attempt", holder)
    columns = [c for c in _DECISION_INSERT if c in data]
    columns += ["valid_attempt_key", "decision_version", "updated_at"]
    values = [data[c] for c in columns[:-3]] + [data["attempt_id"], 1, data["accepted_at"]]
    marks = ", ".join("?" for _ in columns)
    try:
        get_store()._execute(f"INSERT INTO gate_decisions ({', '.join(columns)}) VALUES ({marks})", values)
    except Exception:
        # Lost a race with the same key (the unique indexes decide): report who won when it can still be read.
        # A PostgreSQL unique violation aborts the caller's transaction, so the intake (a later card) runs this insert
        # as its own short transaction, like ``job_store.create_or_get_job``.
        same_request = by_request(data["run_id"], data["node_key"], data["actor_user_id"], data["request_id"])
        holder = valid_for_attempt(data["attempt_id"])
        if same_request is not None:
            raise DecisionConflict("request_key", same_request) from None
        if holder is not None:
            raise DecisionConflict("attempt", holder) from None
        raise
    return get(data["decision_id"])


def move(decision_id: str, expected_version: int, to_status: Optional[str] = None, **changes: Any) -> bool:
    """One conditional change of a decision: it must still be at ``expected_version`` and, for a status move, in a
    status ``TRANSITIONS`` allows. The version moves up by one, a move to canceled frees the attempt
    (``valid_attempt_key`` NULL), a move into a final status stamps ``completed_at`` unless given. True when
    exactly this call changed it; False means the decision changed first (or the move is not allowed) - read it again.
    Without ``to_status`` the fields change on a decision that still holds its gate; ``ANNOTATIONS`` alone may
    change on a decision in any status."""
    unknown = set(changes) - _DECISION_UPDATABLE
    if unknown:
        raise ValueError(f"not an updatable gate decision column: {sorted(unknown)}")
    if "settings_json" in changes:
        changes["settings_json"] = _json(changes["settings_json"])
    if "recovery_reset_required" in changes:
        changes["recovery_reset_required"] = 1 if changes["recovery_reset_required"] else 0
    now = utc_now_ms()
    sets = [f"{name} = ?" for name in changes]
    values: list = list(changes.values())
    if to_status is not None:
        if to_status not in STATUSES:
            raise ValueError(f"unknown gate decision status {to_status!r}")
        sources = tuple(s for s, targets in TRANSITIONS.items() if to_status in targets)
        sets.append("status = ?")
        values.append(to_status)
        if to_status == CANCELED:
            sets.append("valid_attempt_key = NULL")
        if to_status in (SUCCEEDED, ABANDONED, CANCELED) and "completed_at" not in changes:
            sets.append("completed_at = ?")
            values.append(now)
        where_status = f" AND status IN ({', '.join('?' for _ in sources)})" if sources else " AND 1 = 0"
        where_values = list(sources)
    elif set(changes) <= ANNOTATIONS:
        where_status, where_values = "", []
    else:
        where_status, where_values = f" AND status IN ({', '.join('?' for _ in RESERVING)})", list(RESERVING)
    sets += ["decision_version = decision_version + 1", "updated_at = ?"]
    values.append(now)
    affected = get_store()._execute_affected(
        f"UPDATE gate_decisions SET {', '.join(sets)} WHERE decision_id = ? AND decision_version = ?{where_status}",
        [*values, decision_id, int(expected_version), *where_values])
    return affected == 1


# ── effects ──────────────────────────────────────────────────────────────────

def effects_of(decision_id: str) -> list[dict]:
    """The evidence rows of a decision, in the order the phases were first recorded."""
    if not available():
        return []
    return [_decoded(r, "evidence_json") for r in _all(
        "SELECT * FROM gate_decision_effects WHERE decision_id = ? ORDER BY created_at, phase", [decision_id])]


def effect(decision_id: str, phase: str) -> Optional[dict]:
    if not available():
        return None
    return _decoded(_one("SELECT * FROM gate_decision_effects WHERE decision_id = ? AND phase = ?",
                         [decision_id, phase]), "evidence_json")


def record_effect(decision_id: str, phase: str, *, new_attempt: bool = False, **fields: Any) -> dict:
    """Store what a phase did, in the phase's one row: created at the first call, then updated in place (a retry runs
    the phase again and never adds a second row). Every call moves the evidence version; ``new_attempt`` also counts
    one more run of the phase. ``evidence`` (a dict) is stored as ``evidence_json``. Returns the row."""
    if "evidence" in fields:
        fields["evidence_json"] = _json(fields.pop("evidence"))
    unknown = set(fields) - set(_EFFECT_UPDATABLE)
    if unknown:
        raise ValueError(f"not an updatable effect column: {sorted(unknown)}")
    now = utc_now_ms()
    store = get_store()
    if effect(decision_id, phase) is None:
        columns = ["decision_id", "phase", "status", "attempt_count", "evidence_version", "created_at", "updated_at"]
        values: list = [decision_id, phase, fields.pop("status", PENDING), 1, 1, now, now]
        for name, value in fields.items():
            columns.append(name)
            values.append(value)
        store._execute(f"INSERT INTO gate_decision_effects ({', '.join(columns)}) "
                       f"VALUES ({', '.join('?' for _ in columns)})", values)
    else:
        sets = [f"{name} = ?" for name in fields] + ["evidence_version = evidence_version + 1", "updated_at = ?"]
        if new_attempt:
            sets.append("attempt_count = attempt_count + 1")
        store._execute(f"UPDATE gate_decision_effects SET {', '.join(sets)} WHERE decision_id = ? AND phase = ?",
                       [*fields.values(), now, decision_id, phase])
    return effect(decision_id, phase)


# ── archive checkpoints ──────────────────────────────────────────────────────

def insert_checkpoint(row: dict) -> dict:
    """Store one archive checkpoint. The archive id, the decision id and the ref are each unique: a second
    checkpoint of a group is a new row, and the same decision or ref twice is refused (the store raises)."""
    data = dict(row)
    data.setdefault("archive_id", new_archive_id())
    data.setdefault("preserved_at", utc_now_ms())
    data["restore_info"] = _json(data.get("restore_info"))
    columns = [c for c in _CHECKPOINT_COLUMNS if c in data]
    get_store()._execute(f"INSERT INTO gate_archive_checkpoints ({', '.join(columns)}) "
                         f"VALUES ({', '.join('?' for _ in columns)})", [data[c] for c in columns])
    return checkpoint(data["archive_id"])


def checkpoint(archive_id: str) -> Optional[dict]:
    if not available():
        return None
    return _decoded(_one("SELECT * FROM gate_archive_checkpoints WHERE archive_id = ?", [archive_id]), "restore_info")


def checkpoint_of_decision(decision_id: str) -> Optional[dict]:
    if not available():
        return None
    return _decoded(_one("SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", [decision_id]), "restore_info")


def checkpoints_of_group(group_id: str) -> list[dict]:
    """Every checkpoint of a group, oldest first - none is replaced by a later one."""
    if not available():
        return []
    return [_decoded(r, "restore_info") for r in _all(
        "SELECT * FROM gate_archive_checkpoints WHERE group_id = ? ORDER BY preserved_at, archive_id", [group_id])]


def mark_restored(archive_id: str) -> bool:
    """A gate checkpoint restore does not consume the checkpoint (D0007 §7.1): it only notes when."""
    return get_store()._execute_affected(
        "UPDATE gate_archive_checkpoints SET last_restored_at = ? WHERE archive_id = ?",
        [utc_now_ms(), archive_id]) == 1


# ── cleanup records ──────────────────────────────────────────────────────────

def insert_cleanup(row: dict) -> dict:
    """Store one cleanup outcome with the HEAD it was judged at and the basis it relied on."""
    data = dict(row)
    data.setdefault("cleanup_id", new_cleanup_id())
    data.setdefault("created_at", utc_now_ms())
    data["evidence_json"] = _json(data.pop("evidence", data.get("evidence_json")))
    for flag in ("reachable", "keep_requested"):
        if data.get(flag) is not None:
            data[flag] = 1 if data[flag] else 0
    data.setdefault("keep_requested", 0)
    columns = [c for c in _CLEANUP_COLUMNS if c in data]
    get_store()._execute(f"INSERT INTO gate_cleanup_records ({', '.join(columns)}) "
                         f"VALUES ({', '.join('?' for _ in columns)})", [data[c] for c in columns])
    return cleanup(data["cleanup_id"])


def cleanup(cleanup_id: str) -> Optional[dict]:
    if not available():
        return None
    return _decoded(_one("SELECT * FROM gate_cleanup_records WHERE cleanup_id = ?", [cleanup_id]), "evidence_json")


def cleanups_of_group(group_id: str) -> list[dict]:
    """A group's cleanup records, oldest first; the last one carries the real end HEAD a slot restore starts from."""
    if not available():
        return []
    return [_decoded(r, "evidence_json") for r in _all(
        "SELECT * FROM gate_cleanup_records WHERE group_id = ? ORDER BY created_at, cleanup_id", [group_id])]


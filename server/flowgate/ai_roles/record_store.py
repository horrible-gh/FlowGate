"""Role invocation records (flowgate.v02.0113 T#1, DB-1 §2.6-§2.7, §4).

Migration 165: ``ai_role_invocations`` (one row per provider launch and one per admission refusal, L-3
``RoleInvocation``) and the ``invocation_origin_kind`` / ``graph_exec_id`` columns of ``ai_invoke_runs``.

- History: no foreign key, no cascade, and the ``ai_invoke_runs`` retention sweep does not touch these rows
  (DB-1 I-17). There is no DELETE statement for this table.
- Order is ``create_seq`` (DB-D12), fixed once when the record is made in memory (``issue_create_seq``, DB-1
  §2.6.2 C-4); ``record_seq`` is only the storage key and ``created_at`` only a display time.
- A work unit is found by ``unit_key_hash`` (§2.6.1, ``unit_key``): sha256 of a canonical JSON array.
- Writing one record is one short transaction of its own (``write_record``): W-10 insert under a savepoint
  (a unique clash = "already there"), then W-12 finish while still running, then W-13 run note. All three are
  safe to repeat. It is never part of the calling path's transaction (L-3 ``RECORD_WRITE_BLOCKS_CALL = false``).
- No secret column: only the requested model value and a keyed hash of the provider settings
  (``provider_fingerprint``) come from a CLI command.

Which records are made, when, and in which order a run writes them (S-1~S-3) is L-3 (a later card).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence

from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.db.dialect import MYSQL

TABLE = "ai_role_invocations"
RUNS_TABLE = "ai_invoke_runs"
RUNNING = "running"

COLUMNS = (
    "inv_id", "create_seq", "ai_run_id", "launch_no", "hop_no", "graph_exec_id", "origin_kind", "inv_role",
    "purpose", "target_group_id", "target_graph_run_id", "target_node_key", "target_attempt_id", "target_stage",
    "target_seq", "target_doc_id", "target_doc_revision_no", "target_unresolved_reason", "qa_item_id",
    "qa_question_id", "qa_asker_ai_run_id", "qa_asker_inv_id", "qa_card_class", "round_no", "requested_ai_id",
    "role_source", "legacy_selected_provider_source", "actual_ai_id", "actual_ai_name", "provider_exec_type",
    "provider_kind", "provider_shell_kind", "provider_fingerprint", "requested_model_state",
    "requested_model_value", "requested_model_reason", "confirmed_model_state", "confirmed_model_values_json",
    "confirmed_model_truncated", "confirmed_coverage", "calls_total", "calls_reported", "calls_absent",
    "calls_unreadable", "calls_failed", "bad_lines", "unverifiable_reason", "model_capability", "result",
    "result_detail", "exit_code", "timeout_kind", "model_call_failure_kind", "model_call_no", "model_turn_no",
    "model_http_status", "run_end_reason", "run_stop_code", "link_kind", "prev_inv_id", "unit_kind",
    "unit_key_hash", "chain_root_id", "started_at", "ended_at", "created_at", "updated_at",
)
# W-12 sets these (and updated_at) once, while the row is still running.
FINISH_COLUMNS = (
    "result", "result_detail", "exit_code", "timeout_kind", "model_call_failure_kind", "model_call_no",
    "model_turn_no", "model_http_status", "confirmed_model_state", "confirmed_model_values_json",
    "confirmed_model_truncated", "confirmed_coverage", "calls_total", "calls_reported", "calls_absent",
    "calls_unreadable", "calls_failed", "bad_lines", "unverifiable_reason", "ended_at",
)

_INSERT = "INSERT INTO ai_role_invocations (" + ", ".join(COLUMNS) + ") VALUES (" + ", ".join("?" for _ in COLUMNS) + ")"
_FINISH = (
    "UPDATE ai_role_invocations SET result = ?, result_detail = ?, exit_code = ?, timeout_kind = ?, "
    "model_call_failure_kind = ?, model_call_no = ?, model_turn_no = ?, model_http_status = ?, "
    "confirmed_model_state = ?, confirmed_model_values_json = ?, confirmed_model_truncated = ?, "
    "confirmed_coverage = ?, calls_total = ?, calls_reported = ?, calls_absent = ?, calls_unreadable = ?, "
    "calls_failed = ?, bad_lines = ?, unverifiable_reason = ?, ended_at = ?, updated_at = ? "
    "WHERE inv_id = ? AND result = 'running'"
)
_FINISH_ORPHANS = (
    "UPDATE ai_role_invocations SET result = ?, result_detail = ?, exit_code = ?, timeout_kind = ?, "
    "model_call_failure_kind = ?, model_call_no = ?, model_turn_no = ?, model_http_status = ?, "
    "confirmed_model_state = ?, confirmed_model_values_json = ?, confirmed_model_truncated = ?, "
    "confirmed_coverage = ?, calls_total = ?, calls_reported = ?, calls_absent = ?, calls_unreadable = ?, "
    "calls_failed = ?, bad_lines = ?, unverifiable_reason = ?, ended_at = ?, updated_at = ? "
    "WHERE ai_run_id = ? AND result = 'running'"
)

# The provider fingerprint's key (DB-1 [DEFERRED] "지문 키 출처", decided here): derived from the AI-key
# encryption master key of ``utils.api_key_crypto`` (FLOWGATE_AI_ENCRYPT_KEY, else the storage key file) under
# its own label, so the fingerprint is a keyed hash nobody without the server key can reproduce, the API keys'
# cipher key itself is never used as an HMAC key, and no new secret has to be provisioned. Without a readable
# key the fingerprint is None and the record is still written (L-3 §2.5, E-21). Rotating the master key makes
# older fingerprints read as "settings changed".
_FINGERPRINT_LABEL = b"flowgate.ai_role_invocations.provider_fingerprint.v1"

_logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def available() -> bool:
    """False until migration 165 is applied: no record is written and lookups report the feature off."""
    return get_store().table_exists(TABLE)


# ── work unit key (§2.6.1) ───────────────────────────────────────────────────

def unit_key_hash(parts: Sequence) -> str:
    """sha256 hex (lower case, 64 chars) of the canonical JSON array: no blanks, non-ASCII as is."""
    canonical = json.dumps(list(parts), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def unit_key(record: Mapping) -> tuple[str, list]:
    """``(unit_kind, hash input)`` of a record by its origin (§2.6.1 table)."""
    origin = record["origin_kind"]
    if origin == "card_assigned":
        seq = record.get("target_seq")
        return "assignment", ["assignment", record.get("target_attempt_id"), record.get("target_stage"),
                              None if seq is None else int(seq)]
    if origin == "card_legacy_attempt":
        return "legacy", ["legacy", record.get("target_graph_run_id"), record.get("target_node_key"),
                          record.get("target_attempt_id"), record.get("target_stage")]
    if origin == "card_qa":
        return "qa", ["qa", record.get("qa_item_id")]
    if origin == "non_card":
        return "run", ["run", record.get("ai_run_id") or record.get("inv_id")]
    raise ValueError(f"unknown origin_kind: {origin!r}")


# ── creation order (§2.6.2 C-4) ──────────────────────────────────────────────

_seq_lock = threading.Lock()
_last_create_seq = 0


def issue_create_seq(*floors: Optional[int]) -> int:
    """C-4: ``max(issuer.next(), *floors)``, recorded as the issuer's last value. The issuer is process-wide and
    strictly increasing: ``next = max(UTC epoch ms × 1000, last + 1)``. Pass R-17 + 1 and the run's in-memory
    maximum + 1 as floors so a new record lands after every record of its work unit it can see."""
    global _last_create_seq
    with _seq_lock:
        value = max(int(time.time() * 1000) * 1000, _last_create_seq + 1,
                    *(int(f) for f in floors if f is not None))
        _last_create_seq = value
        return value


# ── provider fingerprint key (L-3 §2.5) ──────────────────────────────────────

def _fingerprint_key() -> Optional[bytes]:
    try:
        from modules.flow_gate.utils import api_key_crypto
        master = api_key_crypto._get_current_key(create=False)
    except Exception:                                    # no key, unreadable key, crypto library missing
        return None
    return hmac.new(master, _FINGERPRINT_LABEL, hashlib.sha256).digest()


def provider_fingerprint(canonical_settings: str) -> Optional[str]:
    """Keyed hash (HMAC-SHA256, lower-case hex 64) of the canonical provider settings L-3 §2.5 builds. None when
    the server key cannot be read. The input itself is never stored."""
    key = _fingerprint_key()
    if key is None:
        return None
    return hmac.new(key, canonical_settings.encode("utf-8"), hashlib.sha256).hexdigest()


# ── reads (R-10 … R-18) ─────────────────────────────────────────────────────

def by_run(ai_run_id: str) -> list[dict]:
    """R-10: a run's records by launch number (run finalize T-5, orphan sweep T-6, asker lookup)."""
    return list(get_store()._fetch_all(
        "SELECT * FROM ai_role_invocations WHERE ai_run_id = ? ORDER BY launch_no", [ai_run_id]) or [])


def first(unit_hash: str) -> Optional[dict]:
    """R-11: the work unit's first record by creation order — the definition of its root. ``{inv_id, create_seq}``."""
    return get_store()._fetch_one(
        "SELECT inv_id, create_seq FROM ai_role_invocations WHERE unit_key_hash = ? "
        "ORDER BY create_seq ASC, inv_id ASC LIMIT 1", [unit_hash])


def latest(unit_hash: str, before_create_seq: int, purposes: Optional[Iterable[str]] = None,
           launched_only: bool = False) -> Optional[dict]:
    """R-12: the unit's last record made before ``before_create_seq`` (optionally only these purposes).
    ``launched_only`` (flowgate.v02test.0004 T#2): never a start refused at admission (``admission_refused``, nothing
    launched) — the previous call of a later one is a launched record, failed or not."""
    params: list = [unit_hash, before_create_seq]
    sql = "SELECT * FROM ai_role_invocations WHERE unit_key_hash = ? AND create_seq < ?"
    if launched_only:
        sql += " AND result <> 'admission_refused'"
    if purposes is not None:
        wanted = list(purposes)
        if not wanted:
            return None
        sql += " AND purpose IN (" + ", ".join("?" for _ in wanted) + ")"
        params += wanted
    sql += " ORDER BY create_seq DESC, inv_id DESC LIMIT 1"
    return get_store()._fetch_one(sql, params)


def by_exec(graph_exec_id: str) -> list[dict]:
    """R-13: the records of one Graph ledger execution in creation order."""
    return list(get_store()._fetch_all(
        "SELECT * FROM ai_role_invocations WHERE graph_exec_id = ? ORDER BY create_seq, inv_id",
        [graph_exec_id]) or [])


def for_node(group_id: str, node_key: str, limit: int) -> list[dict]:
    """R-14: a card's records, newest first (P-1 invocation list)."""
    return list(get_store()._fetch_all(
        "SELECT * FROM ai_role_invocations WHERE target_group_id = ? AND target_node_key = ? "
        "ORDER BY create_seq DESC, inv_id DESC LIMIT ?", [group_id, node_key, int(limit)]) or [])


def page_for_node(group_id: str, node_key: str, limit: int, before_seq=None, before_inv_id=None) -> list[dict]:
    """P-1 §8 keyset page, including records of removed nodes."""
    params = [group_id, node_key]
    sql = "SELECT * FROM ai_role_invocations WHERE target_group_id = ? AND target_node_key = ?"
    if before_seq is not None:
        sql += " AND (create_seq < ? OR (create_seq = ? AND inv_id < ?))"
        params += [before_seq, before_seq, before_inv_id]
    sql += " ORDER BY create_seq DESC, inv_id DESC LIMIT ?"
    return list(get_store()._fetch_all(sql, [*params, int(limit)]) or [])


def for_graph_run(graph_run_id: str) -> list[dict]:
    """R-19 (flowgate.v02.0113 T#5, the command result): every record of one Graph run in creation order. Which
    command a record is of is told by the run event its request was made at (``command_result``), never by a time
    window here. A read only."""
    return [dict(r) for r in get_store()._fetch_all(
        "SELECT * FROM ai_role_invocations WHERE target_graph_run_id = ? ORDER BY create_seq, inv_id",
        [graph_run_id]) or []]


def run_link_columns_available() -> bool:
    """``ai_invoke_runs`` has the two link columns (migration 165)."""
    store = get_store()
    return (store.column_exists(RUNS_TABLE, "invocation_origin_kind")
            and store.column_exists(RUNS_TABLE, "graph_exec_id"))


def runs_for_exec(graph_exec_id: str) -> list[str]:
    """R-15: the finished ``ai_invoke_runs`` of one Graph ledger execution (none before migration 165)."""
    if not run_link_columns_available():
        return []
    found = get_store()._fetch_all("SELECT run_id FROM ai_invoke_runs WHERE graph_exec_id = ?", [graph_exec_id])
    return [row["run_id"] for row in found or []]


def max_create_seq(unit_hash: str) -> Optional[int]:
    """R-17: the unit's largest ``create_seq`` (None = no record)."""
    row = get_store()._fetch_one(
        "SELECT create_seq FROM ai_role_invocations WHERE unit_key_hash = ? ORDER BY create_seq DESC LIMIT 1",
        [unit_hash])
    return int(row["create_seq"]) if row else None


def get(inv_id: str) -> Optional[dict]:
    """R-18: one record by its key."""
    return get_store()._fetch_one(
        "SELECT inv_id, create_seq, unit_key_hash, result FROM ai_role_invocations WHERE inv_id = ?", [inv_id])


def _get_committed(inv_id: str) -> Optional[dict]:
    """R-18L: the existing row after a unique clash (MySQL takes a locking read, DB-1 §4.3)."""
    store = get_store()
    sql = "SELECT inv_id, create_seq, unit_key_hash, result FROM ai_role_invocations WHERE inv_id = ?"
    if store.dialect == MYSQL:
        sql += " FOR UPDATE"
    return store._fetch_one(sql, [inv_id])


# ── writes (W-10, W-12 … W-14) ──────────────────────────────────────────────

def insert(record: Mapping) -> bool:
    """W-10 ①②: insert the record as it is in memory, inside an open transaction. True = inserted, False = the
    key (``inv_id`` or ``ai_run_id + launch_no``) is already there — never changed; a different ``create_seq`` /
    ``unit_key_hash`` on the stored row is a defect and only logged. A CHECK violation raises."""
    unknown = set(record) - set(COLUMNS)
    if unknown:
        raise ValueError(f"not a role invocation column: {sorted(unknown)}")
    values = {column: record.get(column) for column in COLUMNS}
    if values["confirmed_model_truncated"] is None:
        values["confirmed_model_truncated"] = 0
    if values["updated_at"] is None:
        values["updated_at"] = values["created_at"]
    if get_store().insert_isolated(_INSERT, [values[column] for column in COLUMNS], "fg_ari_ins"):
        return True
    stored = _get_committed(values["inv_id"])
    if stored is None or (int(stored["create_seq"]), stored["unit_key_hash"]) != (
            int(values["create_seq"]), values["unit_key_hash"]):
        _logger.warning("role invocation %s: the stored record differs from the one in memory", values["inv_id"])
    return False


def _finish_params(outcome: Mapping, at: str) -> list:
    values = [outcome.get(column) for column in FINISH_COLUMNS]
    truncated_at = FINISH_COLUMNS.index("confirmed_model_truncated")
    if values[truncated_at] is None:
        values[truncated_at] = 0
    return [*values, at]


def finish(inv_id: str, outcome: Mapping, now: Optional[str] = None) -> bool:
    """W-12: close a running record once (T-3 / T-3′). A record that already ended is left as it is (False)."""
    return get_store()._execute_affected(_FINISH, [*_finish_params(outcome, now or _now()), inv_id]) == 1


def finish_orphans(ai_run_id: str, outcome: Mapping, now: Optional[str] = None) -> int:
    """W-12 for the orphan sweep (T-6): close every still-running record of the run. Returns how many."""
    return get_store()._execute_affected(_FINISH_ORPHANS, [*_finish_params(outcome, now or _now()), ai_run_id])


def annotate_run(inv_id: str, run_end_reason: Optional[str], run_stop_code: Optional[str],
                 now: Optional[str] = None) -> bool:
    """W-13: the run-level note on the run's last launch record, once. ``result`` is not touched."""
    return get_store()._execute_affected(
        "UPDATE ai_role_invocations SET run_end_reason = ?, run_stop_code = ?, updated_at = ? "
        "WHERE inv_id = ? AND run_end_reason IS NULL AND run_stop_code IS NULL",
        [run_end_reason, run_stop_code, now or _now(), inv_id]) == 1


def fill_root(inv_id: str, chain_root_id: str, now: Optional[str] = None) -> bool:
    """W-14: fill a root left NULL when C-2 could not read (the R-11 result at that time). A set root stays."""
    return get_store()._execute_affected(
        "UPDATE ai_role_invocations SET chain_root_id = ?, updated_at = ? WHERE inv_id = ? AND chain_root_id IS NULL",
        [chain_root_id, now or _now(), inv_id]) == 1


def write_record(record: Mapping) -> bool:
    """Write one in-memory record at a write point (DB-1 §4.2 (d)): W-10, then W-12 when the record has ended,
    then W-13 when it carries the run note — one short transaction of its own, safe to repeat.

    True = the record is stored (inserted now or already there): the run may go on to its next record (S-1).
    False = not stored; a warning is logged (the key only, no values) and the call goes on (L-3 §2.2.6). Never
    inside the caller's transaction: a record write must not take the calling path down with it.
    """
    if in_transaction():
        raise RuntimeError("write_record() runs in its own transaction, not inside the caller's")
    store = get_store()
    try:
        with store.transaction():
            insert(record)
            if record.get("result", RUNNING) != RUNNING:
                finish(record["inv_id"], record, record.get("updated_at"))
            if record.get("run_end_reason") is not None or record.get("run_stop_code") is not None:
                annotate_run(record["inv_id"], record.get("run_end_reason"), record.get("run_stop_code"),
                             record.get("updated_at"))
        return True
    except Exception as exc:  # noqa: BLE001 — a record write never fails the call (L-3 §2.2.6)
        _logger.warning("role invocation %s was not stored (%s)", record.get("inv_id"), type(exc).__name__)
        return False


# ── launch outputs (flowgate.v02.0113 T#5, migration 168) ───────────────────────────────────────────────────────
# One row per launch record (``inv_id``), added once when the launch ended: the AI's last message of that launch, its
# stderr / stdout tail, the registration errors it added and the classification detail — so a command result shows each
# launch's own body and error, never the run's final launch's output on another launch. Additive and safe to repeat
# (a key already there is kept as it is); no DELETE. A launch recorded before this store has no row.

OUTPUTS_TABLE = "ai_role_invocation_outputs"
OUTPUT_COLUMNS = ("inv_id", "ai_run_id", "launch_no", "body", "stderr_tail", "stdout_tail", "register_errors_json",
                  "detail", "created_at")
_INSERT_OUTPUT = ("INSERT INTO ai_role_invocation_outputs (" + ", ".join(OUTPUT_COLUMNS) + ") VALUES ("
                  + ", ".join("?" for _ in OUTPUT_COLUMNS) + ")")


def outputs_available() -> bool:
    """False until migration 168 is applied: no output is written and none is read."""
    return get_store().table_exists(OUTPUTS_TABLE)


def write_output(output: Mapping) -> bool:
    """Store one launch's output in a short transaction of its own (never the caller's). True = stored now or already
    there; False = not stored (the store missing, a write error: logged, the call goes on)."""
    if in_transaction():
        raise RuntimeError("write_output() runs in its own transaction, not inside the caller's")
    store = get_store()
    try:
        if not outputs_available():
            return False
        values = [output.get(column) for column in OUTPUT_COLUMNS]
        with store.transaction():
            store.insert_isolated(_INSERT_OUTPUT, values, "fg_aro_ins")
        return True
    except Exception as exc:  # noqa: BLE001 — an output write never fails the call (L-3 §2.2.6)
        _logger.warning("role invocation output %s was not stored (%s)", output.get("inv_id"), type(exc).__name__)
        return False


def outputs_for(inv_ids: Sequence[str]) -> dict:
    """``{inv_id: output row}`` of the launches that have one (none before migration 168)."""
    wanted = [i for i in dict.fromkeys(inv_ids) if i]
    if not wanted or not outputs_available():
        return {}
    found: dict = {}
    store = get_store()
    for start in range(0, len(wanted), 400):
        chunk = wanted[start:start + 400]
        rows = store._fetch_all("SELECT * FROM ai_role_invocation_outputs WHERE inv_id IN ("
                                + ", ".join("?" for _ in chunk) + ")", chunk) or []
        for row in rows:
            found[row["inv_id"]] = dict(row)
    return found

"""ai_invoke_runs CRUD (group 0359 DB0008).

One row = one finished AI-invoke hop, written exactly once at finalize (upsert on
run_id -- see L0007 2.10.1 persist_run_record). Live runs never appear here; they
stay in server memory until they finish (DB0008 1.1). Callers pass native Python
values for the array fields (reached_doc_ids / fallback_history / register_errors)
-- this module owns JSON (de)serialization and the write-time value caps from
DB0008 2.4, so a read always yields back the same shape it was given, or [] on a
corrupt/legacy value (DB0008 5.1 invariant 10).

0446 T0016 (migration 086) adds the exit diagnostics the same way: the timeout kind,
its one-line human reading, the two output tails and the unfinished-source path list.
Before it, all four existed only on the in-memory run, so a restarted process could
not tell "worked and ran out of clock" from "hung" for the hop it just lost.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from .connection import get_store, iso_days_ago

# DB0008 2.4 write-time caps (MySQL TEXT is 65,535 bytes; truncate rather than fail
# outright, and mark what was dropped instead of silently shrinking the history).
_REACHED_DOC_IDS_MAX_ITEMS = 200
_HISTORY_MAX_ITEMS = 20
_HISTORY_MAX_SERIALIZED_BYTES = 16384

# 0446 T0016 2 and 5.2: the write-side floor for the exit diagnostics. The tails arrive
# already cut to ai_invoke_service.OUTPUT_TAIL_BYTES and finalize already hands over at
# most 20 paths -- these are not a second, looser budget but the same one restated where
# the row is actually built, so no other caller can widen either of them.
_OUTPUT_TAIL_MAX_CHARS = 8192
_SOURCE_DIRTY_FILES_MAX_ITEMS = 20

# DB0008 3.7: no scheduler exists in this deployment, so retention is swept from the
# write path -- at most once a day, mirroring the _cleanup_retained_scratches(project_id)
# precedent.
_PURGE_INTERVAL_SEC = 24 * 60 * 60
_RETENTION_DAYS = 90
_PURGE_BATCH_LIMIT = 1000

# Column order mirrors DB0008 4 Q5, minus `status` (always the literal 'finished',
# spliced in at _STATUS_INSERT_AT) and `run_id` itself (the upsert conflict key).
_BOUND_COLUMNS = (
    "run_id", "group_id", "project_id", "doc_ref", "mode",
    "outcome", "docs_reached", "docs_target", "reached_doc_ids",
    "end_reason", "stop_code", "stop_reason", "resumable", "exit_code",
    "last_message", "last_message_excerpt",
    "provider_id", "provider_name", "attempt_no", "attempts_used", "attempts_max",
    "fallback_history", "register_errors", "tool_call_misses", "turn_limit_exhausted",
    "oracle_mismatch", "source_dirty", "scratch_retained", "hop_item_seq",
    "token_id", "issued_to", "started_at", "finished_at", "duration_ms",
    "timeout_sec", "deadline_at",
    # ── 0406 T0022 items 3 and 5 (migration 080) ────────────────────────────
    # Who ran this hop, what the server handled instead, and whether the user's handoff note
    # actually made it into the prompt. The text is not stored — only kind, length and sha256.
    "worker_document_type",
    "continuation_instruction_mode_requested",
    "continuation_instruction_mode_normalized",
    "continuation_instruction_mode_fallback_applied",
    "auto_handled_item_seqs",
    "prompt_message_source",
    "prompt_common_default_applied",
    "prompt_user_message_length",
    "prompt_user_message_sha256",
    "prompt_final_length",
    "prompt_final_sha256",
    # -- 0446 T0016 2 (migration 086) --------------------------------------
    # Why this hop's clock ran out, said once for a machine (timeout_kind) and once for a
    # person (timeout_diagnosis), plus what the worker last printed and which files it left
    # behind. `timeout_kind` NULL on a timeout row is not "unknown kind" -- it means the
    # watchdog left no mark at all (a legacy row, or a plain communicate() expiry).
    "timeout_kind", "timeout_diagnosis", "stdout_tail", "stderr_tail",
    "source_dirty_files",
    "created_at", "updated_at",
)
_STATUS_INSERT_AT = 5  # after (run_id, group_id, project_id, doc_ref, mode)

_ARRAY_FIELDS = ("reached_doc_ids", "fallback_history", "register_errors",
                 "auto_handled_item_seqs", "source_dirty_files")
_BOOL_FIELDS = ("resumable", "turn_limit_exhausted", "oracle_mismatch",
                "continuation_instruction_mode_fallback_applied",
                "prompt_common_default_applied")
_NULLABLE_BOOL_FIELDS = ("source_dirty",)

_last_purge_mono: Optional[float] = None


def _dump_array(field: str, value: Any) -> Optional[str]:
    """Serialize an array field, applying its DB0008 2.4 cap. None passes through."""
    if value is None:
        return None
    items = list(value)

    if field in ("reached_doc_ids", "auto_handled_item_seqs"):
        if len(items) > _REACHED_DOC_IDS_MAX_ITEMS:
            items = items[-_REACHED_DOC_IDS_MAX_ITEMS:]  # drop the oldest (head)
        return json.dumps(items, ensure_ascii=False)

    if field == "source_dirty_files":
        # Capped at the HEAD, unlike the two histories above: finalize sorts the spilled
        # paths and keeps the first 20, so the 20 a reader gets back have to be that same
        # first 20. Taking the tail here would answer a different question than the run did.
        return json.dumps(items[:_SOURCE_DIRTY_FILES_MAX_ITEMS], ensure_ascii=False)

    # fallback_history / register_errors: item cap, then byte cap, oldest dropped
    # first, with the drop count recorded at the head so a truncated history reads
    # as truncated instead of "that's all there ever was" (DB0008 2.4).
    dropped = max(0, len(items) - _HISTORY_MAX_ITEMS)
    if dropped:
        items = items[dropped:]
    while items and len(json.dumps(items, ensure_ascii=False).encode("utf-8")) > _HISTORY_MAX_SERIALIZED_BYTES:
        items.pop(0)
        dropped += 1
    if dropped:
        items = [{"reason": "truncated", "dropped": dropped}, *items]
    return json.dumps(items, ensure_ascii=False)


def _clip_tail(value: Any) -> Optional[str]:
    """Last `_OUTPUT_TAIL_MAX_CHARS` characters, or None. Never widens what it was given."""
    if value is None:
        return None
    return str(value)[-_OUTPUT_TAIL_MAX_CHARS:]


def _load_array(raw: Any) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _row_to_payload(row: dict) -> dict:
    payload = dict(row)
    for field in _ARRAY_FIELDS:
        payload[field] = _load_array(row.get(field))
    for field in _BOOL_FIELDS:
        payload[field] = bool(row.get(field))
    for field in _NULLABLE_BOOL_FIELDS:
        raw = row.get(field)
        payload[field] = None if raw is None else bool(raw)
    return payload


def upsert(row: dict[str, Any]) -> None:
    """Persist a finished run -- one call per run_id, ever (L0007 2.10.1).

    `row` carries native values for the array fields; this function handles JSON
    encoding and the DB0008 2.4 truncation rules. `created_at` is bound on INSERT
    only and left out of the DO UPDATE SET clause, so a repeat call (there should
    never be one) cannot move the row's original creation time (DB0008 4 Q5).
    """
    values = {
        "run_id": row["run_id"],
        "group_id": row["group_id"],
        "project_id": row["project_id"],
        "doc_ref": row["doc_ref"],
        "mode": row["mode"],
        "outcome": row.get("outcome"),
        "docs_reached": row.get("docs_reached", 0),
        "docs_target": row.get("docs_target"),
        "reached_doc_ids": _dump_array("reached_doc_ids", row.get("reached_doc_ids")),
        "end_reason": row.get("end_reason"),
        "stop_code": row.get("stop_code"),
        "stop_reason": row.get("stop_reason"),
        "resumable": 1 if row.get("resumable") else 0,
        "exit_code": row.get("exit_code"),
        "last_message": row.get("last_message"),
        "last_message_excerpt": row.get("last_message_excerpt"),
        "provider_id": row.get("provider_id"),
        "provider_name": row.get("provider_name"),
        "attempt_no": row.get("attempt_no", 0),
        "attempts_used": row.get("attempts_used", 0),
        "attempts_max": row.get("attempts_max"),
        "fallback_history": _dump_array("fallback_history", row.get("fallback_history")),
        "register_errors": _dump_array("register_errors", row.get("register_errors")),
        "tool_call_misses": row.get("tool_call_misses", 0),
        "turn_limit_exhausted": 1 if row.get("turn_limit_exhausted") else 0,
        "oracle_mismatch": 1 if row.get("oracle_mismatch") else 0,
        "source_dirty": (
            None if row.get("source_dirty") is None else (1 if row.get("source_dirty") else 0)
        ),
        "scratch_retained": row.get("scratch_retained"),
        "hop_item_seq": row.get("hop_item_seq"),
        "token_id": row.get("token_id"),
        "issued_to": row.get("issued_to"),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row.get("duration_ms"),
        "timeout_sec": row.get("timeout_sec"),
        "deadline_at": row.get("deadline_at"),
        # ── 0406 T0022 items 3 and 5 (migration 080) ───────────────────────
        "worker_document_type": row.get("worker_document_type"),
        "continuation_instruction_mode_requested": row.get(
            "continuation_instruction_mode_requested"
        ),
        "continuation_instruction_mode_normalized": row.get(
            "continuation_instruction_mode_normalized"
        ),
        "continuation_instruction_mode_fallback_applied": (
            1 if row.get("continuation_instruction_mode_fallback_applied") else 0
        ),
        "auto_handled_item_seqs": _dump_array(
            "auto_handled_item_seqs", row.get("auto_handled_item_seqs")
        ),
        "prompt_message_source": row.get("prompt_message_source"),
        "prompt_common_default_applied": (
            1 if row.get("prompt_common_default_applied") else 0
        ),
        "prompt_user_message_length": int(row.get("prompt_user_message_length") or 0),
        "prompt_user_message_sha256": row.get("prompt_user_message_sha256"),
        "prompt_final_length": int(row.get("prompt_final_length") or 0),
        "prompt_final_sha256": row.get("prompt_final_sha256"),
        # -- 0446 T0016 3.2: every caller that has no diagnostics -- a spawn failure, an
        # API-mode run, the orphaned-lease record written from a bare lease row -- reaches
        # here through .get() and stores NULL. Absence is a value, and it must never be the
        # reason a finished hop fails to leave a row behind.
        "timeout_kind": row.get("timeout_kind"),
        "timeout_diagnosis": row.get("timeout_diagnosis"),
        "stdout_tail": _clip_tail(row.get("stdout_tail")),
        "stderr_tail": _clip_tail(row.get("stderr_tail")),
        "source_dirty_files": _dump_array("source_dirty_files", row.get("source_dirty_files")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    ordered_values = [values[col] for col in _BOUND_COLUMNS]

    columns_sql = ", ".join((
        *_BOUND_COLUMNS[:_STATUS_INSERT_AT], "status", *_BOUND_COLUMNS[_STATUS_INSERT_AT:],
    ))
    placeholders_sql = ", ".join((
        *(["?"] * _STATUS_INSERT_AT), "'finished'",
        *(["?"] * (len(_BOUND_COLUMNS) - _STATUS_INSERT_AT)),
    ))
    update_clause = ", ".join(
        f"{col} = excluded.{col}" for col in _BOUND_COLUMNS if col not in ("run_id", "created_at")
    )
    get_store()._execute(
        f"INSERT INTO ai_invoke_runs ({columns_sql}) VALUES ({placeholders_sql}) "
        f"ON CONFLICT(run_id) DO UPDATE SET {update_clause}",
        ordered_values,
    )


def max_serial_for_date(date_str: str) -> int:
    """Highest NNNNNN serial already used by a FINISHED run whose run_id starts with
    ``aiv_<date_str>_`` (0401 NR0003 §4 / T0004 item 7).

    Paired with group_ai_leases.max_serial_for_date (which covers a run still open,
    with no row here yet) to floor the in-memory run-id counter after a restart --
    otherwise a fresh process reissues a serial an earlier process already used
    today, and upsert() being keyed on run_id means the earlier row is silently
    overwritten at the next finalize.
    """
    rows = get_store()._fetch_all(
        "SELECT run_id FROM ai_invoke_runs WHERE run_id LIKE ?", [f"aiv_{date_str}_%"]
    )
    highest = 0
    for row in rows:
        try:
            highest = max(highest, int(str(row["run_id"]).rsplit("_", 1)[-1]))
        except (TypeError, ValueError):
            continue
    return highest


def get(run_id: str) -> Optional[dict]:
    """Detail lookup (DB0008 4 Q1) -- only consulted when the run is not in memory."""
    row = get_store()._fetch_one(
        "SELECT * FROM ai_invoke_runs WHERE run_id = ?", [run_id]
    )
    return _row_to_payload(row) if row is not None else None


def latest_finished_for_group(group_id: str, doc_ref: Optional[str] = None) -> Optional[dict]:
    """The ONE most recent finished run of a group (0446 T0016 4.1).

    Ordered exactly like :func:`list_by_group` -- newest `started_at`, then `run_id` as the
    tie-break -- and limited to one row on purpose. The caller (the rework prompt) asks
    "how did the run right before this one end?", so a row that is not the newest one must
    not be able to answer: searching backwards for an older timeout would hand a worker a
    handoff from a run that has already been superseded by a clean one.

    `doc_ref`, when given, additionally pins the row to the same document, so a group whose
    last run was on a different document does not leak that run's unfinished files here.
    """
    if doc_ref:
        row = get_store()._fetch_one(
            "SELECT * FROM ai_invoke_runs WHERE group_id = ? AND doc_ref = ? "
            "ORDER BY started_at DESC, run_id DESC LIMIT 1",
            [group_id, doc_ref],
        )
    else:
        row = get_store()._fetch_one(
            "SELECT * FROM ai_invoke_runs WHERE group_id = ? "
            "ORDER BY started_at DESC, run_id DESC LIMIT 1",
            [group_id],
        )
    return _row_to_payload(row) if row is not None else None


def list_by_group(group_id: str, limit: int) -> list[dict]:
    """Newest-first page for a group (DB0008 4 Q2).

    The caller pads `limit` with the live-run count before merging with in-memory
    runs, so a page never shrinks just because some of its slots are still running
    (L0007 2.10.3).
    """
    rows = get_store()._fetch_all(
        "SELECT * FROM ai_invoke_runs WHERE group_id = ? "
        "ORDER BY started_at DESC, run_id DESC LIMIT ?",
        [group_id, limit],
    )
    return [_row_to_payload(row) for row in rows]


def list_by_project(project_id: str, limit: int) -> list[dict]:
    """Newest-first page for a project (DB0008 4 Q3)."""
    rows = get_store()._fetch_all(
        "SELECT * FROM ai_invoke_runs WHERE project_id = ? "
        "ORDER BY started_at DESC, run_id DESC LIMIT ?",
        [project_id, limit],
    )
    return [_row_to_payload(row) for row in rows]


def count_by_group(group_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM ai_invoke_runs WHERE group_id = ?", [group_id]
    )
    return row["cnt"] if row else 0


def count_by_project(project_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM ai_invoke_runs WHERE project_id = ?", [project_id]
    )
    return row["cnt"] if row else 0


def purge_older_than(cutoff_iso: str, limit: int = _PURGE_BATCH_LIMIT) -> None:
    """Delete the oldest finished runs past `cutoff_iso`, capped at `limit` per call
    (DB0008 3.7, 4 Q6).

    The victim id list is wrapped in a derived table because MySQL refuses to
    reference the delete target directly inside its own subquery; SQLite and
    PostgreSQL tolerate the extra wrapping the same way.
    """
    get_store()._execute(
        "DELETE FROM ai_invoke_runs WHERE run_id IN ("
        "  SELECT run_id FROM ("
        "    SELECT run_id FROM ai_invoke_runs WHERE finished_at < ? "
        "    ORDER BY finished_at LIMIT ?"
        "  ) AS victims"
        ")",
        [cutoff_iso, limit],
    )


def maybe_purge() -> None:
    """Sweep rows past the 90-day retention window, at most once per 24h process-local
    (DB0008 3.7 -- this deployment has no scheduler, so retention rides the write path).

    Call right after `upsert()`. Exceptions are swallowed: a failed sweep must never
    affect the save that triggered it.
    """
    global _last_purge_mono
    now_mono = time.monotonic()
    if _last_purge_mono is not None and (now_mono - _last_purge_mono) < _PURGE_INTERVAL_SEC:
        return
    _last_purge_mono = now_mono
    try:
        purge_older_than(iso_days_ago(_RETENTION_DAYS))
    except Exception:
        pass

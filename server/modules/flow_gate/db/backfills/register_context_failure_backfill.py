"""Expand legacy ``ai_invoke_runs.register_errors`` into register_context_failures rows.

flowgate.default.0492 T0018 item 4.

Why this is Python and not DML inside migration 094: the source column is a JSON array in a
TEXT column, and unrolling it in SQL needs ``jsonb_array_elements`` / ``JSON_TABLE`` /
``json_each`` — three incompatible spellings, one per dialect. B0091 already paid for that
lesson once (the SQLite-only ``037_rejection_id_backfill.sql`` blocked startup outright on
MariaDB/PostgreSQL), so the transform lives here, beside
:mod:`~modules.flow_gate.db.backfills.rejection_id_backfill`, and runs identically on every
backend after the schema migrations.

Two rules the T is explicit about:

* **Nothing is invented.** A pre-T0018 element recorded ``{"status", "reason", "turn"}`` and
  no axis. Those four fields are preserved and the row is labelled
  ``boundary='legacy_unclassified'`` with NULL axes — writing ``axis='action'`` because the
  incident happened to be an action mismatch would turn a guess into evidence.
* **The source is not touched.** ``register_errors`` stays exactly as it was, as the
  backfill source and as the rollback safety net.

Idempotent: each element's correlation id is derived from (run_id, index, payload), so a
second run produces the same (correlation_id, boundary) keys and inserts nothing. A row
whose JSON, timestamp or shape is unusable is skipped with a log line — one bad row must
never abort a deployment.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from ..dialect import SQLITE, translate
from ..register_context_failures import (
    AXES,
    BOUNDARY_LEGACY,
    INSERT_SQL,
    RegisterFailureError,
    bind_row,
)

logger = logging.getLogger(__name__)

_SELECT_RUNS = (
    "SELECT run_id, project_id, group_id, doc_ref, register_errors, updated_at, created_at "
    "FROM ai_invoke_runs "
    "WHERE register_errors IS NOT NULL AND register_errors <> '' AND register_errors <> '[]'"
)
_SELECT_EXISTING = "SELECT correlation_id, boundary FROM register_context_failures"

_LIVE_BOUNDARIES = ("register_dispatch", "inbox")


def legacy_correlation_id(run_id: str, index: int, element: Any) -> str:
    """Deterministic id for an element that never had one.

    Derived from the run, the array position and the element's own payload, so re-running
    the backfill reproduces it exactly, while two different elements at the same position of
    two different runs never collide.
    """
    try:
        payload = json.dumps(element, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = repr(element)
    digest = hashlib.sha256(f"{run_id}\x1f{index}\x1f{payload}".encode("utf-8")).hexdigest()
    return f"legacy_{digest[:32]}"


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _legacy_notes(element: dict) -> str:
    """Everything the element carried that has no column of its own, kept verbatim."""
    keep = {k: v for k, v in element.items() if k not in ("status", "reason", "turn")}
    return json.dumps({"legacy_source": "ai_invoke_runs.register_errors", "fields": keep},
                      ensure_ascii=False)


def plan_rows(run_row: dict, existing: set[tuple[str, str]]) -> list[dict]:
    """Rows for one ai_invoke_runs record. Returns [] for anything unusable."""
    run_id = _text(run_row.get("run_id"))
    if not run_id:
        return []
    raw = run_row.get("register_errors")
    try:
        elements = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        logger.warning("register backfill: unparsable register_errors on run %s", run_id)
        return []
    if not isinstance(elements, list):
        return []

    recorded_at = (
        _text(run_row.get("updated_at")) or _text(run_row.get("created_at")) or "1970-01-01T00:00:00+09:00"
    )
    project_run = _text(run_row.get("project_id"))
    group_run = _text(run_row.get("group_id"))
    doc_ref_run = _text(run_row.get("doc_ref"))
    if not (project_run and group_run and doc_ref_run):
        logger.warning("register backfill: run %s has no project/group/doc to anchor", run_id)
        return []

    rows: list[dict] = []
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        # A truncation marker is bookkeeping about the array, not a failure that happened.
        if element.get("reason") == "truncated" and "dropped" in element:
            continue

        axis = element.get("axis") if element.get("axis") in AXES else None
        axes = element.get("axes")
        axes = list(axes) if isinstance(axes, list) and axes and all(a in AXES for a in axes) else None
        if axis is None or axes is None or axes[0] != axis:
            axis, axes = None, None

        boundary = element.get("boundary")
        if axis is None or boundary not in _LIVE_BOUNDARIES:
            # No axis, or no boundary we can trust: the honest label, not a fabricated one.
            boundary, axis, axes = BOUNDARY_LEGACY, None, None

        correlation_id = _text(element.get("correlation_id")) or legacy_correlation_id(
            run_id, index, element
        )
        if (correlation_id, boundary) in existing:
            continue
        existing.add((correlation_id, boundary))

        rows.append({
            "recorded_at": _text(element.get("recorded_at")) or recorded_at,
            "run_id": run_id,
            "correlation_id": correlation_id,
            "boundary": boundary,
            "action_scope_run": _text(element.get("action_scope_run")),
            "action_scope_token": _text(element.get("action_scope_token")),
            "action_scope_request": _text(element.get("action_scope_request")),
            "project_run": project_run,
            "project_token": _text(element.get("project_token")),
            "group_run": group_run,
            "group_token_db": _text(element.get("group_token_db")),
            "group_token_resolved": _text(element.get("group_token_resolved")),
            "doc_ref_run": doc_ref_run,
            "doc_ref_token": _text(element.get("doc_ref_token")),
            "prev_doc_id_request": _text(element.get("prev_doc_id_request")),
            "target_doc_id_request": _text(element.get("target_doc_id_request")),
            "ai_run_id": run_id,
            "axis_first_mismatch": axis,
            "axes_all_mismatches": axes,
            "token_id_hash": _text(element.get("token_id_hash")),
            "expected_fingerprint": _text(element.get("expected_fingerprint")),
            "actual_fingerprint": _text(element.get("actual_fingerprint")),
            "binding_relaxed": False,
            "relaxed_axis": None,
            "status": _int(element.get("status")),
            "code": _text(element.get("code")),
            "reason": _text(element.get("reason")),
            "turn": _int(element.get("turn")),
            "notes": _legacy_notes(element),
        })
    return rows


def plan_all(run_rows: list[dict], existing: set[tuple[str, str]]) -> list[list[Any]]:
    """Bound value lists for every backfillable element, skipping what cannot be stored."""
    bound: list[list[Any]] = []
    for run_row in run_rows:
        try:
            rows = plan_rows(run_row, existing)
        except Exception:
            logger.warning("register backfill: run row skipped", exc_info=True)
            continue
        for row in rows:
            try:
                bound.append(bind_row(row))
            except RegisterFailureError as exc:
                logger.warning("register backfill: row refused (%s)", exc)
    return bound


def run_register_context_failure_backfill(db_instance) -> int:
    """Apply the backfill once. Returns the number of rows offered to the DB.

    ``db_instance`` is the live sqloader DB instance (config.DatabaseSetting.db_instance).
    """
    dialect = getattr(db_instance, "db_type", None) or SQLITE

    def q(sql: str) -> str:
        return translate(sql, dialect)

    run_rows = [dict(row) for row in (db_instance.fetch_all(q(_SELECT_RUNS), []) or [])]
    if not run_rows:
        return 0
    existing = {
        (str(dict(row)["correlation_id"]), str(dict(row)["boundary"]))
        for row in (db_instance.fetch_all(q(_SELECT_EXISTING), []) or [])
    }

    bound = plan_all(run_rows, existing)
    if not bound:
        return 0
    statement = q(INSERT_SQL)
    with db_instance.begin_transaction() as txn:
        for values in bound:
            txn.execute(statement, values)
    return len(bound)

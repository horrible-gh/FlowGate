"""register_context_failures CRUD (flowgate.default.0492 DB0011 §2.1 / T0018 item 3).

One row per (correlation_id, boundary): the axis-classified form of a context-binding 403
that used to survive only as free text inside ``ai_invoke_runs.register_errors``.

Two facts shape this module:

* the FK is ``run_id`` TEXT -> ``ai_invoke_runs(run_id)``, and a live run has no row there
  yet, so rows are written by the finalize flow immediately after ``ai_invoke_runs.upsert()``
  succeeds — never mid-turn. Until then the live run's ``register_errors`` list is the SSOT;
* ``axes_all_mismatches`` is stored as serialized JSON TEXT, the same convention the other
  array columns in this schema use, so no dialect gets a different meaning out of it. The
  guarantees DB0011 wanted from JSONB (non-empty array, allowed members only, first element
  equal to ``axis_first_mismatch``) are enforced here, on every dialect, by :func:`_validate`.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

from .connection import get_store

logger = logging.getLogger(__name__)

AXES = ("action", "project", "group", "doc")
BOUNDARY_LEGACY = "legacy_unclassified"
BOUNDARIES = ("register_dispatch", "inbox", BOUNDARY_LEGACY)

# Insert order. `id` is generated and deliberately absent.
COLUMNS = (
    "recorded_at", "run_id", "correlation_id", "boundary",
    "action_scope_run", "action_scope_token", "action_scope_request",
    "project_run", "project_token",
    "group_run", "group_token_db", "group_token_resolved",
    "doc_ref_run", "doc_ref_token", "prev_doc_id_request", "target_doc_id_request",
    "ai_run_id", "axis_first_mismatch", "axes_all_mismatches",
    "token_id_hash", "expected_fingerprint", "actual_fingerprint",
    "binding_relaxed", "relaxed_axis",
    "status", "code", "reason", "turn", "notes",
)

_REQUIRED = ("recorded_at", "run_id", "correlation_id", "boundary",
             "project_run", "group_run", "doc_ref_run")

_LIVE_REQUIRED = ("action_scope_run", "action_scope_token", "project_token",
                  "group_token_resolved", "doc_ref_token")

_NOTES_MAX_CHARS = 4000


class RegisterFailureError(ValueError):
    """A row that would violate the table's contract, refused before it reaches the driver."""


def _validate(row: dict) -> None:
    for column in _REQUIRED:
        if row.get(column) in (None, ""):
            raise RegisterFailureError(f"{column} is required")
    boundary = row["boundary"]
    if boundary not in BOUNDARIES:
        raise RegisterFailureError(f"boundary must be one of {BOUNDARIES}")

    axis = row.get("axis_first_mismatch")
    axes = row.get("axes_all_mismatches")
    if boundary == BOUNDARY_LEGACY and axis is None:
        if axes not in (None, [], ()):
            raise RegisterFailureError("an unclassified legacy row cannot carry axes")
        return

    # Everything below is what DB0011 wanted JSONB to guarantee, restated so that SQLite
    # and MySQL enforce it too.
    if axis not in AXES:
        raise RegisterFailureError(f"axis_first_mismatch must be one of {AXES}")
    if not isinstance(axes, (list, tuple)) or not axes:
        raise RegisterFailureError("axes_all_mismatches must be a non-empty array")
    unknown = [value for value in axes if value not in AXES]
    if unknown:
        raise RegisterFailureError(f"axes_all_mismatches contains unknown axes: {unknown}")
    if axes[0] != axis:
        raise RegisterFailureError("axes_all_mismatches[0] must equal axis_first_mismatch")
    if boundary != BOUNDARY_LEGACY:
        for column in _LIVE_REQUIRED:
            if row.get(column) in (None, ""):
                raise RegisterFailureError(f"{column} is required on a {boundary} row")
    if row.get("binding_relaxed") and row.get("relaxed_axis") != "doc":
        raise RegisterFailureError("binding_relaxed requires relaxed_axis='doc'")
    if row.get("relaxed_axis") is not None and not row.get("binding_relaxed"):
        raise RegisterFailureError("relaxed_axis requires binding_relaxed")


def _bind(row: dict) -> list[Any]:
    axes = row.get("axes_all_mismatches")
    values: list[Any] = []
    for column in COLUMNS:
        value = row.get(column)
        if column == "axes_all_mismatches":
            value = None if axes in (None, [], ()) else json.dumps(list(axes), ensure_ascii=False)
        elif column == "binding_relaxed":
            value = 1 if value else 0
        elif column in ("status", "turn"):
            value = None if value is None else int(value)
        elif column == "notes" and value is not None:
            value = str(value)[:_NOTES_MAX_CHARS]
        values.append(value)
    return values


# SQLite-dialect canonical form; db.dialect.translate() turns the ON CONFLICT clause into
# MySQL's INSERT IGNORE and leaves it alone on PostgreSQL.
INSERT_SQL = (
    f"INSERT INTO register_context_failures ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join(['?'] * len(COLUMNS))}) "
    f"ON CONFLICT (correlation_id, boundary) DO NOTHING"
)


def bind_row(row: dict) -> list[Any]:
    """Validate one row and return its bound values in :data:`COLUMNS` order.

    Exposed so the boot-time backfill can drive :data:`INSERT_SQL` on the live connection
    it already holds, instead of growing a second copy of the column contract.
    """
    _validate(row)
    return _bind(row)


def insert_many(rows: Iterable[dict]) -> int:
    """Insert validated rows, skipping any (correlation_id, boundary) already stored.

    ``ON CONFLICT DO NOTHING`` is the idempotency: a finalize retry, a second upsert of the
    same run, and a re-run of the legacy backfill all land on the same unique key and add
    nothing. Returns the number of rows offered (not the number the DB kept), because
    neither MySQL's INSERT IGNORE nor a multi-dialect rowcount can be trusted to say which.
    """
    prepared = [bind_row(row) for row in rows]
    if not prepared:
        return 0
    store = get_store()
    for values in prepared:
        store._execute(INSERT_SQL, values)
    return len(prepared)


def _row_to_payload(row: dict) -> dict:
    payload = dict(row)
    raw = row.get("axes_all_mismatches")
    if raw in (None, ""):
        payload["axes_all_mismatches"] = []
    else:
        try:
            loaded = json.loads(raw)
        except (TypeError, ValueError):
            loaded = []
        payload["axes_all_mismatches"] = loaded if isinstance(loaded, list) else []
    payload["binding_relaxed"] = bool(row.get("binding_relaxed"))
    return payload


def list_by_run(run_id: str, limit: int = 100) -> list[dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM register_context_failures WHERE run_id = ? "
        "ORDER BY recorded_at, id LIMIT ?",
        [run_id, limit],
    )
    return [_row_to_payload(row) for row in rows]


def existing_keys(run_id: str) -> set[tuple[str, str]]:
    """The (correlation_id, boundary) pairs already stored for a run — what the backfill
    consults so a re-run neither duplicates nor relies on catching a driver error."""
    rows = get_store()._fetch_all(
        "SELECT correlation_id, boundary FROM register_context_failures WHERE run_id = ?",
        [run_id],
    )
    return {(str(row["correlation_id"]), str(row["boundary"])) for row in rows}


def count_for_run(run_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM register_context_failures WHERE run_id = ?", [run_id]
    )
    return int(row["cnt"]) if row else 0


def rows_from_run_errors(
    run_id: str,
    register_errors: Iterable[Any],
    *,
    recorded_at: str,
    fallback: Optional[dict] = None,
) -> list[dict]:
    """Turn a run's live ``register_errors`` list into insertable rows.

    Only elements carrying a ``telemetry`` block (i.e. produced by
    :mod:`modules.flow_gate.services.register_binding`) become rows — an ordinary 409/422
    registration failure has no axis and must not be given one. ``fallback`` supplies the
    run-side project/group/doc for the NOT NULL columns.
    """
    fallback = fallback or {}
    out: list[dict] = []
    for element in register_errors or []:
        if not isinstance(element, dict):
            continue
        telemetry = element.get("telemetry")
        if not isinstance(telemetry, dict) or not telemetry.get("correlation_id"):
            continue
        row = dict(telemetry)
        row["run_id"] = run_id
        row.setdefault("recorded_at", recorded_at)
        row["recorded_at"] = row.get("recorded_at") or recorded_at
        for column, key in (("project_run", "project_id"), ("group_run", "group_id"),
                            ("doc_ref_run", "doc_ref")):
            if not row.get(column):
                row[column] = fallback.get(key)
        out.append(row)
    return out

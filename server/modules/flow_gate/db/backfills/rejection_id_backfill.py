"""Dialect-agnostic replacement for migration 037_rejection_id_backfill.sql.

B0091 / T0004. The SQL form of this backfill was authored in SQLite-only JSON DML
(``json_each`` table-valued function, the virtual ``key``/``value`` columns,
``json_group_array``, ``json_array_length`` and ``||`` concatenation). None of
those translate to MariaDB/PostgreSQL, so the converted ``mysql``/``postgres``
migrations failed at parse time (error 1064 near the reserved word ``key``) and
blocked startup on those backends entirely (NR0003).

The transform is therefore moved here, where it runs once at startup after the
schema migrations against the live connection — identical on every dialect. The
matching ``sqlite/037`` migration is kept for already-migrated SQLite databases;
this backfill is idempotent, so running both on SQLite is harmless.

Backfill rule (identical to ``workflow.rejection_identity.legacy_rejection_id``):
each rejection_history item lacking a ``rejection_id`` gets
``rej_legacy_<YYYYMMDDHHMMSS>_<index>`` where ``index`` is the **0-based** array
position — the exact value SQLite's ``json_each.key`` produced — so ids stay
identical to any already assigned on a SQLite deployment. Idempotent: items that
already carry a ``rejection_id`` (and the four response fields) are left
untouched, so re-running on every boot is a no-op.
"""
from __future__ import annotations

import json

from ..dialect import SQLITE, translate
from ...workflow.rejection_identity import legacy_rejection_id

# The four response fields introduced alongside the rejection_id (P0005 / T0006).
# They are initialised to NULL when absent and preserved when present.
_RESPONSE_FIELDS = (
    "ai_response",
    "responded_at",
    "response_recorded_by",
    "response_revision_no",
)

_SELECT = (
    "SELECT doc_id, rejection_history FROM documents "
    "WHERE rejection_history IS NOT NULL AND rejection_history <> '[]'"
)
_UPDATE = "UPDATE documents SET rejection_history = ? WHERE doc_id = ?"


def _backfill_items(items: list) -> bool:
    """Mutate ``items`` in place; return True if anything changed.

    A change is: assigning a missing rejection_id, or initialising a missing
    response field to None. Existing keys (including any not in the canonical
    set) are preserved — safer than the SQL's rebuild-and-drop while keeping the
    same id / response-field semantics and idempotency.
    """
    changed = False
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if not item.get("rejection_id"):
            item["rejection_id"] = legacy_rejection_id(item.get("rejected_at"), index)
            changed = True
        for field in _RESPONSE_FIELDS:
            if field not in item:
                item[field] = None
                changed = True
    return changed


def run_rejection_id_backfill(db_instance) -> int:
    """Apply the rejection_id backfill once. Returns the number of rows updated.

    ``db_instance`` is the live sqloader DB instance (config.DatabaseSetting.
    db_instance). SQL is translated for the resolved dialect via db.dialect, so
    the same code path works on SQLite / MariaDB / PostgreSQL.
    """
    dialect = getattr(db_instance, "db_type", None) or SQLITE

    def q(sql: str) -> str:
        return translate(sql, dialect)

    rows = db_instance.fetch_all(q(_SELECT), []) or []

    pending: list[tuple[str, str]] = []
    for row in rows:
        d = row if isinstance(row, dict) else dict(row)
        raw = d.get("rejection_history")
        if not raw:
            continue
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(items, list) or not items:
            continue
        if _backfill_items(items):
            pending.append((d["doc_id"], json.dumps(items, ensure_ascii=False)))

    if not pending:
        return 0

    with db_instance.begin_transaction() as txn:
        for doc_id, payload in pending:
            txn.execute(q(_UPDATE), [payload, doc_id])
    return len(pending)

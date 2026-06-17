"""B0091 / TR — tests for the dialect-agnostic rejection_id backfill.

The backfill replaces the SQLite-only migration 037 (json_each/json_group_array
/`||`/`key`) that failed to apply on MariaDB/PostgreSQL. These tests pin:
  * the transform (id assignment rule, 0-based index, response-field init),
  * idempotency (re-run is a no-op),
  * SQLite id parity with workflow.rejection_identity.legacy_rejection_id,
  * the converter no-op stubs + guard that prevent the bug class from recurring.
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from modules.flow_gate.db.backfills.rejection_id_backfill import (
    _backfill_items,
    run_rejection_id_backfill,
)
from modules.flow_gate.db.dialect import SQLITE
from modules.flow_gate.workflow.rejection_identity import legacy_rejection_id

_SERVER = Path(__file__).resolve().parents[1]
_MIG = _SERVER / "sql" / "migrations"


# --- fake live DB instance (sqlite-backed; dialect=SQLITE → translate is a no-op) ---

class _Txn:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.execute(sql, params or [])


class FakeDB:
    db_type = SQLITE

    def __init__(self, conn):
        self.conn = conn

    def fetch_all(self, sql, params=None):
        cur = self.conn.execute(sql, params or [])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    @contextmanager
    def begin_transaction(self):
        try:
            yield _Txn(self.conn)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


def _mkdb():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE documents (doc_id TEXT PRIMARY KEY, rejection_history TEXT)")
    return conn


def _insert(conn, doc_id, history):
    conn.execute(
        "INSERT INTO documents (doc_id, rejection_history) VALUES (?, ?)",
        [doc_id, json.dumps(history, ensure_ascii=False) if history is not None else None],
    )
    conn.commit()


def _history(conn, doc_id):
    raw = conn.execute(
        "SELECT rejection_history FROM documents WHERE doc_id=?", [doc_id]
    ).fetchone()[0]
    return json.loads(raw)


# --- transform unit tests ---

def test_backfill_assigns_legacy_id_zero_based():
    items = [
        {"reason": "a", "rejected_at": "2026-06-10T12:34:56+09:00"},
        {"reason": "b", "rejected_at": "2026-06-11T01:02:03+09:00"},
    ]
    changed = _backfill_items(items)
    assert changed is True
    # 0-based index, matching SQLite json_each.key and legacy_rejection_id.
    assert items[0]["rejection_id"] == legacy_rejection_id("2026-06-10T12:34:56+09:00", 0)
    assert items[1]["rejection_id"] == legacy_rejection_id("2026-06-11T01:02:03+09:00", 1)
    assert items[0]["rejection_id"] == "rej_legacy_20260610123456_0"


def test_backfill_initialises_response_fields():
    items = [{"reason": "x", "rejected_at": "2026-01-01T00:00:00Z"}]
    _backfill_items(items)
    for field in ("ai_response", "responded_at", "response_recorded_by", "response_revision_no"):
        assert field in items[0] and items[0][field] is None


def test_backfill_preserves_existing_id_and_is_idempotent():
    items = [{"rejection_id": "rej_keepme", "reason": "x", "rejected_at": "2026-01-01T00:00:00Z",
              "ai_response": "answered", "responded_at": "t", "response_recorded_by": "u",
              "response_revision_no": 2}]
    changed = _backfill_items(items)
    assert changed is False  # nothing missing → no change
    assert items[0]["rejection_id"] == "rej_keepme"
    assert items[0]["ai_response"] == "answered"  # response field preserved


# --- end-to-end backfill over a fake live DB ---

def test_run_backfill_updates_and_is_idempotent():
    conn = _mkdb()
    _insert(conn, "d1", [
        {"reason": "r0", "rejected_at": "2026-06-10T12:34:56+09:00"},
        {"reason": "r1", "rejected_at": "2026-06-11T01:02:03+09:00"},
    ])
    _insert(conn, "empty", [])          # filtered out by WHERE rejection_history <> '[]'
    _insert(conn, "null", None)         # filtered out by WHERE IS NOT NULL
    db = FakeDB(conn)

    n = run_rejection_id_backfill(db)
    assert n == 1  # only d1 changed

    hist = _history(conn, "d1")
    assert hist[0]["rejection_id"] == legacy_rejection_id("2026-06-10T12:34:56+09:00", 0)
    assert hist[1]["rejection_id"] == legacy_rejection_id("2026-06-11T01:02:03+09:00", 1)
    assert hist[0]["response_revision_no"] is None

    # Idempotent: a second run touches nothing.
    assert run_rejection_id_backfill(db) == 0


def test_run_backfill_skips_invalid_json():
    conn = _mkdb()
    conn.execute("INSERT INTO documents (doc_id, rejection_history) VALUES (?, ?)",
                 ["bad", "{not json"])
    conn.commit()
    assert run_rejection_id_backfill(FakeDB(conn)) == 0


# --- artifact / converter-guard regression tests ---

def test_mysql_postgres_037_are_noop_stubs():
    for target in ("mysql", "postgres"):
        text = (_MIG / target / "037_rejection_id_backfill.sql").read_text(encoding="utf-8")
        assert "intentional no-op" in text
        # The *executable* SQL (comment lines stripped) must be only the no-op and
        # carry none of the SQLite-only JSON DML that caused B0091.
        executable = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("--")
        ).strip()
        assert executable == "SELECT 1;"
        for bad in ("json_each", "json_group_array", "json_array_length", "||"):
            assert bad not in executable


def test_converter_guard_detects_unsupported_dml():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "regen_dialect_migrations", _SERVER / "tools" / "regen_dialect_migrations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._migration_id("037_rejection_id_backfill.sql") == "037"
    assert "037" in mod._CODE_HANDLED_IDS
    # The guard regex matches the SQLite-only JSON DML idioms.
    assert mod._UNSUPPORTED_DML.search("SELECT json_each(x)")
    assert mod._UNSUPPORTED_DML.search("json_group_array(y)")
    assert not mod._UNSUPPORTED_DML.search("SELECT json_extract(x, '$.k')")

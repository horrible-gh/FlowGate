"""A minimal FlowGateStore double for tests that drive a route opening a transaction.

flowgate.default.0535 T0007 §3 made ``POST /inbox action=review`` claim its token and
store the review inside ONE ``FlowGateStore.transaction()``. Tests that mock the writes
themselves (``db_reviews.insert_review``, ``token_service.consume``) still need a store
whose ``transaction()`` opens, because the route now goes through one on the way to
those mocks.

``install_null_transaction_store()`` provides exactly that and nothing more: a real
``FlowGateStore`` bound to a backend that opens a transaction, records the statements it
is handed and returns no rows. It is for tests whose subject is validation/routing, not
persistence — a test about what is actually stored, rolled back or claimed must use a
real SQLite-backed store instead (see ``test_review_atomicity_0535.py``).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


class RecordingTxn:
    """A transaction handle that remembers every statement and returns no rows."""

    def __init__(self, statements: list):
        self.statements = statements
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, list(params or [])))
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class NullTransactionDB:
    """Backend stub: transactions open and commit, and nothing is ever stored."""

    db_type = 1  # dialect.SQLITE — translate() stays a no-op

    def __init__(self):
        self.statements: list = []

    @contextmanager
    def begin_transaction(self):
        yield RecordingTxn(self.statements)

    def execute(self, sql, params=None):
        self.statements.append((sql, list(params or [])))
        return RecordingTxn(self.statements)

    def commit(self):
        pass

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        return []


def install_null_transaction_store(monkeypatch) -> NullTransactionDB:
    """Point db.connection.STORE at a NullTransactionDB and return that backend."""
    from modules.flow_gate.db import connection as db_connection

    backend = NullTransactionDB()
    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = backend, None
    monkeypatch.setattr(db_connection, "STORE", store)
    return backend


class _LiveTxn:
    """Transaction handle used by FlowGateStore with a real SQLite connection."""

    def __init__(self, db: "LiveSqliteDB"):
        self._db = db
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._db.conn.execute(sql, params or [])
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(row) for row in self._cur.fetchall()] if self._cur else []


class LiveSqliteDB:
    """sqloader-shaped adapter backed by a migrated SQLite database."""

    db_type = 1  # dialect.SQLITE

    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    @contextmanager
    def begin_transaction(self):
        try:
            yield _LiveTxn(self)
        except BaseException:
            self.conn.rollback()
            raise
        self.conn.commit()

    def execute(self, sql, params=None):
        cursor = self.conn.execute(sql, params or [])
        self.conn.commit()
        return cursor

    def commit(self):
        self.conn.commit()

    def fetch_one(self, sql, params=None):
        row = self.conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(row) for row in self.conn.execute(sql, params or []).fetchall()]


def build_live_sqlite_db(path) -> LiveSqliteDB:
    """Apply every applicable SQLite migration to a fresh file-backed database."""
    db = LiveSqliteDB(path)
    migrations_dir = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"
    for migration in sorted(migrations_dir.glob("*.sql")):
        try:
            db.conn.executescript(migration.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            # Fresh-database convention used by the existing migration tests.
            pass
    db.conn.commit()
    return db


def install_live_sqlite_store(monkeypatch, db: LiveSqliteDB) -> LiveSqliteDB:
    """Point db.connection.STORE at a migrated live SQLite backend."""
    from modules.flow_gate.db import connection as db_connection

    store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
    store._db, store._sq = db, None
    monkeypatch.setattr(db_connection, "STORE", store)
    return db

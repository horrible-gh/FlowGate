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

from contextlib import contextmanager


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

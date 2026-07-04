"""FlowGate DB connection and base store.

Based on the sqloader pattern. Uses get_db_instance() and get_sqloader_instance() from config.py.
When TESTING=1, it runs without external dependencies.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator, Optional

from . import dialect as _dialect

_JST = timezone(timedelta(hours=9))


def now_iso() -> str:
    """Return the current time as a JST ISO 8601 string."""
    return datetime.now(_JST).isoformat(timespec="seconds")


def iso_days_ago(days: int) -> str:
    """Return the JST ISO 8601 string for `days` days before now.

    Used to compute date cutoffs in Python and bind them as parameters, instead
    of relying on the SQLite-only datetime('now', '-N days') / strftime() — which
    are not portable to MariaDB/PostgreSQL (group 0088). The created_at/expires_at
    columns store now_iso() strings, so a same-format JST string compares correctly
    everywhere.
    """
    return (datetime.now(_JST) - timedelta(days=days)).isoformat(timespec="seconds")


def _get_db():
    """Dynamically import get_db_instance() from config.py. Return None when TESTING=1."""
    if os.environ.get("TESTING") == "1":
        return None
    try:
        from config import get_db_instance
        return get_db_instance()
    except ImportError:
        return None


def _get_sq():
    """Dynamically import get_sqloader_instance() from config.py. Return None when TESTING=1."""
    if os.environ.get("TESTING") == "1":
        return None
    try:
        from config import get_sqloader_instance
        return get_sqloader_instance()
    except ImportError:
        return None


_tx_local = threading.local()


class FlowGateStore:
    """sqloader-based DB access store."""

    def __init__(self):
        self._db = _get_db()
        self._sq = _get_sq()

    @property
    def dialect(self) -> int:
        """Resolve the live backend dialect (sqloader db_type code).

        Defaults to SQLITE when absent (TESTING bridge / unknown backend), which
        makes translate() a no-op and preserves the existing SQLite behaviour.
        """
        return getattr(self._db, "db_type", None) or _dialect.SQLITE

    def _tr(self, sql: str) -> str:
        return _dialect.translate(sql, self.dialect)

    @contextmanager
    def transaction(self) -> Iterator["FlowGateStore"]:
        if getattr(_tx_local, "txn", None) is not None:
            yield self
            return
        with self._db.begin_transaction() as txn:
            # L0007 §9: the live sqlite backend opens a fresh connection per call and
            # does NOT default foreign_keys ON, so ON DELETE CASCADE (questions/answers
            # → documents, DB0006 §3.3) would not fire. The transaction connection is
            # the operational write locus, so guarantee the pragma here. SQLite-only:
            # MySQL/PostgreSQL enforce declared FKs natively, and issuing PRAGMA there
            # would raise a syntax error that aborts the PostgreSQL transaction (0088).
            if self.dialect == _dialect.SQLITE:
                try:
                    txn.execute("PRAGMA foreign_keys = ON")
                except Exception:
                    pass
            _tx_local.txn = txn
            try:
                yield self
            finally:
                _tx_local.txn = None

    def with_transaction(self):
        """Alias of transaction()."""
        return self.transaction()

    def _execute(self, sql: str, params=None) -> None:
        sql = self._tr(sql)
        txn = getattr(_tx_local, "txn", None)
        if txn:
            txn.execute(sql, params or [])
        else:
            self._db.execute(sql, params or [])
            if hasattr(self._db, "commit"):
                self._db.commit()

    def _fetch_one(self, sql: str, params=None) -> Optional[dict]:
        sql = self._tr(sql)
        txn = getattr(_tx_local, "txn", None)
        if txn:
            txn.execute(sql, params or [])
            if hasattr(txn, "fetchone"):
                row = txn.fetchone()
            else:
                row = txn.fetch_one()
        elif hasattr(self._db, "fetch_one"):
            row = self._db.fetch_one(sql, params or [])
        else:
            cur = self._db.execute(sql, params or [])
            row = cur.fetchone()
        if row is None:
            return None
        return dict(row) if not isinstance(row, dict) else row

    def _fetch_all(self, sql: str, params=None) -> list[dict]:
        sql = self._tr(sql)
        txn = getattr(_tx_local, "txn", None)
        if txn:
            txn.execute(sql, params or [])
            if hasattr(txn, "fetchall"):
                rows = txn.fetchall()
            else:
                rows = txn.fetch_all()
        elif hasattr(self._db, "fetch_all"):
            rows = self._db.fetch_all(sql, params or [])
        else:
            cur = self._db.execute(sql, params or [])
            rows = cur.fetchall()
        if not rows:
            return rows
        return [dict(r) if not isinstance(r, dict) else r for r in rows]

    def table_exists(self, name: str) -> bool:
        """Dialect-portable check for whether a table exists.

        Replaces direct sqlite_master probes (group 0088). Uses information_schema
        on MySQL/PostgreSQL, sqlite_master on SQLite.
        """
        if self.dialect == _dialect.SQLITE:
            row = self._fetch_one(
                "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name=?",
                [name],
            )
        else:
            # information_schema.tables exists on both MySQL/MariaDB and PostgreSQL.
            row = self._fetch_one(
                "SELECT 1 AS ok FROM information_schema.tables WHERE table_name=?",
                [name],
            )
        return row is not None

    def _sql(self, key: str) -> str:
        sq = getattr(self, "_sq", None)
        if sq is not None:
            sql = sq.load_sql("queries", key)
        else:
            sql = _load_fallback_sql(key)
        return sql.replace("%s", "?")

    def update_cas(
        self,
        table: str,
        row_id: str,
        id_col: str,
        expected_col: str,
        expected_val: str,
        updates: dict,
    ) -> bool:
        """Compare-And-Swap UPDATE.

        expected_col が expected_val と一致する場合のみ更新。
        updated_at を version として使うか、status を期待値として確認後に UPDATE する。
        戻り値: 成功=True (rowcount 未確認; 後続 SELECT で検証推奨)
        """
        if not updates:
            return False
        set_parts = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [expected_val, row_id]
        sql = self._tr(
            f"UPDATE {table} SET {set_parts} "
            f"WHERE {expected_col} = ? AND {id_col} = ?"
        )
        txn = getattr(_tx_local, "txn", None)
        if txn:
            txn.execute(sql, vals)
        else:
            self._db.execute(sql, vals)
        return True


STORE: FlowGateStore | None = None

_FALLBACK_QUERIES: dict | None = None


def _load_fallback_sql(key: str) -> str:
    """Load a query from sql/queries/queries.json when sqloader is absent in tests."""
    global _FALLBACK_QUERIES
    if _FALLBACK_QUERIES is None:
        path = Path(__file__).resolve().parents[3] / "sql" / "queries" / "queries.json"
        _FALLBACK_QUERIES = json.loads(path.read_text(encoding="utf-8"))

    node = _FALLBACK_QUERIES
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"SQL query not found: {key}")
        node = node[part]
    if not isinstance(node, str):
        raise KeyError(f"SQL query does not resolve to text: {key}")
    return node


def get_store() -> FlowGateStore:
    """Return the singleton FlowGateStore."""
    global STORE
    if STORE is None:
        STORE = FlowGateStore()
    return STORE

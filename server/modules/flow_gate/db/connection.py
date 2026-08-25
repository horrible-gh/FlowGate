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
from . import request_cache as _request_cache

_JST = timezone(timedelta(hours=9))

# 0279 T0005: how long a SQLite connection waits for a competing writer's lock
# before raising "database is locked". Python's default is 5s; 15s absorbs the
# document-write bursts an unmanned worker chain produces without masking a
# genuine deadlock (there is none — every writer is a bounded transaction).
_SQLITE_BUSY_TIMEOUT_MS = 15_000


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
                # 0279 T0005 (NR0003 §4, 'SQLite lock contention'): busy_timeout is a
                # per-connection setting and the sqlite backend opens a fresh
                # connection per call, so it reverted to Python's 5s default on
                # every transaction, with no retry anywhere in the codebase. A
                # writer holding the EXCLUSIVE lock past 5s therefore surfaced as
                # a hard "database is locked" error rather than a wait. This is
                # the write locus, so raise the ceiling here.
                #
                # NOTE this is a partial fix. The production connection factory
                # lives in the third-party sqloader package (config.get_db_instance),
                # not in this tree, so read-only connections opened outside a
                # transaction still get the 5s default. See the T0005 report.
                try:
                    txn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
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
        # 0291 P3-1: something changed in this request — discard the whole request-scope cache.
        # Which SELECT this statement affects cannot be known from the SQL string alone, so
        # there is no per-table tracking (request_cache rule 2). Invalidation goes **before**
        # execution because the cache must already be empty even if this raises: the worst
        # outcome is a stale cache masking the effect of a partially applied statement.
        _request_cache.invalidate()
        txn = getattr(_tx_local, "txn", None)
        if txn:
            txn.execute(sql, params or [])
        else:
            self._db.execute(sql, params or [])
            if hasattr(self._db, "commit"):
                self._db.commit()

    def _execute_affected(self, sql: str, params=None) -> int:
        """Execute one write and return its actual affected-row count.

        sqloader's three transaction adapters expose this through the live cursor:
        SQLite returns that cursor from ``execute``; MySQL returns an integer but also
        keeps ``transaction.cursor.rowcount``; PostgreSQL returns ``None`` and keeps
        the count only on that cursor.  Running through ``transaction()`` therefore
        gives one portable contract without dialect-specific SQL such as RETURNING,
        ROW_COUNT(), or GET DIAGNOSTICS.

        The count is read before the transaction context exits and closes its cursor.
        A missing/negative count is not a successful write: callers using this method
        need an exact CAS result, so fail closed instead of guessing from a later read.
        """
        txn = getattr(_tx_local, "txn", None)
        if txn is None:
            with self.transaction():
                return self._execute_affected(sql, params)

        sql = self._tr(sql)
        _request_cache.invalidate()
        result = txn.execute(sql, params or [])
        rowcount = getattr(result, "rowcount", None)
        if rowcount is None:
            rowcount = getattr(getattr(txn, "cursor", None), "rowcount", None)
        if rowcount is None and isinstance(result, int):
            rowcount = result
        try:
            affected = int(rowcount)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("database driver did not expose affected row count") from exc
        if affected < 0:
            raise RuntimeError("database driver returned an unknown affected row count")
        return affected

    def _fetch_one(self, sql: str, params=None) -> Optional[dict]:
        sql = self._tr(sql)
        txn = getattr(_tx_local, "txn", None)
        cached = _request_cache.lookup(sql, params, txn is not None)
        if not _request_cache.is_miss(cached):
            return cached
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
        result = None if row is None else (dict(row) if not isinstance(row, dict) else row)
        _request_cache.store(sql, params, result, txn is not None)
        return result

    def _fetch_all(self, sql: str, params=None) -> list[dict]:
        sql = self._tr(sql)
        txn = getattr(_tx_local, "txn", None)
        cached = _request_cache.lookup(sql, params, txn is not None)
        if not _request_cache.is_miss(cached):
            return cached
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
        result = rows if not rows else [dict(r) if not isinstance(r, dict) else r for r in rows]
        _request_cache.store(sql, params, result, txn is not None)
        return result

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
        _request_cache.invalidate()  # 0291 P3-1: this is a write — same rule as _execute
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

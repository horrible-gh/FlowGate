"""FlowGate DB connection and base store.

Based on the sqloader pattern. Uses get_db_instance() and get_sqloader_instance() from config.py.
When TESTING=1, it runs without external dependencies.
"""
from __future__ import annotations

import json
import logging as _logging
import os
import re
import sqlite3
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


def to_now_iso_tz(value: str) -> str:
    """Normalize an ISO 8601 timestamp string to now_iso()'s tz/format (JST, seconds).

    0545 T0019 (NR0017 Sec.2): classify()/db_receipts.claim() compare stored ISO
    strings against now_iso() lexicographically, so both sides must share the same
    offset and precision or the comparison silently drifts by the offset difference.
    A tz-naive value is treated as UTC -- the exact rule token_service.verify() and
    inspect_for_replay() already apply to stored expires_at values -- so a value this
    function already normalized (or one already in JST/seconds form) round-trips
    unchanged.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_JST).isoformat(timespec="seconds")


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
        """Run the body in one transaction, then drain the after-commit queue.

        Callbacks registered with :func:`after_commit` while this frame is open run
        only after the outermost frame has committed, on a connection that is no
        longer the transaction's. That distinction matters for best-effort side
        writes (the forensic `group_ai_lease_events` appends, 0502 T0004 §10): under
        PostgreSQL a failed statement aborts the whole transaction, so a side write
        that fails *inside* the transaction takes the real mutation down with it even
        when its own exception is caught. Deferring it past the commit keeps a
        forensic write failure from ever changing the outcome it was recording.
        """
        if getattr(_tx_local, "txn", None) is not None:
            yield self
            return
        _tx_local.after_commit = []
        _tx_local.after_rollback = []
        try:
            with self._transaction_frame():
                yield self
        except BaseException:
            # Rolled back: whatever the queued callbacks were going to record describes
            # writes that no longer exist, so drop them instead of running them.
            _tx_local.after_commit = []
            callbacks, _tx_local.after_rollback = list(getattr(_tx_local, "after_rollback", None) or []), []
            for callback in reversed(callbacks):
                try:
                    callback()
                except Exception:  # cleanup is best-effort, with no retry
                    _logging.getLogger(__name__).warning("after-rollback callback failed", exc_info=True)
            raise
        _tx_local.after_rollback = []
        callbacks, _tx_local.after_commit = list(getattr(_tx_local, "after_commit", None) or []), []
        for callback in callbacks:
            # after_commit() is documented as best-effort: a failing callback must not
            # turn an already-committed transaction into a failed request.
            try:
                callback()
            except Exception:  # noqa: BLE001
                _logging.getLogger(__name__).warning(
                    "after-commit callback failed", exc_info=True)

    @contextmanager
    def _transaction_frame(self) -> Iterator["FlowGateStore"]:
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

    def column_exists(self, table: str, column: str) -> bool:
        """Dialect-portable check for one column, like :meth:`table_exists` (flowgate.v02.0113 T#1, DB-1 §3.4).

        A write that binds a column a later migration added to an existing table checks it first: before that
        migration the INSERT would fail and, where the caller swallows the error, the whole row would be lost
        (the 086b precedent in ``ai_invoke_runs``).
        """
        if self.dialect == _dialect.SQLITE:
            row = self._fetch_one("SELECT 1 AS ok FROM pragma_table_info(?) WHERE name = ?", [table, column])
        else:
            row = self._fetch_one(
                "SELECT 1 AS ok FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
                [table, column],
            )
        return row is not None

    def insert_isolated(self, sql: str, params, savepoint: str) -> bool:
        """One INSERT that may hit a unique key, isolated by a savepoint (flowgate.v02.0113 T#1, DB-1 §4.3 CI).

        True = inserted (CI-1 ``SAVEPOINT`` → CI-2 INSERT → CI-3 ``RELEASE``). False = a unique violation
        (CI-4): only this INSERT is undone (``ROLLBACK TO`` + ``RELEASE``), every earlier write of the same
        transaction stays, and the transaction is usable again — also on PostgreSQL, where one failed statement
        otherwise aborts the whole transaction. Any other error (CHECK / NOT NULL / FK violation, lock, deadlock,
        serialization failure) is not a conflict: the savepoint is rolled back when it still can be and the
        original error is raised (CI-5). The statements are the same on every dialect.

        Only inside an open transaction: a nested :meth:`transaction` frame opens no savepoint of its own, so
        without an outer one there is nothing to return to. ``ON CONFLICT DO NOTHING`` is not used on purpose —
        MySQL runs it as ``INSERT IGNORE``, which also turns a CHECK violation into a skipped row (DB-1 DB-D13).
        """
        txn = getattr(_tx_local, "txn", None)
        if txn is None:
            raise RuntimeError("insert_isolated() needs an open transaction")
        if not _SAVEPOINT_NAME.fullmatch(savepoint or ""):
            raise ValueError(f"not a savepoint name: {savepoint!r}")
        if self.dialect == _dialect.SQLITE:
            _open_sqlite_transaction(txn)
        self._execute(f"SAVEPOINT {savepoint}")
        try:
            self._execute(sql, params)
        except Exception as exc:
            if is_unique_violation(exc):
                self._execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._execute(f"RELEASE SAVEPOINT {savepoint}")
                return False
            try:
                self._execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:  # the transaction is already lost; the INSERT's own error is the one to report
                _logging.getLogger(__name__).warning("savepoint rollback after a failed insert failed",
                                                     exc_info=True)
            raise
        self._execute(f"RELEASE SAVEPOINT {savepoint}")
        return True

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

_SAVEPOINT_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
_TRANSIENT_SQLSTATES = frozenset({"40001", "40P01"})     # PostgreSQL serialization failure / deadlock
_TRANSIENT_MYSQL_ERRORS = frozenset({1205, 1213})        # MySQL/MariaDB lock wait timeout / deadlock


def _open_sqlite_transaction(txn) -> None:
    """Make sure the SQLite transaction connection is inside a transaction before a SAVEPOINT.

    Python's sqlite3 (sqloader and the TESTING bridge both use its default deferred mode) leaves the connection
    in autocommit until the first INSERT / UPDATE / DELETE. A SAVEPOINT issued before that opens the transaction
    itself, and its RELEASE would then commit everything up to that point instead of nesting.
    """
    conn = getattr(txn, "conn", None) or getattr(txn, "_conn", None)
    if isinstance(conn, sqlite3.Connection) and not conn.in_transaction:
        txn.execute("BEGIN")


def _error_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_unique_violation(exc: BaseException) -> bool:
    """A duplicate key on any dialect (flowgate.v02.0113 T#1, DB-1 §4.3 CI-4).

    SQLite ``IntegrityError`` naming UNIQUE (a PRIMARY KEY clash reads "UNIQUE constraint failed" too),
    PostgreSQL SQLSTATE 23505, MySQL/MariaDB error 1062, also when a wrapper re-raised the driver error. The
    shared form of the copies in ``settings/user_admin_service`` and ``services/conversation_turn_service``.
    """
    for current in _error_chain(exc):
        if isinstance(current, sqlite3.IntegrityError) and "unique" in str(current).lower():
            return True
        if (getattr(current, "pgcode", None) or getattr(current, "sqlstate", None)) == "23505":
            return True
        args = getattr(current, "args", ())
        if args and args[0] == 1062:
            return True
    return False


def is_transient_failure(exc: BaseException) -> bool:
    """A lock / deadlock / serialization failure: the whole transaction is gone and may run again (DB-1 §4.3).

    SQLite ``database is locked`` / busy, PostgreSQL SQLSTATE 40001 / 40P01, MySQL/MariaDB 1213 / 1205.
    """
    for current in _error_chain(exc):
        if isinstance(current, sqlite3.OperationalError):
            message = str(current).lower()
            if "locked" in message or "busy" in message:
                return True
        if (getattr(current, "pgcode", None) or getattr(current, "sqlstate", None)) in _TRANSIENT_SQLSTATES:
            return True
        args = getattr(current, "args", ())
        if args and args[0] in _TRANSIENT_MYSQL_ERRORS:
            return True
    return False

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


def in_transaction() -> bool:
    """True while this thread is inside a :meth:`FlowGateStore.transaction` frame."""
    return getattr(_tx_local, "txn", None) is not None


def after_commit(callback) -> bool:
    """Queue *callback* to run once the current transaction commits.

    Returns True when it was queued (a transaction is open) and False when there is
    no transaction to wait for -- the caller should then just run it inline. The
    queue is dropped, not run, if the transaction rolls back, and a callback that
    raises is logged and skipped: this is for best-effort side effects only (see
    :meth:`FlowGateStore.transaction`), never for writes the caller must keep.
    """
    if not in_transaction():
        return False
    queue = getattr(_tx_local, "after_commit", None)
    if queue is None:
        # Defensive: a transaction opened before this facility existed, or a caller
        # that stashed the txn itself. Inline is still better than losing the call.
        return False
    queue.append(callback)
    return True


def after_rollback(callback) -> bool:
    """Queue best-effort cleanup after the outer transaction rolls back (including commit failure).

    Nested frames join the outer queue. Callbacks run in reverse registration order,
    after the DB frame closes; successful commits discard them. No transaction: False.
    """
    if not in_transaction():
        return False
    queue = getattr(_tx_local, "after_rollback", None)
    if queue is None:
        return False
    queue.append(callback)
    return True


def track_new_file(abs_path, still_ours=None, under=None) -> bool:
    """Remove a newly created file on rollback; never use for an overwritten existing file.

    The callback runs after this transaction's locks are gone, so another writer may already have moved the
    file aside and created its own at the same path (0064 T#3). ``still_ours(path)`` keeps the removal to the
    file this transaction created; ``under(fn)`` runs check and removal under the durable lock that writer has
    to take. Without them the path is removed unchecked (the earlier behaviour).
    """
    path = Path(abs_path)

    def remove() -> None:
        if still_ours is not None and not still_ours(path):
            return
        path.unlink(missing_ok=True)

    return after_rollback(remove if under is None else (lambda: under(remove)))

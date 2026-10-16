"""Flow-level serialization (L0009 §2.1): in-process Flow lock + durable guard row.

Everything written inside ``with_flow_guard`` is one transaction; the guard bump is its first
SQL statement (GUARD_FIRST_STATEMENT). SSE publication is queued with ``after_commit`` by the
body, so it runs after the commit and before the in-process lock is released.

0064 L0005 2.6 / 2.7.1: ``run_in_document_guard`` is the one boundary an R↔B conversion and the
attachment writes (upload registration, delete, legacy registration) share. A document in a Flow is
serialized by that Flow's guard; a document without one by its own row lock
(``with_document_lock``). A caller already inside a transaction joins only when that transaction is
the very guard the document needs; any other open transaction is refused before ``body`` runs.

0064 T#3: the in-process lock outlives the transaction, the durable lock does not — it ends with the commit or
the rollback, before the queued callbacks run. A file cleanup a guarded write queues therefore runs through
``run_cleanup``: a fresh short transaction whose first statement takes the durable lock again (no guard bump),
so a writer of another process staging files at the same paths is never interleaved with it.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, TypeVar

from modules.flow_gate.db import dialect as db_dialect
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.graph_workflow.store import definition_store, layout_store

from .errors import FlowLockTimeout, FlowNotFound

FLOW_LOCK_WAIT_SEC = 30

T = TypeVar("T")
GuardKey = tuple[str, str]                      # ("flow", flow_id) | ("document", doc_id)

_locks: dict[str, threading.Lock] = {}
_doc_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_held = threading.local()                       # the guard this thread's open transaction is


class GuardNotHeld(RuntimeError):
    """A guarded write was called inside a transaction that is not the guard its document needs."""

    def __init__(self, wanted: GuardKey, held: Optional[GuardKey]) -> None:
        super().__init__(f"guard {wanted} required, the open transaction holds {held}")
        self.wanted, self.held = wanted, held


def _named_lock(table: dict[str, threading.Lock], key: str) -> threading.Lock:
    with _locks_guard:
        lock = table.get(key)
        if lock is None:
            lock = table[key] = threading.Lock()
        return lock


def _flow_lock(flow_id: str) -> threading.Lock:
    return _named_lock(_locks, flow_id)


def held_guard() -> Optional[GuardKey]:
    """The guard whose transaction this thread is inside, or None (none, or a plain transaction)."""
    return getattr(_held, "key", None)


@contextmanager
def _holding(key: GuardKey) -> Iterator[None]:
    previous = held_guard()
    _held.key = key
    try:
        yield
    finally:
        _held.key = previous


def with_flow_guard(flow_id: str, body: Callable[[], T]) -> T:
    # L0009 E-01: entering from inside another transaction would make the bump not the
    # first statement and could nest two Flow guards.
    assert not in_transaction(), "with_flow_guard must not run inside an open transaction"
    lock = _flow_lock(flow_id)
    if not lock.acquire(timeout=FLOW_LOCK_WAIT_SEC):
        raise FlowLockTimeout(flow_id)
    try:
        try:
            layout_store.ensure_guard_row(flow_id)
        except Exception:
            if db_groups.get_by_id(flow_id) is None:
                raise FlowNotFound(flow_id)
            raise
        with get_store().transaction():
            if layout_store.bump_guard(flow_id) == 0:
                raise FlowNotFound(flow_id)
            with _holding(("flow", flow_id)):
                return body()
    finally:
        lock.release()


def lock_document_row(doc_id: str) -> None:
    """The durable half of ``with_document_lock``; the first statement of its transaction.

    PostgreSQL / MySQL: ``SELECT … FOR UPDATE`` takes the row lock and, being a locking read, waits
    for a writer holding it and then reads the committed row (a row deleted meanwhile locks nothing
    and the body's own re-read refuses). SQLite has no row lock and no ``FOR UPDATE``: a no-op UPDATE
    takes the database write lock instead, which serializes every writer of every document.
    """
    store = get_store()
    if getattr(store, "dialect", db_dialect.SQLITE) == db_dialect.SQLITE:
        store._execute_affected("UPDATE documents SET updated_at = updated_at WHERE doc_id = ?", [doc_id])
    else:
        store._fetch_one("SELECT doc_id FROM documents WHERE doc_id = ? FOR UPDATE", [doc_id])


def with_document_lock(doc_id: str, body: Callable[[], T]) -> T:
    """The guard of a document without a Flow: in-process lock + the document row lock (L0005 2.7.1)."""
    assert not in_transaction(), "with_document_lock must not run inside an open transaction"
    lock = _named_lock(_doc_locks, doc_id)
    if not lock.acquire(timeout=FLOW_LOCK_WAIT_SEC):
        raise FlowLockTimeout(doc_id)
    try:
        with get_store().transaction():
            lock_document_row(doc_id)
            with _holding(("document", doc_id)):
                return body()
    finally:
        lock.release()


def lock_flow_guard_row(flow_id: str) -> None:
    """The durable half of the Flow guard without a bump (a cleanup changes no Flow state).

    PostgreSQL / MySQL lock the guard row (``FOR UPDATE``); SQLite takes its write lock with a no-op UPDATE.
    """
    store = get_store()
    if getattr(store, "dialect", db_dialect.SQLITE) == db_dialect.SQLITE:
        store._execute_affected("UPDATE graph_flow_guards SET guard_seq = guard_seq WHERE group_id = ?", [flow_id])
    else:
        store._fetch_one("SELECT group_id FROM graph_flow_guards WHERE group_id = ? FOR UPDATE", [flow_id])


def lock_durably(key: GuardKey) -> None:
    """The durable half of the guard ``key``: the Flow guard row, or the document row."""
    if key[0] == "flow":
        lock_flow_guard_row(key[1])
    else:
        lock_document_row(key[1])


def run_cleanup(key: GuardKey, body: Callable[[], T], what: str = "cleanup") -> T:
    """Run a file cleanup queued by a guarded write, after that write's commit / rollback (0064 T#3).

    The cleanup's own transaction takes the durable lock of ``key`` as its first statement and holds it while
    ``body`` checks and removes, so it waits for a writer of another process that is staging under that guard
    (and then sees its committed rows), and that writer waits for it. ``key`` is the guard the *next* writer of
    the paths takes: after a commit the guard of the document as committed, after a rollback the one held.
    The in-process lock of this process, if any, stays with the caller; nothing here takes it.
    """
    assert not in_transaction(), f"run_cleanup must not run inside an open transaction ({what})"
    with get_store().transaction():
        lock_durably(key)
        return body()


def document_guard_key(doc: Optional[dict], doc_id: str) -> GuardKey:
    """Which guard serializes writes to ``doc``: its Flow's when the Flow exists, else its own row."""
    flow_id = (doc or {}).get("group_id")
    if flow_id and definition_store.available() and db_groups.get_by_id(flow_id) is not None:
        return ("flow", flow_id)
    return ("document", doc_id)


def run_in_document_guard(doc_id: str, body: Callable[[], T]) -> T:
    """One guarded transaction for a write to ``doc_id`` (0064 L0005 2.6 / 2.7.1).

    Inside an open transaction ``body`` joins only when that transaction is the guard this document
    needs (re-entry runs the guard once; the commit / rollback callbacks stay with the outermost frame).
    A plain transaction or another Flow's guard raises ``GuardNotHeld`` before ``body`` runs, so nothing
    is staged without the lock. Otherwise the guard is taken here and the document's guard is checked
    again under it: a document that is gone meanwhile is left to ``body`` to refuse.
    """
    key = document_guard_key(db_docs.get_by_id(doc_id), doc_id)
    if in_transaction():
        if held_guard() != key:
            raise GuardNotHeld(key, held_guard())
        return body()

    def checked() -> T:
        now = db_docs.get_by_id(doc_id)
        if now is not None and document_guard_key(now, doc_id) != key:
            raise GuardNotHeld(document_guard_key(now, doc_id), key)
        return body()

    if key[0] == "flow":
        return with_flow_guard(key[1], checked)
    return with_document_lock(doc_id, checked)

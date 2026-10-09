"""Flow-level serialization (L0009 §2.1): in-process Flow lock + durable guard row.

Everything written inside ``with_flow_guard`` is one transaction; the guard bump is its first
SQL statement (GUARD_FIRST_STATEMENT). SSE publication is queued with ``after_commit`` by the
body, so it runs after the commit and before the in-process lock is released.
"""
from __future__ import annotations

import threading
from typing import Callable, TypeVar

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.graph_workflow.store import layout_store

from .errors import FlowLockTimeout, FlowNotFound

FLOW_LOCK_WAIT_SEC = 30

T = TypeVar("T")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _flow_lock(flow_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(flow_id)
        if lock is None:
            lock = _locks[flow_id] = threading.Lock()
        return lock


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
            return body()
    finally:
        lock.release()

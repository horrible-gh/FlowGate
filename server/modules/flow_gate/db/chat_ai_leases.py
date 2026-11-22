"""Durable per-conversation read-chat ownership, separate from mutation leases.

Only admission may acquire these leases after resolving server-side chat mode.
Lifecycle operations compare run identity, so stopping a card cannot stop a chat.
Observations outlive leases: a finished reply must retain its admission context.
"""
from __future__ import annotations

import threading
from .connection import get_store, now_iso
from . import group_ai_leases as groups

_memory = {}
_observations = {}
_lock = threading.RLock()
_test_id = None


def _memory_mode():
    import os
    global _test_id
    if not groups._using_memory():
        return False
    current = os.environ.get("PYTEST_CURRENT_TEST")
    if current and current != _test_id:
        _memory.clear()
        _observations.clear()
        _test_id = current
    return True


def get_by_run_id(run_id):
    if _memory_mode():
        with _lock:
            return next((dict(r) for r in _memory.values() if r["run_id"] == run_id), None)
    return get_store()._fetch_one("SELECT * FROM chat_ai_leases WHERE run_id = ?", [run_id])


def get_by_token_id(token_id):
    if _memory_mode():
        with _lock:
            return next((dict(r) for r in _memory.values() if r.get("token_id") == token_id), None)
    return get_store()._fetch_one("SELECT * FROM chat_ai_leases WHERE token_id = ?", [token_id])


def get_active(doc_id):
    if _memory_mode():
        with _lock:
            row = _memory.get(doc_id)
            if row and row["state"] == "acquiring" and groups._expired(row):
                _memory.pop(doc_id, None)
                return None
            return dict(row) if row else None
    store = get_store()
    row = store._fetch_one("SELECT * FROM chat_ai_leases WHERE doc_id = ?", [doc_id])
    # An active chat can be unlimited. A TTL alone is not evidence that its
    # provider stopped. Terminal cleanup or startup recovery (with remote-job
    # fencing) owns release; only abandoned pre-worker acquisition expires.
    if row and row["state"] == "acquiring" and groups._expired(row):
        store._execute("DELETE FROM chat_ai_leases WHERE doc_id = ? AND run_id = ? AND expires_at = ?",
                       [doc_id, row["run_id"], row["expires_at"]])
        return None
    return row


def acquire(*, doc_id, group_id, project_id, run_id, chain_id, action_scope, worker_identity):
    if action_scope != "chat" or not doc_id:
        raise ValueError("read-chat lease requires a conversation")
    stamp = now_iso()
    row = dict(doc_id=doc_id, group_id=group_id, project_id=project_id, run_id=run_id,
               chain_id=chain_id, action_scope=action_scope, worker_identity=worker_identity,
               token_id=None, state="acquiring", generation=1, acquired_at=stamp,
               heartbeat_at=stamp, expires_at=groups._expiry(groups.ACQUIRING_TTL_SEC), updated_at=stamp)
    if _memory_mode():
        with _lock:
            current = get_active(doc_id)
            if current:
                return current
            if get_by_run_id(run_id):
                raise groups.RunIdCollision(run_id)
            _memory[doc_id] = row
            return dict(row)
    store = get_store()
    with store.transaction():
        current = get_active(doc_id)
        if current:
            return current
        fields = list(row)
        store._execute("INSERT INTO chat_ai_leases (" + ", ".join(fields) + ") VALUES ("
                       + ", ".join("?" for _ in fields) + ") ON CONFLICT(doc_id) DO NOTHING",
                       [row[k] for k in fields])
        return store._fetch_one("SELECT * FROM chat_ai_leases WHERE doc_id = ?", [doc_id])


def activate(group_id, run_id, token_id, action_scope, worker_identity, ttl_seconds):
    stamp, expires = now_iso(), groups._expiry(ttl_seconds + groups.ACTIVE_GRACE_SEC)
    if _memory_mode():
        with _lock:
            row = next((r for r in _memory.values() if r["run_id"] == run_id and r["group_id"] == group_id), None)
            if not row or row["state"] != "acquiring":
                return None
            row.update(token_id=token_id, state="active", heartbeat_at=stamp, expires_at=expires, updated_at=stamp)
            return dict(row)
    get_store()._execute("UPDATE chat_ai_leases SET token_id = ?, state = 'active', heartbeat_at = ?, "
                         "expires_at = ?, updated_at = ? WHERE group_id = ? AND run_id = ? AND state = 'acquiring'",
                         [token_id, stamp, expires, stamp, group_id, run_id])
    row = get_by_run_id(run_id)
    return row if row and row["group_id"] == group_id and row["state"] == "active" else None


def heartbeat(group_id, run_id, ttl_seconds=groups.ACTIVE_HEARTBEAT_TTL_SEC):
    stamp, expires = now_iso(), groups._expiry(ttl_seconds)
    if _memory_mode():
        with _lock:
            for row in _memory.values():
                if row["run_id"] == run_id and row["group_id"] == group_id:
                    row.update(heartbeat_at=stamp, expires_at=expires, updated_at=stamp)
                    return True
            return False
    get_store()._execute("UPDATE chat_ai_leases SET heartbeat_at = ?, expires_at = ?, updated_at = ? "
                         "WHERE group_id = ? AND run_id = ?", [stamp, expires, stamp, group_id, run_id])
    row = get_by_run_id(run_id)
    return bool(row and row["group_id"] == group_id)


def update_token(group_id, run_id, token_id, action_scope=None):
    if _memory_mode():
        with _lock:
            for row in _memory.values():
                if row["run_id"] == run_id and row["group_id"] == group_id:
                    row["token_id"] = token_id
        return
    get_store()._execute("UPDATE chat_ai_leases SET token_id = ? WHERE group_id = ? AND run_id = ?",
                         [token_id, group_id, run_id])


def release(group_id, run_id, reason="released"):
    if _memory_mode():
        with _lock:
            for doc_id, row in list(_memory.items()):
                if row["run_id"] == run_id and row["group_id"] == group_id:
                    del _memory[doc_id]
                    return True
            return False
    row = get_by_run_id(run_id)
    if not row or row["group_id"] != group_id:
        return False
    get_store()._execute("DELETE FROM chat_ai_leases WHERE group_id = ? AND run_id = ?", [group_id, run_id])
    return get_by_run_id(run_id) is None


def reclaim_orphaned(before, keep_run_ids=frozenset(), owns_run_id=None):
    if _memory_mode():
        with _lock:
            rows = [dict(r) for r in _memory.values()]
    else:
        rows = get_store()._fetch_all("SELECT * FROM chat_ai_leases WHERE acquired_at < ?", [before])
    victims = [r for r in rows if r["acquired_at"] < before and r["run_id"] not in keep_run_ids
               and (owns_run_id is None or owns_run_id(r["run_id"]))]
    for row in victims:
        release(row["group_id"], row["run_id"], "orphaned_by_restart")
    return victims


def observe(run_id, doc_id, group_id, card_running):
    row = dict(run_id=run_id, doc_id=doc_id, group_id=group_id, observed_during_card_run=int(bool(card_running)))
    if _memory_mode():
        with _lock:
            _observations.setdefault(run_id, row)
        return
    get_store()._execute("INSERT INTO chat_run_observations (run_id, doc_id, group_id, observed_during_card_run) "
                         "VALUES (?, ?, ?, ?) ON CONFLICT(run_id) DO NOTHING", list(row.values()))


def observation(run_id, doc_id=None):
    if not run_id:
        return False
    if _memory_mode():
        with _lock:
            row = _observations.get(run_id)
    else:
        row = get_store()._fetch_one("SELECT * FROM chat_run_observations WHERE run_id = ?", [run_id])
    return bool(row and (doc_id is None or row["doc_id"] == doc_id) and row["observed_during_card_run"])

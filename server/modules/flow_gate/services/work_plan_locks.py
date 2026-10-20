"""The work plan save lock, shared by the WP routes and the v0.2 deploy (flowgate.v02.0074 T#4, D0006 §3.6.3).

0403 NR0004 F1 — an advisory lock allowing only one save per document at a time. Same mechanism and same reason as
document-number reservation (numbering_service._get_lock): every invariant of the form "two concurrent requests,
only one wins" rests on this in-process lock. The lock is per document, so requests saving different plans never
wait on each other.

The WP save (PUT), the revision restore and the work plan deploy (``flowgate.work_plan_deploy``) take this same lock
object, so a save in this process waits while a deploy reads and projects a fixed revision of the plan. A save of
another process is held by the plan document's row lock the deploy takes inside its Flow guard.
"""
from __future__ import annotations

import threading

_save_locks: dict[str, threading.Lock] = {}
_save_locks_meta = threading.Lock()


def plan_save_lock(doc_id: str) -> threading.Lock:
    with _save_locks_meta:
        lock = _save_locks.get(doc_id)
        if lock is None:
            lock = threading.Lock()
            _save_locks[doc_id] = lock
        return lock

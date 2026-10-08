"""Job wake-up Notifier (flowgate.default.0669 unit 5b; 0666 0009-L §1.4, §2.17.1).

An in-process queue of wake-up signals plus the hand-off slot through which the
Sweeper passes jobs it already claimed to a worker. Signals carry identifiers only;
whoever takes one re-reads the job from the DB, so a dropped, duplicated or
re-ordered signal is normal (D 3.6.6) — the next sweep tick covers any loss.

``publish`` always runs after the writing transaction commits (``after_commit``),
so a worker never wakes for a row it cannot see yet.

Not here: the optional Redis fan-out between instances (L 1.4 REDIS_CHANNEL). With
one server the local queue is the whole channel; several instances still converge
through the sweep tick and the claim CAS.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, Union

from modules.flow_gate.db.connection import after_commit

from .lock_manager import _env_num

_log = logging.getLogger(__name__)


def wakeup_queue_max() -> int:
    return int(_env_num("FLOWGATE_WAKEUP_QUEUE_MAX", 1024, 64, 65536))


JOB_CREATED = "job_created"
JOB_PUBLISHABLE = "job_publishable"
DOMAIN_RELEASED = "domain_released"
JOB_TERMINAL = "job_terminal"


@dataclass(frozen=True)
class Signal:
    kind: str
    job_id: Optional[str] = None
    project_id: Optional[str] = None
    lock_key: Optional[str] = None
    domain: Optional[str] = None
    group_id: Optional[str] = None
    target_key: Optional[str] = None


class _Queue:
    """Claimed jobs first (never dropped, at most one per free worker), then signals
    (bounded; the oldest is dropped when full)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._claimed: deque = deque()
        self._signals: deque = deque()
        self._dropped = 0

    def offer(self, sig: Signal) -> None:
        with self._cond:
            if len(self._signals) >= wakeup_queue_max():
                self._signals.popleft()
                self._dropped += 1
                if self._dropped % 100 == 1:
                    _log.warning("wake-up queue full; dropped %s signal(s) so far", self._dropped)
            self._signals.append(sig)
            self._cond.notify()

    def hand_off(self, claimed) -> None:
        with self._cond:
            self._claimed.append(claimed)
            self._cond.notify()

    def take(self, timeout: float) -> Union[Signal, object, None]:
        with self._cond:
            if not self._claimed and not self._signals:
                self._cond.wait(timeout)
            if self._claimed:
                return self._claimed.popleft()
            if self._signals:
                return self._signals.popleft()
            return None

    def pending_hand_offs(self) -> int:
        with self._cond:
            return len(self._claimed)

    def wake_all(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def clear(self) -> None:
        with self._cond:
            self._claimed.clear()
            self._signals.clear()


_queue = _Queue()


def queue() -> _Queue:
    return _queue


def publish(sig: Signal) -> None:
    """Offer after the current transaction commits (inline when there is none)."""
    def _offer() -> None:
        _queue.offer(sig)

    if not after_commit(_offer):
        _offer()


def job_created(job: dict) -> None:
    publish(Signal(JOB_CREATED, job_id=job["job_id"], project_id=job.get("project_id")))


def job_publishable(job: dict) -> None:
    publish(Signal(JOB_PUBLISHABLE, job_id=job["job_id"], project_id=job.get("project_id")))


def job_terminal(job: dict) -> None:
    publish(Signal(JOB_TERMINAL, job_id=job["job_id"], project_id=job.get("project_id")))


def domain_released(lock_key: str, domain: str, project_id: str,
                    group_id: Optional[str] = None, target_key: Optional[str] = None) -> None:
    publish(Signal(DOMAIN_RELEASED, project_id=project_id, lock_key=lock_key, domain=domain,
                   group_id=group_id, target_key=target_key))

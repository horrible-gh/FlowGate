"""Per-run document edit gate and cancel serialization (L#3 §2.7, §3.1).

Goal: once a cancel is announced no new save starts, and a save already past the gate
runs to its end. ``enter`` checks ``closed`` and bumps ``inflight`` under one lock;
``close`` sets ``closed`` first and only then waits (at most ``CANCEL_DRAIN_SEC``) for
``inflight == 0``. The gate never waits on a Flow guard or a document row lock, and the
work after the gate waits on nothing the cancel holds, so no wait cycle exists.

Memory only: no DB access here. The run's closed reason is kept up to
``CLOSED_RUNS_MAX`` (the Conversation command service's ``_CLOSED_RUNS_MAX``).

Locking and lifetime: every gate's state (``closed``/``reason``/``inflight``) and the
registry (``_gates``, the retention list ``_closed``) change only under the one module
lock ``_lock``; each gate keeps its own condition variable on that lock, so a drain wait
releases the lock and wakes only for its own run. Every critical section is constant
time (eviction is amortized O(1)) and never blocks on anything else.

A closed gate pushed out of the retention list while edits are still in flight is not
dropped at once: it is marked ``evicted`` and stays registered and CLOSED (new entries
are still refused with the first reason), and the release that brings its ``inflight``
to 0 removes it, unless a ``close`` is still draining it (that close then retains it again
as the newest record). An OPEN gate is likewise removed when its ``inflight`` returns to 0
(an absent gate reads as OPEN, 0), so the registry holds only gates with edits in flight
and closed gates within retention. A ticket carries the exact gate object it entered,
so a release never lands on another gate registered later under the same run ID.

Beyond retention a run is forgotten exactly like the command service's closed runs: a
later entry finds no gate. ``chat_recheck``'s run state defence (cancelling / cancel
event / finished) still refuses such an edit right after the gate.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from . import chat_errors

_log = logging.getLogger(__name__)

CANCEL_DRAIN_SEC = 10
CANCELLED = "cancelled"
FINISHED = "finished"
REASONS = (CANCELLED, FINISHED)
OPEN = "OPEN"


def closed_runs_max() -> int:
    """CLOSED_RUNS_MAX: referenced from ``flowgate.conversation.commands`` (L#3 §1), never copied."""
    from flowgate.conversation import commands
    return int(commands._CLOSED_RUNS_MAX)


_lock = threading.Lock()


class _Gate:
    __slots__ = ("run_id", "cond", "closed", "reason", "inflight", "evicted", "detached", "waiters")

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.cond = threading.Condition(_lock)
        self.closed = False
        self.reason: Optional[str] = None
        self.inflight = 0
        self.evicted = False      # out of the retention list, removal waits for the last release
        self.detached = False     # removed from the registry
        self.waiters = 0          # closes currently draining this gate (it stays registered)


@dataclass
class Ticket:
    run_id: str
    released: bool = False
    gate: Optional[_Gate] = field(default=None, repr=False, compare=False)


@dataclass
class TicketHolder:
    """Carries the ticket from ``recheck`` (inside the boundary) to the transport ``finally``."""
    ticket: Optional[Ticket] = field(default=None)


_gates: dict[str, _Gate] = {}
_closed: "OrderedDict[str, str]" = OrderedDict()


def _get_locked(run_id: str, create: bool) -> Optional[_Gate]:
    gate = _gates.get(run_id)
    if gate is None and create:
        gate = _gates[run_id] = _Gate(run_id)
    return gate


def _detach_locked(gate: _Gate) -> None:
    if _gates.get(gate.run_id) is gate:
        del _gates[gate.run_id]
    gate.evicted = False
    gate.detached = True


def enter(run_id) -> Ticket:
    """Pass the gate (only from ``recheck``, inside the boundary). A closed gate refuses."""
    if not run_id:
        raise chat_errors.forbidden()
    with _lock:
        gate = _get_locked(str(run_id), create=True)
        if gate.closed:
            raise (chat_errors.run_cancelled(gate.run_id) if gate.reason == CANCELLED
                   else chat_errors.run_finished(gate.run_id))
        gate.inflight += 1
        return Ticket(gate.run_id, gate=gate)


def closed_reason(run_id) -> Optional[str]:
    """None while the gate is absent or open; else the reason it FIRST closed with."""
    if not run_id:
        return None
    run_id = str(run_id)
    with _lock:
        gate = _gates.get(run_id)
        if gate is None:
            return _closed.get(run_id)
        return gate.reason if gate.closed else None


def release(ticket: Optional[Ticket]) -> None:
    """Transport ``finally`` after commit/rollback. No ticket → no-op; double release is safe.

    The ticket's own gate is decremented (never a lookup by run ID). The release that
    empties an evicted or an OPEN gate removes it from the registry."""
    if ticket is None:
        return
    with _lock:
        if ticket.released:
            return
        ticket.released = True
        gate = ticket.gate
        if gate is None:
            # Not issued by ``enter``: nothing was counted, so nothing is applied to any gate.
            return
        gate.inflight -= 1
        gate.cond.notify_all()
        if gate.inflight == 0 and gate.waiters == 0 and (gate.evicted or not gate.closed):
            # An evicted gate's last release removes it. An OPEN gate with nothing in flight
            # holds no information (absent == OPEN, 0), so it is dropped too: the registry
            # only ever holds gates with edits in flight or closed gates kept by retention.
            _detach_locked(gate)


def _limit_or_none() -> Optional[int]:
    try:
        return closed_runs_max()
    except Exception:
        # The reference is unreadable: keep the record, evict nothing (never a wrong reason).
        _log.warning("CLOSED_RUNS_MAX unreadable; closed gates kept without eviction", exc_info=True)
        return None


def close(run_id, reason: str, drain_sec: float = CANCEL_DRAIN_SEC) -> bool:
    """Close the gate (first reason kept) and wait for in-flight edits. Never blocks the caller
    past ``drain_sec``; returns whether the gate drained."""
    if reason not in REASONS:
        raise ValueError(f"unknown gate close reason: {reason}")
    if not run_id:
        return True
    run_id = str(run_id)
    limit = _limit_or_none()
    with _lock:
        gate = _get_locked(run_id, create=True)
        if not gate.closed:
            gate.closed = True
            gate.reason = reason
        gate.waiters += 1
        try:
            drained = gate.cond.wait_for(lambda: gate.inflight == 0, timeout=drain_sec)
            inflight = gate.inflight
            _remember_locked(gate, limit)
        finally:
            gate.waiters -= 1
    if not drained:
        _log.warning("chat_doc_edit_drain_timeout run_id=%s inflight=%s", run_id, inflight)
    return bool(drained)


def _remember_locked(gate: _Gate, limit: Optional[int]) -> None:
    """Record the closed run as the newest retained one and evict beyond CLOSED_RUNS_MAX.

    A gate being drained by a close is never removed (``waiters``), so the run stays closed
    in the registry for the whole close. Defensively (``reset`` in tests), a gate found
    detached is put back, and another gate registered under the same run ID is closed with
    the same first reason: a close never leaves the run open. An evicted gate with in-flight
    edits stays registered and CLOSED until its last release removes it."""
    run_id = gate.run_id
    current = _gates.get(run_id)
    if current is None:
        gate.detached = False
        _gates[run_id] = current = gate
    elif current is not gate and not current.closed:
        current.closed = True
        current.reason = gate.reason
    current.evicted = False
    _closed[run_id] = current.reason
    _closed.move_to_end(run_id)
    if limit is None:
        return
    while len(_closed) > limit:
        old_id, _ = _closed.popitem(last=False)
        old = _gates.get(old_id)
        if old is None:
            continue
        if old.inflight == 0:
            _detach_locked(old)
        else:
            old.evicted = True


def remember_closed(run_id: str) -> None:
    """Keep the closed reason within CLOSED_RUNS_MAX (no-op unless the gate is closed)."""
    if not run_id:
        return
    limit = _limit_or_none()
    with _lock:
        gate = _gates.get(str(run_id))
        if gate is None or not gate.closed:
            return
        _remember_locked(gate, limit)


def state(run_id) -> tuple[str, int]:
    """(``OPEN`` | ``CLOSED(<reason>)``, inflight) — observation for diagnostics and tests."""
    if not run_id:
        return OPEN, 0
    run_id = str(run_id)
    with _lock:
        gate = _gates.get(run_id)
        if gate is None:
            reason = _closed.get(run_id)
            return (f"CLOSED({reason})" if reason else OPEN), 0
        return (f"CLOSED({gate.reason})" if gate.closed else OPEN), gate.inflight


def registry_sizes() -> tuple[int, int]:
    """(registered gates, retained closed records) — observation for diagnostics and tests."""
    with _lock:
        return len(_gates), len(_closed)


def close_on_cancel(run: dict) -> None:
    """W4 cancel hook step ①: close(cancelled). A failure never blocks the cancel."""
    try:
        close((run or {}).get("run_id"), CANCELLED, CANCEL_DRAIN_SEC)
    except Exception:
        _log.warning("chat document gate close failed on cancel for %s", (run or {}).get("run_id"), exc_info=True)


def close_on_finalize(run: dict) -> None:
    """W4 finalize hook step ①: close(finished). A failure never blocks finalization."""
    try:
        close((run or {}).get("run_id"), FINISHED, CANCEL_DRAIN_SEC)
    except Exception:
        _log.warning("chat document gate close failed on finalize for %s", (run or {}).get("run_id"), exc_info=True)


def reset() -> None:
    """Test helper: forget every gate and closed record."""
    with _lock:
        for gate in _gates.values():
            gate.detached = True
        _gates.clear()
        _closed.clear()

"""One chat AI run per AI-call press -- ``client_request_id`` on /ai-invoke/start (flowgate.v02.0152).

The browser waits a bounded time for /ai-invoke/start. When that wait ends without an
answer (timeout, dropped connection) the server may still be admitting -- or may already
have run and even finished -- the run, and the browser cannot tell which. Polling
``/ai-invoke/active`` misses a run that is not registered yet or already finished, so a
second press then started the same request again.

The press now names itself: the browser mints one ``client_request_id`` per press and
sends it on the first /start and on every recovery resend of that same press. This
registry answers a resend with what the first request did:

* ``started`` -- the run id the first request admitted (running or already finished);
  the route answers 200 with that run instead of starting another;
* ``pending`` -- the first request is still inside admission; the route answers 409
  ``start_pending`` and the browser asks again shortly;
* nothing -- the first request never arrived, or it was refused (a refused admission
  leaves nothing behind, so the resend is simply a fresh request).

A new press mints a new id, so a deliberate second call is never folded into the first.

flowgate.v02gui.0011 B: the press can be stopped before the browser knows its run id
(``cancel``). Whatever the press has reached, the stop is kept here under the same key:

* nothing yet (the /start is still on its way, or never came) -- a ``cancelled`` entry is
  left behind, and a /start that arrives later with this id starts nothing;
* ``pending`` -- the entry is marked; when admission ends, ``complete`` tells the route to
  cancel the run it just admitted (``release`` leaves a ``cancelled`` entry instead);
* ``started`` -- the caller cancels the run id it gets back.

Both the stop and the admission's outcome take the same lock, so no order of the two can
let the run go on.

The registry is in memory, like the active-run table ``/ai-invoke/active`` reads: a
server restart ends the runs it describes as well. Entries are scoped by requester and
chat document, so one user's id never answers another user's request.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

FIELD = "client_request_id"
_PATTERN = re.compile(r"[A-Za-z0-9._:-]{8,200}\Z")

# A pending entry outlives any admission that can still answer (the slowest step, the
# group's first source sync, runs git commands bounded at 120s each); after this it is
# treated as abandoned so a crashed request thread cannot block the press forever.
PENDING_TTL_SEC = 15 * 60
# A started entry must outlive the browser's recovery window for that press.
STARTED_TTL_SEC = 30 * 60

NEW, PENDING, STARTED, CANCELLED = "new", "pending", "started", "cancelled"


@dataclass
class Claim:
    kind: str
    run_id: Optional[str] = None
    token: Optional[object] = None


@dataclass
class _Entry:
    state: str
    expires: float
    owner: object
    run_id: Optional[str] = None
    # flowgate.v02gui.0011 B: the user stopped this press while it was pending or started.
    cancel_requested: bool = False


@dataclass
class CancelOutcome:
    """What a stop found: CANCELLED (no run will start), PENDING (admission still running;
    the run is cancelled as soon as it is admitted) or STARTED (``run_id`` to cancel)."""
    state: str
    run_id: Optional[str] = None


_lock = threading.Lock()
_entries: dict[tuple[str, str, str], _Entry] = {}


def valid(value: object) -> bool:
    return isinstance(value, str) and _PATTERN.fullmatch(value) is not None


def _key(user_id: str, doc_ref: str, request_id: str) -> tuple[str, str, str]:
    return (str(user_id), str(doc_ref), request_id)


def _sweep(now: float) -> None:
    for key in [k for k, e in _entries.items() if e.expires <= now]:
        del _entries[key]


def claim(user_id: str, doc_ref: str, request_id: str) -> Claim:
    """Reserve this press for the calling request, or report what an earlier one did."""
    now = time.monotonic()
    key = _key(user_id, doc_ref, request_id)
    with _lock:
        _sweep(now)
        entry = _entries.get(key)
        if entry is not None and entry.state == CANCELLED:
            return Claim(CANCELLED)
        if entry is not None and entry.state == STARTED:
            return Claim(STARTED, run_id=entry.run_id)
        if entry is not None:
            return Claim(PENDING)
        owner = object()
        _entries[key] = _Entry(PENDING, now + PENDING_TTL_SEC, owner)
        return Claim(NEW, token=owner)


def complete(user_id: str, doc_ref: str, request_id: str, token: object, run_id: str) -> bool:
    """The reserving request admitted ``run_id``.

    Returns True when the press was stopped while it was being admitted: the caller must
    cancel ``run_id`` now (flowgate.v02gui.0011 B)."""
    key = _key(user_id, doc_ref, request_id)
    with _lock:
        entry = _entries.get(key)
        if entry is None or entry.owner is token:
            stopped = bool(entry is not None and entry.cancel_requested)
            _entries[key] = _Entry(STARTED, time.monotonic() + STARTED_TTL_SEC, token, run_id,
                                   cancel_requested=stopped)
            return stopped
    return False


def release(user_id: str, doc_ref: str, request_id: str, token: object) -> None:
    """The reserving request ended without a run: forget the press so a resend starts fresh.

    A press the user stopped meanwhile stays stopped: a resend starts nothing."""
    key = _key(user_id, doc_ref, request_id)
    with _lock:
        entry = _entries.get(key)
        if entry is not None and entry.owner is token and entry.state == PENDING:
            if entry.cancel_requested:
                _entries[key] = _Entry(CANCELLED, time.monotonic() + STARTED_TTL_SEC, object())
            else:
                del _entries[key]


def cancel(user_id: str, doc_ref: str, request_id: str) -> CancelOutcome:
    """flowgate.v02gui.0011 B: the user stopped this press before (or without) knowing its run id.

    Idempotent: stopping the same press again answers the same way."""
    now = time.monotonic()
    key = _key(user_id, doc_ref, request_id)
    with _lock:
        _sweep(now)
        entry = _entries.get(key)
        if entry is None:
            # The /start has not reached admission (or never will): it must start nothing.
            _entries[key] = _Entry(CANCELLED, now + STARTED_TTL_SEC, object())
            return CancelOutcome(CANCELLED)
        if entry.state == CANCELLED:
            return CancelOutcome(CANCELLED)
        entry.cancel_requested = True
        if entry.state == STARTED:
            return CancelOutcome(STARTED, run_id=entry.run_id)
        return CancelOutcome(PENDING)


def clear() -> None:
    """Tests only."""
    with _lock:
        _entries.clear()

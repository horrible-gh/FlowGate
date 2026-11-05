"""API chat call identity: first-acceptance ``server_call_id``, immutable call records,
re-dispatch (L#3 §2.4, §3.2, §4.3; P#2 §5.2).

The ledger lives in the run context (a server-only entry of the ``run`` dict), guarded by
one lock per run. A record is never changed or removed after acceptance; it goes away only
with the run context. Records are looked up by ``server_call_id`` only: there is no path
that finds an earlier call by the provider's ``tool_call_id``.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Optional

from . import chat_errors

_log = logging.getLogger(__name__)

CALL_LEDGER_MAX = 500
LEDGER_KEY = "_chat_call_ledger"
ACCEPTED, DISPATCHED, DONE, REDISPATCHED = "ACCEPTED", "DISPATCHED", "DONE", "REDISPATCHED"
_create_lock = threading.Lock()


@dataclass(frozen=True)
class CallContext:
    """Server-only ``call_context = {run_id, server_call_id, tool_call_id}``. Never model input."""
    run_id: str
    server_call_id: str
    tool_call_id: Optional[str]


@dataclass(frozen=True)
class CallRecord:
    run_id: str
    server_call_id: str
    tool_call_id: Optional[str]
    tool_name: Optional[str]
    input: Any            # deep-frozen copy
    input_sha256: str
    accepted_at: str
    turn_seq: Optional[int]

    def input_copy(self):
        """A mutable deep copy of the frozen input (the record itself is never handed out to change)."""
        return thaw(self.input)


class _Ledger:
    __slots__ = ("lock", "records", "states")

    def __init__(self):
        self.lock = threading.Lock()
        self.records: dict[str, CallRecord] = {}
        self.states: dict[str, list] = {}       # server_call_id -> [state, dispatch_count]


def deep_freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(v) for v in value)
    return copy.deepcopy(value)


def thaw(value):
    if isinstance(value, (MappingProxyType, dict)):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(v) for v in value]
    if isinstance(value, (frozenset, set)):
        return [thaw(v) for v in sorted(value, key=repr)]
    return copy.deepcopy(value)


def sha256_of(value) -> str:
    """SHA-256 of the whitespace-free, key-sorted JSON (UTF-8) of ``value`` (frozen or not)."""
    text = json.dumps(thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=repr)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_id(value) -> Optional[str]:
    """A provider call id, or None when it is not a string or is blank."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _ledger(run: dict, create: bool) -> Optional[_Ledger]:
    ledger = run.get(LEDGER_KEY)
    if isinstance(ledger, _Ledger):
        return ledger
    if not create:
        return None
    with _create_lock:
        ledger = run.get(LEDGER_KEY)
        if not isinstance(ledger, _Ledger):
            ledger = run[LEDGER_KEY] = _Ledger()
        return ledger


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def accept_call(run, call) -> Optional[CallContext]:
    """W1 port implementation: the worker calls it exactly once per received call.

    Every acceptance is a new ``server_call_id`` (UUID v4), even when the provider repeats a
    ``tool_call_id`` in a later turn. A full ledger refuses (None); nothing is evicted.
    """
    if not isinstance(run, dict) or run.get("action_scope") != "chat" or not run.get("run_id"):
        return None
    call = call if isinstance(call, dict) else {}
    ledger = _ledger(run, create=True)
    with ledger.lock:
        if len(ledger.records) >= CALL_LEDGER_MAX:
            _log.warning("chat call ledger full for %s (%s); new call not accepted",
                         run.get("run_id"), CALL_LEDGER_MAX)
            return None
        raw_input = call.get("input")
        name = call.get("name")
        record = CallRecord(
            run_id=str(run["run_id"]), server_call_id=str(uuid.uuid4()), tool_call_id=clean_id(call.get("id")),
            tool_name=name if isinstance(name, str) else None,
            input=deep_freeze(raw_input), input_sha256=sha256_of(raw_input), accepted_at=_now(),
            turn_seq=run.get("_api_turn"),
        )
        ledger.records[record.server_call_id] = record
        ledger.states[record.server_call_id] = [ACCEPTED, 0]
        return CallContext(record.run_id, record.server_call_id, record.tool_call_id)


def _unavailable(run, ctx, why: str):
    run_id = run.get("run_id") if isinstance(run, dict) else None
    _log.warning("chat call identity unavailable run=%s ctx=%s cause=%s", run_id, ctx, why)
    return chat_errors.call_identity_unavailable()


def resolve_call(run, ctx) -> CallRecord:
    """Call-handler entry (always after ``authorize_chat_run``): the frozen record, never caller input.

    No context, no record (run context lost), a run / ``tool_call_id`` mismatch or a frozen-input
    fingerprint mismatch is 422 ``call_identity_unavailable``; no new ID is ever made up.
    """
    if not isinstance(ctx, CallContext):
        raise _unavailable(run, ctx, "no_context")
    if not isinstance(run, dict) or str(ctx.run_id) != str(run.get("run_id") or ""):
        raise _unavailable(run, ctx, "run_mismatch")
    ledger = _ledger(run, create=False)
    if ledger is None:
        raise _unavailable(run, ctx, "no_ledger")
    with ledger.lock:
        record = ledger.records.get(ctx.server_call_id)
        if record is None:
            raise _unavailable(run, ctx, "no_record")
        if record.tool_call_id != ctx.tool_call_id:
            raise _unavailable(run, ctx, "tool_call_id_mismatch")
        if sha256_of(record.input) != record.input_sha256:
            raise _unavailable(run, ctx, "frozen_input_changed")
        state = ledger.states.setdefault(ctx.server_call_id, [ACCEPTED, 0])
        state[1] += 1
        state[0] = DISPATCHED if state[1] == 1 else REDISPATCHED
        return record


def complete(run, ctx) -> None:
    """The handler returned (success or refusal): DISPATCHED/REDISPATCHED -> DONE. Record untouched."""
    ledger = _ledger(run, create=False) if isinstance(run, dict) else None
    if ledger is None or not isinstance(ctx, CallContext):
        return
    with ledger.lock:
        state = ledger.states.get(ctx.server_call_id)
        if state is not None and state[0] in (DISPATCHED, REDISPATCHED):
            state[0] = DONE


@contextlib.contextmanager
def dispatching(run, ctx):
    """``with dispatching(run, ctx) as record:`` resolve, run, then DONE whatever the outcome."""
    record = resolve_call(run, ctx)
    try:
        yield record
    finally:
        complete(run, ctx)


def require_tool_call_id(record: CallRecord) -> None:
    """§4.3 last branch: a record without a provider id is 422 tool_call_id required."""
    if record.tool_call_id is None:
        raise chat_errors.tool_call_id_required()


def api_key(record: CallRecord) -> str:
    """API_KEY_FORM: ``chat-api:`` + SHA-256 hex of ``[run_id, server_call_id, tool_call_id]``."""
    text = json.dumps([record.run_id, record.server_call_id, record.tool_call_id],
                      separators=(",", ":"), ensure_ascii=False)
    return "chat-api:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def state_of(run, server_call_id) -> Optional[tuple]:
    """(state, dispatch count) of one call record (§3.2), or None when unknown."""
    ledger = _ledger(run, create=False) if isinstance(run, dict) else None
    if ledger is None:
        return None
    with ledger.lock:
        state = ledger.states.get(server_call_id)
        return (state[0], state[1]) if state else None


def size(run) -> int:
    ledger = _ledger(run, create=False) if isinstance(run, dict) else None
    if ledger is None:
        return 0
    with ledger.lock:
        return len(ledger.records)

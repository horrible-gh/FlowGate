"""Pure decision table of the non-destructive push reconciliation (flowgate.v02.0153 T#3).

``reconcile_push_session`` gathers facts step by step (local checkout state, the
``ls-remote`` probes, an optional ``fetch`` and two ancestry checks) and asks
:func:`decide` what to do next after every step. ``decide`` never runs Git, never
reads the database and never looks at the clock: the same :class:`Inputs` always
give the same :class:`Decision`. A ``need_*`` action means "gather this fact, then
ask again"; every other action is terminal for the current cycle and the executor
performs exactly that action.

The order of the checks is T0009 §3.2:

1. (lock, kind re-check and the T#2 preservation gate run before this table)
2. cycle budget spent (counted before this cycle) → ``exhausted`` without any remote
   contact
3. local checkout: HEAD == publish commit, no MERGE_HEAD, clean tracked files;
   an ancestor HEAD with a clean tree is restored by ``merge --ff-only``; anything
   else is ``local_mismatch`` (never a reset)
4. ``ls-remote``: an ``auth`` failure is ``auth`` at once, any other failure transient
5. observed == publish commit or merge commit → ``complete``
6. observed == publish lease → ``repush``
7. ``fetch``: auth → ``auth``; other failure or ``F != observed`` → transient
8. merge commit ⊑ F → ``complete``
9. publish lease ⊑ F → ``integrate`` (or ``exhausted`` at the integration limit)
10. otherwise the remote history was rewritten → ``rewritten``

A transient outcome in the last allowed cycle becomes ``exhausted`` at once
(§3.6), so no further schedule is ever written for it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

RECONCILE_MAX_CYCLES = 5
RECONCILE_MAX_INTEGRATIONS = 2
RECONCILE_BACKOFF_BASE_SEC = 60
RECONCILE_BACKOFF_MAX_SEC = 900

# Local checkout verdicts.
LOCAL_OK = "ok"
LOCAL_RESTORABLE = "restorable"
LOCAL_MISMATCH = "mismatch"

# Ancestry verdicts (``merge-base --is-ancestor``: 0 → yes, 1 → no, other → error).
YES = "yes"
NO = "no"
ERROR = "error"

# Actions.
NEED_LOCAL = "need_local"
NEED_PROBE = "need_probe"
NEED_FETCH = "need_fetch"
NEED_MERGE_ANCESTRY = "need_merge_ancestry"
NEED_LEASE_ANCESTRY = "need_lease_ancestry"
RESTORE_LOCAL = "restore_local"
LOCAL_STATE_MISMATCH = "local_mismatch"
EXHAUSTED = "exhausted"
AUTH = "auth"
TRANSIENT = "transient"
COMPLETE = "complete"
REPUSH = "repush"
INTEGRATE = "integrate"
REWRITTEN = "rewritten"

TERMINAL_ACTIONS = (
    RESTORE_LOCAL, LOCAL_STATE_MISMATCH, EXHAUSTED, AUTH, TRANSIENT, COMPLETE, REPUSH,
    INTEGRATE, REWRITTEN,
)

# Transient reasons (``reconcile_last_outcome``).
OUT_LS_REMOTE_FAILED = "ls_remote_failed"
OUT_FETCH_FAILED = "fetch_failed"
OUT_REMOTE_MOVING = "remote_moving"
OUT_ANCESTRY_FAILED = "ancestry_failed"
OUT_PUSH_LEASE_REJECTED = "push_lease_rejected"
OUT_PUSH_RESULT_UNKNOWN = "push_result_unknown"
OUT_MERGE_TREE_FAILED = "merge_tree_failed"
OUT_CYCLE_BUDGET = "cycle_budget_spent"
OUT_INTEGRATION_LIMIT = "integration_limit"

# Push result classes (§3.4).
PUSH_SUCCESS = "success"
PUSH_AUTH = "auth"
PUSH_PERMISSION = "permission"
PUSH_LEASE_REJECTED = "lease_rejected"
PUSH_UNKNOWN = "unknown"

_PERMISSION_PHRASES = ("403", "protected branch", "pre-receive hook declined", "not allowed to push")
_LEASE_PHRASES = ("stale info", "fetch first", "non-fast-forward", "rejected")


@dataclass(frozen=True)
class Probe:
    """One fact read from a remote (``ls-remote``) or from ``fetch`` + ``FETCH_HEAD``.
    ``sha`` None means the read failed; ``cause`` says why (``auth``/``permission``/
    ``timeout``/``network``/...)."""

    sha: Optional[str]
    cause: Optional[str] = None


@dataclass(frozen=True)
class Inputs:
    cycle_count: int
    merge_commit: Optional[str]
    publish_commit: Optional[str]
    publish_lease: Optional[str]
    integrations: int = 0
    local: Optional[str] = None
    probe: Optional[Probe] = None
    fetch: Optional[Probe] = None
    merge_in_remote: Optional[str] = None
    lease_in_remote: Optional[str] = None
    max_cycles: int = RECONCILE_MAX_CYCLES
    max_integrations: int = RECONCILE_MAX_INTEGRATIONS


@dataclass(frozen=True)
class Decision:
    action: str
    reason: Optional[str] = None
    stage: Optional[str] = None
    cause: Optional[str] = None
    extra: dict = field(default_factory=dict)


def backoff_seconds(cycle: int) -> int:
    """``min(60 * 2**(c-1), 900)`` for the cycle number ``c`` after its increment."""
    c = max(int(cycle), 1)
    return min(RECONCILE_BACKOFF_BASE_SEC * 2 ** (c - 1), RECONCILE_BACKOFF_MAX_SEC)


def is_last_cycle(inputs: Inputs) -> bool:
    return inputs.cycle_count + 1 >= inputs.max_cycles


def transient(inputs: Inputs, reason: str) -> Decision:
    """§3.6: a transient outcome keeps the scheduled retry, unless this cycle was the
    last one allowed — then it becomes ``exhausted`` immediately."""
    if is_last_cycle(inputs):
        return Decision(EXHAUSTED, reason=reason)
    return Decision(TRANSIENT, reason=reason)


def _auth_cause(cause: Optional[str]) -> Optional[str]:
    return cause if cause in ("auth", "permission") else None


def decide(inputs: Inputs) -> Decision:
    if inputs.cycle_count >= inputs.max_cycles:
        return Decision(EXHAUSTED, reason=OUT_CYCLE_BUDGET)
    if inputs.local is None:
        return Decision(NEED_LOCAL)
    if inputs.local == LOCAL_RESTORABLE:
        return Decision(RESTORE_LOCAL)
    if inputs.local != LOCAL_OK:
        return Decision(LOCAL_STATE_MISMATCH, reason="local_state")
    if inputs.probe is None:
        return Decision(NEED_PROBE)
    observed = inputs.probe.sha
    if not observed:
        cause = _auth_cause(inputs.probe.cause)
        if cause:
            return Decision(AUTH, stage="ls_remote", cause=cause)
        return transient(inputs, OUT_LS_REMOTE_FAILED)
    if observed in (inputs.publish_commit, inputs.merge_commit):
        return Decision(COMPLETE, reason="observed")
    if observed == inputs.publish_lease:
        return Decision(REPUSH)
    if inputs.fetch is None:
        return Decision(NEED_FETCH)
    if not inputs.fetch.sha:
        cause = _auth_cause(inputs.fetch.cause)
        if cause:
            return Decision(AUTH, stage="fetch", cause=cause)
        return transient(inputs, OUT_FETCH_FAILED)
    if inputs.fetch.sha != observed:
        return transient(inputs, OUT_REMOTE_MOVING)
    if inputs.merge_in_remote is None:
        return Decision(NEED_MERGE_ANCESTRY)
    if inputs.merge_in_remote == YES:
        return Decision(COMPLETE, reason="contained")
    if inputs.merge_in_remote != NO:
        return transient(inputs, OUT_ANCESTRY_FAILED)
    if inputs.lease_in_remote is None:
        return Decision(NEED_LEASE_ANCESTRY)
    if inputs.lease_in_remote == YES:
        if inputs.integrations >= inputs.max_integrations:
            return Decision(EXHAUSTED, reason=OUT_INTEGRATION_LIMIT)
        return Decision(INTEGRATE)
    if inputs.lease_in_remote != NO:
        return transient(inputs, OUT_ANCESTRY_FAILED)
    return Decision(REWRITTEN, reason="remote_rewritten")


def classify_push(returncode: Optional[int], cause: Optional[str], stderr: Optional[str]) -> str:
    """§3.4, in this order (a hook rejection also says ``rejected``):
    success → auth → permission → lease rejection → unknown (timeout and the rest)."""
    if returncode == 0:
        return PUSH_SUCCESS
    if cause == "auth":
        return PUSH_AUTH
    text = (stderr or "").lower()
    if ("permission to" in text and "denied" in text) or any(p in text for p in _PERMISSION_PHRASES):
        return PUSH_PERMISSION
    if returncode != -1 and any(p in text for p in _LEASE_PHRASES):
        return PUSH_LEASE_REJECTED
    return PUSH_UNKNOWN


def classify_read_failure(cause: Optional[str], stderr: Optional[str]) -> Optional[str]:
    """``auth``/``permission`` for an ``ls-remote``/``fetch`` failure that a retry cannot
    fix; None for everything else (transient)."""
    if cause == "auth":
        return "auth"
    text = (stderr or "").lower()
    if ("permission to" in text and "denied" in text) or any(p in text for p in _PERMISSION_PHRASES):
        return "permission"
    return None

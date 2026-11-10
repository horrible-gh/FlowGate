"""Merge-session claim / lease (flowgate.v02.0153 T#4, T0011 §4).

A claim is the DB record "this instance is doing the automatic processing (sweep, startup
or reconcile) of this merge session right now". It is held for one processing pass and
released at the end. It sits OUTSIDE ``session_lock`` (the domain lock of the Git work):

    claim → session_lock → fresh re-read → decision → unlock → claim release

Correctness rests first on the orphan guard and the fresh re-read under the lock
(``orphan_guard`` / ``cleanup._judge_orphan``); the claim removes the window in which two
instances process the same session at once, and records who processed it last.

* Acquire never waits. A free row is taken with a CAS on ``claim_token IS NULL``; a row
  whose lease expired for others (``instance_registry.expired_for_others``) or whose owner
  is DEAD (``instance_registry.instance_state``) is reclaimed with a CAS on the observed
  token; anything else is busy (``None``). No clock rule of its own.
* Re-entry: the same thread asking again for a merge id whose claim it already holds — as
  the same instance — gets the same claim back with its depth raised; only the outermost
  release writes. A different instance id in the same thread (the tests' instance switch)
  is a different holder and goes through the DB like any other instance.
* ``check`` = ``renew`` then ``fence`` right before every guarded write (orphan close, hold
  and resume records, the TTL ``merge --abort``, the reconcile lock entry / push / reset /
  integration / local restore / completion). A false fence means: write nothing, warn, stop.
* Every other change made while the claim is held is fenced too, without a call site having
  to remember it (review rej_01M6ZWFPF1E6JE2M): ``db.git_integration`` runs
  :func:`_session_write_guard` before each session-row write (context, context CAS, close)
  and ``git_service._run_session_git`` runs :func:`require` before and after each audited
  Git command (preservation refs, fetch, push, reset, merge). When this thread holds the
  claim of that merge and the claim is no longer valid in the DB (token replaced, lease
  expired), :class:`ClaimLost` is raised and the write does not happen — also after a slow
  push / fetch whose lease ran out meanwhile. ``ClaimLost`` is a ``BaseException`` so no
  ``except Exception`` cleanup in between can swallow it and go on writing; ``held`` and
  ``git_service.reconcile_push_session`` turn it into a ``claim_lost`` outcome.
* Clock and instance id are read late through ``git_service.now_iso`` /
  ``git_service.current_instance_id`` so the 0153 fixtures' clock and instance switch
  (``env.clock``) apply to claims too. No instance id (even after a lazy registration) is a
  failed acquisition: the processing is skipped (fail closed).
* A process that dies keeps its row; the lease expiry and the owner's DEAD state free it.
"""
from __future__ import annotations

import logging
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from modules.flow_gate.db import merge_session_claim as db_claim

from . import instance_registry as registry
from . import lock_manager as locks

_log = logging.getLogger(__name__)

PURPOSE_SWEEP = "sweep"
PURPOSE_STARTUP = "startup"
PURPOSE_RECONCILE = "reconcile"
PURPOSES = (PURPOSE_SWEEP, PURPOSE_STARTUP, PURPOSE_RECONCILE)

LEASE_ENV = "FLOWGATE_MERGE_SESSION_CLAIM_LEASE_SEC"
LEASE_DEFAULT_SEC = 600
LEASE_MIN_SEC = 120
LEASE_MAX_SEC = 3600

OUTCOME_DEFAULT = "released"
OUTCOME_ERROR = "error"
OUTCOME_CLAIM_LOST = "claim_lost"
RECLAIMED_PREFIX = "reclaimed_from:"


def merge_session_claim_lease_sec() -> int:
    return int(locks._env_num(LEASE_ENV, LEASE_DEFAULT_SEC, LEASE_MIN_SEC, LEASE_MAX_SEC))


@dataclass
class Claim:
    merge_id: int
    token: str
    owner: str
    purpose: str
    claimed_at: str
    lease_until: str
    depth: int = 1
    reclaimed_from: Optional[str] = None
    outcome: Optional[str] = None
    lost: bool = False
    released: bool = False


class ClaimLost(BaseException):
    """The session claim this thread held is no longer valid in the DB (lease expired and
    taken over, or the token replaced). Raised before a fenced write; nothing is written.

    A ``BaseException`` on purpose: a broad ``except Exception`` between the write and the
    claim boundary must not swallow it and carry on with further writes. ``held`` and
    ``git_service.reconcile_push_session`` catch it for their own merge id."""

    def __init__(self, merge_id: int, step: str):
        super().__init__(f"merge session claim lost for merge {merge_id} before {step}")
        self.merge_id = int(merge_id)
        self.step = step


# ── late-bound clock / instance (the 0153 fixtures patch git_service) ─────────

def _gs():
    from modules.flow_gate.services import git_service
    return git_service


def _now() -> str:
    return _gs().now_iso()


def _instance_id() -> Optional[str]:
    gs = _gs()
    instance_id = gs.current_instance_id()
    if instance_id is None:
        locks._register_lazily()
        instance_id = gs.current_instance_id()
    return instance_id


# ── thread-local re-entry stack ──────────────────────────────────────────────

_tls = threading.local()


def _stack(merge_id: int) -> list:
    stacks = getattr(_tls, "stacks", None)
    if stacks is None:
        stacks = _tls.stacks = {}
    return stacks.setdefault(int(merge_id), [])


def current(merge_id: int) -> Optional[Claim]:
    """The innermost claim this thread holds for the session, if any."""
    stacks = getattr(_tls, "stacks", None)
    stack = stacks.get(int(merge_id)) if stacks else None
    return stack[-1] if stack else None


def _reset_thread_state() -> None:
    """Forget every claim this thread believes it holds (a test models a process death)."""
    _tls.stacks = {}


# ── acquire / renew / fence / release ────────────────────────────────────────

def acquire(merge_id: int, purpose: str) -> Optional[Claim]:
    """The claim, or None when it is busy, lost a race, or cannot be recorded."""
    merge_id = int(merge_id)
    try:
        instance_id = _instance_id()
    except Exception:
        _log.warning("merge session claim: instance id unavailable for merge %s", merge_id,
                     exc_info=True)
        instance_id = None
    if not instance_id:
        _log.warning("merge session claim: no server instance id — merge %s skipped (fail closed)",
                     merge_id)
        return None
    stack = _stack(merge_id)
    if stack and stack[-1].owner == instance_id and not stack[-1].lost:
        stack[-1].depth += 1
        return stack[-1]
    try:
        now = _now()
        db_claim.ensure_row(merge_id)
        row = db_claim.get(merge_id)
        if row is None:
            return None
        observed = row.get("claim_token")
        reclaimed = None
        if observed is not None:
            owner = row.get("owner_instance_id")
            expired = registry.expired_for_others(row.get("lease_until"), now)
            dead = registry.instance_state(owner, now) == registry.DEAD
            if not (expired or dead):
                _log.info("merge session claim busy: merge %s held by %s (%s) until %s",
                          merge_id, owner, row.get("purpose"), row.get("lease_until"))
                return None
            reclaimed = {"owner": owner, "purpose": row.get("purpose"),
                         "outcome": f"{RECLAIMED_PREFIX}{owner}", "at": now}
        token = secrets.token_hex(16)
        lease_until = registry.add_seconds(now, merge_session_claim_lease_sec())
        won = db_claim.take(merge_id, observed, token=token, owner=instance_id, purpose=purpose,
                            claimed_at=now, lease_until=lease_until, reclaimed=reclaimed)
    except Exception:
        _log.warning("merge session claim failed for merge %s (fail closed)", merge_id,
                     exc_info=True)
        return None
    if won != 1:
        _log.info("merge session claim lost the race for merge %s", merge_id)
        return None
    claim = Claim(merge_id=merge_id, token=token, owner=instance_id, purpose=purpose,
                  claimed_at=now, lease_until=lease_until)
    if reclaimed is not None:
        claim.reclaimed_from = reclaimed["owner"]
        _log.warning(
            "merge session claim reclaimed: merge %s from %s (token %s, purpose %s, "
            "lease %s) by %s for %s", merge_id, reclaimed["owner"], observed,
            reclaimed["purpose"], row.get("lease_until"), instance_id, purpose)
    stack.append(claim)
    return claim


def renew(claim: Claim) -> bool:
    """Token CAS lease extension. Advisory: ``fence`` decides (MySQL same-second note)."""
    try:
        lease_until = registry.add_seconds(_now(), merge_session_claim_lease_sec())
        if db_claim.renew(claim.merge_id, claim.token, lease_until) == 1:
            claim.lease_until = lease_until
            return True
    except Exception:
        _log.warning("merge session claim renew failed for merge %s", claim.merge_id, exc_info=True)
    return False


def fence(claim: Claim) -> bool:
    """True while the row still carries this token and the lease is valid for its holder."""
    try:
        row = db_claim.get(claim.merge_id)
    except Exception:
        _log.warning("merge session claim fence read failed for merge %s", claim.merge_id,
                     exc_info=True)
        return False
    if row is None or row.get("claim_token") != claim.token:
        return False
    return registry.valid_for_self(row.get("lease_until"), _now())


def check(merge_id: int, step: str) -> bool:
    """``renew`` + ``fence`` on this thread's claim right before a guarded write."""
    claim = current(merge_id)
    if claim is None:
        _log.warning("merge session claim missing before %s for merge %s — nothing written",
                     step, merge_id)
        return False
    renew(claim)
    if fence(claim):
        return True
    claim.lost = True
    _log.warning("merge session claim fence failed before %s for merge %s (owner %s) — "
                 "nothing written", step, merge_id, claim.owner)
    return False


def require(merge_id: int, step: str) -> None:
    """Fence a change of a session this thread is processing under its claim.

    No claim held by this thread for ``merge_id``: nothing to fence (user API paths,
    other sessions). A claim already marked lost, or one whose ``check`` fails now (the DB
    row carries another token or the lease is no longer valid for its holder): raise
    :class:`ClaimLost` — the caller's write does not happen."""
    claim = current(merge_id)
    if claim is None:
        return
    if claim.lost or not check(merge_id, step):
        claim.lost = True
        raise ClaimLost(int(merge_id), step)


def _session_write_guard(merge_id, operation: str) -> None:
    """``db.git_integration`` pre-write guard: every session-row write of a claimed session."""
    if merge_id is None:
        return
    require(int(merge_id), f"session_{operation}")


def note(merge_id: int, outcome: str) -> None:
    """Record the outcome the outermost release will write to ``last_outcome``."""
    claim = current(merge_id)
    if claim is not None:
        claim.outcome = outcome


def _release_row(claim: Claim, outcome: str) -> None:
    released_at = _now()
    if claim.reclaimed_from:
        outcome = f"{outcome}/{RECLAIMED_PREFIX}{claim.reclaimed_from}"
    if db_claim.release(claim.merge_id, claim.token, owner=claim.owner, purpose=claim.purpose,
                        outcome=outcome, released_at=released_at) != 1:
        _log.warning("merge session claim release found another token for merge %s "
                     "(lease expired and reclaimed?)", claim.merge_id)


def release(claim: Optional[Claim], outcome: Optional[str] = None) -> None:
    """Drop one level; the outermost level writes the release. Never raises."""
    if claim is None or claim.released:
        return
    claim.depth -= 1
    if claim.depth > 0:
        return
    claim.released = True
    stack = _stack(claim.merge_id)
    if claim in stack:
        stack.remove(claim)
    try:
        _release_row(claim, outcome or claim.outcome or OUTCOME_DEFAULT)
    except Exception:
        _log.warning("merge session claim release failed for merge %s (the lease will expire)",
                     claim.merge_id, exc_info=True)


@contextmanager
def held(merge_id: int, purpose: str) -> Iterator[Optional[Claim]]:
    """``with held(mid, purpose) as claim:`` — None when busy. Released in ``finally``,
    for ``BaseException`` too (a dying process is freed by lease expiry / owner DEAD).
    A :class:`ClaimLost` of this merge ends the pass quietly (outcome ``claim_lost``):
    nothing more is written and the other instance's result stands."""
    claim = acquire(merge_id, purpose)
    try:
        yield claim
    except ClaimLost as lost:
        if lost.merge_id != int(merge_id):
            raise
        if claim is not None:
            claim.outcome = OUTCOME_CLAIM_LOST
        _log.warning("merge session claim lost during %s of merge %s (before %s) — pass ended, "
                     "nothing written", purpose, merge_id, lost.step)
    except BaseException:
        if claim is not None and claim.outcome is None:
            claim.outcome = OUTCOME_ERROR
        raise
    finally:
        release(claim)


# Every session-row write (context, context CAS, close) of a session this thread processes
# under its claim passes the fence (review rej_01M6ZWFPF1E6JE2M). Registered once per process.
from modules.flow_gate.db import git_integration as _db_git  # noqa: E402

_db_git.on_session_write(_session_write_guard)

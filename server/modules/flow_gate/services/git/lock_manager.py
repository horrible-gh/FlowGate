"""Lock Manager (flowgate.default.0669 unit 1b; 0666 0009-L §2.1~2.9, §4.1, §4.2).

``acquire`` / ``release`` — the domain locks (P/G/W/B/R/M) on ``resource_lock``, owned
by an ``ExecutionContext``. Re-entry, order checks, stale reclaim and long-lock
heartbeats follow L 2.3~2.9. Unit 2 moved ordinary source mutation (tr2_file_policy)
and Source Bundle ensure onto G; unit 3 moved TR2 approval, TR commit, Time Machine
cancel, the TR conflict commit and the TR2 document delete guard; unit 4 moved
self-check and narrowed its recovery check to the Group.

Unit 9c (L 2.6 step 9): every old path has moved, so the Legacy Admission Gate and the
``legacy_acquire`` bridge over ``git_project_lock`` are gone. Admission is the lock row
alone.

Unit 5b: ``on_released`` lets the Job Runner turn a release into a wake-up; the sweep
daemon step (L 2.17.2) lives in ``job_sweeper`` and reuses ``is_stale`` /
``reclaim_stale_row``.

Unit 6a: a won G for a source-changing holder kind passes the Group freeze-claim guard
(L 2.10) before it is handed back; a claimed Group answers ``GROUP_FROZEN``.

Not here (later units or DEFERRED): fairness reservations (L 2.12), gated spawn /
exec_* recording (L 2.20.3).
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Callable, Optional

from modules.flow_gate.db import git_concurrency as db
from modules.flow_gate.db import request_cache as _request_cache
from modules.flow_gate.db.connection import get_store, in_transaction, now_iso

from . import instance_registry as registry

_log = logging.getLogger(__name__)


# ── parameters (L 1.1; FLOWGATE_ env override, clamped) ──────────────────────

def _env_num(name: str, default: float, low: float, high: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        _log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    return max(low, min(high, value))


def poll_interval_sec() -> float:
    return _env_num("FLOWGATE_LOCK_POLL_INTERVAL_SEC", 0.25, 0.05, 1)


def heartbeat_interval_sec() -> float:
    return _env_num("FLOWGATE_LOCK_HEARTBEAT_INTERVAL_SEC", 15, 5, 60)


def heartbeat_ttl_sec() -> int:
    return int(_env_num("FLOWGATE_LOCK_HEARTBEAT_TTL_SEC", 90, 3 * heartbeat_interval_sec(), 600))


def suspect_age_sec() -> int:
    return int(_env_num("FLOWGATE_LOCK_SUSPECT_AGE_SEC", 600, 60, 3600))


_WAIT_PARAMS = {
    # mode -> (env name, default, low, high)
    "interactive": ("FLOWGATE_INTERACTIVE_WAIT_SEC", 5, 0, 15),
    "freeze_interactive": ("FLOWGATE_FREEZE_INTERACTIVE_WAIT_SEC", 2, 0, 5),
    "selfcheck_start": ("FLOWGATE_SELFCHECK_START_WAIT_SEC", 5, 0, 15),
    "bundle_start": ("FLOWGATE_BUNDLE_WAIT_SEC", 5, 0, 15),
    "job": ("FLOWGATE_JOB_LOCK_WAIT_SEC", 0, 0, 2),
}

# One attempt, no wait: TR commit (L0007 §1 tr_commit_lock_wait_sec = 0).
NO_WAIT = "no_wait"


def wait_budget(domain: str, mode: str) -> float:
    """L 4.2."""
    if domain == "M":
        return _env_num("FLOWGATE_M_WAIT_SEC", 10, 1, 30)
    if mode == "interactive" and domain == "R":
        return _env_num("FLOWGATE_R_INTERACTIVE_WAIT_SEC", 5, 0, 15)
    spec = _WAIT_PARAMS.get(mode)
    return _env_num(*spec) if spec else 0.0


# ── keys and ranks (L 2.1.2, 2.1.3) ──────────────────────────────────────────

RANK = {"P": 1, "G": 2, "W": 3, "B": 4, "R": 5, "M": 6}

# L 2.7: holder kinds whose G/P/R/W/B hold is long (heartbeated). Everything else is short.
_LONG_G_KINDS = {"selfcheck", "bundle", "tr2_apply"}


# resource_lock.holder_kind has a CHECK (migration 132). A caller-side kind outside it keeps its
# own meaning for the hold class and the freeze guard, and is stored as the nearest listed kind
# (found by the 0669 Self-check overlap run: finalize's G was refused by the CHECK).
STORED_HOLDER_KINDS = frozenset((
    "selfcheck", "bundle", "tr2_apply", "source_mutation", "tr_commit", "time_machine", "tr_conflict",
    "approval_freeze", "publish", "worktree_provision", "worktree_cleanup", "base_mutation",
    "branch_meta", "branch_merge", "archive", "rerere", "merge_resolve", "review_action",
    "project_provision", "sweeper", "legacy_bridge"))
_STORED_AS = {
    "finalize": "publish", "approval_retry": "review_action", "dispose": "worktree_cleanup",
    "sweep": "sweeper", "work_base_confirm": "source_mutation", "initial_sync": "worktree_provision",
}


def stored_holder_kind(holder_kind: str) -> str:
    if holder_kind in STORED_HOLDER_KINDS:
        return holder_kind
    return _STORED_AS.get(holder_kind, "review_action")


def default_hold_class(domain: str, holder_kind: str) -> str:
    if domain == "M":
        return "short"
    if domain in ("R", "P"):
        return "long"
    if domain == "G" and holder_kind in _LONG_G_KINDS:
        return "long"
    if domain in ("W", "B") and holder_kind == "publish":
        return "long"
    return "short"


def _escape(component: str) -> str:
    return component.replace("\\", "\\\\").replace("\x1f", "\\u001f")


def normalize_target(target_key: Optional[str]) -> str:
    t = (target_key or "").strip()
    if not t:
        raise LockProgramError("empty_target_key")
    return t.lower() if os.name == "nt" else t


def lock_key(domain: str, project_id: str, group_id: Optional[str] = None,
             target_key: Optional[str] = None) -> str:
    """L 2.1.2: domain + ':' + sha256 of the escaped scope. Always 66 chars."""
    if domain == "G":
        if not group_id:
            raise LockProgramError("g_without_group")
        scope = [project_id, group_id]
    elif domain == "W":
        scope = [project_id, normalize_target(target_key)]
    elif domain in ("P", "B", "R", "M"):
        scope = [project_id]
    else:
        raise LockProgramError("unknown_domain")
    canonical = "\x1f".join(_escape(str(c)) for c in scope)
    return domain + ":" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── errors and outcomes ──────────────────────────────────────────────────────

class LockProgramError(RuntimeError):
    """A caller bug (L 2.3.2 / 2.4 / 2.5.1): never a retry target, surfaces as 5xx."""

    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code = code
        self.details = details


ACQUIRED = "acquired"
BUSY = "busy"
RECOVERY_REQUIRED = "recovery_required"
STORE_ERROR = "store_error"
GROUP_FROZEN = "group_frozen"
_RETRY_WHILE_WAITING = (BUSY,)


@dataclass
class LockOutcome:
    kind: str
    lock_key: Optional[str] = None
    lock_epoch: Optional[str] = None
    reentrant: bool = False
    blocker: Optional[dict] = None
    cause: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.kind == ACQUIRED


def blocker_from(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {key: row.get(key) for key in (
        "domain", "project_id", "group_id", "target_key", "holder_kind", "holder_ctx_id",
        "instance_id", "acquired_at", "hold_class", "protected", "protect_reason")}


# ── execution context (L 2.3) ────────────────────────────────────────────────

@dataclass
class Held:
    lock_key: str
    domain: str
    project_id: str
    group_id: Optional[str]
    target_key: Optional[str]
    lock_epoch: str
    hold_class: str
    count: int = 1
    lost: bool = False
    heartbeat_failures: int = 0


@dataclass(eq=False)
class ExecutionContext:
    ctx_id: str
    kind: str
    job_id: Optional[str] = None
    held: list = field(default_factory=list)
    lock_lost: bool = False
    on_lock_lost: Optional[Callable[["ExecutionContext", Held], None]] = None

    def find_held(self, key: str) -> Optional[Held]:
        for h in self.held:
            if h.lock_key == key:
                return h
        return None


_live_contexts: "weakref.WeakSet[ExecutionContext]" = weakref.WeakSet()
_live_guard = threading.Lock()
_slot = threading.local()


def new_context(kind: str = "req", ident: Optional[str] = None, *,
                job_id: Optional[str] = None) -> ExecutionContext:
    """ctx_id per L 2.3.1: 'req:{32hex}' when no ident, otherwise '{kind}:{ident}'."""
    ctx_id = f"{kind}:{ident}" if ident else f"{kind}:{secrets.token_hex(16)}"
    ctx = ExecutionContext(ctx_id=ctx_id[:120], kind=kind, job_id=job_id)
    with _live_guard:
        _live_contexts.add(ctx)
    return ctx


def bind(ctx: ExecutionContext) -> None:
    current = getattr(_slot, "ctx", None)
    if current is not None and current is not ctx:
        raise LockProgramError("context_already_bound", bound=current.ctx_id, requested=ctx.ctx_id)
    _slot.ctx = ctx


def unbind(ctx: ExecutionContext) -> None:
    if getattr(_slot, "ctx", None) is ctx:
        _slot.ctx = None
    if ctx.held:
        # L 2.4 operation mode: warn and release what leaked.
        _log.warning("lock_leak: context %s unbound holding %s", ctx.ctx_id,
                     [h.lock_key for h in ctx.held])
        for h in list(reversed(ctx.held)):
            h.count = 1
            release(ctx, h.lock_key)


def current_context() -> Optional[ExecutionContext]:
    return getattr(_slot, "ctx", None)


def _resolve(ctx: Optional[ExecutionContext]) -> ExecutionContext:
    ctx = ctx or current_context()
    if ctx is None:
        raise LockProgramError("no_execution_context")
    return ctx


def _mark_all_lost(_old_instance_id: str) -> None:
    """Instance declared dead (L 2.8): every live context's locks may be reclaimed already."""
    with _live_guard:
        contexts = list(_live_contexts)
    for ctx in contexts:
        ctx.lock_lost = True
        for h in ctx.held:
            h.lost = True


registry.on_instance_lost(_mark_all_lost)


# ── order check (L 2.4) ──────────────────────────────────────────────────────

def check_order(ctx: ExecutionContext, domain: str, key: str) -> None:
    if ctx.find_held(key) is not None or not ctx.held:
        return
    if any(h.domain == "M" for h in ctx.held):
        raise LockProgramError("lock_order_violation", reason="m_is_leaf",
                               held=[h.domain for h in ctx.held], requested=domain)
    top = max(RANK[h.domain] for h in ctx.held)
    r = RANK[domain]
    if r > top:
        return
    if r == top and domain in ("G", "W"):
        same = [h.lock_key for h in ctx.held if h.domain == domain]
        if key > max(same):
            return
        raise LockProgramError("lock_order_violation", reason="same_domain_key_order",
                               held=[h.domain for h in ctx.held], requested=domain)
    raise LockProgramError("lock_order_violation", reason="rank_order",
                           held=[h.domain for h in ctx.held], requested=domain)


# ── stale judgement and reclaim (L 2.9) ──────────────────────────────────────

LIVE = "live"
SUSPECT = "suspect"
PROTECTED = "protected"
STALE_RECLAIMABLE = "stale_reclaimable"


def _age_sec(since: Optional[str], now: str) -> float:
    from datetime import datetime
    if not since:
        return 0.0
    try:
        return (datetime.fromisoformat(now) - datetime.fromisoformat(since)).total_seconds()
    except ValueError:
        return 0.0


def is_stale(row: dict, now: Optional[str] = None) -> str:
    now = now or now_iso()
    if int(row.get("protected") or 0) == 1:
        return PROTECTED
    if registry.instance_state(row.get("instance_id"), now) == registry.DEAD:
        return STALE_RECLAIMABLE
    if row.get("hold_class") == "long" and registry.expired_for_others(row.get("heartbeat_until"), now):
        return STALE_RECLAIMABLE
    if row.get("hold_class") == "short" and _age_sec(row.get("acquired_at"), now) >= suspect_age_sec():
        return SUSPECT
    return LIVE


def reclaim_stale_row(row: dict) -> bool:
    """L 2.9.2, minus gated spawn (no path records exec_* before unit L 2.20.3 lands).

    A row that nevertheless carries an exec record is protected rather than deleted:
    without the gated-spawn contract nothing proves its child process is gone.
    """
    try:
        if row.get("exec_state") or row.get("exec_pid"):
            db.protect_lock(row["lock_key"], row["lock_epoch"], "exec_unrecorded", now_iso())
            _log.error("lock %s has an exec record without gated spawn; protected", row["lock_key"])
            return False
        n = db.reclaim_lock(row["lock_key"], row["lock_epoch"], int(row.get("exec_seq") or 0))
    except Exception:
        _log.warning("stale lock reclaim failed for %s", row.get("lock_key"), exc_info=True)
        return False
    if n == 1:
        _log.warning("lock_reclaimed: %s", blocker_from(row))
        return True
    return False


# ── acquire / release (L 2.5) ────────────────────────────────────────────────

def _fresh_lock_row(key: str) -> Optional[dict]:
    _request_cache.invalidate()     # bypass_cache=true (L 2.5.2/2.5.3)
    return db.get_resource_lock(key)


def _new_epoch() -> str:
    return "le_" + secrets.token_hex(12)     # 27 chars (DB §2.1 lock_epoch)


def group_recovery_check(project_id: str, group_id: str) -> bool:
    """L 2.5.2 (1) for G: True = this Group's self-check recovery is incomplete (D 3.4).

    Another Group's incomplete run no longer blocks this one.
    """
    from modules.flow_gate.db import tr_self_check_runs as _selfcheck_runs
    return bool(_selfcheck_runs.has_group_recovery_incomplete(project_id, group_id))


# ── self-check restart recovery (L 2.24.3) ───────────────────────────────────

def selfcheck_ctx_id(run_id: str) -> str:
    return f"scr:{run_id}"


def selfcheck_release_recovered(project_id: str, group_id: str, run_id: str) -> None:
    """unprotect_and_release: the run's process is proved gone."""
    db.recovery_release_lock(lock_key("G", project_id, group_id), selfcheck_ctx_id(run_id))


def selfcheck_ensure_protected(project_id: str, group_id: str, run_id: str) -> bool:
    """ensure_protected_g: keep (or create) this Group's G as a protected row.

    False = the Group could not be protected here (store error, or G held by someone
    else). The run stays recovery_incomplete either way, and that alone keeps the
    Group's G closed (``group_recovery_check``).
    """
    key = lock_key("G", project_id, group_id)
    holder = selfcheck_ctx_id(run_id)
    now = now_iso()
    try:
        _request_cache.invalidate()
        row = db.get_resource_lock(key)
        if row is not None:
            if row.get("holder_ctx_id") != holder:
                _log.error("selfcheck recovery: G of %s/%s held by %s, not %s",
                           project_id, group_id, row.get("holder_ctx_id"), holder)
                return False
            if int(row.get("protected") or 0) == 1:
                return True
            return db.protect_lock(key, row["lock_epoch"], "selfcheck_reap_unconfirmed", now) == 1
        instance_id = registry.current_instance_id() or _register_lazily()
        if instance_id is None:
            return False
        epoch = _new_epoch()
        with get_store().transaction():
            db.insert_lock(
                lock_key=key, domain="G", project_id=project_id, group_id=group_id,
                target_key=None, holder_ctx_id=holder, holder_kind="selfcheck", job_id=None,
                instance_id=instance_id, lock_epoch=epoch, hold_class="long",
                acquired_at=now, heartbeat_until=None,
            )
            db.protect_lock(key, epoch, "selfcheck_reap_unconfirmed", now)
        return True
    except Exception:
        _log.warning("selfcheck recovery: protecting G of %s/%s failed", project_id, group_id,
                     exc_info=True)
        return False


def _register_lazily() -> Optional[str]:
    """A process that never ran server startup (tests, scripts) still needs an owner id."""
    try:
        return registry.startup()
    except Exception:
        _log.warning("server instance registration failed", exc_info=True)
        return None


def _try_acquire_once(ctx: ExecutionContext, domain: str, project_id: str, group_id: Optional[str],
                      target_key: Optional[str], key: str, holder_kind: str, hold_class: str,
                      attempt: int = 0) -> LockOutcome:
    now = now_iso()
    # (1) protection / Group recovery — read only
    row = _fresh_lock_row(key)
    if row is not None and int(row.get("protected") or 0) == 1:
        return LockOutcome(RECOVERY_REQUIRED, key, blocker=blocker_from(row), cause="protected")
    if domain == "G" and group_recovery_check(project_id, group_id):
        return LockOutcome(RECOVERY_REQUIRED, key, cause="selfcheck_recovery_incomplete")
    # (2) fairness reservation: DEFERRED (no jobs in 0669)
    # (3) stale occupant
    if row is not None and row.get("holder_ctx_id") != ctx.ctx_id:
        if is_stale(row, now) == STALE_RECLAIMABLE:
            reclaim_stale_row(row)
        else:
            return LockOutcome(BUSY, key, blocker=blocker_from(row))
    instance_id = registry.current_instance_id() or _register_lazily()
    if instance_id is None:
        return LockOutcome(STORE_ERROR, key, cause="no_server_instance")
    # (4) insert, in a transaction of its own (D 8.4)
    epoch = _new_epoch()
    try:
        with get_store().transaction():
            db.insert_lock(
                lock_key=key, domain=domain, project_id=project_id, group_id=group_id,
                target_key=target_key, holder_ctx_id=ctx.ctx_id,
                holder_kind=stored_holder_kind(holder_kind), job_id=ctx.job_id, instance_id=instance_id, lock_epoch=epoch,
                hold_class=hold_class, acquired_at=now,
                heartbeat_until=registry.add_seconds(now, heartbeat_ttl_sec()) if hold_class == "long" else None,
            )
    except Exception as exc:
        return _classify_insert_failure(ctx, domain, project_id, group_id, target_key, key,
                                        holder_kind, hold_class, epoch, exc, attempt)
    return LockOutcome(ACQUIRED, key, lock_epoch=epoch)


def _classify_insert_failure(ctx, domain, project_id, group_id, target_key, key, holder_kind,
                             hold_class, epoch, exc, attempt) -> LockOutcome:
    """L 2.5.3: judge by a fresh re-read, never by the driver's error text."""
    try:
        row = _fresh_lock_row(key)
    except Exception:
        return LockOutcome(STORE_ERROR, key, cause=f"reread:{type(exc).__name__}")
    if row is not None:
        if row.get("holder_ctx_id") == ctx.ctx_id and row.get("lock_epoch") == epoch:
            return LockOutcome(ACQUIRED, key, lock_epoch=epoch)
        if int(row.get("protected") or 0) == 1:
            return LockOutcome(RECOVERY_REQUIRED, key, blocker=blocker_from(row), cause="protected")
        return LockOutcome(BUSY, key, blocker=blocker_from(row))
    if attempt < 1:     # LOCK_INSERT_RETRY
        return _try_acquire_once(ctx, domain, project_id, group_id, target_key, key,
                                 holder_kind, hold_class, attempt + 1)
    _log.warning("lock insert failed with no row present: %s", key, exc_info=exc)
    return LockOutcome(STORE_ERROR, key, cause=f"insert:{type(exc).__name__}")


def acquire(domain: str, project_id: str, *, group_id: Optional[str] = None,
            target_key: Optional[str] = None, holder_kind: str, mode: str = "interactive",
            hold_class: Optional[str] = None, ctx: Optional[ExecutionContext] = None) -> LockOutcome:
    """L 2.5.1 / 4.1. Returns an outcome; only caller bugs raise (LockProgramError)."""
    ctx = _resolve(ctx)
    if domain not in RANK:
        raise LockProgramError("unknown_domain", domain=domain)
    key = lock_key(domain, project_id, group_id, target_key)
    h = ctx.find_held(key)
    if h is not None:
        h.count += 1
        return LockOutcome(ACQUIRED, key, lock_epoch=h.lock_epoch, reentrant=True)
    check_order(ctx, domain, key)
    if in_transaction():
        raise LockProgramError("lock_acquire_inside_transaction", requested=domain)
    hold_class = hold_class or default_hold_class(domain, holder_kind)
    deadline = time.monotonic() + wait_budget(domain, mode)
    interval = poll_interval_sec()
    while True:
        outcome = _try_acquire_once(ctx, domain, project_id, group_id, target_key, key,
                                    holder_kind, hold_class)
        if outcome.ok:
            held = Held(key, domain, project_id, group_id, target_key, outcome.lock_epoch, hold_class)
            ctx.held.append(held)
            if hold_class == "long":
                _heartbeats.add(ctx, held)
            if domain == "G" and holder_kind in _FREEZE_REJECTED_KINDS:
                return _guard_group_admission(ctx, key, project_id, group_id, outcome)
            return outcome
        if outcome.kind not in _RETRY_WHILE_WAITING:
            return outcome
        if time.monotonic() + interval > deadline:
            return outcome
        time.sleep(interval)


# L 2.10: holder kinds a Group freeze claim turns away. selfcheck and bundle read the
# frozen SHA's content and stay allowed; approval_freeze is the claim's own job.
_FREEZE_REJECTED_KINDS = frozenset(("source_mutation", "tr2_apply", "tr_commit", "time_machine",
                                    "tr_conflict"))


def _guard_group_admission(ctx: ExecutionContext, key: str, project_id: str, group_id: str,
                           outcome: LockOutcome) -> LockOutcome:
    """L 2.10 guard_group_admission, right after G is won and before anything runs.

    Judged on the durable claim, not on a lock, so it outlives a stale-reclaimed G (D
    test 35). A claim read failure fails closed. Re-entry never gets here: the outer
    acquire already passed the guard.
    """
    from modules.flow_gate.db import operation_job as db_jobs
    try:
        _request_cache.invalidate()
        claim = db_jobs.get_group_freeze_claim(group_id)
    except Exception:
        _log.warning("freeze claim read failed for %s/%s", project_id, group_id, exc_info=True)
        release(ctx, key)
        return LockOutcome(STORE_ERROR, key, cause="freeze_claim_unreadable")
    if claim is None or claim.get("job_id") == ctx.job_id:
        return outcome
    release(ctx, key)
    return LockOutcome(GROUP_FROZEN, key, cause="group_frozen_for_approval", blocker={
        "kind": "approval_freeze", "project_id": project_id, "group_id": group_id,
        "job_id": claim.get("job_id"), "state": claim.get("state"), "since": claim.get("at")})


def release(ctx_or_key, key: Optional[str] = None) -> None:
    """release(ctx, key) or release(key) with the bound context (L 2.3.3, 2.5.4)."""
    if key is None:
        ctx, key = _resolve(None), ctx_or_key
    else:
        ctx = _resolve(ctx_or_key)
    h = ctx.find_held(key)
    if h is None:
        raise LockProgramError("release_not_held", lock_key=key)
    h.count -= 1
    if h.count > 0:
        return
    ctx.held.remove(h)
    _heartbeats.remove(h)
    _db_release(ctx, h)


def _db_release(ctx: ExecutionContext, h: Held, retry_delay: float = 0.0) -> None:
    try:
        with get_store().transaction():
            n = db.delete_lock(h.lock_key, ctx.ctx_id, h.lock_epoch)
    except Exception:
        delay = min(60.0, max(1.0, retry_delay * 2))
        _log.warning("lock_release_store_error: %s; retry in %ss", h.lock_key, delay, exc_info=True)
        timer = threading.Timer(delay, _db_release, args=(ctx, h, delay))
        timer.daemon = True
        timer.start()
        return
    if n == 0:
        row = _fresh_lock_row(h.lock_key)
        if row is not None and row.get("lock_epoch") == h.lock_epoch and int(row.get("protected") or 0) == 1:
            _log.info("release_skipped_protected: %s", h.lock_key)
        else:
            _log.warning("lock_lost_before_release: %s", h.lock_key)
            ctx.lock_lost = True
        return
    _notify_released(h)


# Release listeners (unit 5b): the Job Runner turns a committed release into a
# DOMAIN_RELEASED wake-up (L 2.17.1). Called after the delete committed, never raises.
_released_listeners: list[Callable[[Held], None]] = []


def on_released(callback: Callable[[Held], None]) -> None:
    _released_listeners.append(callback)


def _notify_released(h: Held) -> None:
    for callback in list(_released_listeners):
        try:
            callback(h)
        except Exception:
            _log.warning("lock release listener failed", exc_info=True)


def protect(ctx: ExecutionContext, key: str, reason: str) -> bool:
    """L 2.5.5: keep the row for recovery, detach it from the context."""
    h = ctx.find_held(key)
    if h is None:
        raise LockProgramError("protect_not_held", lock_key=key)
    n = db.protect_lock(h.lock_key, h.lock_epoch, reason, now_iso())
    _heartbeats.remove(h)
    ctx.held.remove(h)
    if n == 0:
        _log.error("protect_failed_lock_lost: %s", key)
        return False
    return True


# ── caller helpers (unit 2) ──────────────────────────────────────────────────

# P §1: outcome -> refined reason code and retryable. Callers keep their old error code
# (legacy first, P §0.5) and carry these next to it.
_REASON = {
    BUSY: ("lock_busy", True),
    RECOVERY_REQUIRED: ("recovery_required", False),
    STORE_ERROR: ("store_error", True),
    GROUP_FROZEN: ("group_frozen_for_approval", False),
}


def outcome_details(outcome: LockOutcome) -> dict:
    reason, retryable = _REASON.get(outcome.kind, (outcome.kind, False))
    details = {"reason_code": reason, "retryable": retryable}
    if outcome.cause:
        details["cause"] = outcome.cause
    if outcome.blocker:
        details["blocker"] = outcome.blocker
    return details


def acquire_group(project_id: str, group_id: str, *, holder_kind: str,
                  mode: str = "interactive") -> tuple[LockOutcome, ExecutionContext]:
    """G for one call: the bound context when there is one (re-entry), else a fresh one.

    The caller releases with ``release(ctx, outcome.lock_key)`` only when outcome.ok.
    """
    ctx = current_context() or new_context("req")
    outcome = acquire("G", project_id, group_id=group_id, holder_kind=holder_kind,
                      mode=mode, ctx=ctx)
    return outcome, ctx


def still_held(ctx: ExecutionContext, key: str) -> bool:
    """Is this hold still ours in the store? (fresh read before a source write)."""
    h = ctx.find_held(key)
    if h is None or h.lost or ctx.lock_lost:
        return False
    try:
        row = _fresh_lock_row(key)
    except Exception:
        return False
    return bool(row and row.get("holder_ctx_id") == ctx.ctx_id
                and row.get("lock_epoch") == h.lock_epoch)


# ── long-lock heartbeat (L 2.7) ──────────────────────────────────────────────

class _Heartbeats:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[int, tuple[ExecutionContext, Held]] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def add(self, ctx: ExecutionContext, h: Held) -> None:
        with self._guard:
            self._entries[id(h)] = (ctx, h)
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="lock-heartbeat", daemon=True)
                self._thread.start()

    def remove(self, h: Held) -> None:
        with self._guard:
            self._entries.pop(id(h), None)

    def _loop(self) -> None:
        while not self._stop.wait(heartbeat_interval_sec()):
            self.beat_once()

    def beat_once(self) -> None:
        with self._guard:
            entries = list(self._entries.values())
        for ctx, h in entries:
            until = registry.add_seconds(now_iso(), heartbeat_ttl_sec())
            try:
                n = db.heartbeat_long_lock(h.lock_key, h.lock_epoch, ctx.ctx_id, until)
            except Exception:
                h.heartbeat_failures += 1
                margin = registry.clock_skew_margin_sec()
                if h.heartbeat_failures * heartbeat_interval_sec() >= heartbeat_ttl_sec() - margin:
                    self._lost(ctx, h)
                continue
            if n == 1:
                h.heartbeat_failures = 0
            else:
                self._lost(ctx, h)

    def _lost(self, ctx: ExecutionContext, h: Held) -> None:
        ctx.lock_lost = True
        h.lost = True
        self.remove(h)
        _log.error("lock_heartbeat_lost: %s (%s)", h.lock_key, ctx.ctx_id)
        if ctx.on_lock_lost is not None:
            try:
                ctx.on_lock_lost(ctx, h)
            except Exception:
                _log.warning("on_lock_lost callback failed", exc_info=True)


_heartbeats = _Heartbeats()

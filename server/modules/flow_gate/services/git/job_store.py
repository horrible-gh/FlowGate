"""Operation Job Store (flowgate.default.0669 units 5a/5b; 0666 0009-L §1.3, §2.13~2.16, §2.19.1, §3.2).

Creation with request_key idempotency, the three claim modes, lease renewal and
checks, the fenced ``job_write`` and run-result classification with backoff.
Storage is ``db.operation_job``; every state move is a single CAS on one job row.

Invariants this module keeps (0669 conversation seq 44):

* I1 — claim is one job per CAS. A candidate list may be read in bulk, but a job is
  won only by an affected count of 1; 0 is LOST.
* I2 — every write of a running/freezing job is conditioned on job_id + lease_token +
  expected status set. 0 rows is LeaseLost: the executor writes nothing more, releases
  the locks it holds and stops.
* I3 — running/freezing are not a source status of any claim, so an expired lease is
  never re-claimed here; only the Sweeper's reconcile (``job_sweeper``, unit 5b) moves
  it.
* I4 — a kind with a freeze phase is never created as pending, and its publish claim
  requires freeze_completed with sha/tree/pin (the DB CHECK repeats this).
* I5 — terminal statuses never change: every transition has a non-terminal WHERE, and
  the same request_key returns the terminal result (a different fingerprint is
  ``request_key_conflict``).
* I6 — a job lease is not a resource lock. Locks are taken through the job's own
  ``ExecutionContext`` (ctx_id ``job:{job_id}:{lease_token}``) and live on their own.

Runner, Notifier, sweep and reconcile are ``job_runner`` / ``job_notifier`` /
``job_sweeper`` (5b); freeze F1~F5 and freeze_release are ``approval_freeze`` (6a),
writing through ``fenced_write``. Not here: kind phase executors — the final approval
(``approval_publish``, 6b) is the first path that creates jobs; the other kinds are 7~8.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

from modules.flow_gate.db import operation_job as db
from modules.flow_gate.db import request_cache as _request_cache
from modules.flow_gate.db.connection import get_store, in_transaction, now_iso

from . import instance_registry as registry
from . import job_notifier as notifier
from . import lock_manager as locks
from .lock_manager import _env_num

_log = logging.getLogger(__name__)


# ── parameters (L 1.3; FLOWGATE_ env override, clamped) ──────────────────────

def job_lease_sec() -> int:
    return int(_env_num("FLOWGATE_JOB_LEASE_SEC", 60, 30, 600))


def lease_renew_interval_sec() -> float:
    return _env_num("FLOWGATE_JOB_LEASE_RENEW_INTERVAL_SEC", 15, 5, job_lease_sec() / 3)


def lease_min_remaining_sec() -> int:
    return 2 * registry.clock_skew_margin_sec()


def retry_base_sec() -> float:
    return _env_num("FLOWGATE_RETRY_BASE_SEC", 5, 1, 60)


def retry_max_delay_sec() -> float:
    return _env_num("FLOWGATE_RETRY_MAX_DELAY_SEC", 300, retry_base_sec(), 3600)


def retry_jitter_ratio() -> float:
    return _env_num("FLOWGATE_RETRY_JITTER_RATIO", 0.2, 0, 0.5)


def retry_max_attempts() -> int:
    return int(_env_num("FLOWGATE_RETRY_MAX_ATTEMPTS", 8, 1, 50))


def reconcile_retry_interval_sec() -> int:
    return int(_env_num("FLOWGATE_RECONCILE_RETRY_INTERVAL_SEC", 60, 10, 600))


# ── kinds and identifiers (L 1.6, L 2.13.1, L 2.28) ──────────────────────────

KINDS = ("final_approval_publish", "branch_merge_publish", "worktree_provision",
         "worktree_cleanup", "base_publish", "archive_preserve", "archive_restore",
         "archive_purge", "project_provision")
FREEZE_KINDS = frozenset(("final_approval_publish",))

# Meaning fields per kind — requester and time excluded (L 2.13.1).
_CANONICAL_FIELDS = {
    "final_approval_publish": ("doc_id", "doc_revision_no", "git_action", "target_branch",
                               "commit_title", "group_id"),
    "worktree_provision": ("group_id", "group_generation"),
    "worktree_cleanup": ("group_id", "reason", "group_generation"),
    "branch_merge_publish": ("branch_merge_attempt_id",),
    "base_publish": ("project_id", "action", "source_identity"),
    "archive_preserve": ("archive_id", "action_seq"),
    "archive_restore": ("archive_id", "action_seq"),
    "archive_purge": ("archive_id", "action_seq"),
    "project_provision": ("project_id", "config_generation"),
}

PUBLISH = "publish"
FREEZE = "freeze"
RELEASE = "release"
_CLAIMS = {PUBLISH: db.claim_publish, FREEZE: db.claim_freeze, RELEASE: db.claim_release}

EXECUTING = frozenset(db.EXECUTING_STATUSES)


class JobProgramError(RuntimeError):
    """A caller broke the store's contract (not a runtime race)."""

    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code = code
        self.details = details


class LeaseLost(Exception):
    """The job is no longer ours. Write nothing, release locks, stop (L 2.15)."""


def new_job_id() -> str:
    return "opj_" + secrets.token_hex(13)       # 30 chars (DB §2.3 job_id)


def new_lease_token() -> str:
    return "lt_" + secrets.token_hex(16)        # 35 chars (DB §2.3 lease_token)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_or_none(value) -> Optional[str]:
    if value is None or isinstance(value, str):
        return value
    return _canonical_json(value)


def _normalize_branch(name) -> Optional[str]:
    if name is None:
        return None
    name = str(name).strip()
    return name[len("refs/heads/"):] if name.startswith("refs/heads/") else name


def _require(kind: str, req: dict, *names: str) -> list:
    missing = [n for n in names if req.get(n) in (None, "")]
    if missing:
        raise JobProgramError("request_key_required", kind=kind, missing=missing)
    return [str(req[n]) for n in names]


def derive_request_key(kind: str, project_id: str, req: dict) -> str:
    """L 2.13.1. A client-supplied key wins; otherwise the kind's deterministic key."""
    if kind not in KINDS:
        raise JobProgramError("unknown_job_kind", kind=kind)
    client_key = req.get("client_request_key")
    if client_key:
        return f"{kind}:{client_key}"
    if kind == "final_approval_publish":
        doc_id, rev = _require(kind, req, "doc_id", "doc_revision_no")
        # Concurrent clicks share a key. Failed, cancelled, and conflict hand-off
        # attempts advance it; a completed publish remains idempotent.
        seq = db.count_fap_retry_by_key_prefix(project_id, f"fap:{doc_id}:r")
        return f"fap:{doc_id}:r{rev}:{seq}"
    if kind == "worktree_provision":
        return "wtp:" + ":".join(_require(kind, req, "group_id", "group_generation"))
    if kind == "worktree_cleanup":
        return "wtc:" + ":".join(_require(kind, req, "group_id", "reason", "group_generation"))
    if kind == "branch_merge_publish":
        return "bmp:" + ":".join(_require(kind, req, "branch_merge_attempt_id"))
    if kind == "base_publish":
        return "bsp:" + ":".join([project_id] + _require(kind, req, "action", "source_identity"))
    if kind.startswith("archive_"):
        return f"{kind}:" + ":".join(_require(kind, req, "archive_id", "action_seq"))
    if kind == "project_provision":
        return "prv:" + ":".join([project_id] + _require(kind, req, "config_generation"))
    raise JobProgramError("request_key_required", kind=kind)


def fingerprint(kind: str, project_id: str, req: dict) -> str:
    """sha256 of the kind's meaning fields as canonical JSON (sorted keys, no spaces, UTF-8)."""
    fields = {}
    for name in _CANONICAL_FIELDS[kind]:
        value = project_id if name == "project_id" else req.get(name)
        if name == "target_branch":
            value = _normalize_branch(value)
        fields[name] = value
    return _sha256_hex(_canonical_json(fields))


# ── execution context of one claim (L 2.3.1, L 2.15) ─────────────────────────

@dataclass(eq=False)
class JobContext:
    job_id: str
    lease_token: str
    lease_until_local: str
    job: dict
    expected_statuses: frozenset = EXECUTING
    lock_ctx: Optional[locks.ExecutionContext] = None
    lease_lost: bool = False
    _guard: threading.Lock = field(default_factory=threading.Lock, repr=False)


def _job_context(job: dict, token: str, until: str) -> JobContext:
    lock_ctx = locks.new_context("job", f"{job['job_id']}:{token}", job_id=job["job_id"])
    return JobContext(job_id=job["job_id"], lease_token=token, lease_until_local=until,
                      job=job, lock_ctx=lock_ctx)


def _fresh_job(job_id: str) -> Optional[dict]:
    _request_cache.invalidate()     # bypass_cache=true
    return db.get_job(job_id)


def _instance_id() -> Optional[str]:
    return registry.current_instance_id() or locks._register_lazily()


# ── create / get (L 2.13.2) ──────────────────────────────────────────────────

CREATED = "created"
EXISTING = "existing"
REJECTED = "rejected"
STORE_ERROR = "store_error"


@dataclass
class CreateResult:
    outcome: str
    job: Optional[dict] = None
    ctx: Optional[JobContext] = None
    code: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome in (CREATED, EXISTING)


def _compare_existing(job: dict, fp: str) -> CreateResult:
    if job.get("request_fingerprint") != fp:
        return CreateResult(REJECTED, job=job, code="request_key_conflict")
    return CreateResult(EXISTING, job=job)      # a terminal job comes back as it is (D 3.6.3)


def create_or_get_job(kind: str, project_id: str, req: dict, *, group_id: Optional[str] = None,
                      target_key: Optional[str] = None, payload=None,
                      requested_by: Optional[str] = None, wake: bool = True) -> CreateResult:
    """Create a job or return the one this request already made.

    A freeze-phase kind is born ``freezing`` with a lease held by the request context
    (ctx returned, no wake-up signal); any other kind is born ``pending`` and JOB_CREATED
    wakes a worker (a kind without an executor is left for its unit to pick up).
    ``wake=False`` skips that signal for a request that claims the job itself right
    away (unit 7b worktree_provision); the sweep tick still picks it up if it does not.
    """
    if in_transaction():
        # The insert must be its own transaction (PostgreSQL unique violation, D 8.4).
        raise JobProgramError("job_create_inside_transaction", kind=kind)
    key = derive_request_key(kind, project_id, req)
    key_hash = _sha256_hex(key)
    fp = fingerprint(kind, project_id, req)
    _request_cache.invalidate()
    existing = db.get_job_by_key(project_id, kind, key_hash)
    if existing:
        return _compare_existing(existing, fp)

    now = now_iso()
    job_id = new_job_id()
    row = {
        "job_id": job_id, "kind": kind, "project_id": project_id, "group_id": group_id,
        "target_key": target_key, "request_key": key, "request_key_hash": key_hash,
        "request_fingerprint": fp, "payload": _json_or_none(payload),
        "requested_by": requested_by, "available_at": now, "created_at": now, "updated_at": now,
    }
    token = until = None
    if kind in FREEZE_KINDS:
        instance_id = _instance_id()
        if instance_id is None:
            return CreateResult(STORE_ERROR, code="no_server_instance")
        token = new_lease_token()
        until = registry.add_seconds(now, job_lease_sec())
        row.update(has_freeze_phase=1, status="freezing", phase="freeze_pending",
                   lease_owner=instance_id, lease_token=token, lease_until=until)
    else:
        row.update(has_freeze_phase=0, status="pending", phase="start")
    try:
        with get_store().transaction():
            db.insert_job(row)
    except Exception as exc:
        _request_cache.invalidate()
        existing = db.get_job_by_key(project_id, kind, key_hash)
        if existing:
            return _compare_existing(existing, fp)
        _log.warning("operation_job insert failed (%s, %s)", kind, key, exc_info=True)
        return CreateResult(STORE_ERROR, code=type(exc).__name__)
    job = _fresh_job(job_id) or row
    if kind in FREEZE_KINDS:
        return CreateResult(CREATED, job=job, ctx=_job_context(job, token, until))
    if wake:
        notifier.job_created(job)
    return CreateResult(CREATED, job=job)


def get_job(job_id: str) -> Optional[dict]:
    return _fresh_job(job_id)


# ── claim (L 2.14) ───────────────────────────────────────────────────────────

WON = "won"
LOST = "lost"


@dataclass
class ClaimResult:
    outcome: str
    ctx: Optional[JobContext] = None
    phase_entry: Optional[str] = None       # = mode; the caller never picks the phase


def mode_for(job: dict) -> Optional[str]:
    """Which claim a waiting job can take (DB §4(i) predicate, per job)."""
    status = job.get("status")
    wants_release = int(job.get("cancel_requested") or 0) == 1 or bool(job.get("release_intent"))
    if int(job.get("has_freeze_phase") or 0) == 1 and wants_release and status in db.WAITING_STATUSES:
        return RELEASE
    if status == "freeze_wait" and not wants_release:
        return FREEZE
    if status in ("pending", "blocked", "retry_wait") and int(job.get("cancel_requested") or 0) == 0:
        return PUBLISH
    return None


def claim(job_id: str, mode: str) -> ClaimResult:
    """One CAS on one job (I1). WON carries a fresh JobContext with a new lease_token."""
    if mode not in _CLAIMS:
        raise JobProgramError("unknown_claim_mode", mode=mode)
    instance_id = _instance_id()
    if instance_id is None:
        return ClaimResult(LOST)
    now = now_iso()
    token = new_lease_token()
    until = registry.add_seconds(now, job_lease_sec())
    if _CLAIMS[mode](job_id, instance_id, token, until, now) != 1:
        return ClaimResult(LOST)
    job = _fresh_job(job_id) or {"job_id": job_id, "lease_token": token}
    return ClaimResult(WON, ctx=_job_context(job, token, until), phase_entry=mode)


def claimable_job_ids(limit_per_project: int, limit_total: int, *, kinds=None,
                      cursor: Optional[str] = None) -> tuple[list[str], Optional[str]]:
    """Candidates by L 2.17.2 step_claim_candidates: projects in project_id order rotated
    to start after ``cursor`` (the caller's RR_CURSOR, memory only), up to
    ``limit_per_project`` oldest jobs each, ``limit_total`` overall.

    Returns (ids, new cursor). Read only; each id still has to be won with
    claim(job_id, mode_for(job)).
    """
    now = now_iso()
    projects = sorted(r["project_id"] for r in db.claimable_projects(now, kinds))
    if not projects:
        return [], cursor
    if cursor is not None:
        after = [p for p in projects if p > cursor]
        projects = after + [p for p in projects if p <= cursor]
    out: list[str] = []
    for p in projects:
        if len(out) >= limit_total:
            break
        out += [r["job_id"] for r in db.claimable_jobs(p, now, limit_per_project, kinds)]
        cursor = p
    return out[:limit_total], cursor


def expired_lease_jobs(limit: int) -> list[dict]:
    """running/freezing jobs a reconcile would look at (DB §4(j)). Read only (I3)."""
    threshold = registry.add_seconds(now_iso(), -registry.clock_skew_margin_sec())
    return db.list_expired_leases(threshold, limit)


# ── lease (L 2.15) ───────────────────────────────────────────────────────────

def _lose(ctx: JobContext) -> None:
    ctx.lease_lost = True


def renew_now(ctx: JobContext) -> bool:
    """One renewal. False = the lease is gone (or a store error past our own validity)."""
    with ctx._guard:
        if ctx.lease_lost:
            return False
        now = now_iso()
        until = registry.add_seconds(now, job_lease_sec())
        try:
            n = db.renew_lease(ctx.job_id, ctx.lease_token, now, until)
        except Exception:
            _log.warning("job lease renew failed: %s", ctx.job_id, exc_info=True)
            if registry.valid_for_self(ctx.lease_until_local, now_iso()):
                return True         # next round retries
            _lose(ctx)
            return False
        if n == 1:
            ctx.lease_until_local = until
            return True
        _lose(ctx)
        return False


def _remaining_sec(until: str) -> float:
    from datetime import datetime
    return (datetime.fromisoformat(until) - datetime.fromisoformat(now_iso())).total_seconds()


def ensure_lease(ctx: JobContext) -> None:
    """Phase boundaries and right before every Git side effect."""
    if ctx.lease_lost:
        raise LeaseLost(ctx.job_id)
    if not registry.instance_valid_for_self(now_iso()):
        _lose(ctx)          # others may already be allowed to declare us DEAD (L 2.8)
        raise LeaseLost(ctx.job_id)
    if _remaining_sec(ctx.lease_until_local) < lease_min_remaining_sec():
        if not renew_now(ctx):
            raise LeaseLost(ctx.job_id)


def job_write(ctx: JobContext, **changes) -> None:
    """Fenced write (I2). Raises LeaseLost when the row is no longer ours."""
    fenced_write(ctx, changes)


def fenced_write(ctx: JobContext, changes: dict, *, expected=None, allow_freeze: bool = False,
                 update_local: bool = True) -> dict:
    """job_write with the knobs the freeze transactions need (unit 6a).

    ``expected`` narrows the status condition (F1~F5 write only from freezing),
    ``allow_freeze`` opens the freeze/pin columns, and ``update_local=False`` leaves
    ``ctx.job`` alone so a write inside a transaction that may still roll back does not
    leak into memory — the caller applies the returned (encoded) changes after commit.
    """
    if ctx.lease_lost:
        raise LeaseLost(ctx.job_id)
    changes = dict(changes)
    for name in ("payload", "evidence", "release_intent", "result", "freeze_record"):
        if name in changes:
            changes[name] = _json_or_none(changes[name])
    n = db.job_write(ctx.job_id, ctx.lease_token, expected or ctx.expected_statuses, changes,
                     now_iso(), allow_freeze=allow_freeze)
    if n != 1:
        _lose(ctx)
        raise LeaseLost(ctx.job_id)
    if update_local:
        ctx.job.update(changes)
    return changes


class _LeaseRenewer:
    """One daemon thread per claim. Stopped by an Event, so stop() returns at once
    unless a renewal statement is in flight; join is bounded either way."""

    def __init__(self, ctx: JobContext) -> None:
        self._ctx = ctx
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"job-lease-{ctx.job_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(lease_renew_interval_sec()):
            if not renew_now(self._ctx):
                _log.error("job_lease_lost: %s", self._ctx.job_id)
                return

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout)


def release_held_locks(ctx: JobContext) -> None:
    """LeaseLost exit: give back whatever the job context still holds (I6)."""
    lock_ctx = ctx.lock_ctx
    if lock_ctx is None:
        return
    for h in list(reversed(lock_ctx.held)):
        h.count = 1
        try:
            locks.release(lock_ctx, h.lock_key)
        except Exception:
            _log.warning("job lock release failed: %s %s", ctx.job_id, h.lock_key, exc_info=True)


@contextmanager
def lease_scope(ctx: JobContext) -> Iterator[JobContext]:
    """Run one claim's body with background renewal (lease_renew_loop).

    The renewer is always stopped and joined in ``finally`` — the lesson of the last
    timed-out run (0669 seq 40): no thread outlives the execution path that started it.
    On LeaseLost the job's locks are released and the exception propagates; nothing
    else is written.
    """
    renewer = _LeaseRenewer(ctx)
    renewer.start()
    try:
        yield ctx
    except LeaseLost:
        _lose(ctx)
        release_held_locks(ctx)
        raise
    finally:
        renewer.stop()


# ── result classification and backoff (L 2.16) ───────────────────────────────

SUCCESS = "success"
BLOCKED = "blocked"
TRANSIENT = "transient"
UNKNOWN = "unknown"
PERMANENT = "permanent"
HANDOFF = "handoff"
RECOVERY = "recovery"


@dataclass
class RunResult:
    cls: str
    code: Optional[str] = None
    result: object = None
    db_finalize_only: bool = False
    blocked_domain: Optional[str] = None
    blocked_lock_key: Optional[str] = None
    blocked_holder: Optional[str] = None
    blocked_operation: Optional[str] = None


def next_delay(attempt_count: int) -> int:
    raw = retry_base_sec() * 2 ** (max(1, attempt_count) - 1)
    d = max(retry_base_sec(), min(retry_max_delay_sec(), raw))
    jitter = d * retry_jitter_ratio() * random.uniform(-1, 1)
    return max(1, round(d + jitter))


_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}


def _has_freeze_record(job: dict) -> bool:
    return int(job.get("has_freeze_phase") or 0) == 1 and bool(job.get("freeze_record"))


def _fail(ctx: JobContext, code: Optional[str], now: str) -> None:
    if _has_freeze_record(ctx.job):
        # failed with a freeze record goes only through freeze_release (L 2.19.3, unit 6).
        raise JobProgramError("freeze_release_required", job_id=ctx.job_id, code=code)
    job_write(ctx, status="failed", last_error_code=code, retryable=0, finished_at=now,
              **_LEASE_CLEARED)
    run_terminal_cleanup(ctx.job)


def finish_run(ctx: JobContext, result: RunResult) -> None:
    """Record one claim's outcome (L 2.16). Raises LeaseLost if the job is not ours."""
    now = now_iso()
    job = ctx.job
    attempts = int(job.get("attempt_count") or 0)
    if result.cls == SUCCESS:
        job_write(ctx, status="succeeded", result=result.result, finished_at=now, **_LEASE_CLEARED)
    elif result.cls == BLOCKED:
        same_wait = (job.get("blocked_lock_key") == result.blocked_lock_key
                     and job.get("wait_started_at"))
        job_write(ctx, status="blocked", available_at=now,       # no attempt increase (D 3.6.5)
                  blocked_domain=result.blocked_domain, blocked_lock_key=result.blocked_lock_key,
                  blocked_holder=(result.blocked_holder or "")[:120] or None,
                  blocked_operation=(result.blocked_operation or "")[:32] or None,
                  wait_started_at=job.get("wait_started_at") if same_wait else now,
                  **_LEASE_CLEARED)
    elif result.cls == TRANSIENT:
        a = attempts + 1
        if a > retry_max_attempts():
            if result.db_finalize_only:
                # Git is already terminal: never failed, DB finalize keeps retrying (L 2.16 exception).
                job_write(ctx, status="recovery_required", attempt_count=a,
                          last_error_code="db_finalize_exhausted", retryable=1, recovery_since=now,
                          available_at=registry.add_seconds(now, reconcile_retry_interval_sec()),
                          **_LEASE_CLEARED)
            else:
                _fail(ctx, result.code, now)
        else:
            job_write(ctx, status="retry_wait", attempt_count=a,
                      available_at=registry.add_seconds(now, next_delay(a)),
                      last_error_code=result.code, retryable=1, **_LEASE_CLEARED)
    elif result.cls == UNKNOWN:
        # The job stays running and its lease simply runs out, which hands it to the
        # Sweeper's reconcile (job_sweeper.step_expired_leases, I3).
        job_write(ctx, attempt_count=attempts + 1, phase_note="result_unknown",
                  last_error_code=result.code)
    elif result.cls == PERMANENT:
        _fail(ctx, result.code, now)
    elif result.cls == HANDOFF:
        job_write(ctx, status="succeeded", result=result.result or "handed_off_to_conflict_review",
                  finished_at=now, **_LEASE_CLEARED)
    elif result.cls == RECOVERY:
        job_write(ctx, status="recovery_required", recovery_since=now, last_error_code=result.code,
                  available_at=registry.add_seconds(now, reconcile_retry_interval_sec()),
                  **_LEASE_CLEARED)
    else:
        raise JobProgramError("unknown_result_class", cls=result.cls)


def run_terminal_cleanup(job: dict) -> None:
    """L 2.19.3, idempotent. Never releases a freeze claim or a pin: a job with a freeze
    record only becomes failed/cancelled through freeze_release, which clears the claim
    in the same transaction. A claim still owned here is an invariant breach — logged
    for the operator, left in place. Fairness reservations (L 2.12) are not built."""
    if (job.get("kind") in FREEZE_KINDS and job.get("status") in ("failed", "cancelled")
            and job.get("group_id")):
        try:
            _request_cache.invalidate()
            claim = db.get_group_freeze_claim(job["group_id"])
        except Exception:
            _log.warning("freeze claim diagnosis read failed: %s", job.get("job_id"), exc_info=True)
            return None
        if claim and claim.get("job_id") == job.get("job_id"):
            _log.error("freeze_claim_left_by_terminal_job: %s (group %s, %s)",
                       job.get("job_id"), job["group_id"], claim.get("state"))
    return None


# ── cancel (L 2.19.1) ────────────────────────────────────────────────────────

CANCELLED = "cancelled"
CANCEL_PENDING = "cancel_pending"


def request_cancel(job_id: str, by: Optional[str] = None) -> str:
    """Cancel a job. Returns CANCELLED, CANCEL_PENDING or the job's current status
    ('not_found' when there is none)."""
    now = now_iso()
    if db.cancel_unfrozen_waiting(job_id, now, "cancelled_by_user") == 1:
        job = _fresh_job(job_id)
        if job is not None:
            run_terminal_cleanup(job)
            notifier.job_terminal(job)
        _log.info("job cancelled: %s by %s", job_id, by)
        return CANCELLED
    intent = _canonical_json({"final_status": "cancelled", "code": "cancelled_by_user"})
    if db.mark_cancel_for_release(job_id, intent, now) == 1:
        return CANCEL_PENDING       # release claim (unit 6) clears the pin, then terminal
    if db.mark_cancel_requested(job_id, now) == 1:
        return CANCEL_PENDING       # executor checks at its next phase boundary
    job = _fresh_job(job_id)
    return job["status"] if job else "not_found"

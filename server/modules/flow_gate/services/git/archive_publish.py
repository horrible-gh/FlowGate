"""Archive jobs: preserve, restore, purge (flowgate.default.0669 unit 8b; 0666 0008-D §3.6.1,
§7, 0009-L §2.28, §4.4).

None of the three takes the old project mutex any more. The request registers an
``archive_preserve`` / ``archive_restore`` / ``archive_purge`` job and runs it once right away
with its own claim (as base_publish does); if a domain is busy the job stays queued and the
Runner finishes it, while the caller sees the old ``git_busy`` 409 with the job id. Phases
(L 2.28):

    start -> refs_written (M) -> workspace_done -> done

* start: the plan (ref names, the branch, the worktree path) is written before any ref moves.
* refs_written: preserve pins the branch head and the uncommitted changes to the archive refs
  (``stash push`` + ``update-ref`` + ``stash drop`` — refs/stash is the shared stack, so all of
  it under M); restore recreates the work branch at the archived head (create-only); purge
  deletes the archive refs.
* workspace_done: preserve releases the slot (``_cleanup_group_slot``, under M because it
  removes a worktree and a branch); restore adds the worktree (M) and applies the preserved
  changes; purge has no workspace.
* done: restore first drops the archive refs (M); then the terminal write and the archive
  record / ledger change in one transaction, then the SSE.

The bodies stay in ``api/inbox_routes.py`` next to the archive record they own; this module
holds the job around them and hands them a :class:`Step` (M and phase writes).

Deviations recorded as design changes (0669 chat):

* Domain: the design names W keyed by the archive id. The archive has no workspace of its own
  in this code — preserve stashes and removes the Group's own slot, restore recreates it — so
  preserve and restore hold the Group's G (holder_kind ``archive``), the domain every other
  slot writer holds, plus M for the ref/worktree commands. Purge only touches refs: M alone.
  A preserve also goes through the freeze guard (L 2.10), so a manual archive never removes
  a slot an approval froze.
* An approval-coupled archive (``action="stash"`` inside final_approval_publish) is not a job
  of its own: it runs inline in the approval job, which already holds R, and takes M from the
  approval job's context for the ref commands. It no longer borrows a project lock (6b
  removed that) and it keeps the slot, as before.
* request_key: ``archive_id`` is the group id and ``action_seq`` the number of terminal jobs of
  that kind for the group, so a double click is the same job and the next action after one
  ended is a new job.
* The 4.4 deciders are NOT_APPLIED for every phase but restore's ``start`` (the bodies resume
  from the archive record and the recorded phase). Restore's ``start`` reads the work branch:
  at the archived head -> APPLIED_NEXT(refs_written), absent -> NOT_APPLIED, else UNDECIDABLE.
  A restore resumed at ``refs_written`` whose worktree path already exists stops at
  recovery_required instead of guessing whose directory it is.
* Every GitServiceError is PERMANENT (the old error goes out); a body never retries on its own
  in the background.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

from modules.flow_gate.db import operation_job as db
from modules.flow_gate.db.connection import get_store, now_iso

from . import approval_freeze
from . import job_runner as runner
from . import job_store as store
from . import lock_manager as locks
from .credentials import GitServiceError

_log = logging.getLogger(__name__)

PRESERVE = "archive_preserve"
RESTORE = "archive_restore"
PURGE = "archive_purge"
KINDS = (PRESERVE, RESTORE, PURGE)
PHASES = ("start", "refs_written", "workspace_done")
_ORDER = {"start": 0, "refs_written": 1, "workspace_done": 2, "done": 3}
_OPERATION = {PRESERVE: "archived", RESTORE: "restored", PURGE: "purged"}

_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}

# per-thread: "interactive" in a request, None (= "job") otherwise; and the error the
# request re-raises when its own attempt ended the job.
_origin = threading.local()


class _Stop(Exception):
    def __init__(self, result: store.RunResult):
        super().__init__(result.code)
        self.result = result


def _transient(code: str) -> _Stop:
    return _Stop(store.RunResult(store.TRANSIENT, code=code))


def recovery(code: str) -> _Stop:
    """For a body: stop at recovery_required (an operator settles it)."""
    return _Stop(store.RunResult(store.RECOVERY, code=code[:64]))


def _blocked(domain: str, o: locks.LockOutcome) -> _Stop:
    b = o.blocker or {}
    if o.kind in (locks.BUSY, locks.GROUP_FROZEN):
        return _Stop(store.RunResult(
            store.BLOCKED, code=o.kind, blocked_domain=domain, blocked_lock_key=o.lock_key,
            blocked_holder=b.get("holder_ctx_id") or b.get("job_id"),
            blocked_operation=b.get("holder_kind") or b.get("kind")))
    if o.kind == locks.RECOVERY_REQUIRED:
        return recovery(f"archive_{domain}_recovery_required")
    return _transient((o.cause or o.kind or "lock_store_error")[:64])


def _payload(job: dict) -> dict:
    return approval_freeze._json(job.get("payload"))


def _evidence(job: dict) -> dict:
    return approval_freeze._json(job.get("evidence"))


def _mode() -> str:
    return getattr(_origin, "mode", None) or "job"


# ── what a body gets ─────────────────────────────────────────────────────────

class Step:
    """M and phase records for one archive body. ``job_ctx`` is None for the
    approval-coupled preserve, which runs inline in the approval job (M from its context,
    no phases of its own)."""

    def __init__(self, project_id: str, lock_ctx: locks.ExecutionContext,
                 job_ctx: Optional[store.JobContext] = None, mode: str = "job"):
        self.project_id = project_id
        self.lock_ctx = lock_ctx
        self.job_ctx = job_ctx
        self.mode = mode

    @contextmanager
    def m(self):
        if self.job_ctx is not None:
            store.ensure_lease(self.job_ctx)
        o = locks.acquire("M", self.project_id, holder_kind="archive", mode=self.mode,
                          ctx=self.lock_ctx)
        if not o.ok:
            if self.job_ctx is None:
                raise GitServiceError(409, "git_busy", "another Git operation is in progress",
                                      details=locks.outcome_details(o))
            raise _blocked("M", o)
        try:
            yield
        finally:
            if self.lock_ctx.find_held(o.lock_key) is not None:
                locks.release(self.lock_ctx, o.lock_key)

    def reached(self, phase: str) -> bool:
        if self.job_ctx is None:
            return False
        return _ORDER.get(self.job_ctx.job.get("phase") or "start", 0) >= _ORDER[phase]

    def plan(self) -> dict:
        if self.job_ctx is None:
            return {}
        return dict(_evidence(self.job_ctx.job).get("plan") or {})

    def phase(self, phase: str, **evidence) -> None:
        if self.job_ctx is None:
            return
        changes: dict = {"phase": phase}
        if evidence:
            ev = _evidence(self.job_ctx.job)
            ev.update(evidence)
            changes["evidence"] = store._canonical_json(ev)
        store.ensure_lease(self.job_ctx)
        store.job_write(self.job_ctx, **changes)


def inline_step(project_id: str, lock_ctx: locks.ExecutionContext) -> Step:
    """The approval-coupled preserve's Step: M from the approval job's own context."""
    return Step(project_id, lock_ctx, None, mode="interactive")


# ── creation and the request's one attempt ───────────────────────────────────

def request(kind: str, project_id: str, group_id: str, payload: dict) -> dict:
    """Create (or join) the job, run it once here. Returns the body's result with the job
    id; raises the body's GitServiceError, or 409 git_busy (queued) / recovery_required."""
    install()
    seq = len(db.jobs_of_group(group_id, kind, db.TERMINAL_STATUSES))
    req = {"archive_id": group_id, "action_seq": str(seq)}
    cr = store.create_or_get_job(kind, project_id, req, group_id=group_id,
                                 payload=dict(payload, group_id=group_id), wake=False)
    if not cr.ok:
        raise GitServiceError(503, "job_store_unavailable", "the operation could not be registered",
                              details={"cause": cr.code})
    job = cr.job
    _origin.error = None
    if job.get("status") not in db.TERMINAL_STATUSES:
        claim = runner.try_claim(job["job_id"])
        if claim is not None:
            _origin.mode = "interactive"
            try:
                runner.execute(claim)
            finally:
                _origin.mode = None
            job = store.get_job(job["job_id"]) or claim.ctx.job
    error, _origin.error = getattr(_origin, "error", None), None
    status = job.get("status")
    if status == "succeeded":
        return dict(approval_freeze._json(job.get("result")) or {}, job_id=job["job_id"])
    if status in ("failed", "cancelled"):
        if error is not None:
            raise error
        raise GitServiceError(500, "archive_failed", "the archive operation failed",
                              details={"job_id": job["job_id"], "code": job.get("last_error_code")})
    details = {"queued": True, "job_id": job["job_id"], "job_status": status,
               "reason_code": job.get("last_error_code")}
    if job.get("blocked_domain"):
        details["blocker"] = {"domain": job.get("blocked_domain"),
                              "operation": job.get("blocked_operation")}
    if status == "recovery_required":
        raise GitServiceError(409, "recovery_required",
                              "the archive operation needs recovery before it can go on",
                              details=details)
    raise GitServiceError(409, "git_busy", "another Git operation is in progress; "
                          "this one is queued and runs when it is free", details=details)


# ── execution ────────────────────────────────────────────────────────────────

def _bodies() -> dict:
    """kind -> body(group_id, payload, step) -> (result, done_writes). The bodies live
    beside the archive record in the API module (module doc)."""
    from modules.flow_gate.api import inbox_routes as routes
    return {PRESERVE: routes._archive_preserve_locked, RESTORE: routes._archive_restore_locked,
            PURGE: routes._archive_purge_locked}


def _admit(ctx: store.JobContext, kind: str, project_id: str, group_id: str) -> Optional[str]:
    """G for preserve/restore (purge needs none). Returns the held key."""
    if kind == PURGE:
        return None
    store.ensure_lease(ctx)
    o = locks.acquire("G", project_id, group_id=group_id, holder_kind="archive", mode=_mode(),
                      ctx=ctx.lock_ctx)
    if o.ok and kind == PRESERVE and not o.reentrant:
        o = locks._guard_group_admission(ctx.lock_ctx, o.lock_key, project_id, group_id, o)
    if not o.ok:
        raise _blocked("G", o)
    return o.lock_key


def _finish(ctx: store.JobContext, result: dict, done_writes: Optional[Callable[[], None]]) -> None:
    store.ensure_lease(ctx)
    with get_store().transaction():
        written = store.fenced_write(ctx, dict(status="succeeded", phase="done", finished_at=now_iso(),
                                               result=result, **_LEASE_CLEARED),
                                     update_local=False)
        if done_writes is not None:
            done_writes()
    ctx.job.update(written)


def run(ctx: store.JobContext, entry: str) -> Optional[store.RunResult]:
    """Runner executor. None once the terminal state is written, else the claim's result."""
    if entry != store.PUBLISH:
        raise store.JobProgramError("archive_entry_not_publish", job_id=ctx.job_id, entry=entry)
    job = ctx.job
    kind, project_id, group_id = job.get("kind"), job["project_id"], job.get("group_id")
    if kind not in KINDS or not group_id:
        return store.RunResult(store.RECOVERY, code="archive_payload_unresolved")
    if (job.get("phase") or "start") == "done":
        return None
    key = None
    try:
        key = _admit(ctx, kind, project_id, group_id)
        step = Step(project_id, ctx.lock_ctx, ctx, mode=_mode())
        result, done_writes = _bodies()[kind](group_id, _payload(job), step)
        _finish(ctx, result, done_writes)
    except _Stop as stop:
        return stop.result
    except GitServiceError as exc:
        _origin.error = exc
        return store.RunResult(store.PERMANENT, code=exc.code[:64])
    finally:
        if key is not None and ctx.lock_ctx.find_held(key) is not None:
            locks.release(ctx.lock_ctx, key)
    if not result.get("idempotent"):
        try:
            from modules.flow_gate.api import inbox_routes as routes
            routes._emit_git_archive_refresh(project_id, group_id, _OPERATION[kind])
        except Exception:
            _log.warning("archive refresh emit failed for %s", group_id, exc_info=True)
    return None


# ── L 4.4 deciders (lock-free F reads) ───────────────────────────────────────

def _decide_not_applied(job: dict) -> "runner.Decision":
    return runner.Decision(runner.NOT_APPLIED)


def _decide_restore_start(job: dict) -> "runner.Decision":
    """The work branch vs the archived head recorded at start."""
    from modules.flow_gate.services import git_service as _gs
    plan = _evidence(job).get("plan") or {}
    branch, head_sha, base_root = plan.get("branch"), plan.get("head_sha"), plan.get("base_root")
    if not (branch and head_sha and base_root):
        return runner.Decision(runner.NOT_APPLIED)
    ref = f"refs/heads/{branch}"
    proc = _gs._run_git(["for-each-ref", "--format=%(refname) %(objectname)", ref], cwd=Path(base_root))
    if proc.returncode != 0:
        return runner.Decision(runner.TRANSIENT_CHECK_FAILURE, code="ref_check_failed")
    current = None
    for line in (proc.stdout or "").splitlines():
        name, _, sha = line.strip().partition(" ")
        if name == ref and sha:
            current = sha
    if current is None:
        return runner.Decision(runner.NOT_APPLIED)
    if current == head_sha:
        return runner.Decision(runner.APPLIED_NEXT, next_phase="refs_written")
    return runner.Decision(runner.UNDECIDABLE, code="restore_branch_unexpected")


def install() -> None:
    """Executors and L 4.4 deciders. Idempotent."""
    for kind in KINDS:
        runner.register_executor(kind, run)
        for phase in PHASES:
            fn = _decide_restore_start if (kind, phase) == (RESTORE, "start") else _decide_not_applied
            runner.register_decider(kind, phase, fn)

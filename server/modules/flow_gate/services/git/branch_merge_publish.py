"""Branch merge job (flowgate.default.0669 unit 8a; 0666 0008-D §3.6.1, §3.11, §7,
0009-L §2.28, §4.4).

``merge_branches`` no longer takes the old project mutex. The request registers a
``branch_merge_publish`` job and runs it once right away with its own claim (as
base_publish does); if a domain is busy the job stays queued and the Runner finishes it,
while the caller sees the old ``git_busy`` 409 with the job id.

Admission (L 2.4 order): the target's domain, then R, held for the whole merge —

* target is a Group worktree → G of that Group
* target is the project base branch → B
* any other branch → W keyed by the target branch (the same W a final approval to that
  branch takes, so the two exclude each other)

The merge itself is the old body (``branches.run_merge``) with ``job:{job_id}`` as the
attempt's lock holder, so ``merge_target.attempt_phase`` reads the attempt as in progress
for as long as the job is not terminal and the sweep/startup recovery leave it to the job.
Phases are recorded before the Git step they precede (L 2.28): workspace_registered
(merge_id) → merged (merge commit) → pushed → done. A conflict hands the attempt to the
existing resolver/review flow: the job ends ``succeeded`` with the conflict answer.

Deviations recorded as design changes (0669 chat):

* request_key: the attempt does not exist before the job, so ``branch_merge_attempt_id``
  is the request identity — source/target endpoints, their heads, push — plus the number
  of failed/cancelled jobs for it (a double click is the same job, a retry after a
  failure a new one).
* The L 4.4 rows are not evidence deciders, as for final_approval_publish (6b): every
  phase decides NOT_APPLIED and the next claim's ``_resume`` judges this job's attempt
  under the same locks (open conflict → handed off; otherwise
  ``recover_interrupted_attempt``: landed → succeeded, interrupted → run again,
  unprovable → recovery_required). That recovery may need ``merge --abort`` /
  ``reset --keep``, which a lock-free decider must not run.
* There is no ``target_published`` (M update-ref) phase: the merge commit moves the
  target ref in its managed workspace (or is pushed from the detached base workspace)
  exactly as before. The managed workspace's ``worktree add``/``remove`` stays inside the
  W/B hold without a separate M, as in finalize (6b).
* Every GitServiceError is PERMANENT and reaches the request as the old error; nothing
  retries a failed merge in the background. An unexpected exception is PERMANENT too (the
  old body already closed the attempt failed).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import operation_job as db
from modules.flow_gate.db.connection import get_store, now_iso

from . import approval_freeze
from . import job_runner as runner
from . import job_store as store
from . import lock_manager as locks
from . import merge_target
from .credentials import GitServiceError

_log = logging.getLogger(__name__)

KIND = "branch_merge_publish"
PHASES = ("start", "workspace_registered", "merged", "pushed")

_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}

# per-thread: "interactive" in a request, None (= "job") otherwise; and the error the
# request re-raises when its own attempt ended the job.
_origin = threading.local()


class _Stop(Exception):
    def __init__(self, result: store.RunResult):
        super().__init__(result.code)
        self.result = result


def _fail(exc: Exception) -> _Stop:
    """PERMANENT, keeping the old error for the request that runs it."""
    _origin.error = exc
    code = exc.code if isinstance(exc, GitServiceError) else "unexpected_error"
    return _Stop(store.RunResult(store.PERMANENT, code=str(code)[:64]))


def holder_of(job_id: str) -> str:
    return merge_target.JOB_HOLDER_PREFIX + job_id


def _payload(job: dict) -> dict:
    return approval_freeze._json(job.get("payload"))


def _evidence(job: dict) -> dict:
    return approval_freeze._json(job.get("evidence"))


def _endpoints(p: dict):
    from .branches import MergeEndpointIdentity
    source = MergeEndpointIdentity(kind=p.get("source_kind") or "branch",
                                   branch=p.get("source_branch") or "",
                                   group_id=p.get("source_group_id"))
    target = MergeEndpointIdentity(kind=p.get("target_kind") or "branch",
                                   branch=p.get("target_branch") or "",
                                   group_id=p.get("target_group_id"))
    if not source.branch or not target.branch:
        raise _Stop(store.RunResult(store.RECOVERY, code="branch_merge_payload_unresolved"))
    return source, target


# ── creation and the request's one attempt ───────────────────────────────────

def _identity(project_id: str, subject: str) -> str:
    prefix = f"bmp:{subject}:"
    return f"{subject}:{db.count_terminal_by_key_prefix(project_id, KIND, prefix)}"


def request(project_id: str, source_ep, target_ep, *, push: bool, source_head: str,
            target_root: Optional[Path], base_branch: str, requested_by: Optional[str] = None,
            provider_id: Optional[str] = None) -> dict:
    """merge_branches' job path (prechecks already done by the caller). Returns the old
    answer (merged / conflict) or raises its old error."""
    from modules.flow_gate.services import git_service as _gs
    target_head = None
    if target_root is not None and target_ep.kind == "worktree":
        target_head = _gs._rev_parse(target_root, "HEAD")
    else:
        base_root = _gs._base_root_of(project_id)
        if base_root is not None:
            target_head = _gs._rev_parse(base_root, f"refs/heads/{target_ep.branch}")
    subject = ":".join([
        project_id,
        f"{source_ep.kind}/{source_ep.group_id or ''}/{source_ep.branch}@{source_head or ''}",
        f"{target_ep.kind}/{target_ep.group_id or ''}/{target_ep.branch}@{target_head or ''}",
        "push" if push else "local",
    ])
    payload = {
        "source_kind": source_ep.kind, "source_branch": source_ep.branch,
        "source_group_id": source_ep.group_id,
        "target_kind": target_ep.kind, "target_branch": target_ep.branch,
        "target_group_id": target_ep.group_id,
        "push": bool(push), "base_branch": base_branch,
        "requested_by": requested_by, "provider_id": provider_id,
    }
    req = {"branch_merge_attempt_id": _identity(project_id, subject)}
    job = _run_request(project_id, req, payload, target_ep.branch, requested_by)
    result = dict(approval_freeze._json(job.get("result")) or {})
    result["job_id"] = job["job_id"]
    return result


def _run_request(project_id: str, req: dict, payload: dict, target_branch: str,
                 requested_by: Optional[str]) -> dict:
    """Create (or find) the job, run it once here, return it succeeded or raise."""
    install()
    cr = store.create_or_get_job(KIND, project_id, req, target_key=target_branch,
                                 payload=payload, requested_by=requested_by, wake=False)
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
        return job
    if status in ("failed", "cancelled"):
        if error is not None:
            raise error
        raise GitServiceError(500, "branch_merge_failed", "the branch merge failed",
                              details={"job_id": job["job_id"], "code": job.get("last_error_code")})
    details = {"queued": True, "job_id": job["job_id"], "job_status": status,
               "reason_code": job.get("last_error_code")}
    if job.get("blocked_domain"):
        details["blocker"] = {"domain": job.get("blocked_domain"),
                              "operation": job.get("blocked_operation")}
    if status == "recovery_required":
        raise GitServiceError(409, "recovery_required",
                              "the branch merge needs recovery before it can go on", details=details)
    raise GitServiceError(
        409, "git_busy",
        f"Another git operation is in progress for project '{project_id}'; "
        "this branch merge is queued and runs when it is free",
        details=details,
    )


# ── locks ────────────────────────────────────────────────────────────────────

def target_domain(project_id: str, target_kind: str, target_branch: str,
                  target_group_id: Optional[str], base_branch: Optional[str] = None) -> tuple:
    """(domain, group_id, target_key) of a branch merge target (module doc)."""
    from modules.flow_gate.services import git_service as _gs
    if target_kind == "worktree":
        return "G", target_group_id, None
    base = base_branch or _gs.base_branch_for(project_id)
    if target_branch == base:
        return "B", None, None
    return "W", None, target_branch


def _admit(ctx: store.JobContext, p: dict, held: list) -> None:
    mode = getattr(_origin, "mode", None) or "job"
    project_id = ctx.job["project_id"]
    domain, group_id, target_key = target_domain(
        project_id, p.get("target_kind") or "branch", p.get("target_branch") or "",
        p.get("target_group_id"))
    for d, g, t, kind in ((domain, group_id, target_key, "branch_merge"), ("R", None, None, "publish")):
        store.ensure_lease(ctx)
        o = locks.acquire(d, project_id, group_id=g, target_key=t, holder_kind=kind, mode=mode,
                          hold_class="long", ctx=ctx.lock_ctx)
        if not o.ok:
            b = o.blocker or {}
            if o.kind == locks.BUSY:
                raise _Stop(store.RunResult(
                    store.BLOCKED, code=o.kind, blocked_domain=d, blocked_lock_key=o.lock_key,
                    blocked_holder=b.get("holder_ctx_id"), blocked_operation=b.get("holder_kind")))
            if o.kind == locks.RECOVERY_REQUIRED:
                raise _Stop(store.RunResult(store.RECOVERY, code=f"branch_merge_{d}_recovery_required"))
            raise _Stop(store.RunResult(store.TRANSIENT, code=(o.cause or o.kind or "lock_store_error")[:64]))
        held.append(o.lock_key)


def _release_all(ctx: store.JobContext, held: list) -> None:
    for key in reversed(held):
        if ctx.lock_ctx.find_held(key) is not None:
            locks.release(ctx.lock_ctx, key)


# ── execution ────────────────────────────────────────────────────────────────

def _phase_writer(ctx: store.JobContext):
    def write(phase: str, **evidence) -> None:
        store.ensure_lease(ctx)
        changes: dict = {"phase": phase}
        if evidence:
            ev = _evidence(ctx.job)
            ev.update(evidence)
            changes["evidence"] = store._canonical_json(ev)
        store.job_write(ctx, **changes)
    return write


def _own_open_attempt(ctx: store.JobContext) -> Optional[dict]:
    from modules.flow_gate.services import git_service as _gs
    holder = holder_of(ctx.job_id)
    for session in _gs.db_git.list_open_sessions():
        if not _gs.db_git.is_branch_merge_session(session):
            continue
        if (merge_target.target_record(session) or {}).get("lock_holder") == holder:
            return session
    return None


def _closed_result(merge_id: int) -> Optional[dict]:
    """The answer of this job's attempt that is already closed completed, else None."""
    from modules.flow_gate.services import git_service as _gs
    session = _gs.db_git.get_session(merge_id)
    if not session:
        return None
    context = _gs.db_git.session_context(session)
    if context.get(merge_target.ATTEMPT_STATE_KEY) != merge_target.ATTEMPT_COMPLETED:
        return None
    result = context.get("attempt_result") or {}
    rec = merge_target.target_record(session) or {}
    return {"ok": True, "status": "merged", "merge_id": merge_id,
            "source_branch": rec.get("source_branch"), "target_branch": rec.get("branch"),
            "target_head": result.get("merge_commit"), "pushed": bool(result.get("pushed")),
            "recovered": True}


def _resume(ctx: store.JobContext) -> Optional[dict]:
    """Settle what an earlier claim of this job left (module doc), under its locks.
    None = nothing left over, run the merge."""
    from . import branch_merge
    session = _own_open_attempt(ctx)
    if session is not None:
        merge_id = int(session["merge_id"])
        if merge_target.attempt_phase(session) == merge_target.PHASE_CONFLICT:
            view = branch_merge.attempt_view(session)
            return {"ok": True, "status": "conflict", "merge_id": merge_id,
                    "source_branch": view.get("source_branch"),
                    "target_branch": view.get("target_branch"), "push": view.get("push"),
                    "pushed": False, "workspace_cleaned": False, "attempt": view,
                    "resumed": True}
        store.ensure_lease(ctx)
        verdict = merge_target.recover_interrupted_attempt(session, "branch_merge_job_resume")
        if verdict == merge_target.ATTEMPT_COMPLETED:
            return _closed_result(merge_id)
        if verdict == merge_target.ATTEMPT_INTERRUPTED:
            return None
        raise _Stop(store.RunResult(store.RECOVERY, code="branch_merge_attempt_unsettled"))
    merge_id = _evidence(ctx.job).get("merge_id")
    if merge_id is not None:
        closed = _closed_result(int(merge_id))
        if closed is not None:
            return closed
        _log.info("branch merge job %s: attempt %s closed unfinished, running again",
                  ctx.job_id, merge_id)
    return None


def _finish(ctx: store.JobContext, result: dict) -> None:
    store.ensure_lease(ctx)
    with get_store().transaction():
        written = store.fenced_write(ctx, dict(status="succeeded", phase="done", finished_at=now_iso(),
                                               result=result, **_LEASE_CLEARED),
                                     update_local=False)
    ctx.job.update(written)


def run(ctx: store.JobContext, entry: str) -> Optional[store.RunResult]:
    """Runner executor: admission, resume, the merge, the terminal write."""
    from .branches import run_merge
    if entry != store.PUBLISH:
        raise store.JobProgramError("branch_merge_entry_not_publish", job_id=ctx.job_id, entry=entry)
    held: list = []
    try:
        if (ctx.job.get("phase") or "start") == "done":
            return None
        p = _payload(ctx.job)
        source, target = _endpoints(p)
        _admit(ctx, p, held)
        result = _resume(ctx)
        if result is None:
            store.ensure_lease(ctx)
            try:
                result = run_merge(
                    ctx.job["project_id"], source, target, bool(p.get("push")),
                    holder=holder_of(ctx.job_id), requested_by=p.get("requested_by"),
                    provider_id=p.get("provider_id"), on_phase=_phase_writer(ctx),
                )
            except (store.LeaseLost, locks.LockProgramError, store.JobProgramError):
                raise
            except Exception as exc:
                raise _fail(exc)
        _finish(ctx, result)
        return None
    except _Stop as stop:
        return stop.result
    finally:
        _release_all(ctx, held)


# ── L 4.4 (module doc: resume judges under the locks) ───────────────────────

def _decide_resume(job: dict) -> "runner.Decision":
    return runner.Decision(runner.NOT_APPLIED)


def install() -> None:
    """Executor and L 4.4 deciders. Idempotent."""
    runner.register_executor(KIND, run)
    for phase in PHASES:
        runner.register_decider(KIND, phase, _decide_resume)


# ── short W/B/G for the attempt's own user actions ───────────────────────────

def attempt_lock(target: merge_target.MergeTargetContext, *, holder_kind: str = "branch_merge"):
    """The target domain of an open branch merge attempt for one user action (abort),
    instead of the project mutex. Returns (ctx, lock_key); raises the old git_busy 409."""
    domain, group_id, target_key = target_domain(
        target.project_id, target.target_kind or "branch", target.target_branch,
        target.target_group_id, target.base_branch)
    ctx = locks.current_context() or locks.new_context("req")
    o = locks.acquire(domain, target.project_id, group_id=group_id, target_key=target_key,
                      holder_kind=holder_kind, ctx=ctx)
    if not o.ok:
        raise GitServiceError(409, "git_busy", "another Git operation is in progress",
                              details=locks.outcome_details(o))
    return ctx, o.lock_key


# ── conflict resolution and review actions (unit 8b) ─────────────────────────

def session_domain(session: dict) -> tuple:
    """(domain, group_id, target_key) of the workspace a conflict/review session writes:
    the Group's G for TR and group-update sessions (their files are the Group's slot),
    otherwise the merge target's G / W / B (``target_domain``)."""
    from modules.flow_gate.services import git_service as _gs
    kind = _gs.db_git.session_kind(session)
    if kind in _gs.db_git.TR_SESSION_KINDS or kind == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
        return "G", session.get("group_id"), None
    t = merge_target.resolve_session_target(session)
    return target_domain(t.project_id, t.target_kind or "branch", t.target_branch,
                         t.target_group_id, t.base_branch)


def session_lock(session: dict, project_id: str, *, holder_kind: str, publish: bool = False,
                 mode: str = "interactive") -> tuple:
    """The session's workspace domain (and R when the action may push or ask the remote),
    in L 2.4 order, instead of the project mutex: ``resolve:``, ``review:``,
    ``reconcile:``. Returns (ctx, held keys) for ``session_unlock``. A refusal raises the
    old git_busy 409 with the lock manager's reason and blocker; nothing is queued (D §7:
    user actions on an open attempt)."""
    domain, group_id, target_key = session_domain(session)
    ctx = locks.current_context() or locks.new_context("req")
    wanted = [(domain, group_id, target_key, holder_kind)]
    if publish:
        wanted.append(("R", None, None, "publish"))
    held: list = []
    for d, g, t, kind in wanted:
        o = locks.acquire(d, project_id, group_id=g, target_key=t, holder_kind=kind,
                          mode=mode, ctx=ctx)
        if not o.ok:
            session_unlock(ctx, held)
            raise GitServiceError(409, "git_busy",
                                  f"another git operation is in progress for '{project_id}'",
                                  details=locks.outcome_details(o))
        held.append(o.lock_key)
    return ctx, held


def session_unlock(ctx: locks.ExecutionContext, held: list) -> None:
    for key in reversed(held):
        if ctx.find_held(key) is not None:
            locks.release(ctx, key)

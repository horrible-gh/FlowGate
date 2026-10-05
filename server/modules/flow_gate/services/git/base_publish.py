"""Base publish job: manual push and unmerge (flowgate.default.0669 unit 7c; 0666 0008-D
§3.6.1, §7, 0009-L §2.28, §4.4).

Neither path takes the old project mutex any more. The request registers a
``base_publish`` job and runs it once right away with its own claim (as worktree_provision
does); if a domain is busy the job stays queued and the Runner finishes it, while the
caller sees the old ``git_busy`` 409 with the job id. Phases (L 2.28):

    start -> applied (B) -> pushed (R) -> done

* start: the plan is written with the phase still ``start`` — base SHA before/after
  (b0, b1) and, for a push, the remote head before (h0) and the expected one (h1 = b1).
* applied: unmerge creates the work branch at the merge's second parent (M, create-only)
  and rewinds base with ``reset --hard b1`` (B). A push applies nothing (b0 = b1).
* pushed: plain ``push origin b1:refs/heads/<branch>`` under R; done once the remote reads
  h1. An unmerge has no push and goes from applied straight to done.
* done: terminal write, and for an unmerge the ledger ``awaiting_choice`` in the same
  transaction. The SSE and the slot re-provision follow it.

Deviations recorded as design changes (0669 chat):

* The push is not forced with a lease on h0: it is the old plain push, so git itself
  refuses anything but a fast-forward. h0 is recorded and checked as 4.4 asks, but a
  remote that moved elsewhere is not a recovery case here: the plain push either
  fast-forwards or is rejected (PERMANENT ``push_rejected``, 500 as before).
* The unmerge ledger change moves from before the reset to the done transaction, so a
  crash can no longer leave ``awaiting_choice`` over an unrewound base. The window is the
  other way round now: base rewound, ledger still ``merged`` until done.
* Lockfile checks (L 2.21) on the base checkout are not done, as in 6a.
* The 4.4 deciders read nothing remote. ``start`` of a push and ``applied`` re-run (a push
  is judged again under R by the step itself), so only ``start`` of an unmerge reads the
  base ref.
* Git failures of an unmerge are PERMANENT (500 ``git_error`` as before), so a failed
  unmerge never retries on its own in the background.

Unit 9a: a manual (non-approval) finalize is a job of this kind too, with action
``finalize``; its executor and resume live in ``finalize_publish``.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import operation_job as db
from modules.flow_gate.db.connection import get_store, now_iso

from . import approval_freeze
from . import job_runner as runner
from . import job_store as store
from . import lock_manager as locks
from .credentials import GitServiceError, _scrub

_log = logging.getLogger(__name__)

KIND = "base_publish"
PUSH = "push"
UNMERGE = "unmerge"
FINALIZE = "finalize"     # unit 9a: finalize_publish

_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}
_READ_ERROR = approval_freeze._READ_ERROR

# per-thread: "interactive" in a request, None (= "job") otherwise; and the error the
# request re-raises when its own attempt ended the job.
_origin = threading.local()


class _Stop(Exception):
    def __init__(self, result: store.RunResult):
        super().__init__(result.code)
        self.result = result


def _transient(code: str) -> _Stop:
    return _Stop(store.RunResult(store.TRANSIENT, code=code))


def _recovery(code: str) -> _Stop:
    return _Stop(store.RunResult(store.RECOVERY, code=code))


def _fail(exc: GitServiceError) -> _Stop:
    """PERMANENT, keeping the old HTTP error for the request that runs it."""
    _origin.error = exc
    return _Stop(store.RunResult(store.PERMANENT, code=exc.code[:64]))


# ── the operation as recorded ────────────────────────────────────────────────

@dataclass
class _Op:
    project_id: str
    group_id: Optional[str]
    action: str
    branch: str            # pushed branch / unmerged group's work branch
    base_branch: str
    repo: Path             # push: the branch's checkout; unmerge: the base checkout
    base_root: Path
    merge_sha: str         # unmerge: the ledger merge commit
    plan: dict             # start record ({} before it)


def _payload(job: dict) -> dict:
    return approval_freeze._json(job.get("payload"))


def _evidence(job: dict) -> dict:
    return approval_freeze._json(job.get("evidence"))


def _op_of(job: dict) -> _Op:
    p = _payload(job)
    if p.get("action") not in (PUSH, UNMERGE) or not p.get("branch") or not p.get("repo"):
        raise _recovery("base_publish_payload_unresolved")
    return _Op(job["project_id"], job.get("group_id"), p["action"], p["branch"],
               p.get("base_branch") or "main", Path(p["repo"]), Path(p.get("base_root") or p["repo"]),
               p.get("merge_sha") or "", dict(_evidence(job).get("plan") or {}))


def _git(repo: Path, args: list, **kw):
    from modules.flow_gate.services import git_service as _gs
    return _gs._run_git(args, cwd=repo, **kw)


def _rev(repo: Path, ref: str):
    """sha, None (absent) or _READ_ERROR; only the exact name counts."""
    proc = _git(repo, ["for-each-ref", "--format=%(refname) %(objectname)", ref])
    if proc.returncode != 0:
        return _READ_ERROR
    for line in (proc.stdout or "").splitlines():
        name, _, sha = line.strip().partition(" ")
        if name == ref and sha:
            return sha
    return None


def _remote_head(op: _Op):
    """origin's refs/heads/<branch>: sha, '' (absent) or _READ_ERROR. Under R."""
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs.db_git.get_config(op.project_id) or {}
    try:
        _gs.ensure_origin_matches_config(op.repo, (cfg.get("repo_url") or "").strip())
    except GitServiceError:
        return _READ_ERROR
    ref = f"refs/heads/{op.branch}"
    proc = _git(op.repo, ["ls-remote", "origin", ref], timeout=_gs.GIT_NET_TIMEOUT_SEC,
                username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "")
    if proc.returncode != 0:
        return _READ_ERROR
    for line in (proc.stdout or "").splitlines():
        sha, _, name = line.partition("\t")
        if name.strip() == ref and sha.strip():
            return sha.strip()
    return ""


# ── creation and the request's one attempt ───────────────────────────────────

def _identity(project_id: str, action: str, subject: str) -> str:
    """subject + the number of failed/cancelled jobs for it: a retry after a failure is a
    new job, a double click is the same one."""
    prefix = f"bsp:{project_id}:{action}:{subject}:"
    return f"{subject}:{db.count_failed_or_cancelled_by_key_prefix(project_id, KIND, prefix)}"


def request_push(project_id: str, branch: str, base_branch: str, repo: Path, base_root: Path) -> dict:
    """manual_push's job path (prechecks already done by the caller)."""
    sha = _rev(repo, f"refs/heads/{branch}")
    if sha is _READ_ERROR or not sha:
        raise GitServiceError(409, "invalid_state", f"local branch '{branch}' cannot be read")
    payload = {"action": PUSH, "branch": branch, "base_branch": base_branch,
               "repo": str(repo), "base_root": str(base_root)}
    req = {"action": PUSH, "source_identity": _identity(project_id, PUSH, f"{branch}@{sha}")}
    job = _run_request(project_id, None, req, payload)
    return {"ok": True, "result": dict(approval_freeze._json(job.get("result")) or {"pushed": True},
                                       job_id=job["job_id"])}


def request_unmerge(project_id: str, group_id: str, branch: str, base_branch: str,
                    base_root: Path, merge_sha: str) -> dict:
    """unmerge's job path (prechecks already done by the caller). Returns the old result;
    the slot re-provision runs after the job, outside it."""
    from modules.flow_gate.services import git_service as _gs
    payload = {"action": UNMERGE, "branch": branch, "base_branch": base_branch,
               "repo": str(base_root), "base_root": str(base_root), "merge_sha": merge_sha}
    req = {"action": UNMERGE,
           "source_identity": _identity(project_id, UNMERGE, f"{group_id}@{merge_sha}")}
    job = _run_request(project_id, group_id, req, payload)
    result = dict(approval_freeze._json(job.get("result")) or {})
    reprovisioned = _gs.ensure_worktree(project_id, _gs._module_of(group_id), group_id, trigger="unmerge")
    result.update(reprovisioned=reprovisioned == "ok", job_id=job["job_id"])
    return {"ok": True, "result": result}


def _run_request(project_id: str, group_id: Optional[str], req: dict, payload: dict) -> dict:
    """Create (or find) the job, run it once here, return it succeeded or raise."""
    install()
    cr = store.create_or_get_job(KIND, project_id, req, group_id=group_id, payload=payload, wake=False)
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
        raise GitServiceError(500, "git_error", "the operation failed",
                              details={"job_id": job["job_id"], "code": job.get("last_error_code")})
    details = {"queued": True, "job_id": job["job_id"], "job_status": status,
               "reason_code": job.get("last_error_code")}
    if job.get("blocked_domain"):
        details["blocker"] = {"domain": job.get("blocked_domain"),
                              "operation": job.get("blocked_operation")}
    if status == "recovery_required":
        raise GitServiceError(409, "recovery_required",
                              "the operation needs recovery before it can go on", details=details)
    raise GitServiceError(
        409, "git_busy",
        f"Another git operation is in progress for project '{project_id}'; "
        "this one is queued and runs when it is free",
        details=details,
    )


# ── steps ────────────────────────────────────────────────────────────────────

def _acquire(ctx: store.JobContext, domain: str, op: _Op) -> locks.LockOutcome:
    store.ensure_lease(ctx)
    mode = getattr(_origin, "mode", None) or "job"
    holder_kind = "publish" if domain == "R" else "base_mutation"
    o = locks.acquire(domain, op.project_id, holder_kind=holder_kind, mode=mode, ctx=ctx.lock_ctx)
    if o.ok:
        return o
    b = o.blocker or {}
    if o.kind == locks.BUSY:
        raise _Stop(store.RunResult(
            store.BLOCKED, code=o.kind, blocked_domain=domain, blocked_lock_key=o.lock_key,
            blocked_holder=b.get("holder_ctx_id"), blocked_operation=b.get("holder_kind")))
    if o.kind == locks.RECOVERY_REQUIRED:
        raise _recovery(f"base_publish_{domain}_recovery_required")
    raise _transient((o.cause or o.kind or "lock_store_error")[:64])


def _release(ctx: store.JobContext, o: locks.LockOutcome) -> None:
    if ctx.lock_ctx.find_held(o.lock_key) is not None:
        locks.release(ctx.lock_ctx, o.lock_key)


def _phase(ctx: store.JobContext, phase: str, **evidence) -> None:
    changes: dict = {"phase": phase}
    if evidence:
        ev = _evidence(ctx.job)
        ev.update(evidence)
        changes["evidence"] = store._canonical_json(ev)
    store.job_write(ctx, **changes)


def _guard_base(op: _Op) -> None:
    from modules.flow_gate.services import git_service as _gs
    try:
        _gs.guard_base_free(op.project_id)
    except GitServiceError as exc:
        raise _fail(exc)


def _plan_push(ctx: store.JobContext, op: _Op) -> None:
    """start for a push: B (base branch only) then R; records b0 = b1 and h0, h1."""
    pushes_base = op.branch == op.base_branch
    b = _acquire(ctx, "B", op) if pushes_base else None
    try:
        if pushes_base:
            _guard_base(op)                      # 0205 §2.2, 2nd gate under B
        sha = _rev(op.repo, f"refs/heads/{op.branch}")
        if sha is _READ_ERROR:
            raise _transient("ref_check_failed")
        if not sha:
            raise _fail(GitServiceError(409, "invalid_state", f"local branch '{op.branch}' is missing"))
        r = _acquire(ctx, "R", op)
        try:
            h0 = _remote_head(op)
            if h0 is _READ_ERROR:
                raise _transient("remote_head_unknown")
            op.plan = {"b0": sha, "b1": sha, "h0": h0, "h1": sha, "push": True}
            _phase(ctx, "start", plan=op.plan)
            _phase(ctx, "applied")              # a push applies nothing to base
        finally:
            _release(ctx, r)
    finally:
        if b is not None:
            _release(ctx, b)


def _unmerge_plan(op: _Op) -> dict:
    """The old unmerge checks, read under B. Raises _Stop on a refusal."""
    from .commit import _ledger_group_by_merge_sha
    from .refs import _full_sha_matches, _rev_parse, _unpushed_commits
    commits = _unpushed_commits(op.base_root, op.base_branch)
    if commits is None:
        raise _fail(GitServiceError(409, "invalid_state", "unpushed base history is not measurable"))
    if not commits:
        raise _fail(GitServiceError(409, "already_pushed", "merge commit is no longer unpushed"))
    top = commits[0]
    if not _full_sha_matches(top["full_sha"], op.merge_sha):
        if any(_full_sha_matches(c["full_sha"], op.merge_sha) for c in commits):
            raise _fail(GitServiceError(
                409, "not_top_merge", "a newer unpushed commit blocks unmerge",
                details={"top_merge_commit": top["full_sha"][:7],
                         "top_group_id": _ledger_group_by_merge_sha(op.project_id, top["full_sha"])}))
        raise _fail(GitServiceError(409, "already_pushed", "merge commit is no longer unpushed"))
    if len(top["parents"]) < 2:
        raise _fail(GitServiceError(409, "stale_target",
                                    "requested merge commit is no longer the current top merge",
                                    details={"current_top": top["full_sha"][:7]}))
    restored = _rev_parse(op.base_root, f"{top['full_sha']}^2")
    if not restored:
        raise _fail(GitServiceError(500, "git_error", "cannot resolve merged work branch head"))
    head = _rev(op.base_root, f"refs/heads/{op.base_branch}")
    if head is _READ_ERROR:
        raise _transient("ref_check_failed")
    if head != top["full_sha"]:
        raise _fail(GitServiceError(409, "stale_target", "base moved during unmerge"))
    return {"b0": top["full_sha"], "b1": top["parents"][0], "restored": restored, "push": False}


def _apply_unmerge(ctx: store.JobContext, op: _Op) -> None:
    """start -> applied under B: plan, work branch (M, create-only), reset --hard b1."""
    b = _acquire(ctx, "B", op)
    try:
        _guard_base(op)
        if not op.plan:
            op.plan = _unmerge_plan(op)
            _phase(ctx, "start", plan=op.plan)
        head = _rev(op.base_root, f"refs/heads/{op.base_branch}")
        if head is _READ_ERROR:
            raise _transient("ref_check_failed")
        if head == op.plan["b0"]:
            _ensure_branch(ctx, op)
            store.ensure_lease(ctx)
            proc = _git(op.base_root, ["reset", "--hard", op.plan["b1"]])
            if proc.returncode != 0:
                _log.warning("unmerge reset failed for %s: %s", op.group_id,
                             _scrub((proc.stderr or "").strip()[-300:]))
            head = _rev(op.base_root, f"refs/heads/{op.base_branch}")
            if head is _READ_ERROR:
                raise _transient("ref_check_failed")
            if head == op.plan["b0"]:
                raise _fail(GitServiceError(500, "git_error", "Git command failed"))
        if head != op.plan["b1"]:
            raise _recovery("base_moved_during_unmerge")
        _phase(ctx, "applied")
    finally:
        _release(ctx, b)


def _ensure_branch(ctx: store.JobContext, op: _Op) -> None:
    """The work branch at the merge's second parent, created only when absent (M)."""
    ref = f"refs/heads/{op.branch}"
    current = _rev(op.base_root, ref)
    if current is _READ_ERROR:
        raise _transient("ref_check_failed")
    if current is None:
        m = _acquire(ctx, "M", op)
        try:
            store.ensure_lease(ctx)
            approval_freeze.run_in_m(ctx.lock_ctx, op.project_id, op.base_root,
                                     ["update-ref", ref, op.plan["restored"], "0" * 40])
            current = _rev(op.base_root, ref)
        finally:
            _release(ctx, m)
        if current is _READ_ERROR:
            raise _transient("ref_check_failed")
    if current != op.plan["restored"]:
        raise _fail(GitServiceError(500, "git_error",
                                    f"local branch '{op.branch}' exists at an unexpected commit"))


def _start(ctx: store.JobContext, op: _Op) -> None:
    if op.action == PUSH:
        _plan_push(ctx, op)
    else:
        _apply_unmerge(ctx, op)


def _push(ctx: store.JobContext, op: _Op) -> None:
    """applied -> pushed under R (push only; an unmerge goes to done)."""
    from modules.flow_gate.services import git_service as _gs
    if not op.plan.get("push"):
        _finish(ctx, op)
        return
    r = _acquire(ctx, "R", op)
    try:
        h1 = op.plan["h1"]
        remote = _remote_head(op)
        if remote is _READ_ERROR:
            raise _transient("remote_head_unknown")
        if remote != h1:
            cfg = _gs.db_git.get_config(op.project_id) or {}
            store.ensure_lease(ctx)
            proc = _git(op.repo, ["push", "origin", f"{h1}:refs/heads/{op.branch}"],
                        timeout=_gs.GIT_NET_TIMEOUT_SEC, username=cfg.get("username"),
                        secret=_gs._load_secret_for(cfg) or "")
            if proc.returncode != 0:
                remote = _remote_head(op)
                if remote is _READ_ERROR:
                    raise _transient("push_result_unknown")
                if remote != h1:
                    raise _fail(GitServiceError(500, "push_rejected", "Git push was rejected",
                                                diagnostic=_gs._last_line(proc.stderr)))
            else:
                remote = h1
        _phase(ctx, "pushed", remote_head=remote)
    finally:
        _release(ctx, r)


def _finish(ctx: store.JobContext, op: _Op) -> None:
    """-> done: terminal write (+ the unmerge ledger change) in one transaction."""
    from modules.flow_gate.services import git_service as _gs
    from .refs import _short_head
    store.ensure_lease(ctx)
    if op.action == PUSH:
        pushes_base = op.branch == op.base_branch
        ahead, behind = _gs._base_ahead_behind(op.repo, op.base_branch) if pushes_base else (None, None)
        result = {"pushed": True, "branch": op.branch, "ahead_count": ahead, "behind_count": behind}
    else:
        result = {"unmerged": True, "merge_commit": op.plan["b0"][:7],
                  "base_head": _short_head(op.base_root), "group_status": "awaiting_choice"}
    with get_store().transaction():
        written = store.fenced_write(ctx, dict(status="succeeded", phase="done", finished_at=now_iso(),
                                               result=result, **_LEASE_CLEARED),
                                     update_local=False)
        if op.action == UNMERGE:
            _gs.db_git.set_status(op.group_id, "awaiting_choice")
    ctx.job.update(written)
    _after_done(op)


def _after_done(op: _Op) -> None:
    from modules.flow_gate.services import git_service as _gs
    try:
        if op.action == UNMERGE:
            _gs._emit_pending_changed(op.project_id, op.group_id, "awaiting_choice")
        elif op.branch == op.base_branch:
            _gs._emit_pending_changed(op.project_id, None, None)
    except Exception:
        _log.warning("base_publish emit failed for %s", op.project_id, exc_info=True)
    if op.action == UNMERGE and getattr(_origin, "mode", None) != "interactive":
        # A queued unmerge finished by the Runner: re-provision off this thread (the
        # request path does it itself after the job).
        try:
            from .worktree import ensure_worktree_async
            ensure_worktree_async(op.project_id, _gs._module_of(op.group_id), op.group_id)
        except Exception:
            _log.warning("unmerge re-provision not started for %s", op.group_id, exc_info=True)


def _done_db(ctx: store.JobContext, op: _Op) -> None:
    _finish(ctx, op)


_STEPS = {
    "start": _start,
    "applied": _push,
    "pushed": _done_db,
}


def run(ctx: store.JobContext, entry: str) -> Optional[store.RunResult]:
    """Runner executor. Resumes at the recorded phase; None once _finish wrote the
    terminal state, otherwise the result this claim ends with."""
    if entry != store.PUBLISH:
        raise store.JobProgramError("base_publish_entry_not_publish", job_id=ctx.job_id, entry=entry)
    if _payload(ctx.job).get("action") == FINALIZE:
        from . import finalize_publish
        return finalize_publish.run(ctx, entry)
    try:
        op = _op_of(ctx.job)
        for _ in range(len(_STEPS) + 1):
            phase = ctx.job.get("phase") or "start"
            if phase == "done":
                return None
            step = _STEPS.get(phase)
            if step is None:
                return store.RunResult(store.RECOVERY, code="base_publish_unknown_phase")
            step(ctx, op)
        return store.RunResult(store.TRANSIENT, code="base_publish_no_progress")
    except _Stop as stop:
        return stop.result


# ── L 4.4 deciders (lock-free F reads) ───────────────────────────────────────

def _decide_not_applied(job: dict) -> "runner.Decision":
    return runner.Decision(runner.NOT_APPLIED)


def _decide_start(job: dict) -> "runner.Decision":
    """unmerge: base ref vs (b0, b1). A push applies nothing, so it always re-plans; a
    finalize is judged by its own resume under the locks (finalize_publish)."""
    if _payload(job).get("action") == FINALIZE:
        return runner.Decision(runner.NOT_APPLIED)
    op = _op_of(job)
    if op.action == PUSH or not op.plan:
        return runner.Decision(runner.NOT_APPLIED)
    head = _rev(op.base_root, f"refs/heads/{op.base_branch}")
    if head is _READ_ERROR:
        return runner.Decision(runner.TRANSIENT_CHECK_FAILURE, code="ref_check_failed")
    if head == op.plan.get("b1"):
        return runner.Decision(runner.APPLIED_NEXT, next_phase="applied")
    if head == op.plan.get("b0"):
        return runner.Decision(runner.NOT_APPLIED)
    return runner.Decision(runner.UNDECIDABLE, code="base_moved_during_unmerge")


def _guarded(fn):
    def decide(job: dict) -> "runner.Decision":
        try:
            return fn(job)
        except _Stop as stop:
            return runner.Decision(runner.UNDECIDABLE, code=stop.result.code)
    return decide


_DECIDERS = {
    "start": _decide_start,
    "applied": _decide_not_applied,     # the push step re-reads the remote under R
    "pushed": _decide_not_applied,      # DB only
}


def install() -> None:
    """Executor and L 4.4 deciders. Idempotent."""
    runner.register_executor(KIND, run)
    for phase, fn in _DECIDERS.items():
        runner.register_decider(KIND, phase, _guarded(fn))

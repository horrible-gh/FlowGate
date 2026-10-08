"""Manual finalize job (flowgate.default.0669 unit 9a; 0666 0008-D §3.6.1, §7 "finalize
일반(비승인)", 0009-L §2.28, §4.4).

``finalize`` without a final approval no longer takes the old project mutex. After its
prechecks the request registers a ``base_publish`` job with action ``finalize`` and runs it
once right away with its own claim (as base_publish does); if a domain is busy the job stays
queued and the Runner finishes it, while the caller sees the old ``git_busy`` 409 with the
job id. ``wait`` sets no Git state and never becomes a job.

Admission (L 2.4 order), held for the whole finalize as the approval publish holds its own:

* G of the Group — the absorb commit, the conversation snapshot and the slot cleanup write
  the Group's slot. It passes the Group freeze guard (L 2.10): a Group an approval froze is
  not finalized by hand; the job waits (blocked ``group_frozen``).
* merge actions: B for the project base target, otherwise W keyed by the target branch (the
  same W a final approval or a branch merge into that branch takes)
* R, except for ``commit_only``

The body is the old ``finalize`` with ``job:{job_id}`` as the attempt's lock holder, so
``merge_target.attempt_phase`` reads the attempt as in progress for as long as the job is
not terminal and the sweep/startup recovery leave it to the job.

Deviations recorded as design changes (0669 chat):

* No new job kind: D §7 allows "base_publish 또는 동일 kind", and a new kind would need the
  operation_job kind CHECK changed in three schemas. The request key is
  ``bsp:{project}:finalize:{group_id}:{n}``, n the number of terminal finalize jobs of the
  Group: a double click is the same job, the next finalize after one ended a new one.
* Phases are ``start`` (evidence ``started`` written right before the body) and ``done``.
  The approval's finer publish phases are not recorded; as there (6b), every phase decides
  NOT_APPLIED and the next claim's ``_resume`` judges under the locks: this job's open
  attempt → conflict handed off, or ``recover_interrupted_attempt`` (landed → succeeded,
  interrupted → run again, unprovable → recovery_required); a started job whose Group
  ledger is already terminal → that terminal answer.
* G, W/B and R are held for the whole body instead of phase by phase (D §7 "승인과 동일
  phase"), exactly as the old mutex covered it: the body interleaves Group, target and
  remote steps (absorb → fetch → merge → push → slot cleanup) and is not split here.
* The merged/pushed slot cleanup stays inline in the body (under G), not a worktree_cleanup
  job, as before.
* Every exception of the body is PERMANENT and reaches the request as the old error; a
  failed finalize never retries on its own in the background.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.db import operation_job as db
from modules.flow_gate.db.connection import get_store, now_iso

from . import approval_freeze
from . import base_publish
from . import job_store as store
from . import lock_manager as locks
from . import merge_target
from .credentials import GitServiceError

_log = logging.getLogger(__name__)

FINALIZE = "finalize"

_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}


def holder_of(job_id: str) -> str:
    return merge_target.JOB_HOLDER_PREFIX + job_id


def _payload(job: dict) -> dict:
    return approval_freeze._json(job.get("payload"))


def _evidence(job: dict) -> dict:
    return approval_freeze._json(job.get("evidence"))


def _fail(exc: Exception) -> base_publish._Stop:
    """PERMANENT, keeping the old error for the request that runs it."""
    base_publish._origin.error = exc
    code = exc.code if isinstance(exc, GitServiceError) else "unexpected_error"
    return base_publish._Stop(store.RunResult(store.PERMANENT, code=str(code)[:64]))


# ── creation and the request's one attempt ───────────────────────────────────

def _seq(group_id: str) -> int:
    jobs = db.jobs_of_group(group_id, base_publish.KIND, db.TERMINAL_STATUSES)
    return sum(1 for job in jobs if _payload(job).get("action") == FINALIZE)


def request(project_id: str, group_id: str, action: str, commit_message: Optional[str],
            target_branch: Optional[str]) -> dict:
    """finalize's job path (prechecks already done by the caller). Returns the old answer
    with the job id, or raises its old error / 409 git_busy (queued) / recovery_required."""
    payload = {"action": FINALIZE, "git_action": action, "commit_message": commit_message,
               "target_branch": target_branch}
    req = {"action": FINALIZE, "source_identity": f"{group_id}:{_seq(group_id)}"}
    job = base_publish._run_request(project_id, group_id, req, payload)
    out = dict(approval_freeze._json(job.get("result")) or {})
    result = dict(out.get("result") or {})
    result["job_id"] = job["job_id"]
    out["result"] = result
    out.setdefault("ok", True)
    return out


# ── locks ────────────────────────────────────────────────────────────────────

def _wanted(project_id: str, group_id: str, p: dict) -> list:
    """(domain, group_id, target_key, holder_kind) in L 2.4 order (module doc)."""
    from modules.flow_gate.services import git_service as _gs
    action = p.get("git_action")
    wanted = [("G", group_id, None, "finalize")]
    if action in merge_target.MERGE_ACTIONS:
        cfg = _gs.db_git.get_config(project_id) or {}
        try:
            target = merge_target.plan_finalize_target(
                group_id, project_id, cfg, merge_target.normalize_requested_target(p.get("target_branch")))
        except GitServiceError as exc:
            raise _fail(exc)
        wanted.append(("B", None, None, "publish") if target.is_project_base
                      else ("W", None, target.target_branch, "publish"))
    if action != "commit_only":
        wanted.append(("R", None, None, "publish"))
    return wanted


def _admit(ctx: store.JobContext, p: dict, held: list) -> None:
    mode = getattr(base_publish._origin, "mode", None) or "job"
    project_id, group_id = ctx.job["project_id"], ctx.job["group_id"]
    for domain, g, target_key, kind in _wanted(project_id, group_id, p):
        store.ensure_lease(ctx)
        o = locks.acquire(domain, project_id, group_id=g, target_key=target_key, holder_kind=kind,
                          mode=mode, hold_class="long", ctx=ctx.lock_ctx)
        if o.ok and domain == "G" and not o.reentrant:
            o = locks._guard_group_admission(ctx.lock_ctx, o.lock_key, project_id, group_id, o)
        if not o.ok:
            b = o.blocker or {}
            if o.kind in (locks.BUSY, locks.GROUP_FROZEN):
                raise base_publish._Stop(store.RunResult(
                    store.BLOCKED, code=o.kind, blocked_domain=domain, blocked_lock_key=o.lock_key,
                    blocked_holder=b.get("holder_ctx_id") or b.get("job_id"),
                    blocked_operation=b.get("holder_kind") or b.get("kind")))
            if o.kind == locks.RECOVERY_REQUIRED:
                raise base_publish._recovery(f"finalize_{domain}_recovery_required")
            raise base_publish._transient((o.cause or o.kind or "lock_store_error")[:64])
        held.append(o.lock_key)


def _release_all(ctx: store.JobContext, held: list) -> None:
    for key in reversed(held):
        if ctx.lock_ctx.find_held(key) is not None:
            locks.release(ctx.lock_ctx, key)


# ── resume (module doc) ──────────────────────────────────────────────────────

def _merged(action: str, rec: dict, result: dict) -> dict:
    return {"ok": True, "result": {
        "action": action, "status": "merged", "merge_commit": result.get("merge_commit"),
        "pushed": bool(result.get("pushed")), "merge_id": None, "conflict_files": [],
        "target_branch": rec.get("branch"), "recovered": True}}


def _own_attempts(group_id: str, holder: str) -> list:
    from modules.flow_gate.services import git_service as _gs
    out = []
    for session in _gs.db_git.sessions_by_group(group_id):
        if _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
            continue
        rec = merge_target.target_record(session) or {}
        if rec.get("lock_holder") == holder:
            out.append((session, rec))
    return out


def _resume(ctx: store.JobContext, p: dict) -> Optional[dict]:
    """Settle what an earlier claim of this job left, under its locks. None = run."""
    from modules.flow_gate.services import git_service as _gs
    group_id, action = ctx.job["group_id"], p.get("git_action")
    holder = holder_of(ctx.job_id)
    session = _gs.db_git.get_open_session_by_group(group_id)
    rec = (merge_target.target_record(session) or {}) if session is not None else {}
    if session is not None and rec.get("lock_holder") == holder:
        merge_id = int(session["merge_id"])
        if merge_target.attempt_phase(session) == merge_target.PHASE_CONFLICT:
            files = [row["path"] for row in _gs.db_git.session_files(merge_id)]
            return {"ok": True, "result": {
                "action": action, "status": "conflict", "merge_commit": None, "pushed": False,
                "merge_id": merge_id, "conflict_files": files, "approval_intent_id": None,
                "target_branch": rec.get("branch"), "resumed": True}}
        store.ensure_lease(ctx)
        verdict = merge_target.recover_interrupted_attempt(session, "finalize_job_resume")
        if verdict == merge_target.ATTEMPT_INTERRUPTED:
            return None
        if verdict == merge_target.ATTEMPT_COMPLETED:
            closed = _gs.db_git.session_context(_gs.db_git.get_session(merge_id))
            return _merged(action, rec, closed.get("attempt_result") or {})
        raise base_publish._recovery("finalize_attempt_unsettled")
    for old, old_rec in _own_attempts(group_id, holder):
        context = _gs.db_git.session_context(old)
        if context.get(merge_target.ATTEMPT_STATE_KEY) == merge_target.ATTEMPT_COMPLETED:
            return _merged(action, old_rec, context.get("attempt_result") or {})
    if not _evidence(ctx.job).get("started"):
        return None
    state = _gs.db_git.get_state(group_id) or {}
    status = state.get("status") or "none"
    if status in ("merged", "pushed"):
        return {"ok": True, "result": {
            "action": action, "status": status, "merge_commit": state.get("merge_commit"),
            "pushed": status == "pushed" or (status == "merged" and action == "merge"),
            "merge_id": None, "conflict_files": [], "resumed": True}}
    if status == "none" and not state.get("worktree_registered"):
        from .finalize import DISCARDED_STATUS
        return {"ok": True, "result": {
            "action": action, "status": DISCARDED_STATUS, "merge_commit": None, "pushed": False,
            "merge_id": None, "conflict_files": [], "resumed": True}}
    return None


# ── execution ────────────────────────────────────────────────────────────────

def _finish(ctx: store.JobContext, result: dict) -> None:
    store.ensure_lease(ctx)
    with get_store().transaction():
        written = store.fenced_write(ctx, dict(status="succeeded", phase="done", finished_at=now_iso(),
                                               result=result, **_LEASE_CLEARED),
                                     update_local=False)
    ctx.job.update(written)


def run(ctx: store.JobContext, entry: str) -> Optional[store.RunResult]:
    """base_publish's executor for action ``finalize``: admission, resume, the old
    finalize body, the terminal write."""
    from modules.flow_gate.services import git_service as _gs
    if entry != store.PUBLISH:
        raise store.JobProgramError("finalize_entry_not_publish", job_id=ctx.job_id, entry=entry)
    held: list = []
    try:
        if (ctx.job.get("phase") or "start") == "done":
            return None
        p = _payload(ctx.job)
        _admit(ctx, p, held)
        result = _resume(ctx, p)
        if result is None:
            store.ensure_lease(ctx)
            ev = _evidence(ctx.job)
            ev["started"] = True
            store.job_write(ctx, phase="start", evidence=store._canonical_json(ev))
            kwargs = {"job_holder": holder_of(ctx.job_id)}
            if p.get("target_branch") is not None:
                kwargs["target_branch"] = p["target_branch"]
            try:
                result = _gs.finalize(ctx.job["group_id"], p.get("git_action"),
                                      p.get("commit_message"), **kwargs)
            except (store.LeaseLost, locks.LockProgramError, store.JobProgramError):
                raise
            except Exception as exc:
                raise _fail(exc)
        _finish(ctx, result)
        return None
    except base_publish._Stop as stop:
        return stop.result
    finally:
        _release_all(ctx, held)

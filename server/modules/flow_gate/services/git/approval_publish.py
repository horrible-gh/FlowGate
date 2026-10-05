"""Final approval publish (flowgate.default.0669 unit 6b; 0666 0008-D §3.7, 0009-L §2.28,
§4.4, §4.5, 0011-P "최종 승인").

A final approval with a ``git_action`` is a ``final_approval_publish`` job. The HTTP
request creates it (``freezing``, lease held by the request), freezes the Group right
there (``approval_freeze``, F1~F5) and then makes ONE publish attempt with the same
lease. Whatever cannot finish in that request stays a durable job — G busy at the
freeze (freeze_wait), W/B/R busy at publish (blocked), a transient Git failure
(retry_wait) — and the Job Runner finishes it later. The approval intent is never lost
to a busy lock, which was the 0669 B (``git_busy`` on approve).

Publish (``publish``), with the job's own lock context:

1. admission — W (non-base target) or B (base target), then R; nothing else. The old
   project mutex no longer exists (0669 unit 9c).
2. resume — what an earlier claim of this job left is settled first, under those
   locks: a clean retry snapshot of this approval → Git is terminal; this job's own
   open attempt → conflict hand-off, or ``recover_interrupted_attempt`` (landed →
   terminal, interrupted → run again, undecidable → recovery_required).
3. Git — the existing ``finalize`` with an ``ApprovalFinalizeContext`` carrying the
   job: frozen SHA as merge/push source, each phase recorded (L 2.28) before the Git
   step it precedes, ``job:{job_id}`` as the attempt's holder.
4. db_finalize — AC approved, root wf_done, the clean retry snapshot consumed, job
   succeeded and the Group freeze claim cleared: one transaction
   (``commit_final_approval``'s consume hook). Slot cleanup and SSE come after it,
   exactly as before.

A conflict hands the approval to the merge review as before (the intent is parked on
the session); the job ends ``succeeded`` (handed off) and clears the claim in one
transaction. A permanent failure ends through ``approval_freeze.freeze_release`` (pin
removed only at the recorded SHA, claim cleared with the terminal write).

Deviations from L, recorded as design changes (0669 chat):

* L 4.4 rows of this kind are not evidence deciders: every publish phase decides
  NOT_APPLIED at its own phase and the evidence is judged by ``_resume`` at the next
  claim, under W/B and R — it may need ``merge --abort`` / ``reset --keep`` (the
  0594 attempt recovery), which a lock-free decider must not run. The evidence is the
  same one 4.4 names (attempt merge inputs, remote ref via ``_settle_landed_merge``);
  an outcome that cannot be proven parks the job in recovery_required.
* A 4xx Git result (base dirty, open conflict elsewhere, invalid target, frozen source
  mismatch, ...) is PERMANENT: the approval fails as it did before and the freeze is
  released. ``git_busy`` and 5xx are TRANSIENT (retry_wait with backoff); past
  RETRY_MAX_ATTEMPTS they fail through freeze_release too.
* The job's execution never asks for the approver's live permissions: like the
  conflict-deferred approval (``approval_intent._DEFERRED_PERMISSIONS``) the grant was
  checked by the request's precheck when the job was created.
* After db_finalize, ``complete_approve_git_action`` registers the Group's
  worktree_cleanup job (unit 7a), which tears the slot down and deletes this job's pin;
  the old inline cleanup remains only as the fallback when registration fails.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db.connection import get_store, now_iso

from . import approval_freeze
from . import approval_intent
from . import job_notifier as notifier
from . import job_runner as runner
from . import job_store as store
from . import lock_manager as locks
from . import merge_target
from .credentials import GitServiceError
from .finalize import APPROVAL_FINALIZE_ACTIONS, ApprovalFinalizeContext

_log = logging.getLogger(__name__)

KIND = approval_freeze.KIND
REQUEST = approval_freeze.REQUEST
JOB = approval_freeze.JOB

# L 2.28 publish phases (after F5); "done" is the Runner's own APPLIED_TERMINAL row.
PUBLISH_PHASES = ("publish_pending", "admission", "fetched", "merge_inputs_recorded", "merged",
                  "push_started", "pushed", "db_finalize")

EXECUTED = "executed"
HANDED_OFF = "handed_off"
QUEUED = "queued"
RECOVERY = "recovery_required"
FAILED = "failed"

_TRANSIENT_CODES = frozenset(("git_busy",))
_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}
_HANDOFF_RESULT = "handed_off_to_conflict_review"

# 0674 T0004 §2-6: what a terminal job's ``status`` alone cannot say. A conflict hand-off
# and an approval committed through the approval-retry route both end ``succeeded`` (the
# Runner's terminal contract), so the result's meaning rides beside it as ``outcome``.
OUTCOME_EXECUTED = "executed"
OUTCOME_HANDED_OFF = "handed_off"
OUTCOME_APPROVED_ELSEWHERE = "approved_elsewhere"

# 0674 T0004 §2-5: the non-terminal transitions the screen is told about (SSE
# ``git_approval_job_changed``). Terminal ones keep their own events (git_finalize_done).
NOTIFY_STATUSES = frozenset(("freeze_wait", "blocked", "retry_wait", "recovery_required"))
APPROVAL_JOB_EVENT = "git_approval_job_changed"


@dataclass
class Outcome:
    """What one request (or claim) ended with; the router turns it into the response."""
    state: str
    job: Optional[dict] = None
    git: Optional[dict] = None
    approval: Optional[dict] = None
    document: Optional[dict] = None
    error: Optional[dict] = None
    http_status: int = 200
    extra: dict = field(default_factory=dict)


def _payload(job: dict) -> dict:
    return approval_freeze._payload(job)


def _evidence(job: dict) -> dict:
    return approval_freeze._json(job.get("evidence"))


def holder_of(job_id: str) -> str:
    return merge_target.JOB_HOLDER_PREFIX + job_id


# ── job view (P "Job 조회" shape, the fields the approval response carries) ──

def job_outcome(job: Optional[dict]) -> Optional[str]:
    """0674 T0004 §2-6: the meaning of a ``succeeded`` job — executed, handed off to the
    conflict review, or approved elsewhere (approval-retry route). failed/cancelled answer
    their status; a job that is not terminal has no outcome yet."""
    status = (job or {}).get("status")
    if status in ("failed", "cancelled"):
        return status
    if status != "succeeded":
        return None
    result = job.get("result")
    if result == _HANDOFF_RESULT:
        return OUTCOME_HANDED_OFF
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = None
    if isinstance(result, dict) and result.get("approved_elsewhere"):
        return OUTCOME_APPROVED_ELSEWHERE
    return OUTCOME_EXECUTED


def job_stage(job: Optional[dict]) -> Optional[str]:
    """The screen's reading of a job status (0674 T0004 §2-4): every waiting/executing
    status as it is, every terminal one as ``terminal`` (``outcome`` tells which)."""
    status = (job or {}).get("status")
    if not status:
        return None
    if status in runner.TERMINAL:
        return "terminal"
    return status


def job_view(job: Optional[dict]) -> Optional[dict]:
    if not job:
        return None
    view = {k: job.get(k) for k in (
        "job_id", "kind", "status", "phase", "project_id", "group_id", "frozen_sha",
        "blocked_domain", "blocked_operation", "wait_started_at", "last_error_code",
        "attempt_count", "created_at", "updated_at", "finished_at")}
    view["cancel_requested"] = int(job.get("cancel_requested") or 0) == 1
    view["retryable"] = job.get("retryable") in (1, True, None)
    view["cancelable"] = (job.get("status") in ("freeze_wait", "pending", "blocked", "retry_wait")
                          and job.get("phase") in ("freeze_pending", "F1", "F3", "publish_pending"))
    view["outcome"] = job_outcome(job)
    view["stage"] = job_stage(job)
    view["doc_id"] = _payload(job).get("doc_id")
    return view


def blocker_of(job: Optional[dict]) -> Optional[dict]:
    if not job or not job.get("blocked_domain"):
        return None
    return {"domain": job.get("blocked_domain"), "lock_key": job.get("blocked_lock_key"),
            "holder": job.get("blocked_holder"), "holder_kind": job.get("blocked_operation"),
            "since": job.get("wait_started_at")}


def approval_job_state(group_id: str) -> Optional[dict]:
    """0674 T0004 §2-4: the Group's live final approval job for the finalize state — the
    job view plus its blocker (the same ``blocker_of`` the approval response carries).
    None when no approval job of this Group is waiting or running."""
    job = active_job_of_group(group_id)
    if job is None:
        return None
    view = job_view(job)
    view["blocker"] = blocker_of(job)
    return view


def notify_progress(job: Optional[dict]) -> None:
    """0674 T0004 §2-5: tell the screen a final approval job moved into a waiting state
    (freeze_wait / blocked / retry_wait / recovery_required), from the request and from
    the Runner/Sweeper alike. Best effort: the screen re-reads the finalize state."""
    if not job or job.get("status") not in NOTIFY_STATUSES or not job.get("group_id"):
        return
    from modules.flow_gate.services import git_service as _gs
    try:
        view = job_view(job)
        view["blocker"] = blocker_of(job)
        _gs._emit(APPROVAL_JOB_EVENT, job["project_id"], job["group_id"], {
            "project": job["project_id"], "group_id": job["group_id"],
            "doc_id": view.get("doc_id"), "status": job.get("status"),
            "stage": view.get("stage"), "job": view,
        })
    except Exception:
        _log.warning("approval job progress event failed: %s", job.get("job_id"), exc_info=True)


def describe(job: Optional[dict]) -> Outcome:
    """Outcome of a job this request did not (or no longer) drive."""
    status = (job or {}).get("status")
    if status == "succeeded":
        handed = (job or {}).get("result") == _HANDOFF_RESULT
        return Outcome(HANDED_OFF if handed else EXECUTED, job=job)
    if status in ("failed", "cancelled"):
        code = (job or {}).get("last_error_code") or (job or {}).get("result") or status
        return Outcome(FAILED, job=job, http_status=409, error={
            "code": code, "message": f"final approval job {status}: {code}"})
    if status == "recovery_required":
        return Outcome(RECOVERY, job=job)
    return Outcome(QUEUED, job=job)


# ── request entry (D 3.7 steps 1~4) ──────────────────────────────────────────

def start(*, doc: dict, group_id: str, git_action: str, target_branch: Optional[str],
          actor_user_id: str, locale: str) -> Outcome:
    """Create (or find) the approval job and drive it as far as this request can.
    The caller already ran the approval precheck."""
    from modules.flow_gate.services import git_service as _gs
    if git_action not in APPROVAL_FINALIZE_ACTIONS:
        raise GitServiceError(422, "invalid_request", "final approval requires a terminal Git action")
    install()
    project_id = _gs._project_of_group(group_id)
    revision = doc.get("revision_no")
    req = {"doc_id": doc["doc_id"], "doc_revision_no": 0 if revision is None else revision,
           "git_action": git_action, "target_branch": target_branch, "commit_title": None,
           "group_id": group_id}
    payload = {"doc_id": doc["doc_id"], "git_action": git_action, "target_branch": target_branch,
               "commit_title": None, "requested_by": actor_user_id, "locale": locale,
               # 0555 T0008 §2: one id per approval, handed to a conflict session if any
               "approval_intent_id": str(uuid.uuid4())}
    cr = store.create_or_get_job(KIND, project_id, req, group_id=group_id, payload=payload,
                                 requested_by=actor_user_id)
    if cr.outcome == store.REJECTED:
        return Outcome(FAILED, job=cr.job, http_status=409, error={
            "code": cr.code or "request_key_conflict",
            "message": "this approval request key belongs to a different request"})
    if cr.outcome == store.STORE_ERROR:
        return Outcome(FAILED, http_status=503, error={
            "code": "store_error", "message": "the approval job could not be recorded",
            "details": {"cause": cr.code}})
    if cr.outcome == store.EXISTING:
        return describe(cr.job)
    return _drive_request(cr.ctx)


def _drive_request(ctx: store.JobContext) -> Outcome:
    """Freeze, then the one immediate publish attempt, with the request's lease."""
    locks.bind(ctx.lock_ctx)
    try:
        with store.lease_scope(ctx):
            r = approval_freeze.run_freeze(ctx, REQUEST)
            if r != approval_freeze.FROZEN:
                return describe(ctx.job)
            return publish(ctx, REQUEST)
    except store.LeaseLost:
        _log.warning("approval job lease lost in request: %s", ctx.job_id)
        return describe(store.get_job(ctx.job_id) or ctx.job)
    except Exception as exc:
        # Same rule as the Runner: an unknown result is recorded and the lease left to
        # lapse, so the Sweeper's reconcile decides. The approval itself stays queued.
        _log.error("approval job failed in request: %s (%s)", ctx.job_id, type(exc).__name__,
                   exc_info=True)
        if not ctx.lease_lost and ctx.job.get("status") in store.EXECUTING:
            try:
                store.finish_run(ctx, store.RunResult(store.UNKNOWN, code="executor_exception"))
            except Exception:
                _log.warning("approval job unknown-result record failed: %s", ctx.job_id, exc_info=True)
        return Outcome(FAILED, job=store.get_job(ctx.job_id) or ctx.job, http_status=500, error={
            "code": "approval_job_error", "message": type(exc).__name__})
    finally:
        try:
            locks.unbind(ctx.lock_ctx)
        except Exception:
            _log.warning("approval job context unbind failed: %s", ctx.job_id, exc_info=True)
        if ctx.job.get("status") in runner.TERMINAL:
            notifier.job_terminal(ctx.job)
        else:
            notify_progress(ctx.job)


# ── executor (Runner entry, L 4.5) ───────────────────────────────────────────

def run(ctx: store.JobContext, entry: str) -> None:
    if entry in (store.FREEZE, store.RELEASE):
        return approval_freeze.run_entry(ctx, entry)
    publish(ctx, JOB)
    return None


# ── publish ──────────────────────────────────────────────────────────────────

def _phase_writer(ctx: store.JobContext):
    def write(phase: str, evidence: dict) -> None:
        store.ensure_lease(ctx)
        changes = {"phase": phase}
        if evidence:
            changes["evidence"] = runner._merged_evidence(ctx.job, evidence)
        store.job_write(ctx, **changes)
    return write


def _cancel_requested(ctx: store.JobContext) -> bool:
    return approval_freeze._cancel_requested(ctx)


def publish(ctx: store.JobContext, origin: str) -> Outcome:
    """One publish attempt of a frozen job (status running). Records its own outcome."""
    job = ctx.job
    if job.get("phase") == "db_finalize":
        return _db_finalize(ctx, origin, _evidence(job).get("git") or {})
    if job.get("phase") == "publish_pending" and _cancel_requested(ctx):
        approval_freeze.freeze_release(ctx, "cancelled", "cancelled_by_user")
        return describe(ctx.job)
    held: list[str] = []
    failure: Optional[tuple] = None
    git: Optional[dict] = None
    try:
        failure = _admit(ctx, origin, held)
        if failure is None:
            if ctx.job.get("phase") == "publish_pending":
                store.ensure_lease(ctx)
                store.job_write(ctx, phase="admission")
            git = _resume(ctx)
            if git is None:
                git = _run_git(ctx)
    except GitServiceError as exc:
        # raised outside finalize (target planning, resume): same classification
        git = {"ok": False, "http_status": exc.status,
               "error": {"code": exc.code, "message": exc.message, **(
                   {"details": exc.details} if getattr(exc, "details", None) else {})}}
    finally:
        for key in reversed(held):
            if ctx.lock_ctx.find_held(key) is not None:
                locks.release(ctx.lock_ctx, key)
    if failure is not None:
        return _record_admission_failure(ctx, origin, failure)
    return _after_git(ctx, origin, git or {})


def _admit(ctx: store.JobContext, origin: str, held: list) -> Optional[tuple]:
    """W or B, then R (L 2.4 order). None = all held; else (domain, LockOutcome)."""
    from modules.flow_gate.services import git_service as _gs
    job = ctx.job
    p = _payload(job)
    project_id, group_id = job["project_id"], job["group_id"]
    wanted: list[tuple] = []
    if p.get("git_action") in merge_target.MERGE_ACTIONS:
        cfg = _gs.db_git.get_config(project_id) or {}
        target = merge_target.plan_finalize_target(
            group_id, project_id, cfg, merge_target.normalize_requested_target(p.get("target_branch")))
        wanted.append(("B", None) if target.is_project_base else ("W", target.target_branch))
    wanted.append(("R", None))
    mode = "interactive" if origin == REQUEST else "job"
    for domain, target_key in wanted:
        store.ensure_lease(ctx)
        o = locks.acquire(domain, project_id, target_key=target_key, holder_kind="publish",
                          mode=mode, ctx=ctx.lock_ctx)
        if not o.ok:
            return domain, o
        held.append(o.lock_key)
    return None


def _record_admission_failure(ctx: store.JobContext, origin: str, failure: tuple) -> Outcome:
    domain, o = failure
    b = o.blocker or {}
    if o.kind == locks.BUSY:
        store.finish_run(ctx, store.RunResult(
            store.BLOCKED, code=o.kind, blocked_domain=domain, blocked_lock_key=o.lock_key,
            blocked_holder=b.get("holder_ctx_id"), blocked_operation=b.get("holder_kind")))
    elif o.kind == locks.RECOVERY_REQUIRED:
        store.finish_run(ctx, store.RunResult(store.RECOVERY, code=f"publish_{domain}_recovery_required"))
    else:
        _transient(ctx, origin, (o.cause or o.kind or "lock_store_error")[:64])
    return describe(ctx.job)


def _approval_context(ctx: store.JobContext) -> ApprovalFinalizeContext:
    job = ctx.job
    p = _payload(job)
    return ApprovalFinalizeContext(
        doc_id=p["doc_id"], group_id=job["group_id"], actor_user_id=p.get("requested_by") or "",
        lock_holder=holder_of(ctx.job_id), approval_intent_id=p.get("approval_intent_id") or "",
        job_ctx=ctx, frozen_sha=job.get("frozen_sha"), on_phase=_phase_writer(ctx),
    )


def _run_git(ctx: store.JobContext) -> dict:
    from modules.flow_gate.services import git_service as _gs
    job = ctx.job
    p = _payload(job)
    store.ensure_lease(ctx)
    kwargs = {"approval_context": _approval_context(ctx)}
    if p.get("target_branch") is not None:
        return _gs.run_approve_git_action(job["group_id"], p["git_action"], p["target_branch"], **kwargs)
    return _gs.run_approve_git_action(job["group_id"], p["git_action"], **kwargs)


def _resume(ctx: store.JobContext) -> Optional[dict]:
    """Settle what an earlier claim of this job left, under W/B + R (see module doc).
    None = nothing left over, run Git."""
    from modules.flow_gate.services import git_service as _gs
    job = ctx.job
    p = _payload(job)
    group_id, action = job["group_id"], p.get("git_action")
    intent_id = p.get("approval_intent_id")
    _state, clean = approval_intent.find_clean_retry(group_id)
    if clean is not None and (clean.get("intent") or {}).get("approval_intent_id") == intent_id:
        return {"ok": True, "terminal": True, "result": {
            "action": action, "status": clean.get("terminal_status"),
            "merge_commit": clean.get("merge_commit"), "merge_id": None,
            "conflict_files": [], "resumed": True}}
    session = _gs.db_git.get_open_session_by_group(group_id)
    if session is None or _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
        return _closed_attempt_result(ctx, action)
    rec = merge_target.target_record(session) or {}
    if rec.get("lock_holder") != holder_of(ctx.job_id):
        return None     # not this job's attempt: finalize's own state guards answer
    merge_id = int(session["merge_id"])
    context = _gs.db_git.session_context(session)
    parked = approval_intent.intent_of_context(context)
    if (parked and parked.get("approval_intent_id") == intent_id) or \
            merge_target.attempt_phase(session) == merge_target.PHASE_CONFLICT:
        return {"ok": True, "terminal": False, "deferred": True, "result": {
            "action": action, "status": "conflict", "merge_id": merge_id,
            "approval_intent_id": intent_id, "resumed": True}}
    store.ensure_lease(ctx)
    verdict = merge_target.recover_interrupted_attempt(session, "approval_job_resume")
    if verdict == merge_target.ATTEMPT_INTERRUPTED:
        return None
    if verdict == merge_target.ATTEMPT_COMPLETED:
        closed = _gs.db_git.session_context(_gs.db_git.get_session(merge_id))
        result = closed.get("attempt_result") or {}
        return {"ok": True, "terminal": True, "result": {
            "action": action, "status": "merged", "merge_commit": result.get("merge_commit"),
            "pushed": bool(result.get("pushed")), "merge_id": None, "conflict_files": [],
            "target_branch": rec.get("branch"), "recovered": True}}
    return {"ok": False, "recovery": True, "error": {
        "code": "approval_attempt_unsettled",
        "message": "the interrupted publish attempt cannot be settled automatically",
        "details": {"merge_id": merge_id}}}


def _closed_attempt_result(ctx: store.JobContext, action: Optional[str]) -> Optional[dict]:
    """This job's newest attempt was already closed as completed by someone else's
    recovery (a job that was terminal-looking for a moment, an operator): Git is
    terminal, only the approval is left. Any other closed attempt means run again."""
    from modules.flow_gate.services import git_service as _gs
    holder = holder_of(ctx.job_id)
    for session in _gs.db_git.sessions_by_group(ctx.job["group_id"]):
        if _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
            continue
        rec = merge_target.target_record(session) or {}
        if rec.get("lock_holder") != holder:
            continue
        context = _gs.db_git.session_context(session)
        if context.get(merge_target.ATTEMPT_STATE_KEY) != merge_target.ATTEMPT_COMPLETED:
            return None
        result = context.get("attempt_result") or {}
        return {"ok": True, "terminal": True, "result": {
            "action": action, "status": "merged", "merge_commit": result.get("merge_commit"),
            "pushed": bool(result.get("pushed")), "merge_id": None, "conflict_files": [],
            "target_branch": rec.get("branch"), "recovered": True}}
    return None


def _transient(ctx: store.JobContext, origin: str, code: str) -> None:
    """retry_wait with backoff; past RETRY_MAX_ATTEMPTS the approval fails through
    freeze_release (store.finish_run would refuse a job with a freeze record)."""
    if int(ctx.job.get("attempt_count") or 0) + 1 > store.retry_max_attempts():
        approval_freeze.freeze_release(ctx, "failed", code[:64])
        _notify_failed(ctx, origin, code)
        return
    store.finish_run(ctx, store.RunResult(store.TRANSIENT, code=code[:64]))


def _notify_failed(ctx: store.JobContext, origin: str, code: str) -> None:
    """A job failing after its request returned tells the screen (the request itself
    answers with the error)."""
    if origin == REQUEST or ctx.job.get("status") not in ("failed", "cancelled"):
        return
    from modules.flow_gate.services import git_service as _gs
    job = ctx.job
    try:
        _gs._emit("git_finalize_done", job["project_id"], job["group_id"], {
            "project": job["project_id"], "group_id": job["group_id"],
            "action": _payload(job).get("git_action"), "status": "failed",
            "approval": {"approved": False, "document_status": "pending_review",
                         "root_status": "wf_in_progress", "stage": "failed", "deferred": False,
                         "job_id": job["job_id"], "error": {"code": code}},
        })
    except Exception:
        _log.warning("approval job failure event failed: %s", job.get("job_id"), exc_info=True)


def _after_git(ctx: store.JobContext, origin: str, git: dict) -> Outcome:
    job = ctx.job
    if git.get("recovery"):
        store.finish_run(ctx, store.RunResult(store.RECOVERY, code=git["error"]["code"]))
        return Outcome(RECOVERY, job=ctx.job, git=git, error=git.get("error"))
    if not git.get("ok"):
        error = git.get("error") or {"code": "git_error", "message": "Git finalize failed"}
        code = str(error.get("code") or "git_error")
        status = int(git.get("http_status") or 500)
        if status >= 500 or code in _TRANSIENT_CODES:
            _transient(ctx, origin, code)
        else:
            approval_freeze.freeze_release(ctx, "failed", code[:64])
            _notify_failed(ctx, origin, code)
        out = describe(ctx.job)
        out.git, out.error = git, error
        if out.state == FAILED:
            out.http_status = status
        return out
    if git.get("deferred") or not git.get("terminal"):
        if (git.get("result") or {}).get("status") != "conflict":
            store.finish_run(ctx, store.RunResult(store.RECOVERY, code="publish_not_terminal"))
            return Outcome(RECOVERY, job=ctx.job, git=git)
        _finish_terminal(ctx, _HANDOFF_RESULT)
        return Outcome(HANDED_OFF, job=ctx.job, git=git)
    store.ensure_lease(ctx)
    store.job_write(ctx, phase="db_finalize",
                    evidence=runner._merged_evidence(job, {"git": _git_summary(git)}))
    return _db_finalize(ctx, origin, git)


def _git_summary(git: dict) -> dict:
    """What a later db_finalize claim needs of the Git result (evidence stays small)."""
    result = git.get("result") or {}
    return {"ok": True, "terminal": True, "result": {k: result.get(k) for k in (
        "action", "status", "merge_commit", "pushed", "merge_id", "target_branch") if k in result}}


def _finish_terminal(ctx: store.JobContext, result) -> None:
    """succeeded + Group freeze claim cleared, one transaction (hand-off / approved elsewhere)."""
    with get_store().transaction():
        enc = store.fenced_write(ctx, dict(status="succeeded", phase="done", result=result,
                                           finished_at=now_iso(), **_LEASE_CLEARED),
                                 update_local=False)
        approval_freeze.clear_freeze_claim(ctx.job["group_id"], ctx.job_id)
    ctx.job.update(enc)


_COMPLETE = {"approved": True, "document_status": "approved", "root_status": "wf_done",
             "stage": "complete", "deferred": False}


def _db_finalize(ctx: store.JobContext, origin: str, git: dict) -> Outcome:
    """L 2.28 db_finalize: Git is terminal; only the approval transaction is left."""
    from modules.flow_gate.services import git_service as _gs
    from modules.flow_gate.workflow.pipeline_service import commit_final_approval
    job = ctx.job
    p = _payload(job)
    group_id, doc_id = job["group_id"], p["doc_id"]
    intent_id = p.get("approval_intent_id")
    action = p.get("git_action") or "merge"
    store.ensure_lease(ctx)
    doc = db_documents.get_by_id(doc_id)
    if doc is not None and doc.get("doc_review_status") == "approved":
        # approved through the approval-retry route meanwhile: nothing left to commit
        _finish_terminal(ctx, {"approved_elsewhere": True, "git": _git_summary(git)["result"]})
        return Outcome(EXECUTED, job=ctx.job, git=git, document=doc, approval=dict(_COMPLETE))
    _state, clean = approval_intent.find_clean_retry(group_id)
    clean_id = (clean.get("intent") or {}).get("approval_intent_id") if clean else None
    if clean is not None and clean_id != intent_id:
        store.finish_run(ctx, store.RunResult(store.RECOVERY, code="final_approval_retry_mismatch"))
        return Outcome(RECOVERY, job=ctx.job, git=git)
    written: dict = {}

    def consume_hook(_doc, _root) -> bool:
        if clean is not None and approval_intent.consume_clean_retry(group_id, clean_id) is False:
            return False
        written.update(store.fenced_write(
            ctx, dict(status="succeeded", phase="done", finished_at=now_iso(),
                      result={"git": _git_summary(git)["result"], "approved": True},
                      **_LEASE_CLEARED),
            update_local=False))
        approval_freeze.clear_freeze_claim(group_id, ctx.job_id)
        return True

    try:
        committed = commit_final_approval(
            doc_id=doc_id, actor_user_id=p.get("requested_by") or "",
            user_permissions=set(approval_intent._DEFERRED_PERMISSIONS),
            locale=p.get("locale") or "ko", consume_hook=consume_hook)
    except store.LeaseLost:
        raise
    except Exception as exc:
        _log.warning("approval db_finalize failed: %s", ctx.job_id, exc_info=True)
        # Git is terminal: publish that fact; the job keeps retrying the DB step only.
        _gs.complete_approve_git_action(group_id, action, git, approved=False)
        store.finish_run(ctx, store.RunResult(store.TRANSIENT, code="approval_commit_failed",
                                              db_finalize_only=True))
        out = describe(ctx.job)
        out.git = git
        out.error = {"code": "approval_commit_failed", "message": str(exc)}
        return out
    ctx.job.update(written)
    _gs.complete_approve_git_action(group_id, action, git, approved=True)
    _gs.realize_wf_done_transition(group_id)
    return Outcome(EXECUTED, job=ctx.job, git=git, document=committed["document"],
                   approval=dict(_COMPLETE))


# ── Runner registration ──────────────────────────────────────────────────────

def _decide_resume(job: dict) -> "runner.Decision":
    """L 4.4 rows of this kind (module doc): resume at the recorded phase; the next
    claim's ``_resume`` judges the evidence under W/B + R."""
    return runner.Decision(runner.NOT_APPLIED)


def install() -> None:
    """Executor, deciders and the freeze hooks. Idempotent."""
    approval_freeze.install()
    runner.register_executor(KIND, run)
    for phase in PUBLISH_PHASES:
        runner.register_decider(KIND, phase, _decide_resume)
    runner.register_observer(KIND, notify_progress)


def active_job_of_group(group_id: str) -> Optional[dict]:
    """The Group's newest non-terminal approval job, if any (approval-retry route)."""
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db import request_cache as _request_cache
    _request_cache.invalidate()
    jobs = db_jobs.active_jobs_of_group(group_id, KIND)
    return jobs[-1] if jobs else None


def response_payload(out: Outcome) -> tuple[int, dict]:
    """HTTP shape (0669 seq 50 decision): the existing approval envelope; a job that is
    not finished answers 200 with ``approval.deferred=true`` and ``stage="queued"``
    (or ``"recovery_required"``), which the screen already shows as "승인 보류"."""
    view = job_view(out.job)
    pending = {"approved": False, "document_status": "pending_review",
               "root_status": "wf_in_progress", "deferred": False}
    if out.state == EXECUTED:
        payload = {"ok": True, "git": out.git, "approval": out.approval or dict(_COMPLETE), "job": view}
        if out.document is not None:
            payload["document"] = out.document
        else:
            payload["document"] = db_documents.get_by_id(
                _payload(out.job or {}).get("doc_id") or "") if out.job else None
        return 200, payload
    if out.state == HANDED_OFF:
        result = (out.git or {}).get("result") or {}
        pending.update(stage="git_finalize", deferred=True,
                       approval_intent_id=_payload(out.job or {}).get("approval_intent_id"),
                       merge_id=result.get("merge_id"))
        return 200, {"ok": True, "git": out.git, "approval": pending, "job": view}
    if out.state in (QUEUED, RECOVERY):
        pending.update(stage="queued" if out.state == QUEUED else "recovery_required",
                       deferred=True, job_id=(out.job or {}).get("job_id"),
                       blocker=blocker_of(out.job))
        git = out.git or {"ok": True, "terminal": False, "queued": True}
        payload = {"ok": True, "response_status": "queued", "git": git, "approval": pending,
                   "job": view}
        if out.error is not None:
            payload["block_reason"] = out.error
        return 200, payload
    error = out.error or {"code": "approval_failed", "message": "final approval failed"}
    pending["stage"] = "git_finalize"
    return out.http_status or 409, {"ok": False, "error": error,
                                    "git": out.git or {"ok": False, "error": error},
                                    "approval": pending, "job": view}


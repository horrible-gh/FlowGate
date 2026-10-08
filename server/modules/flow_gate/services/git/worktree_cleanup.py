"""Group slot cleanup job (flowgate.default.0669 unit 7a; 0666 0008-D §3.6.1, §3.7.2,
0009-L §2.25, §2.28, §4.4).

After a final approval lands (merged / pushed), the Group's slot is torn down by a
``worktree_cleanup`` job instead of inline under the old project mutex. Phases (L 2.28):

    start -> files_emptied (G) -> unregistered (M) -> branch_deleted (M)
          -> pins_deleted (M) -> remote_deleted (R) -> done (DB)

Every value a step compares against is written into the payload when the job is
created, never observed at run time (the L 2.28 write-ahead record): worktree path,
branch and its SHA, the approval pins (name, recorded SHA = the approval job's
frozen_sha) and the origin work-branch SHA. Each step is idempotent: an already-applied
step is detected and skipped; a value that is neither the recorded one nor absent stops
the job in recovery_required and nothing is deleted.

The deletion rules are the old ``_cleanup_group_slot`` ones, unchanged: the local branch
goes only when its content is already in base / the merge target (merged) or on origin
(pushed); the origin work branch is retro-deleted only for merged slots (pre-0172
leftovers) and that delete stays best-effort.

Unit 7b adds the disposed Group (``register_for_dispose`` / ``clean_disposed``, from
``cleanup_disposed_group``): same phases, the old dispose rules (work branch
force-deleted at its recorded SHA, no TR-conflict gate), run once in the request.

Entry points: ``register_after_approval`` (``complete_approve_git_action``), ``run``
(Runner executor), ``install`` (executor + L 4.4 deciders), ``owns_slot`` (the old
cleanup paths step aside while a job is active) and ``pin_cleanup_ended`` (orphan pin
sweep, L 2.25).

Deviations from L, recorded as design changes (0669 chat):

* group_generation is the approval job's id (``wtc:{group}:approved:{fap job}``): the
  ledger has no generation counter, and one approval is one slot lifetime.
* Only approvals that went through a final_approval_publish job, and disposed Groups
  (unit 7b), register a job; any other terminal slot (no such job, job creation failed,
  the manual backlog sweep) keeps the old inline cleanup.
* A disposed Group's key is ``wtc:{group}:disposed:{n}``, n = this Group's terminal
  cleanup jobs, so a later dispose cleanup after a failed one gets a new job.
* The M steps run ``worktree`` as well as ``update-ref`` through
  ``approval_freeze.run_in_m`` (same M-held / no-transaction / timeout guard). The
  worktree directory is emptied under G first, so ``worktree remove`` under M is short.
* pins_deleted -> remote_deleted is decided NOT_APPLIED without a remote read: the
  delete is conditional (force-with-lease on the recorded SHA) and best-effort, so a
  re-run is harmless and a lock-free decider never touches the network.
* A slot that is no longer this approval's (a time-machine reopen re-provisioned it, or
  the branch changed) fails the job PERMANENT ``slot_reopened`` without touching it; a
  failed cleanup job still ends the pin wait (L 2.25 ``cleanup_terminal``).
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import operation_job as db
from modules.flow_gate.db import request_cache as _request_cache
from modules.flow_gate.db.connection import get_store, now_iso

from . import approval_freeze
from . import job_runner as runner
from . import job_store as store
from . import lock_manager as locks
from .credentials import GitServiceError

_log = logging.getLogger(__name__)

KIND = "worktree_cleanup"
REASON_APPROVED = "approved"
REASON_DISPOSED = "disposed"

_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}
_READ_ERROR = approval_freeze._READ_ERROR


class _Stop(Exception):
    """A step cannot go on; carries the RunResult the claim ends with."""

    def __init__(self, result: store.RunResult):
        super().__init__(result.code)
        self.result = result


def _transient(code: str) -> _Stop:
    return _Stop(store.RunResult(store.TRANSIENT, code=code))


def _recovery(code: str) -> _Stop:
    return _Stop(store.RunResult(store.RECOVERY, code=code))


def _permanent(code: str) -> _Stop:
    return _Stop(store.RunResult(store.PERMANENT, code=code))


# ── the slot as recorded at creation ─────────────────────────────────────────

@dataclass
class _Slot:
    project_id: str
    group_id: str
    branch: str
    branch_sha: Optional[str]
    branch_delete: bool
    wt_path: Path
    base_root: Path
    pins: list            # [[ref name, recorded sha, approval job id], ...]
    remote_sha: Optional[str]
    reason: str = REASON_APPROVED


def _payload(job: dict) -> dict:
    return approval_freeze._json(job.get("payload"))


def _slot_of(job: dict) -> _Slot:
    p = _payload(job)
    base_root = approval_freeze._refs_repo(job["project_id"])
    if base_root is None or not p.get("branch") or not p.get("wt_path"):
        raise _permanent("cleanup_slot_unresolved")
    return _Slot(job["project_id"], job["group_id"], p["branch"], p.get("branch_sha"),
                 bool(p.get("branch_delete")), Path(p["wt_path"]), base_root,
                 list(p.get("pins") or []), p.get("remote_sha"),
                 p.get("reason") or REASON_APPROVED)


def _git(repo: Path, args: list, **kw):
    from modules.flow_gate.services import git_service as _gs
    return _gs._run_git(args, cwd=repo, **kw)


def _rev(repo: Path, ref: str):
    """sha, None (absent) or _READ_ERROR. for-each-ref also lists refs below a
    pattern, so only the exact name counts."""
    proc = _git(repo, ["for-each-ref", "--format=%(refname) %(objectname)", ref])
    if proc.returncode != 0:
        return _READ_ERROR
    for line in (proc.stdout or "").splitlines():
        name, _, sha = line.strip().partition(" ")
        if name == ref and sha:
            return sha
    return None


def _leftovers(wt_path: Path) -> Optional[list]:
    """Entries of the worktree directory other than its .git link; None when the
    directory is gone."""
    try:
        return [e for e in wt_path.iterdir() if e.name != ".git"]
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        return [wt_path]


def _registered(s: _Slot) -> Optional[bool]:
    """True / False / None (git cannot tell)."""
    from modules.flow_gate.services import git_service as _gs
    live = _gs._registered_worktrees(s.base_root)
    if live is None:
        return None
    try:
        return s.wt_path.resolve() in live
    except OSError:
        return False


def _still_ours(s: _Slot) -> None:
    """The ledger must still describe the slot this job recorded."""
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(s.group_id)
    if s.reason == REASON_DISPOSED:
        terminal = _gs._is_group_disposed(s.group_id)
    else:
        terminal = state is not None and (state.get("status") or "none") in _gs.CLEANUP_STATUSES
    if not terminal or state is None or (state.get("branch") or "").strip() != s.branch:
        raise _permanent("slot_reopened")


# ── creation (the write-ahead record) ────────────────────────────────────────

def _branch_delete_rule(base_root: Path, state: dict, branch: str, status: str) -> bool:
    """The old _cleanup_group_slot rule, decided once, up front."""
    from modules.flow_gate.services import git_service as _gs
    ref = f"refs/heads/{branch}"
    if status == "merged":
        if _git(base_root, ["merge-base", "--is-ancestor", ref, "HEAD"]).returncode == 0:
            return True
        from .merge_target import completed_target_of_state
        done = completed_target_of_state(state)
        if done is not None and not done.is_project_base:
            return _git(base_root, ["merge-base", "--is-ancestor", ref,
                                    f"refs/heads/{done.target_branch}"]).returncode == 0
        return False
    return _gs._ref_exists(base_root, f"refs/remotes/origin/{branch}")      # pushed


def _slot_record(base_root: Path, state: dict, project_name: str, approvals: list,
                 reason: str) -> Optional[dict]:
    """The write-ahead record of one slot. None when the branch cannot be read."""
    from modules.flow_gate.services import git_service as _gs
    status = (state.get("status") or "none")
    branch = (state.get("branch") or "").strip()
    branch_sha = _rev(base_root, f"refs/heads/{branch}")
    if branch_sha is _READ_ERROR:
        return None
    pins = []
    for j in approvals:
        sha = approval_freeze.recorded_pin_sha(j)
        if sha:
            name = (approval_freeze.freeze_record(j).get("pin_ref_name")
                    or approval_freeze.pin_ref_name(j["job_id"]))
            pins.append([name, sha, j["job_id"]])
    remote_sha = None
    if status == "merged":
        r = _rev(base_root, f"refs/remotes/origin/{branch}")
        remote_sha = r if isinstance(r, str) else None
    if reason == REASON_DISPOSED:
        # 0192 T0005: the disposed work is intentionally thrown away (-D).
        branch_delete = bool(branch_sha)
    else:
        branch_delete = bool(branch_sha) and _branch_delete_rule(base_root, state, branch, status)
    return {
        "reason": reason, "status": status, "branch": branch, "branch_sha": branch_sha,
        "branch_delete": branch_delete, "wt_path": str(_gs.src_root(project_name, branch)),
        "pins": pins, "remote_sha": remote_sha,
    }


def register_after_approval(project_id: str, group_id: str) -> Optional[dict]:
    """Create (or find) the cleanup job of the Group's latest approval job. None = no
    job, the caller cleans inline as before. Never raises; never inside a DB
    transaction (job creation is its own transaction)."""
    from modules.flow_gate.services import git_service as _gs
    try:
        _request_cache.invalidate()
        approvals = db.jobs_of_group(group_id, approval_freeze.KIND, ("succeeded",))
        if not approvals:
            return None
        fap = approvals[-1]
        state = _gs.db_git.get_state(group_id)
        status = (state or {}).get("status") or "none"
        if state is None or not state.get("worktree_registered") or status not in _gs.CLEANUP_STATUSES:
            return None
        branch = (state.get("branch") or "").strip()
        project_name = _gs._project_name(project_id)
        base_root = approval_freeze._refs_repo(project_id)
        if not branch or not project_name or base_root is None:
            return None
        payload = _slot_record(base_root, state, project_name, approvals, REASON_APPROVED)
        if payload is None:
            return None
        payload["approval_job_id"] = fap["job_id"]
        req = {"group_id": group_id, "reason": REASON_APPROVED, "group_generation": fap["job_id"]}
        cr = store.create_or_get_job(KIND, project_id, req, group_id=group_id, payload=payload,
                                     requested_by=fap.get("requested_by"))
        if not cr.ok:
            _log.warning("worktree_cleanup job not registered for %s: %s", group_id, cr.code)
            return None
        if cr.job.get("status") in ("failed", "cancelled"):
            return None             # an earlier cleanup gave up: the inline path retries
        return cr.job
    except Exception:
        _log.warning("worktree_cleanup registration failed for %s", group_id, exc_info=True)
        return None


def register_for_dispose(project_id: str, group_id: str) -> Optional[dict]:
    """Create (or find) the cleanup job of a disposed Group's slot. None = no job, the
    caller cleans inline as before. Never raises; never inside a DB transaction."""
    from modules.flow_gate.services import git_service as _gs
    try:
        _request_cache.invalidate()
        active = db.active_jobs_of_group(group_id, KIND)
        if active:
            return active[0]
        state = _gs.db_git.get_state(group_id)
        if state is None or not state.get("worktree_registered") or not _gs._is_group_disposed(group_id):
            return None
        project_name = _gs._project_name(project_id)
        base_root = approval_freeze._refs_repo(project_id)
        if not (state.get("branch") or "").strip() or not project_name or base_root is None:
            return None
        approvals = db.jobs_of_group(group_id, approval_freeze.KIND, ("succeeded",))
        payload = _slot_record(base_root, state, project_name, approvals, REASON_DISPOSED)
        if payload is None:
            return None
        generation = str(len(db.jobs_of_group(group_id, KIND, db.TERMINAL_STATUSES)))
        req = {"group_id": group_id, "reason": REASON_DISPOSED, "group_generation": generation}
        cr = store.create_or_get_job(KIND, project_id, req, group_id=group_id, payload=payload,
                                     wake=False)
        if not cr.ok:
            _log.warning("dispose cleanup job not registered for %s: %s", group_id, cr.code)
            return None
        return cr.job
    except Exception:
        _log.warning("dispose cleanup registration failed for %s", group_id, exc_info=True)
        return None


def clean_disposed(project_id: str, group_id: str) -> Optional[dict]:
    """cleanup_disposed_group's job path: register, then one attempt in this request.
    None = no job (the caller goes the old way); else the job as it stands now."""
    job = register_for_dispose(project_id, group_id)
    if job is None:
        return None
    install()
    cr = runner.try_claim(job["job_id"])
    if cr is not None:
        runner.execute(cr)
    return store.get_job(job["job_id"]) or job


def owns_slot(group_id: str) -> bool:
    """An active cleanup job owns the slot, so the old inline paths must not race it.
    A read failure counts as owned (fail-closed: the old path only skips the slot)."""
    try:
        _request_cache.invalidate()
        return bool(db.active_jobs_of_group(group_id, KIND))
    except Exception:
        _log.warning("cleanup job probe failed for %s", group_id, exc_info=True)
        return True


def pin_cleanup_ended(approval_job: dict) -> bool:
    """L 2.25 cleanup_terminal: the cleanup job that recorded this approval's pin ended."""
    group_id = approval_job.get("group_id")
    if not group_id:
        return False
    _request_cache.invalidate()
    for c in db.jobs_of_group(group_id, KIND, db.TERMINAL_STATUSES):
        if any(len(p) > 2 and p[2] == approval_job["job_id"] for p in (_payload(c).get("pins") or [])):
            return True
    return False


# ── steps ────────────────────────────────────────────────────────────────────

def _acquire(ctx: store.JobContext, domain: str, s: _Slot) -> locks.LockOutcome:
    store.ensure_lease(ctx)
    o = locks.acquire(domain, s.project_id, group_id=s.group_id if domain == "G" else None,
                      holder_kind=KIND, mode="job", ctx=ctx.lock_ctx)
    if o.ok:
        return o
    b = o.blocker or {}
    if o.kind == locks.BUSY:
        raise _Stop(store.RunResult(
            store.BLOCKED, code=o.kind, blocked_domain=domain, blocked_lock_key=o.lock_key,
            blocked_holder=b.get("holder_ctx_id"), blocked_operation=b.get("holder_kind")))
    if o.kind == locks.RECOVERY_REQUIRED:
        raise _recovery(f"cleanup_{domain}_recovery_required")
    raise _transient((o.cause or o.kind or "lock_store_error")[:64])


def _release(ctx: store.JobContext, o: locks.LockOutcome) -> None:
    if ctx.lock_ctx.find_held(o.lock_key) is not None:
        locks.release(ctx.lock_ctx, o.lock_key)


def _phase(ctx: store.JobContext, phase: str, **evidence) -> None:
    changes: dict = {"phase": phase}
    if evidence:
        ev = approval_freeze._json(ctx.job.get("evidence"))
        ev.update(evidence)
        changes["evidence"] = ev
    store.job_write(ctx, **changes)


def _remove_entry(path: Path) -> None:
    def _retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            _log.debug("cleanup could not remove %s", target, exc_info=True)

    try:
        if path.is_dir() and not path.is_symlink():
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_retry)
            else:
                shutil.rmtree(path, onerror=_retry)
        else:
            try:
                path.unlink()
            except PermissionError:
                os.chmod(path, stat.S_IWRITE)
                path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        _log.warning("cleanup remove failed: %s", path, exc_info=True)


def _empty_files(ctx: store.JobContext, s: _Slot) -> None:
    """start -> files_emptied, under G. The slow part of the teardown (deleting the
    tree) happens here, so the M hold that follows stays short."""
    from modules.flow_gate.services import git_service as _gs
    from .worktree import _force_rmtree
    g = _acquire(ctx, "G", s)
    try:
        _still_ours(s)
        if _gs.get_branch_merge_group_claim(s.group_id) is not None:
            raise _transient("branch_merge_claim_active")
        if s.reason != REASON_DISPOSED and _gs.tr_conflict_session(s.group_id) is not None:
            raise _transient("revert_conflict")
        if s.wt_path.exists():
            kind = _gs._classify_worktree_dir(s.base_root, s.wt_path)
            if kind == "unknown":
                raise _transient("worktree_registration_unknown")
            store.ensure_lease(ctx)
            if kind == "orphan":
                if not _force_rmtree(s.wt_path):
                    raise _transient("files_not_emptied")
            else:
                for entry in _leftovers(s.wt_path) or []:
                    _remove_entry(entry)
                if _leftovers(s.wt_path):
                    raise _transient("files_not_emptied")
        _phase(ctx, "files_emptied")
    finally:
        _release(ctx, g)


def _run_m(ctx: store.JobContext, s: _Slot, args: list):
    store.ensure_lease(ctx)
    return approval_freeze.run_in_m(ctx.lock_ctx, s.project_id, s.base_root, args,
                                    allowed=("update-ref", "worktree"))


def _unregister(ctx: store.JobContext, s: _Slot) -> None:
    """files_emptied -> unregistered, under M."""
    from .worktree import _force_rmtree
    if _leftovers(s.wt_path):
        _phase(ctx, "start")                 # 4.4: files are back -> empty them first
        raise _transient("files_reappeared")
    m = _acquire(ctx, "M", s)
    try:
        _still_ours(s)
        reg = _registered(s)
        if reg is None:
            raise _transient("worktree_registration_unknown")
        if reg:
            _run_m(ctx, s, ["worktree", "remove", "--force", str(s.wt_path)])
        _run_m(ctx, s, ["worktree", "prune"])
        if s.wt_path.exists() and _registered(s) is False:
            _force_rmtree(s.wt_path)         # the bare .git link git no longer owns
        if _registered(s) is not False or s.wt_path.exists():
            raise _transient("worktree_unregister_not_applied")
        _phase(ctx, "unregistered")
    finally:
        _release(ctx, m)


def _delete_ref(ctx: store.JobContext, s: _Slot, name: str, expected: str, moved_code: str) -> None:
    """Conditional delete: only while the ref still holds the recorded SHA."""
    cur = _rev(s.base_root, name)
    if cur is _READ_ERROR:
        raise _transient("ref_check_failed")
    if cur is None:
        return
    if cur != expected:
        _log.error("%s: %s observed=%s expected=%s", moved_code, name, cur, expected)
        raise _recovery(moved_code)
    _run_m(ctx, s, ["update-ref", "-d", name, expected])
    after = _rev(s.base_root, name)
    if after is _READ_ERROR:
        raise _transient("ref_check_failed")
    if after is None:
        return
    if after == expected:
        raise _transient("ref_delete_not_applied")
    raise _recovery(moved_code)


def _delete_branch(ctx: store.JobContext, s: _Slot) -> None:
    """unregistered -> branch_deleted, under M."""
    if s.branch_delete and s.branch_sha:
        m = _acquire(ctx, "M", s)
        try:
            _delete_ref(ctx, s, f"refs/heads/{s.branch}", s.branch_sha, "cleanup_branch_moved")
        finally:
            _release(ctx, m)
    _phase(ctx, "branch_deleted")


def _delete_pins(ctx: store.JobContext, s: _Slot) -> None:
    """branch_deleted -> pins_deleted, under M, M_MAX_CMDS_PER_HOLD per hold. The
    expected value is the approval's recorded SHA, never the observed one (L 2.25)."""
    per_hold = approval_freeze.m_max_cmds_per_hold()
    for i in range(0, len(s.pins), per_hold):
        m = _acquire(ctx, "M", s)
        try:
            for name, sha, *_ in s.pins[i:i + per_hold]:
                _delete_ref(ctx, s, name, sha, "approval_pin_mismatch")
        finally:
            _release(ctx, m)
    _phase(ctx, "pins_deleted")


def _delete_remote(ctx: store.JobContext, s: _Slot) -> None:
    """pins_deleted -> remote_deleted, under R. Best-effort, as before."""
    from modules.flow_gate.services import git_service as _gs
    outcome = "skipped"
    if s.remote_sha:
        r = _acquire(ctx, "R", s)
        try:
            cfg = _gs.db_git.get_config(s.project_id) or {}
            store.ensure_lease(ctx)
            try:
                _gs.ensure_origin_matches_config(s.base_root, (cfg.get("repo_url") or "").strip())
                proc = _git(s.base_root,
                            ["push", f"--force-with-lease=refs/heads/{s.branch}:{s.remote_sha}",
                             "origin", f":refs/heads/{s.branch}"],
                            timeout=_gs.GIT_NET_TIMEOUT_SEC, username=cfg.get("username"),
                            secret=_gs._load_secret_for(cfg) or "")
                outcome = "deleted" if proc.returncode == 0 else "failed"
                if proc.returncode != 0:
                    _log.warning("origin work branch delete failed for %s: %s", s.group_id,
                                 _gs._last_line(proc.stderr))
            except GitServiceError:
                outcome = "failed"
        finally:
            _release(ctx, r)
    _phase(ctx, "remote_deleted", remote_delete=outcome)


def _finish(ctx: store.JobContext, s: _Slot) -> None:
    """remote_deleted -> done: the ledger unregister and the terminal write, one
    transaction."""
    from modules.flow_gate.services import git_service as _gs
    _still_ours(s)
    store.ensure_lease(ctx)
    with get_store().transaction():
        written = store.fenced_write(ctx, dict(status="succeeded", phase="done", finished_at=now_iso(),
                                               result={"cleaned": True}, **_LEASE_CLEARED),
                                     update_local=False)
        _gs.db_git.unregister_worktree(s.group_id)
    ctx.job.update(written)
    try:
        _gs._emit_pending_changed(s.project_id, s.group_id, "none")
    except Exception:
        _log.warning("cleanup pending-changed emit failed for %s", s.group_id, exc_info=True)


_STEPS = {
    "start": _empty_files,
    "files_emptied": _unregister,
    "unregistered": _delete_branch,
    "branch_deleted": _delete_pins,
    "pins_deleted": _delete_remote,
    "remote_deleted": _finish,
}


def run(ctx: store.JobContext, entry: str) -> Optional[store.RunResult]:
    """Runner executor. Resumes at the recorded phase; returns None once _finish wrote
    the terminal state, otherwise the result this claim ends with."""
    if entry != store.PUBLISH:
        raise store.JobProgramError("cleanup_entry_not_publish", job_id=ctx.job_id, entry=entry)
    try:
        s = _slot_of(ctx.job)
        for _ in range(2 * len(_STEPS)):             # a files_reappeared rewind at most
            phase = ctx.job.get("phase") or "start"
            if phase == "done":
                return None
            step = _STEPS.get(phase)
            if step is None:
                return store.RunResult(store.RECOVERY, code="cleanup_unknown_phase")
            step(ctx, s)
        return store.RunResult(store.TRANSIENT, code="cleanup_no_progress")
    except _Stop as stop:
        return stop.result


# ── L 4.4 deciders (lock-free F reads) ───────────────────────────────────────

def _decide_start(job: dict) -> "runner.Decision":
    if _leftovers(_slot_of(job).wt_path):
        return runner.Decision(runner.NOT_APPLIED)
    return runner.Decision(runner.APPLIED_NEXT, next_phase="files_emptied")


def _decide_files_emptied(job: dict) -> "runner.Decision":
    s = _slot_of(job)
    if _leftovers(s.wt_path):
        return runner.Decision(runner.NOT_APPLIED, resume_phase="start")
    reg = _registered(s)
    if reg is None:
        return runner.Decision(runner.TRANSIENT_CHECK_FAILURE, code="worktree_registration_unknown")
    if reg or s.wt_path.exists():
        return runner.Decision(runner.NOT_APPLIED)
    return runner.Decision(runner.APPLIED_NEXT, next_phase="unregistered")


def _ref_verdict(s: _Slot, refs: list, next_phase: str, code: str) -> "runner.Decision":
    """Every ref absent -> next; each absent or at its recorded SHA -> re-run the
    conditional delete; any other value -> undecidable (never deleted)."""
    left = False
    for name, expected in refs:
        cur = _rev(s.base_root, name)
        if cur is _READ_ERROR:
            return runner.Decision(runner.TRANSIENT_CHECK_FAILURE, code="ref_check_failed")
        if cur is None:
            continue
        if cur != expected:
            return runner.Decision(runner.UNDECIDABLE, code=code)
        left = True
    if left:
        return runner.Decision(runner.NOT_APPLIED)
    return runner.Decision(runner.APPLIED_NEXT, next_phase=next_phase)


def _decide_unregistered(job: dict) -> "runner.Decision":
    s = _slot_of(job)
    refs = [(f"refs/heads/{s.branch}", s.branch_sha)] if s.branch_delete and s.branch_sha else []
    return _ref_verdict(s, refs, "branch_deleted", "cleanup_branch_moved")


def _decide_branch_deleted(job: dict) -> "runner.Decision":
    s = _slot_of(job)
    return _ref_verdict(s, [(p[0], p[1]) for p in s.pins], "pins_deleted", "approval_pin_mismatch")


def _decide_not_applied(job: dict) -> "runner.Decision":
    return runner.Decision(runner.NOT_APPLIED)


def _guarded(fn):
    def decide(job: dict) -> "runner.Decision":
        try:
            return fn(job)
        except _Stop as stop:
            return runner.Decision(runner.UNDECIDABLE, code=stop.result.code)
    return decide


_DECIDERS = {
    "start": _decide_start,
    "files_emptied": _decide_files_emptied,
    "unregistered": _decide_unregistered,
    "branch_deleted": _decide_branch_deleted,
    "pins_deleted": _decide_not_applied,        # module doc: conditional, best-effort re-run
    "remote_deleted": _decide_not_applied,      # DB only
}


def install() -> None:
    """Executor and L 4.4 deciders. Idempotent."""
    runner.register_executor(KIND, run)
    for phase, fn in _DECIDERS.items():
        runner.register_decider(KIND, phase, _guarded(fn))

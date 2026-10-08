"""Group worktree provisioning job (flowgate.default.0669 unit 7b; 0666 0008-D §3.6.1,
0009-L §2.23, §2.28, §4.4).

``ensure_worktree`` no longer takes the old project mutex to create a Group's slot. It
registers a ``worktree_provision`` job and, as L 2.28 asks of a synchronous path, runs it
once right away in the request with the request's own claim. If a domain is busy the job
stays queued and the Runner finishes it; the caller sees the old ``git_busy`` failure
until then. Phases (L 2.28):

    start -> intent_recorded (G) -> fetched (R) -> registered (M) -> populated (G) -> done

* intent_recorded: path P and branch b, checked under G (ledger not ready, P free).
* fetched: ``fetch origin`` under R, then the plan is resolved from F reads and written
  with the phase: how b comes to be (existing local branch / origin branch / fork) and
  its expected SHA s.
* registered: ``worktree add --no-checkout`` under M, always from the recorded SHA s,
  judged against the 2.23 "worktree 등록" table in M before and after.
* populated: ``reset --hard`` under G inside P (HEAD = b = s checked first, a clean
  status checked after).
* done: ledger register + provision-failure clear + terminal write, one transaction.
  Floor record and the ready SSE follow, best-effort, as before.

Scope (deviations recorded as design changes, 0669 chat):

* A project whose base checkout is not established yet keeps the old mutex path. The
  base clone/adopt is project_provision's (a later unit).
* 0669 unit 7d: a Time Machine terminal reopen's re-provision (``start_point`` = the
  terminal commit C1) runs as this job too. C1 is recorded in the payload and checked
  where the old path checked it: present in the base repo, contained by the existing
  local/origin branch, or by the base tip the fork starts from (T0007 §4, §11). A
  mismatch is PERMANENT, never a silent fork from base HEAD. Until a provision
  succeeds, a later plain request inherits the C1 of a failed reopen job, so the slot
  cannot come back without it. The floor is the reopen floor.
* group_generation is the number of this Group's terminal provision jobs: concurrent
  requests see the same count (same key, one job), and a slot provisioned again after a
  cleanup or failure gets a new key.
* The origin-branch case does not run ``--track -b b P origin/b``: origin/b can move with
  any fetch, so b is created at the recorded s and the two upstream keys are written as
  ``config`` commands under M (what --track writes).
* s is recorded with ``fetched``, not ``intent_recorded``: it is a fetch result.
* The ready SSE and the fork floor are written after the terminal transaction. A crash in
  between leaves a registered slot without a floor, which scope reads already report as
  ``group_work_base_unverified``.
"""
from __future__ import annotations

import logging
import threading
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
from .credentials import GitServiceError, _scrub

_log = logging.getLogger(__name__)

KIND = "worktree_provision"

OK = "ok"
FAILED = "failed"

_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}
_READ_ERROR = approval_freeze._READ_ERROR

# mode of the attempt running on this thread: "interactive" in a request, "job" otherwise
_origin = threading.local()


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


# ── the slot as recorded ─────────────────────────────────────────────────────

@dataclass
class _Slot:
    project_id: str
    group_id: str
    branch: str
    wt_path: Path
    base_root: Path
    work_base_ref: str
    trigger: str
    plan: dict            # fetched record: mode, sha, fork_ref ({} before fetched)
    start_point: str = ""  # terminal reopen's C1 (unit 7d); "" for a plain provision


def _payload(job: dict) -> dict:
    return approval_freeze._json(job.get("payload"))


def _evidence(job: dict) -> dict:
    return approval_freeze._json(job.get("evidence"))


def _slot_of(job: dict) -> _Slot:
    p = _payload(job)
    if not p.get("branch") or not p.get("wt_path") or not p.get("base_root"):
        raise _permanent("provision_slot_unresolved")
    return _Slot(job["project_id"], job["group_id"], p["branch"], Path(p["wt_path"]),
                 Path(p["base_root"]), p.get("work_base_ref") or "", p.get("trigger") or "",
                 dict(_evidence(job).get("plan") or {}), p.get("start_point") or "")


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


def _registration(s: _Slot):
    """The 2.23 observation of P: (registered, branch ref) or _READ_ERROR. A prunable
    entry is git's own "stale", so it counts as not registered."""
    proc = _git(s.base_root, ["worktree", "list", "--porcelain"])
    if proc.returncode != 0:
        return _READ_ERROR
    try:
        want = s.wt_path.resolve()
    except OSError:
        return _READ_ERROR
    entries, cur = [], None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):].strip(), "branch": None, "prunable": False}
            entries.append(cur)
        elif cur is not None and line.startswith("branch "):
            cur["branch"] = line[len("branch "):].strip()
        elif cur is not None and line.startswith("prunable"):
            cur["prunable"] = True
    for e in entries:
        try:
            same = Path(e["path"]).resolve() == want
        except OSError:
            same = False
        if same and not e["prunable"]:
            return True, e["branch"]
    return False, None


def _dir_state(path: Path) -> str:
    """'absent' | 'empty' | 'files'."""
    try:
        return "files" if any(path.iterdir()) else "empty"
    except FileNotFoundError:
        return "absent"
    except NotADirectoryError:
        return "files"


def _ledger_ready(s: _Slot) -> bool:
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(s.group_id)
    return bool(state is not None and state.get("worktree_registered")
                and state.get("branch") == s.branch and s.wt_path.is_dir()
                and _gs._worktree_link_ok(s.wt_path))


# ── creation and the request's one attempt ───────────────────────────────────

def _generation(group_id: str) -> str:
    _request_cache.invalidate()
    return str(len(db.jobs_of_group(group_id, KIND, db.TERMINAL_STATUSES)))


def _inherited_start_point(group_id: str) -> str:
    """C1 of the newest terminal provision job when that job was a reopen that did not
    succeed: a plain request must not bring the slot back without C1 (T0007 §11)."""
    done = db.jobs_of_group(group_id, KIND, db.TERMINAL_STATUSES)
    if not done or done[-1].get("status") == "succeeded":
        return ""
    return _payload(done[-1]).get("start_point") or ""


def create(cfg: dict, project_id: str, project_name: str, group_id: str, branch: str,
           trigger: str, start_point: Optional[str] = None) -> Optional[dict]:
    """Register (or join) the Group's provision job; None when the store refused. An
    active job is joined whatever it was created for: one slot, one provision."""
    from modules.flow_gate.services import git_service as _gs
    install()
    project_base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    work_base_ref = (_gs.resolve_group_work_base_ref(project_id, group_id, config=cfg)
                     or project_base_branch)
    _request_cache.invalidate()
    active = db.active_jobs_of_group(group_id, KIND)
    if active:
        if start_point and (_payload(active[0]).get("start_point") or "") != start_point:
            _log.warning("provision for %s joins active job %s (start_point %s not its own)",
                         group_id, active[0].get("job_id"), start_point)
        return active[0]
    start_point = start_point or _inherited_start_point(group_id)
    payload = {"branch": branch, "wt_path": str(_gs.src_root(project_name, branch)),
               "base_root": str(_gs.src_root(project_name, project_base_branch)),
               "work_base_ref": work_base_ref, "trigger": trigger}
    req = {"group_id": group_id, "group_generation": _generation(group_id)}
    if start_point:
        payload["start_point"] = start_point
        req["start_point"] = start_point
    cr = store.create_or_get_job(KIND, project_id, req, group_id=group_id, payload=payload,
                                 wake=False)
    if not cr.ok:
        _log.warning("worktree_provision job not registered for %s: %s", group_id, cr.code)
        return None
    return cr.job


def provision(cfg: dict, project_id: str, project_name: str, group_id: str, branch: str,
              trigger: str, start_point: Optional[str] = None) -> str:
    """ensure_worktree's job path. 'ok' | 'failed'; never raises; never inside a DB
    transaction. A failure is recorded the old way (_fail_worktree) so status reads and
    the TR2 retryable mapping see it."""
    from modules.flow_gate.services import git_service as _gs
    job = create(cfg, project_id, project_name, group_id, branch, trigger, start_point)
    if job is None:
        _gs._fail_worktree(project_id, group_id, branch, "provision_job_store_error")
        return FAILED
    return attempt(job)


def attempt(job: dict) -> str:
    """The request's one attempt at a registered job (L 2.28), then _report."""
    if job.get("status") == "succeeded":
        return OK if _ledger_ready(_slot_of(job)) else FAILED
    claim = runner.try_claim(job["job_id"])
    if claim is not None:
        _origin.mode = "interactive"
        try:
            runner.execute(claim)
        finally:
            _origin.mode = None
        job = store.get_job(job["job_id"]) or claim.ctx.job
    return _report(job)


def _report(job: dict) -> str:
    """Translate the job's state for ensure_worktree's 'ok' | 'failed' contract."""
    from modules.flow_gate.services import git_service as _gs
    from .base_slot import _record_attempt
    status = job.get("status")
    if status == "succeeded":
        return OK
    p = _payload(job)
    project_id, group_id, branch = job["project_id"], job["group_id"], p.get("branch")
    code = job.get("last_error_code") or status or "provision_failed"
    if status in ("blocked", "pending", "running") or code in ("git_busy",):
        # E11 as before: queued behind another domain, or another attempt is running.
        _record_attempt(project_id, "failed", "git_busy", p.get("trigger") or "", "none")
        _gs._fail_worktree(project_id, group_id, branch, "git_busy")
    elif code == "branch_merge_claim_active":
        _record_attempt(project_id, "blocked", code, p.get("trigger") or "", "none")
    else:
        _gs._fail_worktree(project_id, group_id, branch, code)
    return FAILED


# ── steps ────────────────────────────────────────────────────────────────────

def _acquire(ctx: store.JobContext, domain: str, s: _Slot) -> locks.LockOutcome:
    store.ensure_lease(ctx)
    mode = getattr(_origin, "mode", None) or "job"
    o = locks.acquire(domain, s.project_id, group_id=s.group_id if domain == "G" else None,
                      holder_kind=KIND, mode=mode, ctx=ctx.lock_ctx)
    if o.ok:
        return o
    b = o.blocker or {}
    if o.kind == locks.BUSY:
        raise _Stop(store.RunResult(
            store.BLOCKED, code=o.kind, blocked_domain=domain, blocked_lock_key=o.lock_key,
            blocked_holder=b.get("holder_ctx_id"), blocked_operation=b.get("holder_kind")))
    if o.kind == locks.RECOVERY_REQUIRED:
        raise _recovery(f"provision_{domain}_recovery_required")
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


def _intent(ctx: store.JobContext, s: _Slot) -> None:
    """start -> intent_recorded, under G: the slot is not ready yet and P is free."""
    from modules.flow_gate.services import git_service as _gs
    from .refs import _commits_present
    from .worktree import _record_reopen_floor
    g = _acquire(ctx, "G", s)
    try:
        try:
            claimed = _gs.get_branch_merge_group_claim(s.group_id) is not None
        except Exception:
            raise _transient("branch_merge_claim_query_failed")
        if claimed:
            raise _permanent("branch_merge_claim_active")
        if _ledger_ready(s):
            if s.start_point:
                # A slot that is still there must already hold C1 (the old ready path).
                if not _commits_present(s.wt_path, [s.start_point]):
                    raise _permanent("terminal_commit_absent")
                _record_reopen_floor(s.project_id, s.group_id, s.base_root, s.branch,
                                     s.work_base_ref, s.start_point)
            raise _Stop(store.RunResult(store.SUCCESS, result="already_provisioned"))
        if s.wt_path.exists():
            reg = _registration(s)
            if reg is _READ_ERROR:
                raise _transient("worktree_registration_unknown")
            if not reg[0]:
                # Unregistered directory squatting on the slot (E7): never deleted here.
                if _dir_state(s.wt_path) == "files":
                    raise _permanent("worktree_path_occupied")
        _phase(ctx, "intent_recorded", intent={"path": str(s.wt_path), "branch": s.branch})
    finally:
        _release(ctx, g)


def _contains(repo: Path, ancestor: str, tip: str) -> bool:
    """``merge-base --is-ancestor``: True / False; an unreadable answer is transient."""
    proc = _git(repo, ["merge-base", "--is-ancestor", ancestor, tip])
    if proc.returncode in (0, 1):
        return proc.returncode == 0
    raise _transient("ref_check_failed")


def check_start_point(base_root: Path, branch: str, work_base_ref: str,
                      start_point: str) -> Optional[str]:
    """The terminal reopen rules on C1 (T0007 §4 condition 1, §11) against the refs as
    they are now: None when the slot can come back holding C1, else the failure code.
    Read-only; raises _Stop (transient) when a ref cannot be read. The job applies the
    rules after its fetch; the reopen request runs them before its transaction commits
    (unit 7d)."""
    from .worktree import _worktree_start_point
    present = _git(base_root, ["cat-file", "-e", f"{start_point}^{{commit}}"])
    if present.returncode != 0:
        return "terminal_commit_absent"
    local = _rev(base_root, f"refs/heads/{branch}")
    remote = _rev(base_root, f"refs/remotes/origin/{branch}")
    if local is _READ_ERROR or remote is _READ_ERROR:
        raise _transient("ref_check_failed")
    if local:
        return None if _contains(base_root, start_point, local) else "terminal_branch_mismatch"
    if remote:
        return None if _contains(base_root, start_point, remote) else "terminal_branch_mismatch"
    # Never fork from bare C1: a base commit made after C1 merged would be dropped.
    tip = _worktree_start_point(base_root, work_base_ref)
    return None if _contains(base_root, start_point, tip) else "terminal_base_diverged"


def _plan(s: _Slot) -> dict:
    """F reads after the fetch: how b comes to be and the SHA it must point at."""
    from modules.flow_gate.services import git_service as _gs
    from .worktree import _worktree_start_point
    if s.start_point:
        refused = check_start_point(s.base_root, s.branch, s.work_base_ref, s.start_point)
        if refused:
            raise _permanent(refused)
    local = _rev(s.base_root, f"refs/heads/{s.branch}")
    if local is _READ_ERROR:
        raise _transient("ref_check_failed")
    if local:
        return {"mode": "existing", "sha": local, "new": False}
    remote = _rev(s.base_root, f"refs/remotes/origin/{s.branch}")
    if remote is _READ_ERROR:
        raise _transient("ref_check_failed")
    if remote:
        return {"mode": "track", "sha": remote, "new": True}
    fork_ref = _worktree_start_point(s.base_root, s.work_base_ref)
    fork_sha = _gs._rev_parse(s.base_root, f"{fork_ref}^{{commit}}")
    if not fork_sha:
        raise _permanent("work_base_unresolvable")
    return {"mode": "fork", "sha": fork_sha, "new": True, "fork_ref": fork_ref}


def _fetch(ctx: store.JobContext, s: _Slot) -> None:
    """intent_recorded -> fetched, under R; the plan is written with the phase."""
    from modules.flow_gate.services import git_service as _gs
    r = _acquire(ctx, "R", s)
    try:
        cfg = _gs.db_git.get_config(s.project_id) or {}
        try:
            # 0361 NR0003 §5.2: a slot created right after a repo_url change must not
            # fetch the old remote.
            _gs.ensure_origin_matches_config(s.base_root, (cfg.get("repo_url") or "").strip())
        except GitServiceError as exc:
            raise _permanent(exc.code[:64])
        store.ensure_lease(ctx)
        proc = _git(s.base_root, ["fetch", "origin"], timeout=_gs.GIT_NET_TIMEOUT_SEC,
                    username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "")
        if proc.returncode != 0:
            _log.warning("provision fetch failed for %s: %s", s.group_id,
                         _scrub(_gs._last_line(proc.stderr)))
            raise _transient("fetch_failed")
        plan = _plan(s)
        _phase(ctx, "fetched", plan=plan)
        s.plan = plan
    finally:
        _release(ctx, r)


def _judge_registration(s: _Slot):
    """2.23 "worktree 등록" against the fetched record. Returns 'applied', or the add
    argv to run, or raises (_Stop) for a mismatch / an unreadable state."""
    plan = s.plan
    if not plan.get("sha"):
        raise _recovery("provision_plan_missing")
    reg = _registration(s)
    if reg is _READ_ERROR:
        raise _transient("worktree_registration_unknown")
    branch_ref = f"refs/heads/{s.branch}"
    b = _rev(s.base_root, branch_ref)
    if b is _READ_ERROR:
        raise _transient("ref_check_failed")
    registered, at = reg
    if registered:
        if at == branch_ref:
            return "applied"
        raise _recovery("worktree_branch_mismatch")
    state = _dir_state(s.wt_path)
    if state == "files":
        raise _recovery("worktree_path_occupied")
    if b is None:
        if not plan.get("new"):
            raise _recovery("provision_branch_vanished")
        return ["worktree", "add", "--no-checkout", "-b", s.branch, str(s.wt_path), plan["sha"]]
    if b != plan["sha"]:
        raise _recovery("provision_branch_moved")
    return ["worktree", "add", "--no-checkout", str(s.wt_path), s.branch]


def _run_m(ctx: store.JobContext, s: _Slot, args: list):
    store.ensure_lease(ctx)
    return approval_freeze.run_in_m(ctx.lock_ctx, s.project_id, s.base_root, args,
                                    allowed=("worktree", "config"))


def _register(ctx: store.JobContext, s: _Slot) -> None:
    """fetched -> registered, under M (judged in M before and after, 2.23)."""
    m = _acquire(ctx, "M", s)
    try:
        verdict = _judge_registration(s)
        if verdict != "applied":
            if _dir_state(s.wt_path) == "empty":
                try:
                    s.wt_path.rmdir()        # an empty leftover; `worktree add` wants it gone
                except OSError:
                    raise _transient("worktree_path_not_free")
            proc = _run_m(ctx, s, verdict)
            if proc.returncode != 0:
                _log.warning("worktree add failed for %s: %s", s.group_id,
                             _scrub((proc.stderr or "").strip()[-300:]))
            if _judge_registration(s) != "applied":
                raise _transient("worktree_register_not_applied")
        if s.plan.get("mode") == "track":
            for key, value in ((f"branch.{s.branch}.remote", "origin"),
                               (f"branch.{s.branch}.merge", f"refs/heads/{s.branch}")):
                _run_m(ctx, s, ["config", key, value])
        _phase(ctx, "registered")
    finally:
        _release(ctx, m)


def _populated_state(s: _Slot) -> str:
    """'applied' | 'not_applied' | 'mismatch' | 'unknown' — the 4.4 registered row."""
    head = _git(s.wt_path, ["symbolic-ref", "-q", "HEAD"])
    if head.returncode != 0 or (head.stdout or "").strip() != f"refs/heads/{s.branch}":
        return "mismatch" if head.returncode in (0, 1) else "unknown"
    b = _rev(s.base_root, f"refs/heads/{s.branch}")
    if b is _READ_ERROR:
        return "unknown"
    if b != s.plan.get("sha"):
        return "mismatch"
    index = _git(s.wt_path, ["rev-parse", "--git-path", "index"])
    if index.returncode != 0:
        return "unknown"
    index_path = Path((index.stdout or "").strip())
    if not index_path.is_absolute():
        index_path = s.wt_path / index_path
    if not index_path.exists():
        return "not_applied"
    status = _git(s.wt_path, ["status", "--porcelain"])
    if status.returncode != 0:
        return "unknown"
    return "not_applied" if (status.stdout or "").strip() else "applied"


def _populate(ctx: store.JobContext, s: _Slot) -> None:
    """registered -> populated, under G. The workspace is not visible to anyone before
    done, so a forced checkout of the recorded SHA cannot lose a user change."""
    g = _acquire(ctx, "G", s)
    try:
        state = _populated_state(s)
        if state == "mismatch":
            raise _recovery("provision_head_mismatch")
        if state == "unknown":
            raise _transient("provision_state_unknown")
        if state == "not_applied":
            store.ensure_lease(ctx)
            proc = _git(s.wt_path, ["reset", "--hard"])
            if proc.returncode != 0:
                _log.warning("worktree checkout failed for %s: %s", s.group_id,
                             _scrub((proc.stderr or "").strip()[-300:]))
            if _populated_state(s) != "applied":
                raise _transient("provision_checkout_not_applied")
        _phase(ctx, "populated")
    finally:
        _release(ctx, g)


def _finish(ctx: store.JobContext, s: _Slot) -> None:
    """populated -> done: ledger register, failure marker clear and the terminal write
    in one transaction; the fork floor and the ready SSE after it."""
    from modules.flow_gate.services import git_service as _gs
    store.ensure_lease(ctx)
    with get_store().transaction():
        written = store.fenced_write(ctx, dict(status="succeeded", phase="done", finished_at=now_iso(),
                                               result={"provisioned": True}, **_LEASE_CLEARED),
                                     update_local=False)
        _gs.db_git.register_worktree(s.group_id, s.project_id, s.branch)
        _gs.db_git.clear_provision_failure(s.group_id)
    ctx.job.update(written)
    _after_done(s)


def _after_done(s: _Slot) -> None:
    from modules.flow_gate.services import git_service as _gs
    from .worktree import _record_floor_safe, _record_reopen_floor
    if s.start_point:
        _record_reopen_floor(s.project_id, s.group_id, s.base_root, s.branch,
                             s.work_base_ref, s.start_point)
    elif s.plan.get("mode") == "fork":
        _record_floor_safe(s.project_id, s.group_id, s.base_root, start_ref=s.plan["sha"],
                           work_base_ref=s.work_base_ref, kind="fork",
                           source_ref=s.plan.get("fork_ref"))
    try:
        _gs._emit_worktree_ready(s.project_id, s.group_id, s.branch, s.work_base_ref, s.wt_path,
                                 created=True, base_root=s.base_root)
    except Exception:
        _log.warning("worktree-ready emit failed for %s", s.group_id, exc_info=True)


_STEPS = {
    "start": _intent,
    "intent_recorded": _fetch,
    "fetched": _register,
    "registered": _populate,
    "populated": _finish,
}


def run(ctx: store.JobContext, entry: str) -> Optional[store.RunResult]:
    """Runner executor. Resumes at the recorded phase; None once _finish wrote the
    terminal state, otherwise the result this claim ends with."""
    if entry != store.PUBLISH:
        raise store.JobProgramError("provision_entry_not_publish", job_id=ctx.job_id, entry=entry)
    try:
        s = _slot_of(ctx.job)
        for _ in range(len(_STEPS) + 1):
            phase = ctx.job.get("phase") or "start"
            if phase == "done":
                return None
            step = _STEPS.get(phase)
            if step is None:
                return store.RunResult(store.RECOVERY, code="provision_unknown_phase")
            step(ctx, s)
        return store.RunResult(store.TRANSIENT, code="provision_no_progress")
    except _Stop as stop:
        return stop.result


# ── L 4.4 deciders (lock-free F reads) ───────────────────────────────────────

def _decide_not_applied(job: dict) -> "runner.Decision":
    return runner.Decision(runner.NOT_APPLIED)


def _decide_fetched(job: dict) -> "runner.Decision":
    """decide_m on 2.23 "worktree 등록"; a partial add (branch only) re-runs."""
    s = _slot_of(job)
    try:
        verdict = _judge_registration(s)
    except _Stop as stop:
        if stop.result.cls == store.TRANSIENT:
            return runner.Decision(runner.TRANSIENT_CHECK_FAILURE, code=stop.result.code)
        return runner.Decision(runner.UNDECIDABLE, code=stop.result.code)
    if verdict == "applied":
        return runner.Decision(runner.APPLIED_NEXT, next_phase="registered")
    return runner.Decision(runner.NOT_APPLIED)


def _decide_registered(job: dict) -> "runner.Decision":
    state = _populated_state(_slot_of(job))
    if state == "applied":
        return runner.Decision(runner.APPLIED_NEXT, next_phase="populated")
    if state == "not_applied":
        return runner.Decision(runner.NOT_APPLIED)
    if state == "unknown":
        return runner.Decision(runner.TRANSIENT_CHECK_FAILURE, code="provision_state_unknown")
    return runner.Decision(runner.UNDECIDABLE, code="provision_head_mismatch")


def _guarded(fn):
    def decide(job: dict) -> "runner.Decision":
        try:
            return fn(job)
        except _Stop as stop:
            return runner.Decision(runner.UNDECIDABLE, code=stop.result.code)
    return decide


_DECIDERS = {
    "start": _decide_not_applied,               # DB pre-record only
    "intent_recorded": _decide_not_applied,     # fetch is idempotent
    "fetched": _decide_fetched,
    "registered": _decide_registered,
    "populated": _decide_not_applied,           # DB only
}


def install() -> None:
    """Executor and L 4.4 deciders. Idempotent."""
    runner.register_executor(KIND, run)
    for phase, fn in _DECIDERS.items():
        runner.register_decider(KIND, phase, _guarded(fn))

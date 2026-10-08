"""Final approval freeze (flowgate.default.0669 unit 6a; 0666 0009-L §2.11, §2.22, §2.25,
§3.3, §4.6; 0008-D §3.7).

A ``final_approval_publish`` job is born ``freezing``. Before any publish code may run,
this module pins exactly what is being approved:

* F1 write-ahead record (pre-freeze head, worktree identity, pin name) and the Group
  freeze claim ``freezing``, one transaction, before any Git side effect;
* F2 absorb the worker's leftover edits as one commit carrying
  ``FlowGate-Freeze: {job_id}`` (G held);
* F3 record the source SHA and tree;
* F4 create ``refs/flowgate/approval-pin/{job_id}`` "only if absent" under M (G+M);
* F5 confirm the pin by observation, then — one transaction — mark the freeze complete,
  store sha/tree/pin and move the claim to ``publish_wait``. F5 is the only way out of
  ``freezing`` into a publish state (D 3.6.3).

Every retry first runs ``reconcile_freeze`` under G, so a crash between any two steps
resumes without a second commit or a second pin (D 3.7.2). Failure and cancel go
through ``freeze_release``: the pin is deleted only when it still points at the SHA
this job recorded, and the job becomes terminal and the claim is cleared in the same
transaction as that confirmation (L 2.22.3). A pin that is not explained by the record
is never deleted or overwritten — the job parks in recovery_required with the claim
kept, so the Group stays closed to source changes.

The job payload this module reads (written by ``approval_publish.start``, unit 6b):
``{"doc_id", "git_action", "commit_title"}``.

Entry points: ``run_freeze`` (request context or a FREEZE claim), ``freeze_release``
(RELEASE claim, recovery retry, publish failure), ``reconcile_freeze_expired`` (Sweeper),
``run_entry`` (the FREEZE/RELEASE half of ``approval_publish.run``),
``step_orphan_pins`` (sweep tick) and ``install`` (Runner hooks).

Deviations from L, recorded as design changes (0669 chat):

* ``run_in_m`` here is the minimum for the pin commands only: M must be held, no DB
  transaction may be open, only ``update-ref`` runs, with M_CMD_TIMEOUT_SEC. The gated
  spawn / exec_* recording of L 2.20.3 is not built yet, so RESULT_UNKNOWN_PROTECTED
  never occurs; a timeout is judged by the following pin observation (4.6 (3)), which
  L already makes authoritative.
* ``handle_lockfile`` never clears: a lockfile younger than LOCKFILE_STALE_AGE_SEC is
  WAIT, an older one UNCONFIRMED (no spawn registry to prove FlowGate's own children
  gone). UNCONFIRMED parks the job; no owner lock is protected because the durable
  claim already closes the Group.
* Orphan pins are deleted one ``update-ref -d <name> <recorded sha>`` at a time (at most
  M_MAX_CMDS_PER_HOLD per M hold), not as one ``--stdin`` batch; a refused delete is
  logged and skipped, which is the batch-failure fallback of L 2.25 anyway. Only
  projects that ever had a final_approval_publish job are scanned.
* A succeeded job's pin is sweep-eligible only after the worktree_cleanup job that
  recorded it ended (unit 7a, ``worktree_cleanup.pin_cleanup_ended``); D 3.7.2 forbids
  deleting it by age. Normally that job already deleted it (pins_deleted).
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import operation_job as db
from modules.flow_gate.db import request_cache as _request_cache
from modules.flow_gate.db.connection import get_store, in_transaction, now_iso

from . import instance_registry as registry
from . import job_notifier as notifier
from . import job_store as store
from . import lock_manager as locks
from .command import GIT_LOCAL_TIMEOUT_SEC
from .credentials import GitServiceError
from .lock_manager import _env_num

_log = logging.getLogger(__name__)

KIND = "final_approval_publish"
PIN_PREFIX = "refs/flowgate/approval-pin/"
FREEZE_TRAILER = "FlowGate-Freeze"
DOC_TRAILER = "FlowGate-Doc"
APPROVAL_ACTIONS = ("merge", "merge_only", "push", "commit_push")


# ── parameters (L 1.4, 1.5; FLOWGATE_ env override, clamped) ─────────────────

def m_cmd_timeout_sec() -> int:
    return int(_env_num("FLOWGATE_M_CMD_TIMEOUT_SEC", 10, 2, 30))


def m_max_cmds_per_hold() -> int:
    return int(_env_num("FLOWGATE_M_MAX_CMDS_PER_HOLD", 4, 1, 8))


def lockfile_stale_age_sec() -> int:
    floor = 2 * max(GIT_LOCAL_TIMEOUT_SEC, m_cmd_timeout_sec())
    return int(_env_num("FLOWGATE_LOCKFILE_STALE_AGE_SEC", floor, floor, 86400))


def orphan_pin_sweep_every_ticks(tick_sec: float) -> int:
    return max(1, math.ceil(600 / tick_sec))


def pin_awaiting_cleanup_alert_sec() -> int:
    return int(_env_num("FLOWGATE_PIN_AWAITING_CLEANUP_ALERT_SEC", 86400, 3600, 604800))


ORPHAN_PIN_PROJECTS_PER_TICK = 4


def pin_ref_name(job_id: str) -> str:
    """L 2.22.1. job_id is [a-z0-9_], safe in a ref name."""
    return PIN_PREFIX + job_id


# ── results ──────────────────────────────────────────────────────────────────

FROZEN = "frozen"
QUEUED = "queued"                 # freeze_wait (freeze retry or release retry)
RECOVERY = "recovery"
RELEASED = "released"             # terminal (failed / cancelled) with the claim cleared

REQUEST = "request"
JOB = "job"

_FREEZING = frozenset(("freezing",))
_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}
_READ_ERROR = object()

F1, F2, F3, F4, F5 = 1, 2, 3, 4, 5


class _Permanent(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _ClaimTaken(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ── job record helpers ───────────────────────────────────────────────────────

def _json(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def freeze_record(job: dict) -> dict:
    return _json(job.get("freeze_record"))


def recorded_pin_sha(job: dict) -> Optional[str]:
    """L 2.22.3: the only pin value this job could have made — frozen_sha after F5,
    otherwise the F3 record (empty before F3: no pin of this job can exist)."""
    return job.get("frozen_sha") or freeze_record(job).get("sha") or None


def _payload(job: dict) -> dict:
    return _json(job.get("payload"))


def _cancel_requested(ctx: store.JobContext) -> bool:
    fresh = store.get_job(ctx.job_id)
    return bool(fresh) and int(fresh.get("cancel_requested") or 0) == 1


# ── the Group's repository (F reads run under G) ─────────────────────────────

@dataclass
class _Group:
    project_id: str
    group_id: str
    branch: str
    wt_path: Path
    cfg: dict


def _group_of(job: dict) -> _Group:
    from modules.flow_gate.services import git_service as _gs
    project_id, group_id = job["project_id"], job.get("group_id")
    if not group_id:
        raise _Permanent("group_missing")
    cfg = _gs.db_git.get_config(project_id)
    state = _gs.db_git.get_state(group_id)
    if cfg is None or not cfg.get("enabled") or state is None or not state.get("worktree_registered"):
        raise _Permanent("git_not_active")
    branch = (state.get("branch") or "").strip()
    project_name = _gs._project_name(project_id)
    if not branch or not project_name:
        raise _Permanent("group_branch_missing")
    wt_path = _gs.src_root(project_name, branch)
    if not wt_path.is_dir():
        raise _Permanent("group_worktree_missing")
    return _Group(project_id, group_id, branch, wt_path, cfg)


def _refs_repo(project_id: str) -> Optional[Path]:
    """Any checkout of the project reads the common refs; the base checkout outlives
    every Group worktree, so pin release and the orphan sweep use it."""
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs.db_git.get_config(project_id)
    project_name = _gs._project_name(project_id)
    if cfg is None or not project_name:
        return None
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    root = _gs.src_root(project_name, base_branch)
    return root if (root / ".git").exists() else None


def _git(repo: Path, args: list, timeout: int = GIT_LOCAL_TIMEOUT_SEC):
    from modules.flow_gate.services import git_service as _gs
    return _gs._run_git(args, cwd=repo, timeout=timeout)


def _rev(repo: Path, rev: str) -> Optional[str]:
    proc = _git(repo, ["rev-parse", "--verify", "--quiet", rev])
    out = (proc.stdout or "").strip()
    return out if proc.returncode == 0 and out else None


def _branch_head(g: _Group) -> Optional[str]:
    return _rev(g.wt_path, f"refs/heads/{g.branch}^{{commit}}")


def _tree_of(repo: Path, sha: str) -> Optional[str]:
    return _rev(repo, f"{sha}^{{tree}}")


def _parents(repo: Path, sha: str) -> Optional[list]:
    proc = _git(repo, ["rev-list", "--parents", "-n", "1", sha])
    if proc.returncode != 0:
        return None
    parts = (proc.stdout or "").split()
    return parts[1:] if parts and parts[0] == sha else None


def _freeze_trailer(repo: Path, sha: str) -> Optional[str]:
    """Value of FlowGate-Freeze in the last paragraph of the commit message (L 2.22.2)."""
    proc = _git(repo, ["show", "-s", "--format=%B", sha])
    if proc.returncode != 0:
        return None
    paragraphs = [p for p in (proc.stdout or "").strip().replace("\r\n", "\n").split("\n\n") if p.strip()]
    if not paragraphs:
        return None
    for line in paragraphs[-1].split("\n"):
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == FREEZE_TRAILER.lower():
            return value.strip()
    return None


def _read_pin(repo: Path, name: str):
    """sha, None (no such ref) or _READ_ERROR."""
    proc = _git(repo, ["for-each-ref", "--format=%(objectname)", name])
    if proc.returncode != 0:
        return _READ_ERROR
    lines = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
    return lines[0] if lines else None


def _worktree_identity(g: _Group) -> dict:
    proc = _git(g.wt_path, ["rev-parse", "--absolute-git-dir"])
    return {"path": str(g.wt_path), "gitdir": (proc.stdout or "").strip() if proc.returncode == 0 else None}


def _group_lockfiles(g: _Group) -> list:
    """L 2.21.1 G rows: the worktree's index.lock / HEAD.lock and the branch ref lock."""
    proc = _git(g.wt_path, ["rev-parse", "--git-dir", "--git-common-dir"])
    lines = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
    if proc.returncode != 0 or len(lines) < 2:
        raise RuntimeError("lockfile_check_failed")
    gitdir, common = (Path(x) if Path(x).is_absolute() else g.wt_path / x for x in lines[:2])
    paths = [gitdir / "index.lock", gitdir / "HEAD.lock",
             common / "refs" / "heads" / (g.branch + ".lock")]
    return [p for p in paths if p.exists()]


GONE = "gone"
WAIT = "wait"
UNCONFIRMED = "unconfirmed"


def handle_lockfile(path: Path) -> str:
    """L 2.21.2 without clearing (module docstring)."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return GONE
    except OSError:
        return WAIT
    if time.time() - st.st_mtime < lockfile_stale_age_sec():
        return WAIT
    _log.error("git_lockfile_owner_unconfirmed: %s", path)
    return UNCONFIRMED


_LOCKFILE_RE = re.compile(r"Unable to create '([^']+\.lock)'")


def _lockfile_in(stderr: Optional[str]) -> Optional[Path]:
    m = _LOCKFILE_RE.search(stderr or "")
    return Path(m.group(1)) if m else None


# ── M (pin commands only; see module docstring) ──────────────────────────────

def run_in_m(lock_ctx: locks.ExecutionContext, project_id: str, repo: Path, args: list,
             allowed: tuple = ("update-ref",)):
    """``allowed``: the worktree_cleanup job (unit 7a) also runs ``worktree``."""
    if lock_ctx.find_held(locks.lock_key("M", project_id)) is None:
        raise locks.LockProgramError("m_command_without_m")
    if not args or args[0] not in allowed:
        raise locks.LockProgramError("m_command_not_allowed", argv=args[:2])
    if in_transaction():
        raise locks.LockProgramError("m_command_inside_db_transaction")
    return _git(repo, list(args), timeout=m_cmd_timeout_sec())


def _class_of(proc) -> str:
    if proc is None:
        return "pin_missing"
    if proc.returncode == -1 or (proc.stderr or "").strip() == "timeout_expired":
        return "timeout"
    return "ok" if proc.returncode == 0 else f"exit_{proc.returncode}"


def _acquire(ctx: store.JobContext, domain: str, origin: str) -> locks.LockOutcome:
    job = ctx.job
    mode = ("freeze_interactive" if domain == "G" else "interactive") if origin == REQUEST else "job"
    return locks.acquire(domain, job["project_id"],
                         group_id=job.get("group_id") if domain == "G" else None,
                         holder_kind="approval_freeze", mode=mode, ctx=ctx.lock_ctx)


def _release(ctx: store.JobContext, outcome: locks.LockOutcome) -> None:
    if outcome.ok and ctx.lock_ctx.find_held(outcome.lock_key) is not None:
        locks.release(ctx.lock_ctx, outcome.lock_key)


# ── writes ───────────────────────────────────────────────────────────────────

def _write(ctx: store.JobContext, expected=None, **changes) -> None:
    store.fenced_write(ctx, changes, expected=expected, allow_freeze=True)


def set_freeze_claim(ctx: store.JobContext, state: str, expected=_FREEZING) -> None:
    """L 2.11 / DB §4(q). Call inside the caller's transaction; raises LeaseLost or
    _ClaimTaken, either of which rolls the whole transaction back."""
    now = now_iso()
    if db.touch_job_lease(ctx.job_id, ctx.lease_token, expected, now) != 1:
        ctx.lease_lost = True
        raise store.LeaseLost(ctx.job_id)
    group_id = ctx.job["group_id"]
    if db.set_group_freeze_claim(group_id, ctx.job_id, state, now) != 1:
        raise _ClaimTaken("group_already_frozen_by_other_job" if db.group_state_exists(group_id)
                          else "group_git_state_missing")


def clear_freeze_claim(group_id: str, job_id: str) -> int:
    return db.clear_group_freeze_claim(group_id, job_id)


def freeze_claim_owner(group_id: str) -> Optional[str]:
    _request_cache.invalidate()
    claim = db.get_group_freeze_claim(group_id)
    return claim.get("job_id") if claim else None


def _blocked_wait(ctx: store.JobContext, domain: str, outcome: locks.LockOutcome, expected=None) -> str:
    """G/M not won: freeze_wait with the blocker, no attempt increase (4.6 last row)."""
    job, now = ctx.job, now_iso()
    b = outcome.blocker or {}
    holder = b.get("holder_ctx_id") or ""
    operation = b.get("holder_kind") or ""
    same = job.get("blocked_lock_key") == outcome.lock_key and job.get("wait_started_at")
    _write(ctx, expected, status="freeze_wait", available_at=now, blocked_domain=domain,
           blocked_lock_key=outcome.lock_key, blocked_holder=holder[:120] or None,
           blocked_operation=operation[:32] or None,
           wait_started_at=job.get("wait_started_at") if same else now,
           last_error_code=outcome.kind, **_LEASE_CLEARED)
    return QUEUED


def finish_freeze_recovery(ctx: store.JobContext, code: str, expected=None) -> str:
    """recovery_required with the claim KEPT (Group stays closed); G is released by the
    caller's finally. Operator: discard (freeze_release) or re-judge (L 2.22.4)."""
    now = now_iso()
    since = ctx.job.get("recovery_since") if ctx.job.get("status") == "recovery_required" else None
    _write(ctx, expected, status="recovery_required", last_error_code=code[:64],
           recovery_since=since or now,
           available_at=registry.add_seconds(now, store.reconcile_retry_interval_sec()),
           **_LEASE_CLEARED)
    _log.error("approval_freeze_recovery_required: %s (%s)", ctx.job_id, code)
    return RECOVERY


def freeze_transient(ctx: store.JobContext, code: str) -> str:
    """No Git side effect to undo: back to freeze_wait with backoff, claim and record kept;
    the next freeze claim's reconcile resumes from the right step (L 2.22.3)."""
    a = int(ctx.job.get("attempt_count") or 0) + 1
    if a > store.retry_max_attempts():
        return freeze_release(ctx, "failed",
                              "pin_create_exhausted" if code.startswith("pin_") else "freeze_retry_exhausted")
    now = now_iso()
    _write(ctx, _FREEZING, status="freeze_wait", attempt_count=a, last_error_code=code[:64],
           retryable=1, available_at=registry.add_seconds(now, store.next_delay(a)), **_LEASE_CLEARED)
    return QUEUED


# ── reconcile (L 2.22.4) ─────────────────────────────────────────────────────

COMPLETE = "complete"
NEXT = "next"
TRANSIENT = "transient"


@dataclass
class Verdict:
    kind: str
    step: int = F1
    sha: Optional[str] = None
    tree: Optional[str] = None
    code: Optional[str] = None


def reconcile_freeze(ctx: store.JobContext, g: _Group) -> Verdict:
    """Which step comes next, judged from the record and the Group's real Git state.
    Only called with G held; the claim keeps the Group still, so the answer is final."""
    job = ctx.job
    fr = freeze_record(job)
    if not fr:
        return Verdict(NEXT, F1)
    if int(fr.get("completed") or 0) == 1:
        return Verdict(COMPLETE)
    try:
        lockfiles = _group_lockfiles(g)
    except Exception:
        return Verdict(TRANSIENT, code="lockfile_check_failed")
    for path in lockfiles:
        r = handle_lockfile(path)
        if r == WAIT:
            return Verdict(TRANSIENT, code="group_lockfile_present")
        if r == UNCONFIRMED:
            return Verdict(RECOVERY, code="group_lockfile_owner_unconfirmed")
    head = _branch_head(g)
    if head is None:
        return Verdict(TRANSIENT, code="freeze_head_unreadable")
    if not fr.get("sha"):
        if head == fr.get("pre_head"):
            return Verdict(NEXT, F2)
        if (_parents(g.wt_path, head) == [fr.get("pre_head")]
                and _freeze_trailer(g.wt_path, head) == job["job_id"]):
            tree = _tree_of(g.wt_path, head)
            if tree is None:
                return Verdict(TRANSIENT, code="freeze_tree_unreadable")
            return Verdict(NEXT, F3, sha=head, tree=tree)
        return Verdict(RECOVERY, code="freeze_head_unexplained")
    if head != fr["sha"]:
        return Verdict(RECOVERY, code="group_branch_moved_after_freeze")
    pin = _read_pin(g.wt_path, fr.get("pin_ref_name") or pin_ref_name(job["job_id"]))
    if pin is _READ_ERROR:
        return Verdict(TRANSIENT, code="pin_check_failed")
    if pin is None:
        return Verdict(NEXT, F4, sha=fr["sha"], tree=fr.get("tree"))
    if pin == fr["sha"]:
        return Verdict(NEXT, F5, sha=fr["sha"], tree=fr.get("tree"))
    return Verdict(RECOVERY, code="pin_points_elsewhere")


def reconcile_freeze_expired(ctx: store.JobContext) -> None:
    """Sweeper hook for a freezing job whose lease lapsed or whose owner died (L 2.22.4).
    Never judges here and never goes to pending: back to freeze_wait, claim kept; the
    next freeze claim judges under G."""
    job, now = ctx.job, now_iso()
    if job.get("release_intent"):
        store.job_write(ctx, status="freeze_wait", available_at=now, **_LEASE_CLEARED)
        notifier.job_publishable(job)
        return
    if int(freeze_record(job).get("completed") or 0) == 1:
        store.job_write(ctx, status="recovery_required", last_error_code="freeze_state_inconsistent",
                        recovery_since=now,
                        available_at=registry.add_seconds(now, store.reconcile_retry_interval_sec()),
                        **_LEASE_CLEARED)
        return
    store.job_write(ctx, status="freeze_wait", available_at=now, **_LEASE_CLEARED)
    notifier.job_created(job)


# ── F1~F5 (L 2.22.3) ─────────────────────────────────────────────────────────

def _freeze_message(job: dict) -> str:
    """Subject: the request's commit title, else the finalize subject resolver (unit 6b:
    the same subject the old approval absorb commit got), plus the trailers."""
    from modules.flow_gate.services import git_service as _gs
    p = _payload(job)
    subject = (p.get("commit_title") or "").strip()
    if not subject and job.get("group_id"):
        try:
            subject = (_gs.resolve_commit_message(job["group_id"])[0] or "").strip()
        except Exception:
            _log.warning("commit subject resolve failed for %s", job.get("group_id"), exc_info=True)
    subject = subject or f"Approve {p.get('doc_id') or job['job_id']}"
    trailers = [f"{FREEZE_TRAILER}: {job['job_id']}"]
    if p.get("doc_id"):
        trailers.append(f"{DOC_TRAILER}: {p['doc_id']}")
    return subject + "\n\n" + "\n".join(trailers)


def _precheck_again(job: dict, g: _Group) -> Optional[str]:
    from modules.flow_gate.services import git_service as _gs
    action = _payload(job).get("git_action")
    if action not in APPROVAL_ACTIONS:
        return "invalid_git_action"
    if action == "push" and _gs._dirty(g.wt_path):
        # a bare push never fabricates a commit (NR 0331.0005 §3)
        return "dirty_worktree"
    return None


def _absorb(job: dict, g: _Group) -> None:
    """F2 body: the finalize absorb (debris-filtered staging), with the trailer."""
    from modules.flow_gate.services import git_service as _gs
    from .commit import _absorb_worker_edits
    from .credentials import _author_env_from_cfg
    if _payload(job).get("git_action") == "push":
        return
    try:
        from modules.flow_gate.services import conversation_markdown_service
        conversation_markdown_service.snapshot_group_conversations(g.project_id, g.group_id)
    except Exception:
        _log.exception("conversation markdown snapshot failed for group %s", g.group_id)
    if _gs._dirty(g.wt_path):
        _absorb_worker_edits(g.wt_path, _freeze_message(job), _author_env_from_cfg(g.cfg))


def run_freeze(ctx: store.JobContext, origin: str = JOB) -> str:
    """One freeze attempt. ctx holds a freezing lease (request context or FREEZE claim)
    and runs inside job_store.lease_scope. Returns FROZEN / QUEUED / RECOVERY / RELEASED;
    LeaseLost propagates (nothing more is written)."""
    g = _acquire(ctx, "G", origin)
    if not g.ok:
        if g.kind == locks.BUSY:
            return _blocked_wait(ctx, "G", g, _FREEZING)
        if g.kind == locks.RECOVERY_REQUIRED:
            return finish_freeze_recovery(ctx, "group_recovery_required", _FREEZING)
        _write(ctx, _FREEZING, status="freeze_wait", last_error_code=(g.cause or g.kind)[:64],
               available_at=registry.add_seconds(now_iso(), store.next_delay(1)), **_LEASE_CLEARED)
        return QUEUED
    try:
        result = _freeze_under_g(ctx, origin)
    finally:
        _release(ctx, g)
    if result == FROZEN and origin != REQUEST:
        notifier.job_publishable(ctx.job)
    return result


def _freeze_under_g(ctx: store.JobContext, origin: str) -> str:
    job = ctx.job
    if _cancel_requested(ctx):
        return freeze_release(ctx, "cancelled", "cancelled_by_user")
    try:
        g = _group_of(job)
    except _Permanent as exc:
        if freeze_record(job):
            return finish_freeze_recovery(ctx, exc.code, _FREEZING)
        return freeze_release(ctx, "failed", exc.code)
    v = reconcile_freeze(ctx, g)
    if v.kind == COMPLETE:
        return finish_freeze_recovery(ctx, "freeze_state_inconsistent", _FREEZING)
    if v.kind == RECOVERY:
        return finish_freeze_recovery(ctx, v.code, _FREEZING)
    if v.kind == TRANSIENT:
        return freeze_transient(ctx, v.code)
    start, sha, tree = v.step, v.sha, v.tree
    name = pin_ref_name(job["job_id"])

    if start <= F1:
        store.ensure_lease(ctx)
        code = _precheck_again(job, g)
        if code:
            return freeze_release(ctx, "failed", code)     # no record, no pin: claim-free terminal
        pre_head = _branch_head(g)
        if pre_head is None:
            return freeze_transient(ctx, "freeze_head_unreadable")
        record = {"attempt": job["job_id"], "pre_head": pre_head, "wt_id": _worktree_identity(g),
                  "pin_ref_name": name, "sha": None, "tree": None, "pin_confirmed": 0, "completed": 0}
        try:
            with get_store().transaction():
                set_freeze_claim(ctx, "freezing")
                enc = store.fenced_write(ctx, {"phase": "F1", "freeze_record": record},
                                         expected=_FREEZING, allow_freeze=True, update_local=False)
        except _ClaimTaken as exc:
            return freeze_release(ctx, "failed", exc.code)
        ctx.job.update(enc)

    if start <= F2:
        store.ensure_lease(ctx)
        if _payload(job).get("git_action") == "push":
            from modules.flow_gate.services import git_service as _gs
            if _gs._dirty(g.wt_path):
                return freeze_release(ctx, "failed", "dirty_worktree")
        try:
            _absorb(job, g)
        except GitServiceError:
            # failed or timed out: the next attempt's reconcile tells "no commit" (F2
            # again) from "commit landed" (trailer, F3) — never a blind second commit
            _log.warning("approval freeze commit failed: %s", ctx.job_id, exc_info=True)
            return freeze_transient(ctx, "freeze_commit_failed")
        sha = _branch_head(g)
        tree = _tree_of(g.wt_path, sha) if sha else None
        if sha is None or tree is None:
            return freeze_transient(ctx, "freeze_head_unreadable")

    if start <= F3:
        store.ensure_lease(ctx)
        rec = freeze_record(ctx.job)
        rec.update(sha=sha, tree=tree)
        _write(ctx, _FREEZING, phase="F3", freeze_record=rec)

    u = None
    if start <= F4:
        store.ensure_lease(ctx)
        m = _acquire(ctx, "M", origin)
        if not m.ok:
            return _blocked_wait(ctx, "M", m, _FREEZING)
        try:
            zero = "0" * len(sha)       # "must not exist" (sha1 or sha256 length)
            u = run_in_m(ctx.lock_ctx, g.project_id, g.wt_path, ["update-ref", name, sha, zero])
        finally:
            _release(ctx, m)
        # the exit status only feeds the lockfile branch; the pin is judged by observation (4.6)
        path = _lockfile_in(u.stderr) if u.returncode != 0 else None
        if path is not None and handle_lockfile(path) == UNCONFIRMED:
            return finish_freeze_recovery(ctx, "pin_lockfile_owner_unconfirmed", _FREEZING)

    store.ensure_lease(ctx)
    pin_now = _read_pin(g.wt_path, name)
    if pin_now is _READ_ERROR:
        return freeze_transient(ctx, "pin_check_failed")
    if pin_now is None:
        return freeze_transient(ctx, "pin_create_failed:" + _class_of(u))
    if pin_now != sha:
        return finish_freeze_recovery(ctx, "pin_points_elsewhere", _FREEZING)
    if _cancel_requested(ctx):
        return freeze_release(ctx, "cancelled", "cancelled_by_user")

    rec = freeze_record(ctx.job)
    rec.update(sha=sha, tree=tree, pin_confirmed=1, completed=1)
    changes = dict(phase="publish_pending", freeze_record=rec, attempt_count=0, freeze_completed=1,
                   frozen_sha=sha, frozen_tree=tree, pin_ref=name, available_at=now_iso(),
                   status="running" if origin == REQUEST else "pending", last_error_code=None,
                   blocked_domain=None, blocked_lock_key=None, blocked_holder=None,
                   blocked_operation=None, wait_started_at=None)
    if origin != REQUEST:
        changes.update(_LEASE_CLEARED)
    with get_store().transaction():
        # claim first: the job write may clear the lease the claim's fence checks
        set_freeze_claim(ctx, "publish_wait")
        enc = store.fenced_write(ctx, changes, expected=_FREEZING, allow_freeze=True,
                                 update_local=False)
    ctx.job.update(enc)
    return FROZEN


# ── release (L 2.22.3 freeze_release / release_pin) ──────────────────────────

CLEARED = "cleared"
BUSY = "busy"
RETRY = "retry"
MISMATCH = "mismatch"
UNVERIFIED = "unverified"


def release_pin(ctx: store.JobContext) -> tuple:
    """(CLEARED | RETRY | BUSY | MISMATCH | UNVERIFIED, code). The delete's expected old
    value is always the recorded SHA, never the observed one."""
    job = ctx.job
    name = freeze_record(job).get("pin_ref_name") or pin_ref_name(job["job_id"])
    exp = recorded_pin_sha(job)
    repo = _refs_repo(job["project_id"])
    if repo is None:
        return UNVERIFIED, "pin_check_failed"
    pin = _read_pin(repo, name)
    if pin is _READ_ERROR:
        return UNVERIFIED, "pin_check_failed"
    if pin is None:
        return CLEARED, None
    if not exp or pin != exp:
        _log.error("approval_pin_release_blocked: %s %s observed=%s expected=%s",
                   job["job_id"], name, pin, exp)
        return MISMATCH, "pin_points_elsewhere"
    m = _acquire(ctx, "M", JOB)
    if not m.ok:
        return BUSY, "pin_release_m_busy"
    try:
        run_in_m(ctx.lock_ctx, job["project_id"], repo, ["update-ref", "-d", name, exp])
    finally:
        _release(ctx, m)
    after = _read_pin(repo, name)
    if after is _READ_ERROR:
        return UNVERIFIED, "pin_check_failed"
    if after is None:
        return CLEARED, None
    if after == exp:
        return RETRY, "pin_delete_not_applied"
    return MISMATCH, "pin_points_elsewhere"


def freeze_release(ctx: store.JobContext, final_status: str, code: str) -> str:
    """Terminal exit of a freeze job (failed / cancelled). Writes the intent first, so a
    retry or reconcile continues the same release; terminal + claim clear only after the
    pin is confirmed gone, in one transaction. Takes G itself (re-entrant when the
    caller holds it). Status fence: ctx.expected_statuses (freezing for a RELEASE claim,
    recovery_required for the L 2.18 retry, running for a publish failure)."""
    store.job_write(ctx, release_intent={"final_status": final_status, "code": code})
    g = _acquire(ctx, "G", JOB)
    if g.ok:
        try:
            r, rcode = release_pin(ctx)
        finally:
            _release(ctx, g)
    else:
        r, rcode = BUSY, f"pin_release_g_{g.kind}"
    job = ctx.job
    if r == CLEARED:
        now = now_iso()
        with get_store().transaction():
            enc = store.fenced_write(ctx, dict(status=final_status, result=code, last_error_code=code[:64],
                                               retryable=0, finished_at=now, **_LEASE_CLEARED),
                                     update_local=False)
            if job.get("group_id"):
                clear_freeze_claim(job["group_id"], job["job_id"])
        ctx.job.update(enc)
        store.run_terminal_cleanup(ctx.job)
        notifier.job_terminal(ctx.job)
        _log.info("approval freeze released: %s -> %s (%s)", ctx.job_id, final_status, code)
        return RELEASED
    if r in (BUSY, RETRY):
        a = int(job.get("attempt_count") or 0) + 1
        if a <= store.retry_max_attempts():
            now = now_iso()
            store.job_write(ctx, status="freeze_wait", attempt_count=a, last_error_code=rcode[:64],
                            available_at=registry.add_seconds(now, store.next_delay(a)),
                            **_LEASE_CLEARED)
            return QUEUED
        rcode = "retry_exhausted"       # never terminal without a confirmed pin
    return finish_freeze_recovery(ctx, ("freeze_release_" + (rcode or r))[:64])


# ── executor half and Runner hooks ───────────────────────────────────────────

def run_entry(ctx: store.JobContext, entry: str) -> None:
    """FREEZE / RELEASE entries of the final_approval_publish executor (6b adds PUBLISH).
    Records its own outcome (returns None to the Runner)."""
    if entry == store.FREEZE:
        run_freeze(ctx, JOB)
        return None
    if entry == store.RELEASE:
        intent = _json(ctx.job.get("release_intent"))
        freeze_release(ctx, intent.get("final_status") or "cancelled",
                       intent.get("code") or "cancelled_by_user")
        return None
    raise store.JobProgramError("publish_entry_not_in_freeze", job_id=ctx.job_id)


def install() -> None:
    """Replace the Runner's unit-6 placeholders (recovery_required) with the freeze
    reconcile and release. Idempotent; harmless while no freeze job exists."""
    from . import job_runner
    job_runner.register_freeze_hooks(reconcile_freeze_expired, freeze_release)


# ── orphan pins (L 2.25) ─────────────────────────────────────────────────────

_pin_cursor: Optional[str] = None


def _age_sec(since: Optional[str]) -> float:
    if not since:
        return 0.0
    try:
        return (datetime.fromisoformat(now_iso()) - datetime.fromisoformat(since)).total_seconds()
    except ValueError:
        return 0.0


def cleanup_terminal(job: dict) -> bool:
    """Has the worktree_cleanup job that recorded this succeeded approval's pin ended
    (any terminal status)? No such job -> never eligible by age (D 3.7.2)."""
    from .worktree_cleanup import pin_cleanup_ended
    return pin_cleanup_ended(job)


def step_orphan_pins() -> int:
    """Up to ORPHAN_PIN_PROJECTS_PER_TICK projects per call, rotating. Returns deletions."""
    global _pin_cursor
    projects = db.projects_with_kind(KIND)
    if not projects:
        return 0
    if _pin_cursor is not None:
        projects = [p for p in projects if p > _pin_cursor] + [p for p in projects if p <= _pin_cursor]
    deleted = 0
    for project_id in projects[:ORPHAN_PIN_PROJECTS_PER_TICK]:
        _pin_cursor = project_id
        try:
            deleted += _sweep_project_pins(project_id)
        except Exception:
            _log.warning("orphan pin sweep failed for %s", project_id, exc_info=True)
    return deleted


def _sweep_project_pins(project_id: str) -> int:
    repo = _refs_repo(project_id)
    if repo is None:
        return 0
    proc = _git(repo, ["for-each-ref", "--format=%(refname) %(objectname)", PIN_PREFIX])
    if proc.returncode != 0:
        return 0
    dels = []
    for line in (proc.stdout or "").splitlines():
        name, _, sha = line.strip().partition(" ")
        if not name.startswith(PIN_PREFIX) or not sha:
            continue
        job_id = name[len(PIN_PREFIX):]
        _request_cache.invalidate()
        job = db.get_job(job_id)
        if job is None:
            _log.warning("approval_pin_unowned: %s %s", name, sha)
            continue
        exp = recorded_pin_sha(job)
        status = job.get("status")
        eligible = status in ("failed", "cancelled") or (status == "succeeded" and cleanup_terminal(job))
        if not eligible:
            if status == "succeeded" and _age_sec(job.get("finished_at")) > pin_awaiting_cleanup_alert_sec():
                _log.warning("approval_pin_awaiting_cleanup: %s %s %s", job_id, name, sha)
            continue
        if not exp or sha != exp:
            _log.error("approval_pin_mismatch: %s %s observed=%s expected=%s", job_id, name, sha, exp)
            continue
        dels.append((name, exp))
    deleted = 0
    per_hold = m_max_cmds_per_hold()
    for i in range(0, len(dels), per_hold):
        ctx = locks.new_context("swp")
        m = locks.acquire("M", project_id, holder_kind="sweeper", mode="job", ctx=ctx)
        if not m.ok:
            break
        try:
            for name, exp in dels[i:i + per_hold]:
                u = run_in_m(ctx, project_id, repo, ["update-ref", "-d", name, exp])
                if u.returncode == 0:
                    deleted += 1
                    _log.info("orphan approval pin deleted: %s", name)
                else:
                    _log.error("approval_pin_mismatch: %s delete refused (%s)", name, _class_of(u))
        finally:
            locks.release(ctx, m.lock_key)
    return deleted

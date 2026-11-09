"""Stale-session cleanup and startup recovery.

Extracted from git_service.py (flowgate.default.0550 T0013, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import terminal_cleanup_snapshots as db_terminal_cleanup

from . import approval_intent
from . import lock_manager as locks
from . import merge_target
from . import orphan_guard
from . import preserve
from . import session_claim
from .branch_merge_publish import session_lock, session_unlock
from .credentials import GitServiceError
from .worktree import _abort_disposed_merge_session, _slot_lock, _slot_unlock

_log = logging.getLogger(__name__)

MERGE_SESSION_TTL_HOURS = 24   # L0004 §1 — quiet-for-this-long conflict → auto-abort
# 0668 T0004 — a session waiting for its human review that holds the base checkout is
# kept while it is being looked at (opening the review touches it) but never longer than
# this from its creation: every other group's base work is blocked by it meanwhile.
REVIEW_PENDING_MAX_HOURS = 72

SWEEP_INTERVAL_MIN = 30        # L0004 §1 — auto-recovery sweep period


def cleanup_disposed_group(project_id: str, group_id: str) -> dict:
    """Tear down a DISPOSED group's git leftovers (worktree dir + local work branch
    + ledger registration). Called right after dispose_group succeeds.

    dispose_group itself never touches git, so without this the discarded group's
    entire source-tree worktree copy, its unmerged local branch, and its ledger row
    all survived — the ledger row also kept the group in the §2 status dropdown as
    an unselectable ghost. Disposal has ALREADY succeeded when we run, so a git
    failure must never surface as an error: everything here is best-effort and
    swallowed. No-op when git integration is off or the group holds no slot."""
    from modules.flow_gate.services import git_service as _gs
    try:
        cfg = _gs.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return {"ok": True, "cleaned": False, "reason": "git_disabled"}
        state = _gs.db_git.get_state(group_id)
        if state is None or not state.get("worktree_registered"):
            return {"ok": True, "cleaned": False, "reason": "no_slot"}
        try:
            if _gs.get_branch_merge_group_claim(group_id) is not None:
                return {"ok": False, "cleaned": False, "reason": "branch_merge_claim_active"}
        except Exception:
            return {"ok": False, "cleaned": False, "reason": "branch_merge_claim_query_failed"}
        if not _gs.git_available():
            return {"ok": True, "cleaned": False, "reason": "git_unavailable"}
        # A conflict/merging slot still has an open session: abort it first, under the
        # session's workspace domain (0669 unit 9b; before, it ran with no lock at all).
        project_name = _gs._project_name(project_id)
        if project_name and (state.get("status") or "none") in ("conflict", "merging"):
            base_branch = (cfg.get("base_branch") or "main").strip() or "main"
            session = _gs.db_git.get_open_session_by_group(group_id)
            if session is not None:
                try:
                    s_ctx, s_held = session_lock(session, project_id, holder_kind="dispose")
                except GitServiceError as exc:
                    return {"ok": False, "cleaned": False, "reason": "git_busy",
                            "lock": exc.details}
                try:
                    _abort_disposed_merge_session(
                        project_id, group_id, _gs.src_root(project_name, base_branch))
                finally:
                    session_unlock(s_ctx, s_held)
        # 0669 unit 7b: a worktree_cleanup job (reason disposed), tried once here. A
        # queued job is finished by the Runner; only "no job" falls back to the mutex.
        from .worktree_cleanup import clean_disposed
        job = clean_disposed(project_id, group_id)
        if job is not None:
            status = job.get("status")
            if status == "succeeded":
                return {"ok": True, "cleaned": True}
            if status in ("failed", "cancelled", "recovery_required"):
                return {"ok": False, "cleaned": False,
                        "reason": job.get("last_error_code") or status, "job_id": job["job_id"]}
            return {"ok": True, "cleaned": False, "reason": "queued", "job_id": job["job_id"]}
        # 0669 unit 9b: no job — the Group's G (+R: a merged slot's origin leftover is
        # retro-deleted) instead of the project mutex.
        lock_ctx, held, refused = _slot_lock(project_id, group_id, publish=True)
        if held is None:
            return {"ok": False, "cleaned": False, "reason": "git_busy", "lock": refused}
        try:
            cleaned = _gs._cleanup_group_slot(project_id, group_id)
        finally:
            _slot_unlock(lock_ctx, held)
        # The slot just left the ledger; nudge clients to re-fetch the group
        # dropdown (the explorer subscribes to git_pending_changed → reload slots).
        if cleaned:
            _gs._emit_pending_changed(project_id, group_id, "none")
        return {"ok": True, "cleaned": cleaned}
    except Exception:
        _log.warning("disposed group cleanup failed for %s", group_id, exc_info=True)
        return {"ok": False, "cleaned": False, "reason": "error"}


def cleanup_terminal_slots(project_id: str) -> dict:
    """POST …/projects/{id}/git/cleanup — backlog sweep of every registered
    slot already finalized (merged/pushed) OR belonging to a disposed group.
    Covers groups finalized/discarded before the per-finalize / per-dispose
    cleanup existed, and any slot whose immediate cleanup failed. (0192 T0005 §3
    adds the disposed backlog: one sweep clears every ghost slot left by a group
    that was discarded before dispose learned to touch git.)"""
    from modules.flow_gate.services import git_service as _gs
    _gs._require_enabled_config(project_id)
    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    # 0669 unit 9b: each slot under its own Group's G (+R), no wait, instead of one
    # project mutex for the whole sweep. A busy Group stays pending (``git_busy``).
    cleaned: list[str] = []
    failed: list[str] = []
    pending: list[dict] = []
    for row in _gs.db_git.list_states_of_project(project_id):
        gid = row["group_id"]
        terminal = (row.get("status") or "none") in _gs.CLEANUP_STATUSES
        if not terminal and not _gs._is_group_disposed(gid):
            continue
        lock_ctx, held, refused = _slot_lock(project_id, gid, publish=True, mode=locks.NO_WAIT)
        if held is None:
            pending.append({"group_id": gid, "reason": "git_busy",
                            "reason_code": refused.get("reason_code")})
            continue
        try:
            # An open TR revert/reapply conflict owns files in this worktree. Cleanup
            # must not destroy the session; it remains a separately actionable row.
            if _gs.tr_conflict_session(gid) is not None:
                pending.append({"group_id": gid, "reason": "revert_conflict"})
                continue
            try:
                if _gs.get_branch_merge_group_claim(gid) is not None:
                    pending.append({"group_id": gid, "reason": "branch_merge_claim_active"})
                    continue
            except Exception:
                pending.append({"group_id": gid, "reason": "branch_merge_claim_query_failed"})
                continue
            if _gs._cleanup_group_slot(project_id, gid):
                cleaned.append(gid)
            else:
                failed.append(gid)
                pending.append({"group_id": gid, "reason": "teardown_failed"})
        finally:
            _slot_unlock(lock_ctx, held)
    status = "ok" if not failed else ("partial" if cleaned else "failed")
    snapshot = db_terminal_cleanup.put(project_id, status, len(cleaned), pending)
    return {"ok": True, "result": {"cleaned": cleaned, "failed": failed},
            "terminal_cleanup": snapshot}


def _ttl_expired(last: Optional[str], hours: float = MERGE_SESSION_TTL_HOURS) -> bool:
    """Whether an activity timestamp is older than ``hours`` (MERGE_SESSION_TTL_HOURS)."""
    if not last:
        return False   # unknown activity → never auto-abort on this basis
    try:
        from datetime import datetime, timedelta, timezone

        dt = datetime.fromisoformat(last)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt >= timedelta(hours=hours)
    except Exception:
        return False


def _review_pending_ttl_expired(session: dict) -> bool:
    """0668 T0004 — the TTL of a session that is resolved and waiting for its review.

    Resolve → leave → come back and review is the normal flow now, so a review wait is
    not abandonment by itself. One that holds the base checkout keeps the ordinary
    activity TTL — opening the review screen counts as activity — under a hard cap from
    creation, so ``guard_base_free`` never blocks other groups without bound. [보류]
    (hold) stays the explicit way to park longer.

    0683 T0004 §5: a session on a managed non-base target workspace is not harmless to
    others — it owns that target's workspace, and every other finalize or branch merge
    into the same target is refused ``merge_target_busy`` until it is approved or
    aborted. It still gets no time-based expiry here: a person's pending review is
    never deleted because it is old. Instead the refusal names it (merge id, owner,
    source, state, route — ``merge_target._blocker_details``) so the person who is
    blocked can find it and finish or abort it, and an attempt that is really dead
    (``PHASE_INTERRUPTED``) is reclaimed at plan time (``recover_interrupted_claim``).
    """
    if not merge_target.holds_base_checkout(session):
        # Not base-gate held; it may well block its own target — see above.
        return False
    if _ttl_expired(session.get("touched_at") or session.get("created_at")):
        return True
    return _ttl_expired(session.get("created_at"), REVIEW_PENDING_MAX_HOURS)


def _session_ttl_expired(session: dict, review_state: Optional[str]) -> bool:
    """The sweep's TTL of a group merge/review session with MERGE_HEAD on disk: a resolved
    session waiting for its review has its own TTL (activity, capped from creation for a
    base-holding one); every other conflict session has the activity TTL. 0153 T#4 (review
    rej_01M6ZWFPF1E6JE2M): judged once on the sweep's fresh row and once more on the row
    re-read under the TTL lock (``_auto_abort_session``)."""
    from modules.flow_gate.services import git_service as _gs
    if review_state in _gs.REVIEW_PENDING_STATES:
        return _review_pending_ttl_expired(session)
    return _ttl_expired(session.get("touched_at") or session.get("created_at"))


def _discard_abandoned_attempt(merge_id: int, reason: str) -> None:
    """0668 T0004 — what an [abort] already does, done for a sweep close too: the parked
    final-approval intent and any rerere checkpoint die with the attempt, so a later
    re-approval starts over cleanly instead of failing ``final_approval_retry_mismatch``.
    Best-effort: the session is already closed and a cleanup failure must not reopen it."""
    from modules.flow_gate.services import git_service as _gs
    try:
        _gs.db_git.invalidate_resolution_checkpoint_for_merge(merge_id)
    except Exception:
        _log.warning("sweep: checkpoint invalidation failed for merge %s", merge_id, exc_info=True)
    try:
        approval_intent.discard_intent(merge_id, reason=reason)
    except Exception:
        _log.warning("sweep: intent discard failed for merge %s", merge_id, exc_info=True)


def _emit_auto_aborted(project_id: str, group_id: str, merge_id: int, reason: str) -> None:
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(group_id) or {}
    _gs._emit("git_merge_auto_aborted", project_id, group_id, {
        "project": project_id, "group_id": group_id, "merge_id": merge_id,
        "reason": reason, "branch": state.get("branch"), "branch_preserved": True,
    })


def _close_orphan(session: dict, project_id: str) -> None:
    """A session whose base checkout has no MERGE_HEAD — the merge is gone from
    disk (manual cleanup / crash). Close it and return the group to 'waiting'
    (0205 L §2.5). branch_preserved: the work branch is untouched."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    # 0594 T0012: an attempt records the outcome and releases only its own
    # workspace (owner match, §9.3); a legacy row is closed exactly as before.
    # A workspace owned by someone else leaves the row open as well (§9.1).
    if not merge_target.close_session_attempt(
        session, merge_target.ATTEMPT_INTERRUPTED, error={"code": "orphan_recovered"},
    ):
        return
    _discard_abandoned_attempt(merge_id, "orphan_recovered")
    _gs._set_status(group_id, "waiting")
    _emit_auto_aborted(project_id, group_id, merge_id, "orphan_recovered")


def _auto_abort_session(
    session: dict, project_id: str, base_root: Path, reason: str
) -> None:
    """Reclaim an abandoned conflict session: git merge --abort (work branch
    preserved), close it, return the group to 'waiting' (0205 L §2.5).

    Takes the session's workspace domain, no wait (0669 unit 9b); if it is busy the
    sweep simply retries next cycle.
    If merge --abort fails (e.g. it collides with unrelated local base changes)
    the session is LEFT intact — a forced reset is never issued, protecting a base
    checkout that has other groups' work mixed in (the exact 0203 accident).

    0153 T#4 §3.4: the row is re-read under the lock and judged on that fresh copy — a
    closed row or a ``reconciling``/``applying``/``completed`` one is left alone, an
    existing recovery hold is kept. A session that carries recovery evidence (a
    human-approved resolution: the T#3 hand-back resolver stage, a clean ``re_review``
    hand-back or its rejection) is parked in ``recovery_hold`` (trigger ``sweep_ttl``)
    without ``merge --abort``, intent discard or a ``waiting`` transition. Only a session
    without evidence keeps the old abort, after the claim fence.

    Review rej_01M6ZWFPF1E6JE2M: the TTL itself is judged again on the fresh row — a person
    who opened the conflict list or the review after the sweep read the row (activity bump)
    makes this a no-op; the review wait keeps its cap from creation. A failed read of the
    evidence (the preservation refs, MERGE_HEAD) aborts nothing either."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    try:
        lock_ctx, held = session_lock(session, project_id, holder_kind="sweep", mode=locks.NO_WAIT)
    except GitServiceError:
        return   # another git op in progress — try again next cycle
    try:
        fresh = _gs.db_git.get_session(merge_id)
        if fresh is None or fresh.get("status") != "open":
            return
        context = _gs.db_git.session_context(fresh)
        if context.get("review_state") in orphan_guard.DELEGATE_STATES:
            return
        if isinstance(context.get(orphan_guard.HOLD_KEY), dict):
            session_claim.note(merge_id, "hold_kept")
            return
        if reason == "ttl_expired" and not _session_ttl_expired(fresh, context.get("review_state")):
            # Activity after the sweep's read (the person is back): nothing to reclaim.
            session_claim.note(merge_id, "ttl_not_expired")
            _log.info("sweep: merge %s is active again under the TTL lock — left as it is", merge_id)
            return
        # 0594 T0012: base_root is the attempt's pinned target root (the managed
        # workspace for a non-base target) — see merge_session_sweep. Ownership is
        # re-read under the sweep lock BEFORE merge --abort (§9.1/§9.3).
        if merge_target.session_workspace_ownership(fresh) != merge_target.OWN_OWNED:
            _log.warning(
                "sweep: merge %s target workspace is not its own — left intact", merge_id,
            )
            return
        facts = _orphan_facts(fresh, context, root=base_root)
        if facts.unobserved:
            # "No evidence" was not observed (a read failed): neither hold nor abort.
            session_claim.note(merge_id, "unobserved")
            _log.warning("sweep: merge %s TTL judgement deferred — unreadable %s", merge_id,
                         list(facts.unobserved))
            return
        if facts.evidence:
            if not session_claim.check(merge_id, "ttl_hold"):
                return
            _record_hold(fresh, project_id, context, facts, orphan_guard.TRIGGER_SWEEP_TTL)
            return
        if not session_claim.check(merge_id, "ttl_abort"):
            return
        session = fresh
        if _gs._merge_in_progress(base_root):
            proc = _gs._run_session_git(merge_id, None, "merge", "sweep_abort",
                                        ["merge", "--abort"], cwd=base_root)
            if proc.returncode != 0:
                _log.warning(
                    "sweep merge --abort failed for %s (%s) — left intact",
                    group_id, _gs._last_line(proc.stderr),
                )
                return   # never force-reset (L §5)
        # owner-matched workspace release for an attempt; legacy close unchanged
        merge_target.close_session_attempt(
            session, merge_target.ATTEMPT_ABORTED, error={"code": reason},
        )
        _discard_abandoned_attempt(merge_id, reason)
        _gs._set_status(group_id, "waiting")
        _emit_auto_aborted(project_id, group_id, merge_id, reason)
        session_claim.note(merge_id, "aborted")
    finally:
        session_unlock(lock_ctx, held)


# ── 0153 T#4: orphan judgement with the recovery-evidence guard ──────────────

def _observe_merge_head(root: Path) -> Optional[bool]:
    """MERGE_HEAD present (True), absent (False) or not observable (None), read-only.

    The shared base checkout (a ``.git`` directory) keeps ``_merge_in_progress``'s file
    check; a linked managed worktree asks git, where exit 1 is "absent" and anything else
    (a timeout, 128) is "not observed" — never read as absent (review rej_01M6ZWFPF1E6JE2M)."""
    from modules.flow_gate.services import git_service as _gs
    try:
        git_path = Path(root) / ".git"
        if git_path.is_dir():
            return bool(_gs._merge_in_progress(Path(root)))
        if git_path.is_file():
            proc = _gs._run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=Path(root))
            if proc.returncode == 0:
                return True
            if proc.returncode == 1:
                return False
    except Exception:
        _log.warning("orphan guard: MERGE_HEAD read failed in %s", root, exc_info=True)
    return None


def _orphan_facts(fresh: dict, context: dict, *, root: Optional[Path] = None) -> orphan_guard.Facts:
    """The disk facts of §3.5 step 4, read-only, re-read under the caller's session lock:
    the session's merge root, MERGE_HEAD, ``HEAD``, a tracked-file status, this merge's
    preservation refs and ``preserve.verify_preserved``. A missing root contributes no
    disk evidence (the context evidence alone decides).

    Review rej_01M6ZWFPF1E6JE2M: a successful empty ref listing and a failed one are not the
    same fact. A failed (non-zero exit, timeout, exception) ``for-each-ref`` or MERGE_HEAD
    read (``_observe_merge_head`` over ``_merge_in_progress`` / ``rev-parse``) is named in
    ``Facts.unobserved``; the verdict is then ``unobserved`` and nothing is closed,
    discarded, aborted or written. HEAD (``_rev_parse``) / status / verify failures only
    fail resume conditions (they never lead to a close)."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(fresh["merge_id"])
    if root is None:
        root, _is_base = merge_target.session_merge_root(fresh)
    exists = bool(root is not None and Path(root).exists())
    merge_head = False
    head = None
    clean = False
    disk_refs: list[str] = []
    verified = False
    unobserved: list[str] = []
    if exists:
        root = Path(root)
        observed = _observe_merge_head(root)
        if observed is None:
            unobserved.append(orphan_guard.OBS_MERGE_HEAD)
        merge_head = bool(observed)
        try:
            head = _gs._rev_parse(root, "HEAD")
        except Exception:
            head = None
        try:
            status = _gs._run_git(["status", "--porcelain", "--untracked-files=no"], cwd=root)
            clean = status.returncode == 0 and not (status.stdout or "").strip()
        except Exception:
            clean = False
        try:
            refs = _gs._run_git(["for-each-ref", "--format=%(refname)",
                                 preserve.preserve_ref_namespace()], cwd=root)
        except Exception:
            refs = None
        if refs is not None and refs.returncode == 0:
            disk_refs = orphan_guard.preserve_refs_of(merge_id, (refs.stdout or "").splitlines())
        else:
            unobserved.append(orphan_guard.OBS_PRESERVE_REFS)
        try:
            verified = bool(preserve.verify_preserved(root, context).get("ok"))
        except Exception:
            verified = False
    evidence = orphan_guard.recovery_evidence(context, preserve_refs_on_disk=disk_refs)
    return orphan_guard.Facts(
        context=context, evidence=tuple(evidence), merge_head_present=merge_head,
        base_root_exists=exists, head_sha=head, worktree_clean=clean,
        preserve_verified=verified,
        hold_present=isinstance(context.get(orphan_guard.HOLD_KEY), dict),
        unobserved=tuple(unobserved),
    )


def _record_hold(fresh: dict, project_id: str, context: dict, facts: orphan_guard.Facts,
                 trigger: str, missing: tuple = ()) -> None:
    """§3.3: write ``recovery_hold`` once. Nothing else in the row changes (review state,
    merge commit, intent, preservation fields, session ``open``, group status) and no Git
    command runs. One event, one warning."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(fresh["merge_id"])
    record = orphan_guard.hold_record(facts, trigger=trigger, at=_gs.now_iso(),
                                      instance_id=_gs.current_instance_id())
    if missing:
        record["resume_missing"] = list(missing)
    context[orphan_guard.HOLD_KEY] = record
    _gs.db_git.set_session_context(merge_id, context)
    session_claim.note(merge_id, "hold")
    _log.warning(
        "merge session %s parked in recovery hold (%s): evidence=%s observed=%s",
        merge_id, trigger, record["evidence"], record["observed"],
    )
    try:
        _gs._emit("git_merge_recovery_hold", project_id, fresh.get("group_id"), {
            "project": project_id, "group_id": fresh.get("group_id"), "merge_id": merge_id,
            "trigger": trigger, "evidence": record["evidence"],
        })
    except Exception:
        _log.warning("recovery hold event failed for merge %s", merge_id, exc_info=True)


def _judge_orphan(session: dict, project_id: str, *, at_boot: bool, trigger: str) -> Optional[str]:
    """§3.5: the only road to ``_close_orphan``. The caller already holds the session
    claim; without it nothing happens. Under ``session_lock`` (sweep, no wait — busy is
    retried next cycle) the row is re-read and the disk facts are read again; the verdict
    of ``orphan_guard.orphan_verdict`` is then performed after the claim fence. A resume
    is written under the lock and the T#3 reconcile is run right after the unlock, still
    under the same (re-entered) claim. Returns the verdict, or None when nothing was
    judged (no claim, busy lock, closed row, failed fence)."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    if session_claim.current(merge_id) is None:
        _log.warning("orphan judgement without a session claim for merge %s — skipped", merge_id)
        return None
    try:
        lock_ctx, held = session_lock(session, project_id, holder_kind="sweep", mode=locks.NO_WAIT)
    except GitServiceError:
        return None   # another git op in progress — try again next cycle
    resume = False
    try:
        fresh = _gs.db_git.get_session(merge_id)
        if fresh is None or fresh.get("status") != "open":
            return None
        context = _gs.db_git.session_context(fresh)
        # Verdict lines 2 and 3 on the fresh row before any disk read: both are no-ops.
        if context.get("review_state") in orphan_guard.DELEGATE_STATES:
            session_claim.note(merge_id, "delegated")
            return orphan_guard.DELEGATE
        if isinstance(context.get(orphan_guard.HOLD_KEY), dict):
            session_claim.note(merge_id, "hold_kept")
            return orphan_guard.HOLD_KEEP
        facts = _orphan_facts(fresh, context)
        verdict = orphan_guard.orphan_verdict(facts)
        action = verdict.action
        if action == orphan_guard.CLOSE_ORPHAN:
            if not session_claim.check(merge_id, "close_orphan"):
                return None
            _gs._close_orphan(fresh, project_id)
            session_claim.note(merge_id, "closed_orphan")
        elif action == orphan_guard.HOLD:
            if not session_claim.check(merge_id, "hold"):
                return None
            _record_hold(fresh, project_id, context, facts, trigger, verdict.missing)
        elif action == orphan_guard.RESUME_RECONCILE:
            if not session_claim.check(merge_id, "resume"):
                return None
            orphan_guard.apply_resume(context, evidence=list(facts.evidence), at=_gs.now_iso(),
                                      instance_id=_gs.current_instance_id())
            _gs.db_git.set_session_context(merge_id, context)
            session_claim.note(merge_id, "resumed")
            _log.warning(
                "merge session %s resumed into reconcile (%s, at_boot=%s): evidence=%s",
                merge_id, trigger, at_boot, list(facts.evidence),
            )
            resume = True
        elif action == orphan_guard.UNOBSERVED:
            # A failed read is not "no evidence": nothing is closed or written (next cycle).
            session_claim.note(merge_id, "unobserved")
            _log.warning("merge session %s: orphan judgement deferred (%s) — unreadable %s",
                         merge_id, trigger, list(verdict.missing))
        else:   # not_orphan / delegate / hold_keep: nothing to write
            session_claim.note(merge_id, action)
    finally:
        session_unlock(lock_ctx, held)
    if resume:
        # T#3 owns every later decision and Git step; nothing is pushed or reset here.
        _gs.reconcile_push_session(merge_id, trigger="orphan_resume")
    return action


def _sweep_tr_session(session: dict, project_id: str) -> None:
    """The sweep's TR branch (088) — the same two outcomes, read off the group worktree.

    Orphan: the revert is no longer in flight, so somebody finished or unwound it outside
    FlowGate; close the row and put the group status back where the session found it, or
    the group would claim a conflict forever. TTL: hand it to the ordinary abort, which is
    the same restore a person's [give up] press performs.
    """
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    context = _gs.db_git.session_context(session)
    state = _gs.db_git.get_state(group_id) or {}
    project_name = _gs._project_name(project_id)
    branch = state.get("branch") or context.get("branch")
    wt_path = _gs.src_root(project_name, branch) if (project_name and branch) else None
    if wt_path is None or not wt_path.is_dir():
        return   # slot gone — never guess, same rule as the base-checkout branch
    # 0061 T#9: a Graph attempt's conflicted merge has a MERGE_HEAD, never a REVERT_HEAD. Its orphan and TTL closes
    # both go through its abort under the group's G (no wait: a busy G is retried next cycle), which re-checks the
    # session and the merge under the lock and closes it only once the merge state is gone.
    if _gs.db_git.session_kind(session) == _gs.db_git.SESSION_KIND_GRAPH_ATTEMPT_MERGE:
        in_flight = _gs._rev_parse(wt_path, "MERGE_HEAD") is not None
        if in_flight:
            if context.get("review_state") == _gs.TR_CONFLICT_REVIEW_RESOLVED:
                if not _review_pending_ttl_expired(session):
                    return
            elif not _ttl_expired(session.get("touched_at") or session.get("created_at")):
                return
        try:
            _gs.abort_tr_conflict(group_id, merge_id, no_wait=True)
        except Exception:
            _log.warning(
                "graph attempt merge session sweep failed for merge %s", merge_id, exc_info=True
            )
            return
        _emit_auto_aborted(project_id, group_id, merge_id, "ttl_expired" if in_flight else "orphan_recovered")
        return
    in_flight = _gs._revert_in_flight(wt_path)
    if not in_flight:
        _gs.db_git.close_session(merge_id, "aborted")
        _gs._set_status(group_id, context.get("prev_status") or "waiting")
        _emit_auto_aborted(project_id, group_id, merge_id, "orphan_recovered")
        return
    if context.get("review_state") == _gs.TR_CONFLICT_REVIEW_RESOLVED:
        # 0668 T0004: resolved and waiting for the common review screen.
        if not _review_pending_ttl_expired(session):
            return
    elif not _ttl_expired(session.get("touched_at") or session.get("created_at")):
        return
    try:
        _gs.abort_tr_conflict(group_id, merge_id)
    except Exception:
        _log.warning(
            "tr conflict session auto-abort failed for merge %s", merge_id, exc_info=True
        )
        return
    _emit_auto_aborted(project_id, group_id, merge_id, "ttl_expired")


def _sweep_group_update_session(session: dict, project_id: str) -> None:
    """Recover a group update against the group worktree without changing its state."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    context = _gs.db_git.session_context(session)
    state = _gs.db_git.get_state(group_id) or {}
    project_name = _gs._project_name(project_id)
    branch = state.get("branch") or context.get("branch")
    root = _gs.src_root(project_name, branch) if (project_name and branch) else None
    if root is None or not root.is_dir():
        return
    merge_head = _gs._run_git(["rev-parse", "--verify", "MERGE_HEAD"], cwd=root)
    if merge_head.returncode != 0:
        _gs.db_git.close_session(merge_id, "aborted")
        return
    if not _ttl_expired(session.get("touched_at") or session.get("created_at")):
        return
    proc = _gs._run_git(["merge", "--abort"], cwd=root)
    if proc.returncode == 0:
        _gs.db_git.close_session(merge_id, "aborted")
        _emit_auto_aborted(project_id, group_id, merge_id, "ttl_expired")


def merge_session_sweep(sessions: Optional[list[dict]] = None) -> None:
    """Auto-recover abandoned / orphaned conflict sessions (0205 L §2.5).

    A caller may pass an already-read open-session list; without one this reads
    its own list (the periodic sweep-daemon path). For each open session: skip
    if the base checkout is gone (never guess); close it as an orphan if the
    merge left no MERGE_HEAD on disk; auto-abort it if it has been quiet past
    the TTL; otherwise leave it. Best-effort and fully isolated per session so
    one bad row cannot sink the pass.

    0153 T#4: a group merge/review session is handled under its DB session claim
    (``session_claim``), re-read after the claim, and its orphan/TTL closes pass the
    recovery-evidence guard (``_judge_orphan`` / ``_auto_abort_session``). There is no
    sweep leader: every instance runs this pass, the per-session claim keeps two of them
    off the same session (T0011 §4.4).
    """
    from modules.flow_gate.services import git_service as _gs
    if sessions is None:
        try:
            sessions = _gs.db_git.list_open_sessions()
        except Exception:
            _log.info("merge session sweep skipped (session table unavailable)", exc_info=True)
            return
    for session in sessions:
        try:
            if _gs.db_git.is_branch_merge_session(session):
                # 0630 T0005 (D0004 §20): an ordinary branch merge has no group and no
                # TTL abort — a conflict waiting for an AI or a person is not abandoned
                # because it is quiet. Only a record/workspace mismatch is flagged.
                from . import branch_merge
                branch_merge.verify_open_attempt(session)
                continue
            group_id = session["group_id"]
            project_id = _gs._project_of_group(group_id)
            kind = _gs.db_git.session_kind(session)
            if kind == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
                _gs._sweep_group_update_session(session, project_id)
                continue
            if kind in _gs.db_git.TR_SESSION_KINDS:
                # 088 — a TR conflict has no MERGE_HEAD anywhere and does not live in the
                # base checkout, so every branch below would call it an orphan and close it
                # while the conflicted revert sat on disk with nothing pointing at it.
                _sweep_tr_session(session, project_id)
                continue
            # 0153 T#4 §4.5: a group merge/review session is processed under its session
            # claim; another instance processing it right now makes this pass skip it.
            # Every step below (reconcile, orphan, TTL, interrupted, completed) re-enters
            # the same claim.
            with session_claim.held(int(session["merge_id"]), session_claim.PURPOSE_SWEEP) as claim:
                if claim is None:
                    _log.info(
                        "merge session sweep: merge %s is being processed by another instance — skipped",
                        session.get("merge_id"),
                    )
                    continue
                _sweep_group_merge_session(session, project_id)
        except Exception:
            _log.warning(
                "merge session sweep failed for merge %s", session.get("merge_id"),
                exc_info=True,
            )


def _sweep_group_merge_session(session: dict, project_id: str) -> None:
    """One group merge/review session of the sweep, under its claim (0153 T#4).

    The row is re-read first (``claim → fresh → decision``): a caller's list may be an
    old snapshot. The branches are the pre-T#4 ones; only the two orphan closes go
    through :func:`_judge_orphan` now, and a session parked in ``recovery_hold`` gets no
    orphan/TTL judgement at all."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    fresh = _gs.db_git.get_session(merge_id)
    if fresh is None or fresh.get("status") != "open":
        session_claim.note(merge_id, "skipped")
        return
    session = fresh
    context = _gs.db_git.session_context(session)
    review_state = context.get("review_state")
    if review_state == _gs.REVIEW_STATE_COMPLETED:
        # 0594 T0012 §6.3: the review finished but the row was never closed.
        _finish_completed_review(session, project_id)
        return
    if review_state in (_gs.REVIEW_STATE_APPLYING, _gs.REVIEW_STATE_RECONCILING):
        # 0481 T0008: `approve_merge_review` already committed by this point, so
        # MERGE_HEAD is gone from disk exactly like a normal successful merge —
        # every branch below would misread that as an orphan and abort a session
        # that is mid-push or already pushed. This state belongs to
        # reconcile_push_session, not the orphan/TTL sweep.
        if review_state == _gs.REVIEW_STATE_RECONCILING:
            try:
                _gs.reconcile_push_session(merge_id, trigger="periodic")
            except Exception:
                _log.warning(
                    "merge review reconciliation failed for merge_id=%s",
                    merge_id, exc_info=True,
                )
        return
    # 0594 T0012 §7: an attempt record exists from BEFORE its merge runs.
    # A live runner (its lock holder still owns the project lock) is left
    # alone; one whose runner is gone without ever reaching a conflict is
    # closed as interrupted. Neither is a conflict session.
    phase = merge_target.attempt_phase(session)
    if phase == merge_target.PHASE_IN_PROGRESS:
        return
    if phase == merge_target.PHASE_INTERRUPTED:
        _recover_interrupted(session, project_id, "interrupted")
        return
    if isinstance(context.get(orphan_guard.HOLD_KEY), dict):
        # 0153 T#4 §3.3: parked for a person (T#5) — no orphan / TTL judgement.
        session_claim.note(merge_id, "hold_kept")
        return
    # §9.1/§9.3: a workspace whose marker names another attempt is never
    # read as this session's orphan/TTL case — row and merge state stay.
    if merge_target.session_workspace_ownership(session) in (
        merge_target.OWN_MISMATCH, merge_target.OWN_STALE,
    ):
        _log.warning(
            "merge session %s: target workspace owned by another attempt — left intact",
            merge_id,
        )
        return
    base_root, is_base = merge_target.session_merge_root(session)
    if is_base:
        if base_root is None or not (base_root / ".git").exists():
            return   # checkout gone — do not touch (log only)
    elif base_root is None or not base_root.exists():
        # The managed workspace is gone: no merge state is left on disk to
        # protect, so the session is an orphan like a vanished MERGE_HEAD —
        # unless it carries recovery evidence (0153 T#4 guard).
        _judge_orphan(session, project_id, at_boot=False, trigger=orphan_guard.TRIGGER_SWEEP_ORPHAN)
        return
    if not _gs._merge_in_progress(base_root):
        _judge_orphan(session, project_id, at_boot=False, trigger=orphan_guard.TRIGGER_SWEEP_ORPHAN)
        return
    # 0668 T0004: a resolved session waiting for its review has its own TTL. The TTL is
    # judged again on the row re-read under the TTL lock (``_auto_abort_session``).
    if not _session_ttl_expired(session, review_state):
        return
    _auto_abort_session(session, project_id, base_root, "ttl_expired")


def _recover_interrupted(session: dict, project_id: str, reason: str) -> None:
    """Close an interrupted finalize attempt (T0012 §6.3) under the attempt's workspace
    domain and R (the verdict may ask the remote), no wait (0669 unit 9b)."""
    from modules.flow_gate.services import git_service as _gs
    try:
        lock_ctx, held = session_lock(session, project_id, holder_kind="sweep", publish=True,
                                      mode=locks.NO_WAIT)
    except GitServiceError:
        return   # another git op in progress — try again next cycle
    try:
        fresh = _gs.db_git.get_session(int(session["merge_id"]))
        if fresh is None or fresh.get("status") != "open":
            return
        if merge_target.attempt_phase(fresh) != merge_target.PHASE_INTERRUPTED:
            return
        # 0153 T#4: under the sweep's claim, fenced before the change (no claim: no-op).
        session_claim.require(int(fresh["merge_id"]), "sweep_interrupted")
        outcome = merge_target.recover_interrupted_attempt(fresh, reason)
        if outcome == merge_target.ATTEMPT_INTERRUPTED:
            _emit_auto_aborted(project_id, fresh["group_id"], int(fresh["merge_id"]), reason)
    finally:
        session_unlock(lock_ctx, held)


def recover_interrupted_claim(session: dict, project_id: str) -> bool:
    """0683 T0004 §4 — the sweep's interrupted-attempt recovery, run on demand when a new
    attempt is planned onto a target this open attempt still claims.

    Same lock (the attempt's workspace domain + R, no wait) and the same re-read under it
    as :func:`_recover_interrupted`; a group finalize or a branch merge alike. True only
    when the claim was really settled (closed as interrupted, or reconciled to the merge
    that had already landed) — anything else leaves the row, the marker and the tree
    alone and the caller keeps its ``merge_target_busy`` refusal."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    try:
        lock_ctx, held = session_lock(session, project_id, holder_kind="sweep", publish=True,
                                      mode=locks.NO_WAIT)
    except GitServiceError:
        return False   # somebody is working on it right now — it is not dead
    try:
        fresh = _gs.db_git.get_session(merge_id)
        if fresh is None:
            return False
        if fresh.get("status") != "open":
            return True    # settled meanwhile (another sweep/plan pass)
        if merge_target.attempt_phase(fresh) != merge_target.PHASE_INTERRUPTED:
            return False
        outcome = merge_target.recover_interrupted_attempt(fresh, "interrupted_at_plan")
        if outcome is None:
            return False
        if _gs.db_git.is_branch_merge_session(fresh):
            from . import branch_merge
            if outcome == merge_target.ATTEMPT_INTERRUPTED:
                branch_merge.record_event(merge_id, branch_merge.EV_FAILED, error="interrupted_at_plan")
        elif outcome == merge_target.ATTEMPT_INTERRUPTED:
            _emit_auto_aborted(project_id, fresh["group_id"], merge_id, "interrupted_at_plan")
        return True
    finally:
        session_unlock(lock_ctx, held)


def _finish_completed_review(session: dict, project_id: str) -> None:
    """Finish a completed review whose row was left open, under the session's workspace
    domain and R, no wait — the same locks a live approve holds until it has closed the
    row itself (0669 unit 9b)."""
    from modules.flow_gate.services import git_service as _gs
    try:
        lock_ctx, held = session_lock(session, project_id, holder_kind="sweep", publish=True,
                                      mode=locks.NO_WAIT)
    except GitServiceError:
        return   # another git op in progress — try again next cycle
    try:
        fresh = _gs.db_git.get_session(int(session["merge_id"]))
        if fresh is None or fresh.get("status") != "open":
            return
        # 0153 T#4: under the sweep's claim, fenced before the change (no claim: no-op).
        session_claim.require(int(fresh["merge_id"]), "sweep_finish_completed")
        merge_target.finish_completed_review(fresh)
    finally:
        session_unlock(lock_ctx, held)


def _start_sweep_daemon() -> None:
    """Launch the periodic sweep loop once (0205 L §2.6). Idempotent.

    0669 unit 5b (0666 L 2.17.2, 2.26 steps 10~11): the loop is the unified sweep tick
    (``job_sweeper.run_forever``) — job workers, instance liveness, stale locks, expired
    job leases, job claims, recovery retry every SWEEP_TICK_SEC — and it still runs
    merge_session_sweep every SWEEP_INTERVAL_MIN (as ceil(1800 / tick) ticks).
    """
    from modules.flow_gate.services import git_service as _gs
    if _gs._sweep_daemon_started:
        return
    _gs._sweep_daemon_started = True
    import threading

    def _loop() -> None:
        from . import job_sweeper
        job_sweeper.run_forever()

    threading.Thread(target=_loop, name="git-merge-sweep", daemon=True).start()


def _open_sessions_after(sessions: list[dict], touched: set[int]) -> list[dict]:
    """Refresh only rows this startup path may have changed, retaining open ones."""
    from modules.flow_gate.services import git_service as _gs
    if not touched:
        return sessions
    result: list[dict] = []
    for session in sessions:
        merge_id = int(session["merge_id"])
        if merge_id not in touched:
            result.append(session)
            continue
        try:
            fresh = _gs.db_git.get_session(merge_id)
        except Exception:
            # A failed refresh must not risk a duplicate orphan abort/event.
            continue
        if fresh and (fresh.get("status") or "") == "open":
            result.append(fresh)
    return result


def _startup_group_session(session: dict, project_id: str) -> bool:
    """One group merge/review session at boot, under its startup claim (0153 T#4). The
    branches are the pre-T#4 ones; the orphan close goes through :func:`_judge_orphan`
    (``at_boot``). True when this path may have changed the row (``touched``).

    Review rej_01M6ZWFPF1E6JE2M: the listed ``session`` is only a candidate. The judgement
    runs on the row re-read under the session's workspace domain + R (``sweep``, no wait:
    the same lock the sweep's completed/interrupted recoveries hold; busy is left to the
    sweep) — claim → session_lock → fresh re-read (still open?) → the pre-T#4 decisions →
    unlock. A row completed, aborted or parked for a person by someone else after the list
    was read is judged as it is now, and every change is preceded by the claim fence. The
    orphan case is handed to :func:`_judge_orphan` after the unlock; it takes the lock
    again and re-reads the row itself."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    try:
        lock_ctx, held = session_lock(session, project_id, holder_kind="sweep", publish=True,
                                      mode=locks.NO_WAIT)
    except GitServiceError:
        session_claim.note(merge_id, "skipped")
        _log.info("startup: merge %s workspace is busy — left to the periodic sweep", merge_id)
        return False
    try:
        fresh = _gs.db_git.get_session(merge_id)
        if fresh is None or fresh.get("status") != "open":
            # Settled after the list was read (completed / aborted elsewhere).
            session_claim.note(merge_id, "skipped")
            return True
        session = fresh
        group_id = session.get("group_id")
        review_state = _gs.db_git.session_context(session).get("review_state")
        if review_state == _gs.REVIEW_STATE_COMPLETED:
            # 0594 T0012 §6.3: the review finished (ledger may already say
            # merged) but the process died before the row was closed.
            if not session_claim.check(merge_id, "startup_finish_completed"):
                return False
            merge_target.finish_completed_review(session)
            return True
        if review_state in (_gs.REVIEW_STATE_APPLYING, _gs.REVIEW_STATE_RECONCILING):
            # 0481 T0008 / L0007 §2.8.1 item 1: the commit already landed by this
            # point, so MERGE_HEAD is gone exactly like an ordinary successful
            # merge — re-affirm 'conflict' (still not merged from the group's
            # point of view) and let the immediate reconcile scan below settle
            # push-unknown/post-push-cleanup sessions without waiting a full
            # PUSH_RECONCILE_RETRY_INTERVAL_SEC.
            if not session_claim.check(merge_id, "startup_reaffirm"):
                return False
            _gs._set_status(group_id, "conflict", merge_id=merge_id)
            return True
        # 0594 T0012 §6.3/§7: every lock is stale at boot, so an attempt that
        # never reached a conflict was interrupted by the restart itself.
        # (The pre-restart lock row is still present until the force-release
        # below, so the live-runner test is skipped here: at boot nothing runs.)
        boot_phase = merge_target.attempt_phase(session, at_boot=True)
        if boot_phase == merge_target.PHASE_IN_PROGRESS:
            # 0669 unit 6b: only a final approval job's attempt reads as in
            # progress at boot; that job settles it itself when it resumes.
            return False
        if boot_phase != merge_target.PHASE_CONFLICT:
            # A merge that already landed is reconciled to completed; one that
            # did not is closed as interrupted; an unprovable one stays open.
            if not session_claim.check(merge_id, "startup_interrupted"):
                return False
            outcome = merge_target.recover_interrupted_attempt(session, "interrupted_by_restart")
            if outcome == merge_target.ATTEMPT_INTERRUPTED:
                _emit_auto_aborted(project_id, group_id, merge_id, "interrupted_by_restart")
            return True
        # §9.1/§9.3: a workspace owned by another attempt is left as it is.
        if merge_target.session_workspace_ownership(session) in (
            merge_target.OWN_MISMATCH, merge_target.OWN_STALE,
        ):
            _log.warning(
                "startup: merge %s target workspace owned by another attempt — left intact",
                merge_id,
            )
            return False
        # The conflict lives where the attempt pinned it: the base checkout
        # for a base/legacy target, the deterministic managed workspace
        # (recomputed from the recorded target) otherwise.
        base_root, _is_base = merge_target.session_merge_root(session)
        merge_head_exists = bool(
            base_root and base_root.exists() and _gs._merge_in_progress(base_root)
        )
        if merge_head_exists:
            # Re-affirm the status only (§2.6).
            if not session_claim.check(merge_id, "startup_reaffirm"):
                return False
            _gs._set_status(group_id, "conflict", merge_id=merge_id)
            return False
    finally:
        session_unlock(lock_ctx, held)
    # 0153 T#4 §3.5: no MERGE_HEAD is no longer enough to close the session — the
    # guard re-reads the row and the disk under the lock and closes only a session
    # without recovery evidence; one with evidence is resumed or held.
    _judge_orphan(session, project_id, at_boot=True, trigger=orphan_guard.TRIGGER_STARTUP_ORPHAN)
    return True


def startup_recovery() -> None:
    """Heal conflict sessions, then sweep + start the daemon at boot (0205 L §2.6).

    A live MERGE_HEAD session is left in 'conflict' (state re-affirmed) but no lock
    is re-acquired — the base is protected by the state gate, not a mutex (0205
    §2.1). Sessions with no MERGE_HEAD go through the orphan guard (0153 T#4: closed
    only without recovery evidence, otherwise resumed or held), each group session under
    its startup claim. Finally a sweep reclaims TTL-expired sessions and the daemon
    repeats it periodically."""
    from modules.flow_gate.services import git_service as _gs
    try:
        sessions = _gs.db_git.list_open_sessions()
        touched: set[int] = set()
        for session in sessions:
            merge_id = session["merge_id"]
            group_id = session.get("group_id")
            try:
                if _gs.db_git.is_branch_merge_session(session):
                    # 0630 T0005 (D0004 §20): workspace / owner marker / MERGE_HEAD /
                    # attempt state / conflict files / review state are compared with the
                    # record; a mismatch is flagged interrupted (never read as success).
                    from . import branch_merge
                    branch_merge.verify_open_attempt(session, at_boot=True)
                    touched.add(int(merge_id))
                    continue
                project_id = _gs._project_of_group(group_id)
                kind = _gs.db_git.session_kind(session)
                if kind == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
                    # Its MERGE_HEAD lives in the group worktree; never rewrite group status.
                    continue
                if kind in _gs.db_git.TR_SESSION_KINDS:
                    # 088 — re-affirm the status and leave the on-disk question to the
                    # sweep at the end of this function, which knows where to look.
                    _gs._set_status(group_id, "conflict", merge_id=merge_id)
                    continue
                # 0153 T#4 §4.5: each group merge/review session is recovered under its
                # session claim. A session another live instance is processing right now
                # (claim held, lease valid, owner ALIVE) is left to it entirely.
                with session_claim.held(int(merge_id), session_claim.PURPOSE_STARTUP) as claim:
                    if claim is None:
                        _log.info(
                            "startup: merge %s is being processed by another instance — left to it",
                            merge_id,
                        )
                        continue
                    if _startup_group_session(session, project_id):
                        touched.add(int(merge_id))
            except Exception:
                _log.warning("git session recovery failed for merge %s", merge_id, exc_info=True)
        # 0669 unit 9c: no project lock rows are written any more, so there is nothing
        # to force-release here. Domain locks left by a dead instance are reclaimed by
        # the lock manager's stale judgement (L 2.9) and the job sweeper.
        # The original snapshot is only a candidate-id list here; reconcile_push_session
        # re-reads each row and re-checks its guard before changing it.
        _gs.reconcile_due_merge_review_sessions("server_startup", sessions=sessions)
        # Sweep consumes context/timestamps, so refresh rows startup/reconcile could change.
        _gs.merge_session_sweep(sessions=_open_sessions_after(sessions, touched))
        _gs._start_sweep_daemon()
    except Exception:
        # Table may not exist yet (pre-migration boot) — recovery is best-effort.
        _log.info("git startup recovery skipped", exc_info=True)

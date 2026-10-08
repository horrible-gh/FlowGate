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
    checkout that has other groups' work mixed in (the exact 0203 accident)."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    try:
        lock_ctx, held = session_lock(session, project_id, holder_kind="sweep", mode=locks.NO_WAIT)
    except GitServiceError:
        return   # another git op in progress — try again next cycle
    try:
        # 0594 T0012: base_root is the attempt's pinned target root (the managed
        # workspace for a non-base target) — see merge_session_sweep. Ownership is
        # re-read under the sweep lock BEFORE merge --abort (§9.1/§9.3).
        if merge_target.session_workspace_ownership(session) != merge_target.OWN_OWNED:
            _log.warning(
                "sweep: merge %s target workspace is not its own — left intact", merge_id,
            )
            return
        if _gs._merge_in_progress(base_root):
            proc = _gs._run_git(["merge", "--abort"], cwd=base_root)
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
    finally:
        session_unlock(lock_ctx, held)


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
    if not _gs._revert_in_flight(wt_path):
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
            review_state = _gs.db_git.session_context(session).get("review_state")
            if review_state == _gs.REVIEW_STATE_COMPLETED:
                # 0594 T0012 §6.3: the review finished but the row was never closed.
                _finish_completed_review(session, project_id)
                continue
            if review_state in (_gs.REVIEW_STATE_APPLYING, _gs.REVIEW_STATE_RECONCILING):
                # 0481 T0008: `approve_merge_review` already committed by this point, so
                # MERGE_HEAD is gone from disk exactly like a normal successful merge —
                # every branch below would misread that as an orphan and abort a session
                # that is mid-push or already pushed. This state belongs to
                # reconcile_push_session, not the orphan/TTL sweep.
                if review_state == _gs.REVIEW_STATE_RECONCILING:
                    try:
                        _gs.reconcile_push_session(int(session["merge_id"]), trigger="periodic")
                    except Exception:
                        _log.warning(
                            "merge review reconciliation failed for merge_id=%s",
                            session.get("merge_id"), exc_info=True,
                        )
                continue
            # 0594 T0012 §7: an attempt record exists from BEFORE its merge runs.
            # A live runner (its lock holder still owns the project lock) is left
            # alone; one whose runner is gone without ever reaching a conflict is
            # closed as interrupted. Neither is a conflict session.
            phase = merge_target.attempt_phase(session)
            if phase == merge_target.PHASE_IN_PROGRESS:
                continue
            if phase == merge_target.PHASE_INTERRUPTED:
                _recover_interrupted(session, project_id, "interrupted")
                continue
            # §9.1/§9.3: a workspace whose marker names another attempt is never
            # read as this session's orphan/TTL case — row and merge state stay.
            if merge_target.session_workspace_ownership(session) in (
                merge_target.OWN_MISMATCH, merge_target.OWN_STALE,
            ):
                _log.warning(
                    "merge session %s: target workspace owned by another attempt — left intact",
                    session.get("merge_id"),
                )
                continue
            base_root, is_base = merge_target.session_merge_root(session)
            if is_base:
                if base_root is None or not (base_root / ".git").exists():
                    continue   # checkout gone — do not touch (log only)
            elif base_root is None or not base_root.exists():
                # The managed workspace is gone: no merge state is left on disk to
                # protect, so the session is an orphan like a vanished MERGE_HEAD.
                _gs._close_orphan(session, project_id)
                continue
            if not _gs._merge_in_progress(base_root):
                _gs._close_orphan(session, project_id)
                continue
            if review_state in _gs.REVIEW_PENDING_STATES:
                # 0668 T0004: a resolved session waiting for its review has its own TTL.
                if _review_pending_ttl_expired(session):
                    _auto_abort_session(session, project_id, base_root, "ttl_expired")
                continue
            last = session.get("touched_at") or session.get("created_at")
            if not _ttl_expired(last):
                continue
            _auto_abort_session(session, project_id, base_root, "ttl_expired")
        except Exception:
            _log.warning(
                "merge session sweep failed for merge %s", session.get("merge_id"),
                exc_info=True,
            )


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


def startup_recovery() -> None:
    """Heal conflict sessions, then sweep + start the daemon at boot (0205 L §2.6).

    A live MERGE_HEAD session is left in 'conflict' (state re-affirmed) but no lock
    is re-acquired — the base is protected by the state gate, not a mutex (0205
    §2.1). Sessions with no MERGE_HEAD are auto-aborted (orphan recovery). Finally
    a sweep reclaims TTL-expired sessions and the daemon repeats it periodically."""
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
                review_state = _gs.db_git.session_context(session).get("review_state")
                if review_state == _gs.REVIEW_STATE_COMPLETED:
                    # 0594 T0012 §6.3: the review finished (ledger may already say
                    # merged) but the process died before the row was closed.
                    merge_target.finish_completed_review(session)
                    touched.add(int(merge_id))
                    continue
                if review_state in (_gs.REVIEW_STATE_APPLYING, _gs.REVIEW_STATE_RECONCILING):
                    # 0481 T0008 / L0007 §2.8.1 item 1: the commit already landed by this
                    # point, so MERGE_HEAD is gone exactly like an ordinary successful
                    # merge — re-affirm 'conflict' (still not merged from the group's
                    # point of view) and let the immediate reconcile scan below settle
                    # push-unknown/post-push-cleanup sessions without waiting a full
                    # PUSH_RECONCILE_RETRY_INTERVAL_SEC.
                    _gs._set_status(group_id, "conflict", merge_id=merge_id)
                    touched.add(int(merge_id))
                    continue
                # 0594 T0012 §6.3/§7: every lock is stale at boot, so an attempt that
                # never reached a conflict was interrupted by the restart itself.
                # (The pre-restart lock row is still present until the force-release
                # below, so the live-runner test is skipped here: at boot nothing runs.)
                boot_phase = merge_target.attempt_phase(session, at_boot=True)
                if boot_phase == merge_target.PHASE_IN_PROGRESS:
                    # 0669 unit 6b: only a final approval job's attempt reads as in
                    # progress at boot; that job settles it itself when it resumes.
                    continue
                if boot_phase != merge_target.PHASE_CONFLICT:
                    # A merge that already landed is reconciled to completed; one that
                    # did not is closed as interrupted; an unprovable one stays open.
                    outcome = merge_target.recover_interrupted_attempt(session, "interrupted_by_restart")
                    if outcome == merge_target.ATTEMPT_INTERRUPTED:
                        _emit_auto_aborted(project_id, group_id, int(merge_id), "interrupted_by_restart")
                    touched.add(int(merge_id))
                    continue
                # §9.1/§9.3: a workspace owned by another attempt is left as it is.
                if merge_target.session_workspace_ownership(session) in (
                    merge_target.OWN_MISMATCH, merge_target.OWN_STALE,
                ):
                    _log.warning(
                        "startup: merge %s target workspace owned by another attempt — left intact",
                        merge_id,
                    )
                    continue
                # The conflict lives where the attempt pinned it: the base checkout
                # for a base/legacy target, the deterministic managed workspace
                # (recomputed from the recorded target) otherwise.
                base_root, _is_base = merge_target.session_merge_root(session)
                merge_head_exists = bool(
                    base_root and base_root.exists() and _gs._merge_in_progress(base_root)
                )
                if merge_head_exists:
                    # Re-affirm the status only (§2.6).
                    _gs._set_status(group_id, "conflict", merge_id=merge_id)
                else:
                    _gs._close_orphan(session, project_id)
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

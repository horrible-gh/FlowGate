"""Ordinary Branch Manager merge as a persistent attempt (flowgate.default.0630 T0005, D0004).

Until 0630 an ordinary branch merge (Branch Manager: local source → local target) that hit a
conflict collected the paths, ran ``git merge --abort``, deleted its managed workspace and
answered ``409 branch_merge_conflict``. By the time anybody — a person or the AI resolver —
could have looked at the conflict, the MERGE_HEAD, the index and the workspace were gone.

This module is the attempt lifecycle that keeps it alive instead. It deliberately builds
nothing new on the resolve or review side:

* the attempt is a ``git_merge_session`` row of kind/owner ``branch_merge`` (migration 123) —
  owned by the project, never by an invented group;
* the conflict lives in the existing managed target workspace (``merge_target``), pinned by
  the same target record and owner marker a group finalize attempt uses;
* conflict listing, EOL-only separation, partial/complete resolve, side-drop/supersede
  checks, candidate freeze, review, approve/reject/re-review, stale/CAS and reconciliation
  are the existing ``conflict``/``git_service`` functions, reached with ``project_id``
  instead of ``group_id``;
* the AI resolver is the existing ``resolve_conflict`` run. The user opens the
  ``GitConflictResolverDialog``, selects a Provider, and clicks ``[기 호출]`` to start it;
  the server never auto-starts the resolver. ``auto_authority`` is always False:
  the resolved candidate always stops at ``resolved_pending_review`` for a person.

Only what genuinely differs for a group-less merge lives here: opening/closing the attempt,
the conflict response, the explicit AI-invoke path (``/ai-resolve``), abort, recovery, and the final
publish (local target ref and the ``push`` choice frozen when the attempt started).
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional
from datetime import datetime, timezone
import threading

from modules.flow_gate.db.connection import get_store, now_iso

from . import merge_target
from .credentials import GitServiceError

_log = logging.getLogger(__name__)

# Context keys (git_merge_session.context JSON).
BRANCH_MERGE_KEY = "branch_merge"
AI_KEY = "ai"
EVENTS_KEY = "attempt_events"
_EVENTS_MAX = 50

# AI resolver bookkeeping (context["ai"]["status"]).
AI_NOT_STARTED = "not_started"
AI_STARTING = "starting"
AI_RUNNING = "running"
AI_START_FAILED = "start_failed"
AI_FINISHED = "finished"
AI_CANCELLED = "cancelled"

# The server-authoritative attempt state the Git UIs render (D0004 §23). Derived — never
# stored — from the session row, its attempt_state/review_state and the AI run.
UI_STARTING = "starting"
UI_CONFLICT = "conflict"
UI_AI_RESOLVING = "ai_resolving"
UI_CONFLICT_REMAINING = "conflict_remaining"
UI_RESOLVED_PENDING_REVIEW = "resolved_pending_review"
UI_RE_REVIEW = "re_review"
UI_APPLYING = "applying"
UI_RECONCILING = "reconciling"
UI_COMPLETED = "completed"
UI_FAILED = "failed"
UI_ABORTED = "aborted"
UI_INTERRUPTED = "interrupted"

# Event names (T0005 §14). SSE through the existing git `_emit`, and the same entries are
# appended to the attempt's own context so the record survives the broadcast.
EV_STARTED = "git_branch_merge_started"
EV_CONFLICT = "git_branch_merge_conflict"
EV_AI_STARTED = "git_branch_merge_ai_started"
EV_AI_START_FAILED = "git_branch_merge_ai_start_failed"
EV_RESOLVED = "git_branch_merge_resolved"
EV_REVIEW_PENDING = "git_branch_merge_review_pending"
EV_REVIEW_REJECTED = "git_branch_merge_review_rejected"
EV_COMPLETED = "git_branch_merge_completed"
EV_ABORTED = "git_branch_merge_aborted"
EV_FAILED = "git_branch_merge_failed"


def _gs():
    from modules.flow_gate.services import git_service
    return git_service


# ── Session access ───────────────────────────────────────────────────────────

def is_branch_merge(session: Optional[dict]) -> bool:
    return _gs().db_git.is_branch_merge_session(session)


def get_branch_merge_group_claim(group_id: str) -> Optional[dict]:
    """Return claim details if `group_id` is claimed by an open branch_merge attempt."""
    if not group_id:
        return None
    gs = _gs()
    try:
        sessions = gs.db_git.list_open_sessions()
        for session in sessions:
            if not is_branch_merge(session):
                continue
            if session.get("status") != "open":
                continue
            ctx = gs.db_git.session_context(session)
            target_rec = ctx.get(merge_target.TARGET_RECORD_KEY) or {}
            bm = ctx.get(BRANCH_MERGE_KEY) or {}
            claimed_group = target_rec.get("target_group_id") or bm.get("target_group_id")
            if claimed_group == group_id:
                attempt_state = ctx.get(merge_target.ATTEMPT_STATE_KEY)
                review_state = ctx.get("review_state")
                if attempt_state in (
                    merge_target.ATTEMPT_COMPLETED,
                    merge_target.ATTEMPT_ABORTED,
                    merge_target.ATTEMPT_FAILED,
                ) or review_state == gs.REVIEW_STATE_COMPLETED:
                    continue
                return {
                    "merge_id": session.get("merge_id"),
                    "project_id": gs.db_git.session_project_id(session),
                    "group_id": group_id,
                    "target_branch": target_rec.get("branch") or bm.get("target_branch"),
                    "source_branch": bm.get("source_branch"),
                    "attempt_state": attempt_state,
                }
    except GitServiceError:
        raise
    except Exception as exc:
        raise GitServiceError(
            500,
            "branch_merge_claim_query_failed",
            f"failed to query branch merge claim for group '{group_id}': {exc}",
            details={"group_id": group_id, "error": str(exc)},
        ) from exc
    return None


def get_session_of_project(project_id: str, merge_id: int, *, open_only: bool = False) -> dict:
    """The branch_merge session ``merge_id`` owned by ``project_id``, or 404."""
    session = _gs().db_git.get_session(int(merge_id))
    if (
        session is None or not is_branch_merge(session)
        or _gs().db_git.session_project_id(session) != project_id
        or (open_only and session.get("status") != "open")
    ):
        raise GitServiceError(404, "not_found", f"branch merge {merge_id} not found")
    return session


def merge_route_prefix(session: dict) -> str:
    """The API path (below ``/api/v1``) that addresses this session's conflict/review
    routes: project-scoped for a branch merge, group-scoped otherwise."""
    merge_id = int(session["merge_id"])
    if is_branch_merge(session):
        return f"/projects/{_gs().db_git.session_project_id(session)}/git/merge/{merge_id}"
    return f"/groups/{session.get('group_id')}/git/merge/{merge_id}"


def info(session: Optional[dict]) -> dict:
    return dict(_gs().db_git.session_context(session).get(BRANCH_MERGE_KEY) or {})


def _update(merge_id: int, **values) -> dict:
    db_git = _gs().db_git
    context = db_git.session_context(db_git.get_session(merge_id))
    context.update(values)
    db_git.set_session_context(merge_id, context)
    return context


def record_event(session_or_id, event_type: str, **fields) -> None:
    """Append one audit entry to the attempt and broadcast it. Best-effort: an event
    must never break the operation it describes. Credentials are never passed here."""
    gs = _gs()
    try:
        merge_id = int(session_or_id["merge_id"] if isinstance(session_or_id, dict) else session_or_id)
        session = gs.db_git.get_session(merge_id)
        if session is None:
            return
        context = gs.db_git.session_context(session)
        bm = context.get(BRANCH_MERGE_KEY) or {}
        project_id = gs.db_git.session_project_id(session)
        payload = {
            "project": project_id, "merge_id": merge_id,
            "source_branch": bm.get("source_branch"), "target_branch": bm.get("target_branch"),
            "push": bm.get("push"),
            **{k: v for k, v in fields.items() if v is not None},
        }
        events = list(context.get(EVENTS_KEY) or [])
        events.append({"type": event_type, "at": now_iso(), **payload})
        context[EVENTS_KEY] = events[-_EVENTS_MAX:]
        gs.db_git.set_session_context(merge_id, context)
        gs._emit(event_type, project_id, None, payload)
    except Exception:
        _log.warning("branch merge event %s could not be recorded", event_type, exc_info=True)


# ── Attempt lifecycle (called by branches.run_merge inside the branch_merge_publish job) ─

def open_attempt(
    ctx: merge_target.MergeTargetContext, *, source_branch: str, push: bool,
    base_branch: str, target_is_base: bool, requested_by: Optional[str] = None,
    provider_id: Optional[str] = None, source_kind: str = "branch",
    source_group_id: Optional[str] = None,
) -> merge_target.MergeTargetContext:
    """Write the attempt row BEFORE ``git merge`` runs (D0004 §4) and return the pinned ctx.

    The target record is the one ``merge_target`` already understands (branch, workspace,
    owner, lock holder, remote expectation, push) plus the source branch. ``push`` is frozen
    here and is the value every later step obeys — review, AI or UI cannot change it.
    """
    gs = _gs()
    base_root = gs._base_root_of(ctx.project_id)
    remote_expectation = (
        gs._rev_parse(base_root, f"refs/remotes/origin/{ctx.target_branch}")
        if base_root is not None and (base_root / ".git").exists() else None
    )
    started_at = now_iso()
    action = "merge" if push else "merge_only"
    record = {
        "version": 1,
        "branch": ctx.target_branch,
        "base_branch": base_branch,
        # The conflict never lives in the shared base checkout, even for the project base:
        # a base target merges in a DETACHED managed workspace (Git refuses a second
        # checkout of the base branch) and is applied to the local ref only on approval.
        "is_project_base": False,
        "target_is_base": bool(target_is_base),
        "detached": bool(target_is_base),
        "workspace_key": ctx.workspace_key,
        "owner": ctx.owner,
        "lock_holder": ctx.lock_holder,
        "started_at": started_at,
        "remote_expectation": remote_expectation,
        "finalize_action": action,
        "push": bool(push),
        "source_branch": source_branch,
        "source_kind": source_kind,
        "source_group_id": source_group_id,
        "target_kind": ctx.target_kind,
        "target_group_id": ctx.target_group_id,
        "managed_workspace": ctx.managed_workspace,
    }
    context = {
        merge_target.TARGET_BRANCH_KEY: ctx.target_branch,
        merge_target.TARGET_RECORD_KEY: record,
        merge_target.ATTEMPT_STATE_KEY: merge_target.ATTEMPT_IN_PROGRESS,
        BRANCH_MERGE_KEY: {
            "source_branch": source_branch,
            "source_kind": source_kind,
            "source_group_id": source_group_id,
            "target_branch": ctx.target_branch,
            "target_kind": ctx.target_kind,
            "target_group_id": ctx.target_group_id,
            "managed_workspace": ctx.managed_workspace,
            "push": bool(push),
            "target_is_base": bool(target_is_base),
            "requested_by": requested_by,
            "provider_id": provider_id,
        },
        # D0004 §11: two separate switches. Starting the resolver automatically never
        # carries the authority to approve its result.
        "auto_authority": False,
        AI_KEY: {"auto_start": False, "auto_authority": False, "status": AI_NOT_STARTED},
    }
    merge_id = gs.db_git.create_branch_merge_session(
        ctx.project_id, finalize_action=action, context=context,
    )
    pinned = replace(ctx, merge_id=merge_id, started_at=started_at, legacy=False)
    record_event(merge_id, EV_STARTED)
    return pinned


def mark_conflict(
    ctx: merge_target.MergeTargetContext, files: list[str], *,
    source_head: Optional[str], target_head: Optional[str], merge_head: Optional[str],
    expected_remote_head: Optional[str], local_target_head: Optional[str] = None,
) -> dict:
    """Turn the in-progress attempt into a durable conflict (D0004 §4/§15 baseline)."""
    gs = _gs()
    assert ctx.merge_id is not None
    merge_target.mark_conflict(ctx, files, {
        "review_state": None,
        "auto_authority": False,
        "resolver_baseline": {
            "base_head": target_head,
            "merge_head": merge_head,
            "expected_remote_head": expected_remote_head,
            "source_head": source_head,
            "local_target_head": local_target_head,
        },
    })
    outcome = gs.apply_eol_separation(ctx.merge_id, ctx.root)
    record_event(ctx.merge_id, EV_CONFLICT, conflict_files=list(files),
                 eol_only=list(outcome.get("eol_only") or []))
    return outcome


def complete_clean(ctx: merge_target.MergeTargetContext, *, merge_commit: Optional[str],
                   pushed: bool) -> None:
    """A clean merge: the attempt closes as completed and only its own workspace goes."""
    assert ctx.merge_id is not None
    merge_target.close_attempt(ctx.merge_id, merge_target.ATTEMPT_COMPLETED, result={
        "merge_commit": merge_commit, "pushed": pushed, "local_applied": True,
    })
    if ctx.managed_workspace:
        merge_target.release_workspace(ctx)
    record_event(ctx.merge_id, EV_COMPLETED, merge_commit=merge_commit, pushed=pushed)


def fail_in_progress(ctx: merge_target.MergeTargetContext, error: dict) -> None:
    """A precondition/mechanical failure after the row was written: failed, workspace gone.
    An attempt that already became a conflict is left untouched (merge_target.fail_attempt)."""
    if ctx.merge_id is None:
        return
    session = _gs().db_git.get_session(ctx.merge_id)
    was_in_progress = bool(session) and session.get("status") == "open" and (
        _gs().db_git.session_context(session).get(merge_target.ATTEMPT_STATE_KEY)
        == merge_target.ATTEMPT_IN_PROGRESS
    )
    merge_target.fail_attempt(ctx, error)
    if was_in_progress:
        record_event(ctx.merge_id, EV_FAILED, error=error.get("code"))


# ── Read model ───────────────────────────────────────────────────────────────

def _ai_run_status(run_id: Optional[str]) -> Optional[str]:
    """``running``/``finished``/``None`` for the resolver run, from the run registry/row."""
    if not run_id:
        return None
    try:
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        detail = ai_diagnostics.get_run_detail(run_id)
    except Exception:
        return None
    status = str(detail.get("status") or "")
    if status in ("running", "starting", "cancelling", "pause_requested", "queued"):
        return AI_RUNNING
    if status:
        return AI_FINISHED
    return None


def resolver_type(context: dict) -> Optional[str]:
    """``ai`` | ``human`` | ``mixed`` from who submitted resolutions (D0004 §27)."""
    kinds = set(context.get("resolver_kinds") or [])
    if not kinds:
        return None
    if kinds == {"ai"}:
        return "ai"
    if kinds == {"human"}:
        return "human"
    return "mixed"


def ui_state(session: dict, *, live_ai: Optional[str] = None) -> str:
    gs = _gs()
    context = gs.db_git.session_context(session)
    attempt_state = context.get(merge_target.ATTEMPT_STATE_KEY)
    review_state = context.get("review_state")
    if session.get("status") != "open":
        if attempt_state == merge_target.ATTEMPT_COMPLETED or review_state == gs.REVIEW_STATE_COMPLETED:
            return UI_COMPLETED
        if attempt_state == merge_target.ATTEMPT_ABORTED:
            return UI_ABORTED
        if attempt_state == merge_target.ATTEMPT_INTERRUPTED:
            return UI_INTERRUPTED
        return UI_FAILED
    if review_state == gs.REVIEW_STATE_COMPLETED:
        return UI_COMPLETED
    if (context.get("attempt_recovery") or {}).get("status") == UI_INTERRUPTED:
        return UI_INTERRUPTED
    if review_state == gs.REVIEW_STATE_PENDING:
        return UI_RESOLVED_PENDING_REVIEW
    if review_state == gs.REVIEW_STATE_RE_REVIEW:
        return UI_RE_REVIEW
    if review_state == gs.REVIEW_STATE_APPLYING:
        return UI_APPLYING
    if review_state == gs.REVIEW_STATE_RECONCILING:
        return UI_RECONCILING
    if attempt_state == merge_target.ATTEMPT_IN_PROGRESS:
        return UI_STARTING
    ai = context.get(AI_KEY) or {}
    if ai.get("status") == AI_STARTING or (ai.get("status") == AI_RUNNING and live_ai != AI_FINISHED):
        return UI_AI_RESOLVING
    merge_id = int(session["merge_id"])
    files = gs.db_git.session_files(merge_id)
    remaining = gs.db_git.remaining_conflicts(merge_id)
    if files and len(remaining) < len(files) and remaining:
        return UI_CONFLICT_REMAINING
    if ai.get("status") in (AI_FINISHED, AI_START_FAILED, AI_CANCELLED) and remaining:
        return UI_CONFLICT_REMAINING
    return UI_CONFLICT


def attempt_view(session: dict) -> dict:
    """What the 0.1 Branch Manager and the v0.2 Git Workspace render (D0004 §23/§25).

    Server state only — never the AI message stream (§26). The AI run id is exposed so a
    screen can open the run's own log as a detail pane."""
    gs = _gs()
    merge_id = int(session["merge_id"])
    context = gs.db_git.session_context(session)
    bm = context.get(BRANCH_MERGE_KEY) or {}
    ai = dict(context.get(AI_KEY) or {})
    live_ai = None
    if ai.get("status") in (AI_RUNNING, AI_STARTING):
        live_ai = _ai_run_status(ai.get("run_id"))
        if live_ai == AI_FINISHED:
            ai["status"] = AI_FINISHED
    files = gs.db_git.session_files(merge_id)
    remaining = [row["path"] for row in files if not row.get("resolved")]
    result = context.get("attempt_result") or {}
    review_state = context.get("review_state")
    return {
        "merge_id": merge_id,
        "project_id": gs.db_git.session_project_id(session),
        "kind": gs.db_git.SESSION_KIND_BRANCH_MERGE,
        "owner_type": gs.db_git.OWNER_BRANCH_MERGE,
        "session_status": session.get("status"),
        "state": ui_state(session, live_ai=live_ai),
        "attempt_state": context.get(merge_target.ATTEMPT_STATE_KEY),
        "review_state": review_state,
        "source_branch": bm.get("source_branch"),
        "source_kind": bm.get("source_kind") or "branch",
        "source_group_id": bm.get("source_group_id"),
        "target_branch": bm.get("target_branch"),
        "target_kind": bm.get("target_kind") or "branch",
        "target_group_id": bm.get("target_group_id"),
        "managed_workspace": bool(bm.get("managed_workspace", True)),
        "target_is_base": bool(bm.get("target_is_base")),
        "push": bool(bm.get("push")),
        "files": [row["path"] for row in files],
        "file_count": len(files),
        "resolved_count": len(files) - len(remaining),
        "unresolved": remaining,
        "eol_only_paths": list(context.get("eol_only_paths") or []),
        "ai": {
            "auto_start": bool(ai.get("auto_start", False)),
            "auto_authority": False,
            "status": ai.get("status") or AI_NOT_STARTED,
            "run_id": ai.get("run_id"),
            "provider_id": ai.get("provider_id"),
            "error": ai.get("error"),
            "started_at": ai.get("started_at"),
        },
        "resolver_type": resolver_type(context),
        "resolver_provider": context.get("resolver_provider"),
        "resolver_run_id": context.get("resolver_run_id"),
        "review_fingerprint": context.get("review_fingerprint"),
        "merge_commit": result.get("merge_commit") or (context.get("merge_commit") or None),
        "pushed": bool(result.get("pushed")) if result else False,
        "local_applied": bool(result.get("local_applied")) if result else False,
        "remote_applied": bool(result.get("pushed")) if result else False,
        "reconciliation_kind": context.get("reconciliation_kind"),
        "last_error": context.get("last_error") or context.get("attempt_error"),
        "started_at": (context.get(merge_target.TARGET_RECORD_KEY) or {}).get("started_at"),
        "closed_at": context.get("attempt_closed_at"),
        "events": list(context.get(EVENTS_KEY) or [])[-20:],
        "routes": {"base": f"/api/v1{merge_route_prefix(session)}"},
    }


def get_attempt(project_id: str, merge_id: int) -> dict:
    session = get_session_of_project(project_id, merge_id)
    return {"ok": True, "result": attempt_view(session)}


def list_attempts(project_id: str, *, open_only: bool = True) -> dict:
    rows = _gs().db_git.list_branch_merge_sessions(project_id, open_only=open_only)
    return {"ok": True, "result": {"attempts": [attempt_view(row) for row in rows[:50]]}}


def conflict_response(ctx: merge_target.MergeTargetContext, files: list[str]) -> dict:
    """The Branch Merge endpoint's answer when the merge stopped on a conflict: a live,
    addressable attempt — not the old terminal ``branch_merge_conflict`` error."""
    session = _gs().db_git.get_session(int(ctx.merge_id))
    view = attempt_view(session)
    return {
        "ok": True,
        "status": "conflict",
        "merge_id": view["merge_id"],
        "source_branch": view["source_branch"],
        "target_branch": view["target_branch"],
        "push": view["push"],
        "pushed": False,
        "conflict_files": list(files),
        "remaining_conflicts": view["unresolved"],
        "eol_only_paths": view["eol_only_paths"],
        "workspace_cleaned": False,
        "attempt": view,
    }


# ── AI resolver — explicit invoke only (D0004 §10–§12; T#3: auto-start removed) ────────

def resolve_provider(project_id: str, requested: Optional[str], session: Optional[dict] = None) -> Optional[str]:
    """Provider priority: 1) the Branch Merge request's explicit provider, 2) a
    conflict-resolution-specific setting, 3) ``None`` — the existing project/runtime
    selection policy inside ``ai_invoke.start_run`` decides.

    FlowGate has no conflict-resolution-specific provider setting today (grep: no such
    key in system/project settings), so tier 2 reads the one the attempt itself recorded
    when it started and otherwise falls through to tier 3."""
    if requested:
        return requested
    stored = info(session).get("provider_id") if session else None
    return stored or None


_resolver_start_locks: dict[tuple[str, int], threading.Lock] = {}
_resolver_start_locks_meta = threading.Lock()


def _attempt_start_lock(project_id: str, merge_id: int) -> threading.Lock:
    key = (str(project_id), int(merge_id))
    with _resolver_start_locks_meta:
        lock = _resolver_start_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _resolver_start_locks[key] = lock
        return lock


def start_resolver(
    project_id: str, merge_id: int, *,
    start_run: Callable[[Optional[str], list[str]], Optional[str]],
    provider_id: Optional[str] = None, messages: Optional[list[str]] = None,
    trigger: str = "manual",
) -> dict:
    """Start (or re-start) the existing ``resolve_conflict`` AI run for this attempt.

    The process-local lock is a same-process debounce only.  The authoritative
    ``not_started/retryable -> starting`` ownership transition is a DB CAS on the raw
    session context, so two server processes cannot both call the resolver starter.
    """
    with _attempt_start_lock(project_id, merge_id):
        gs = _gs()
        provider: Optional[str] = None
        ai: dict = {}
        session: Optional[dict] = None

        # An unrelated context writer (for example an audit append) may beat this request
        # without owning the resolver. Re-read a few times; a real competing resolver
        # becomes STARTING/RUNNING and returns already_running on the next iteration.
        for _cas_round in range(4):
            session = get_session_of_project(project_id, merge_id, open_only=True)
            context = gs.db_git.session_context(session)
            if context.get("review_state"):
                raise GitServiceError(
                    409, "review_in_progress",
                    "this merge already reached review; use the review screen",
                )

            ai = dict(context.get(AI_KEY) or {})
            run_id = ai.get("run_id")
            is_live = False
            if ai.get("status") == AI_STARTING:
                if run_id:
                    is_live = (_ai_run_status(run_id) == AI_RUNNING)
                else:
                    req_at = ai.get("requested_at")
                    if req_at:
                        try:
                            dt = datetime.fromisoformat(str(req_at).replace("Z", "+00:00"))
                            now = datetime.now(timezone.utc)
                            is_live = (now - dt).total_seconds() < 60
                        except Exception:
                            is_live = True
                    else:
                        is_live = True
            elif ai.get("status") == AI_RUNNING:
                is_live = (_ai_run_status(run_id) == AI_RUNNING)

            if is_live:
                return {"ok": True, "result": {
                    "status": "already_running", "run_id": run_id,
                    "attempt": attempt_view(session),
                }}

            provider = resolve_provider(project_id, provider_id, session)
            candidate_ai = dict(ai)
            candidate_ai.update({
                "status": AI_STARTING,
                "provider_id": provider,
                "error": None,
                "trigger": trigger,
                "requested_at": now_iso(),
            })
            candidate_context = dict(context)
            candidate_context[AI_KEY] = candidate_ai
            expected_raw = session.get("context")
            if gs.db_git.cas_session_context(
                merge_id, expected_raw, candidate_context
            ):
                ai = candidate_ai
                break
        else:
            raise GitServiceError(
                409,
                "branch_merge_resolver_state_changed",
                "resolver state changed concurrently; retry the explicit AI invocation",
                {"merge_id": merge_id},
            )

        try:
            run_id = start_run(provider, list(messages or []))
        except Exception as exc:  # noqa: BLE001
            # Production ai_invoke.start_run opens its handoff gate only on success;
            # post-thread startup failures abort that gate before raising, so no worker
            # can become a live resolver after this failure transition.
            code, message = _start_failure(exc)
            ai.update({
                "status": AI_START_FAILED,
                "error": {"code": code, "message": message},
                "run_id": None,
            })
            _update(merge_id, **{AI_KEY: ai})
            record_event(merge_id, EV_AI_START_FAILED, provider=provider, error=code)
            return {"ok": True, "result": {
                "status": AI_START_FAILED,
                "error": {"code": code, "message": message},
                "attempt": attempt_view(gs.db_git.get_session(merge_id)),
            }}

        if not run_id:
            ai.update({
                "status": AI_START_FAILED,
                "error": {"code": "run_not_started", "message": "no AI run was started"},
            })
            _update(merge_id, **{AI_KEY: ai})
            record_event(
                merge_id, EV_AI_START_FAILED,
                provider=provider, error="run_not_started",
            )
        else:
            ai.update({"status": AI_RUNNING, "run_id": run_id, "started_at": now_iso()})
            _update(merge_id, **{AI_KEY: ai})
            record_event(
                merge_id, EV_AI_STARTED,
                provider=provider, resolver_run_id=run_id,
            )

        return {"ok": True, "result": {
            "status": ai["status"],
            "run_id": ai.get("run_id"),
            "error": ai.get("error"),
            "attempt": attempt_view(gs.db_git.get_session(merge_id)),
        }}


def _start_failure(exc: Exception) -> tuple[str, str]:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("code") or "ai_start_failed"), str(detail.get("message") or detail)
    if isinstance(exc, GitServiceError):
        return exc.code, exc.message
    code = getattr(exc, "code", None)
    return str(code or "ai_start_failed"), str(detail or exc) or exc.__class__.__name__


def settle_new_conflict(
    project_id: str, merge_id: int, *,
    start_run: Optional[Callable[[Optional[str], list[str]], Optional[str]]] = None,
    provider_id: Optional[str] = None,
) -> dict:
    """What happens right after the Branch Merge endpoint reports a conflict.

    * Every conflict was line-ending noise (EOL separation already staged them): nothing
      is left for a resolver, so the candidate is frozen straight into review (§13 — never
      committed on the spot). This is the only automatic step.
    * Otherwise the attempt stays open in ``conflict`` state. The AI resolver is NOT
      started automatically. The user opens ``GitConflictResolverDialog``, selects a
      Provider, and clicks ``[AI 호출]`` (``/ai-resolve``) to start it.

    ``start_run`` and ``provider_id`` are accepted for call-site compatibility but are
    intentionally ignored for the non-EOL-only path.
    """
    gs = _gs()
    get_session_of_project(project_id, merge_id, open_only=True)
    if not gs.db_git.remaining_conflicts(merge_id):
        try:
            gs.resolve_conflicts(None, merge_id, [], True, project_id=project_id)
        except GitServiceError as exc:
            _log.warning("branch merge %s: eol-only freeze failed (%s)", merge_id, exc.code)
    return attempt_view(gs.db_git.get_session(merge_id))


def note_resolution_submitted(session: dict, resolver_run_id: Optional[str]) -> None:
    """Remember who resolved (``ai`` when a worker token's run submitted, ``human``
    otherwise) so the screen never says "the AI resolved it" for a manual resolution."""
    gs = _gs()
    merge_id = int(session["merge_id"])
    context = gs.db_git.session_context(gs.db_git.get_session(merge_id))
    kinds = set(context.get("resolver_kinds") or [])
    kinds.add("ai" if resolver_run_id else "human")
    context["resolver_kinds"] = sorted(kinds)
    gs.db_git.set_session_context(merge_id, context)


def note_review_pending(session: dict) -> None:
    """Every conflict is resolved and the candidate is frozen: the resolver's part is over.

    0683 T0004 §6: a ``running``/``starting`` AI status is settled to ``finished`` in the
    stored context right here, instead of staying ``running`` and being corrected at read
    time by ``attempt_view`` — the persisted record now says what really happened."""
    gs = _gs()
    merge_id = int(session["merge_id"])
    context = gs.db_git.session_context(gs.db_git.get_session(merge_id))
    ai = dict(context.get(AI_KEY) or {})
    if ai.get("status") in (AI_RUNNING, AI_STARTING):
        ai.update({"status": AI_FINISHED, "finished_at": now_iso()})
        context = _update(merge_id, **{AI_KEY: ai})
    fields = {"resolver_run_id": context.get("resolver_run_id"),
              "provider": context.get("resolver_provider"),
              "resolver_type": resolver_type(context)}
    record_event(session, EV_RESOLVED, **fields)
    record_event(session, EV_REVIEW_PENDING, **fields)


def note_review_rejected(session: dict, run_id: Optional[str]) -> None:
    gs = _gs()
    merge_id = int(session["merge_id"])
    context = gs.db_git.session_context(gs.db_git.get_session(merge_id))
    context["resolver_kinds"] = []
    ai = dict(context.get(AI_KEY) or {})
    if run_id:
        ai.update({"status": AI_RUNNING, "run_id": run_id, "started_at": now_iso(),
                   "trigger": "review_rejected", "error": None})
    context[AI_KEY] = ai
    gs.db_git.set_session_context(merge_id, context)
    record_event(merge_id, EV_REVIEW_REJECTED, resolver_run_id=run_id)


# ── Approval (called from git_service.approve_merge_review) ──────────────────

def approval_precheck(session: dict, context: dict, target: merge_target.MergeTargetContext) -> None:
    """Stale target/source guard before the frozen candidate is committed (D0004 §18).

    Runs under the project lock the approval holds. The workspace HEAD itself is checked
    by ``_live_candidate_matches_snapshot``; this adds the two refs that live OUTSIDE the
    workspace: the local target branch (a detached base target can be moved by another
    finalize while the review waits) and the source branch the merge was started from."""
    gs = _gs()
    base_root = gs._base_root_of(target.project_id)
    bm = context.get(BRANCH_MERGE_KEY) or {}
    baseline = context.get("resolver_baseline") or {}
    if base_root is None:
        raise GitServiceError(409, "invalid_state", "base checkout is not available")
    target_now = gs._rev_parse(base_root, f"refs/heads/{target.target_branch}")
    # An attached (non-base) workspace IS the target branch, so its ref must still be the
    # candidate's base head. A detached base target is compared with the local ref as it
    # stood when the merge started (the workspace may have fast-forwarded to origin first).
    expected_target = (
        baseline.get("local_target_head") if bm.get("target_is_base")
        else (context.get("base_head") or baseline.get("base_head"))
    )
    if expected_target and target_now != expected_target:
        raise GitServiceError(
            409, "stale_target",
            f"target branch '{target.target_branch}' moved since this merge started; "
            "abort and merge again",
            {"target_branch": target.target_branch, "expected": expected_target,
             "observed": target_now},
        )
    source = bm.get("source_branch")
    source_expected = baseline.get("source_head")
    if source and source_expected:
        source_now = gs._rev_parse(base_root, f"refs/heads/{source}")
        if source_now != source_expected:
            raise GitServiceError(
                409, "stale_source",
                f"source branch '{source}' moved since this merge started; "
                "abort and merge again",
                {"source_branch": source, "expected": source_expected, "observed": source_now},
            )
    if bm.get("target_is_base"):
        # The approved commit is applied to the shared base checkout by fast-forward, so
        # that checkout must be exactly where the merge started and hold no local edits.
        if gs._rev_parse(base_root, "HEAD") != target_now or gs._dirty(base_root, include_untracked=False):
            raise GitServiceError(
                409, "branch_merge_target_dirty",
                "the base checkout has local changes or is not on the target head",
            )


def commit_subject(context: dict, target_branch: str) -> str:
    source = (context.get(BRANCH_MERGE_KEY) or {}).get("source_branch") or "source"
    return f"Merge branch '{source}' into {target_branch}"


def publish_reviewed(
    merge_id: int, session: dict, project_id: str, root: Path, cfg: dict,
    target_branch: str, context: dict,
) -> dict:
    """Apply an approved, committed candidate per the push choice frozen at start (§17).

    push=False → the local target ref only; the remote is never contacted.
    push=True  → a conditional push (``--force-with-lease`` on the expected remote head)
                 of exactly this commit; a moved remote goes through the existing
                 rejection → re-review path, an unknown outcome through reconciliation.
    A base target also fast-forwards the shared base checkout to the commit (the
    approval precheck proved it clean and at the merge's starting head)."""
    gs = _gs()
    bm = context.get(BRANCH_MERGE_KEY) or {}
    push = bool(bm.get("push"))
    commit = context.get("merge_commit")
    if push:
        expected = context.get("expected_remote_head") or ""
        lease = f"refs/heads/{target_branch}:{expected}" if expected else f"refs/heads/{target_branch}:"
        gs.ensure_origin_matches_config(root, (cfg.get("repo_url") or "").strip())
        proc = gs._run_git(
            ["push", f"--force-with-lease={lease}", "origin", f"HEAD:refs/heads/{target_branch}"],
            cwd=root, timeout=gs.GIT_NET_TIMEOUT_SEC,
            username=cfg.get("username"), secret=gs._load_secret_for(cfg) or "",
        )
        if proc.returncode != 0:
            stderr_l = (proc.stderr or "").lower()
            if proc.returncode == -1 or "timeout" in stderr_l or "could not resolve host" in stderr_l:
                return gs._enter_reconciling(merge_id, context, "push_remote_unknown", schedule_retry=True)
            if ("stale info" in stderr_l or "rejected" in stderr_l or "fetch first" in stderr_l
                    or "non-fast-forward" in stderr_l):
                gs._run_git(["reset", "--hard", "ORIG_HEAD"], cwd=root, timeout=gs.GIT_LOCAL_TIMEOUT_SEC)
                return gs._refreeze_for_re_review(
                    None, merge_id, root, target_branch, context, "push_rejected",
                )
            return gs._enter_reconciling(merge_id, context, "push_remote_unknown", schedule_retry=True)
    local_applied = _apply_local_target(project_id, bm, root, commit, required=not push)
    if local_applied is False:
        context["review_state"] = gs.REVIEW_STATE_RECONCILING
        context["reconciliation_kind"] = "target_apply_failed"
        context["last_error"] = {"code": "target_apply_failed"}
        gs.db_git.set_session_context(merge_id, context)
        return {"ok": True, "result": {
            "status": "reconciling", "review_state": gs.REVIEW_STATE_RECONCILING,
            "reconciliation_kind": "target_apply_failed",
        }}
    return complete_reviewed(merge_id, context, pushed=push)


def _apply_local_target(project_id: str, bm: dict, root: Path, commit: Optional[str],
                        *, required: bool) -> Optional[bool]:
    """Make the local target ref point at the approved commit.

    An attached (non-base) workspace already advanced it with the commit itself. A base
    target's commit sits on a detached HEAD: fast-forward the shared base checkout to it,
    which moves ``refs/heads/<base>`` and its working tree together. ``required`` is True
    for push=False (the local ref IS the result); with push=True the remote already has it
    and a skipped sync is logged, not failed."""
    gs = _gs()
    if not bm.get("target_is_base"):
        return True
    base_root = gs._base_root_of(project_id)
    if base_root is None or not commit:
        return False if required else None
    proc = gs._run_git(["merge", "--ff-only", commit], cwd=base_root, timeout=gs.GIT_LOCAL_TIMEOUT_SEC)
    if proc.returncode == 0:
        return True
    _log.warning("branch merge: base checkout could not fast-forward to %s: %s",
                 commit, gs._last_line(proc.stderr))
    return False if required else None


def complete_reviewed(merge_id: int, context: dict, *, pushed: bool) -> dict:
    """The approved merge is really done: record it, close the attempt, drop the workspace."""
    gs = _gs()
    context["review_state"] = gs.REVIEW_STATE_COMPLETED
    context["apply_phase"] = "completed"
    gs.db_git.set_session_context(merge_id, context)
    merge_commit = context.get("merge_commit") or ""
    short = merge_commit[:7] or None
    target = merge_target.resolve_merge_id_target(merge_id)
    merge_target.close_attempt(merge_id, merge_target.ATTEMPT_COMPLETED, result={
        "merge_commit": merge_commit or None, "pushed": pushed, "local_applied": True,
    })
    if target is not None and target.managed_workspace:
        merge_target.release_workspace(target)
    record_event(merge_id, EV_COMPLETED, merge_commit=merge_commit or None, pushed=pushed,
                 resolver_run_id=context.get("resolver_run_id"),
                 provider=context.get("resolver_provider"))
    return {"ok": True, "result": {
        "status": "merged", "review_state": gs.REVIEW_STATE_COMPLETED,
        "merge_commit": short, "pushed": pushed,
    }}


def finish_completed_review(session: dict) -> None:
    """Recovery: review reached ``completed`` but the row is still open (died mid-finish)."""
    gs = _gs()
    context = gs.db_git.session_context(session)
    pushed = bool((context.get(BRANCH_MERGE_KEY) or {}).get("push"))
    complete_reviewed(int(session["merge_id"]), context, pushed=pushed)


def finish_landed_merge(session: dict, ctx: merge_target.MergeTargetContext,
                        merge_commit: Optional[str], *, pushed: bool) -> None:
    """Recovery of an in-progress attempt whose clean merge landed before the crash.

    A base target merges on a DETACHED workspace HEAD, so "the commit landed" there is not
    yet the local base ref. push=False is applied now (the same fast-forward the clean path
    ends with); if that cannot happen the attempt is recorded interrupted — never completed
    over a result that exists nowhere but in a workspace about to be released."""
    gs = _gs()
    assert ctx.merge_id is not None
    bm = info(session)
    local_applied: Optional[bool] = True
    if bm.get("target_is_base"):
        full = gs._rev_parse(ctx.root, "HEAD") if ctx.root is not None else None
        local_applied = _apply_local_target(ctx.project_id, bm, ctx.root, full, required=not pushed)
        if local_applied is False:
            merge_target.close_attempt(ctx.merge_id, merge_target.ATTEMPT_INTERRUPTED, error={
                "code": "landed_merge_not_applied", "merge_commit": merge_commit,
            })
            merge_target.release_workspace(ctx)
            record_event(ctx.merge_id, EV_FAILED, error="landed_merge_not_applied")
            return
    merge_target.close_attempt(ctx.merge_id, merge_target.ATTEMPT_COMPLETED, result={
        "merge_commit": merge_commit, "pushed": pushed, "recovered": True,
        "local_applied": bool(local_applied),
    })
    if ctx.managed_workspace:
        merge_target.release_workspace(ctx)
    record_event(ctx.merge_id, EV_COMPLETED, merge_commit=merge_commit, pushed=pushed, recovered=True)


# ── Abort (D0004 §19) ────────────────────────────────────────────────────────

def abort(project_id: str, merge_id: int) -> dict:
    """User abort: undo the merge in its own workspace, close the attempt as aborted,
    revoke the resolver's tokens, stop its run and release the workspace.

    The target ref never moved (nothing is committed before approval), so there is
    nothing to restore there. An attempt already committing/reconciling is refused —
    its commit may exist and only reconciliation may settle it."""
    gs = _gs()
    session = get_session_of_project(project_id, merge_id, open_only=True)
    context = gs.db_git.session_context(session)
    if context.get("review_state") in (gs.REVIEW_STATE_APPLYING, gs.REVIEW_STATE_RECONCILING):
        raise GitServiceError(
            409, "branch_merge_applying",
            "this merge is being applied or reconciled and cannot be aborted now",
        )
    target = merge_target.resolve_session_target(session)
    merge_target.raise_if_not_workspace_owner(target)
    # 0669 unit 8a: the attempt's target domain (G / W / B), not the project mutex.
    from . import branch_merge_publish
    from . import lock_manager as locks
    lock_ctx, lock_key = branch_merge_publish.attempt_lock(target)
    try:
        if target.root is not None and target.root.exists() and gs._merge_in_progress(target.root):
            proc = gs._run_git(["merge", "--abort"], cwd=target.root)
            if proc.returncode != 0:
                raise GitServiceError(
                    500, "branch_merge_abort_failed", "Git could not abort the merge",
                    diagnostic=gs._last_line(proc.stderr),
                )
        merge_target.close_attempt(merge_id, merge_target.ATTEMPT_ABORTED, error={"code": "user_abort"})
        cleaned = merge_target.release_workspace(target) if target.managed_workspace else False
    finally:
        if lock_ctx.find_held(lock_key) is not None:
            locks.release(lock_ctx, lock_key)
    _stop_resolver(merge_id, context)
    record_event(merge_id, EV_ABORTED)
    return {"ok": True, "result": {
        "status": "aborted", "merge_id": merge_id, "workspace_cleaned": bool(cleaned),
        "attempt": attempt_view(gs.db_git.get_session(merge_id)),
    }}


def _stop_resolver(merge_id: int, context: dict) -> None:
    """Revoke every still-usable resolve token of this merge and cancel its live run."""
    ai = context.get(AI_KEY) or {}
    run_ids = {ai.get("run_id"), context.get("pending_conversation_run_id")} - {None}
    try:
        from modules.flow_gate.services import ai_invoke_service
        for run_id in run_ids:
            try:
                ai_invoke_service.cancel_run(run_id)
            except Exception:
                pass
    except Exception:
        _log.warning("branch merge %s: resolver run cancel failed", merge_id, exc_info=True)
    try:
        from modules.flow_gate.services import token_service
        rows = get_store()._fetch_all(
            "SELECT token_id FROM tokens WHERE merge_id = ? AND action_scope = ? "
            "AND revoked_at IS NULL AND consumed_at IS NULL",
            [int(merge_id), "resolve_conflict"],
        )
        for row in rows:
            try:
                token_service.revoke(row["token_id"], reason="branch_merge_aborted")
            except Exception:
                pass
    except Exception:
        _log.warning("branch merge %s: resolve token revoke failed", merge_id, exc_info=True)
    try:
        ai = dict(ai)
        if ai.get("status") in (AI_RUNNING, AI_STARTING):
            ai["status"] = AI_CANCELLED
            _update(merge_id, **{AI_KEY: ai})
    except Exception:
        pass


# ── Recovery / sweep (D0004 §20) ─────────────────────────────────────────────

RECOVERABLE_UI_STATES = (UI_CONFLICT, UI_CONFLICT_REMAINING, UI_AI_RESOLVING,
                         UI_RESOLVED_PENDING_REVIEW, UI_RE_REVIEW)


def _mark_interrupted(session: dict, reason: str, *, observed: Optional[dict] = None) -> None:
    """Park an open attempt whose on-disk state no longer matches its record. The row stays
    OPEN — never read as success, never silently deleted — so the screen offers abort."""
    merge_id = int(session["merge_id"])
    _update(merge_id, attempt_recovery={
        "status": "interrupted", "reason": reason, "observed": observed or {}, "at": now_iso(),
    })
    record_event(merge_id, EV_FAILED, error=reason)


def verify_open_attempt(session: dict, *, at_boot: bool = False) -> str:
    """Compare an open branch_merge attempt with its workspace (restart/sweep).

    Returns ``ok`` (conflict/review state matches the workspace), ``settled`` (closed or
    reconciled here), ``interrupted`` (left open, flagged for abort/retry), or ``skip``."""
    gs = _gs()
    merge_id = int(session["merge_id"])
    context = gs.db_git.session_context(session)
    review_state = context.get("review_state")
    if review_state == gs.REVIEW_STATE_COMPLETED:
        finish_completed_review(session)
        return "settled"
    if review_state in (gs.REVIEW_STATE_APPLYING, gs.REVIEW_STATE_RECONCILING):
        # At boot the startup scan's own reconcile pass settles these right after this.
        if review_state == gs.REVIEW_STATE_RECONCILING and not at_boot:
            try:
                gs.reconcile_push_session(merge_id, trigger="periodic")
            except Exception:
                _log.warning("branch merge %s reconciliation failed", merge_id, exc_info=True)
        return "skip"
    phase = merge_target.attempt_phase(session, at_boot=at_boot)
    if phase == merge_target.PHASE_IN_PROGRESS:
        return "skip"
    if phase == merge_target.PHASE_INTERRUPTED:
        outcome = merge_target.recover_interrupted_attempt(
            session, "interrupted_by_restart" if at_boot else "interrupted",
        )
        return "settled" if outcome else "interrupted"
    target = merge_target.resolve_session_target(session)
    ownership = merge_target.workspace_ownership(target)
    if ownership != merge_target.OWN_OWNED:
        _mark_interrupted(session, "workspace_owner_mismatch", observed={"ownership": ownership})
        return "interrupted"
    root = target.root
    if root is None or not root.exists():
        _mark_interrupted(session, "workspace_missing")
        return "interrupted"
    if not gs._merge_in_progress(root):
        _mark_interrupted(session, "merge_head_missing")
        return "interrupted"
    baseline = context.get("resolver_baseline") or {}
    observed_merge_head = gs._rev_parse(root, "MERGE_HEAD")
    if baseline.get("merge_head") and observed_merge_head != baseline.get("merge_head"):
        _mark_interrupted(session, "merge_head_mismatch",
                          observed={"merge_head": observed_merge_head})
        return "interrupted"
    if review_state is None:
        unmerged = set(gs._unmerged_paths(root))
        remaining = set(gs.db_git.remaining_conflicts(merge_id))
        if not remaining.issubset(unmerged):
            _mark_interrupted(session, "conflict_files_mismatch",
                              observed={"unmerged": sorted(unmerged)})
            return "interrupted"
    if context.get("attempt_recovery"):
        _update(merge_id, attempt_recovery=None)
    return "ok"


def sweep_open_attempts(*, at_boot: bool = False) -> None:
    """Startup/periodic pass over every open branch_merge attempt. Never a TTL delete: a
    conflict waiting for an AI or a person is not abandoned just because it is quiet."""
    gs = _gs()
    try:
        sessions = gs.db_git.list_open_sessions()
    except Exception:
        return
    for session in sessions:
        if not is_branch_merge(session):
            continue
        try:
            verify_open_attempt(session, at_boot=at_boot)
        except Exception:
            _log.warning("branch merge %s recovery failed", session.get("merge_id"), exc_info=True)

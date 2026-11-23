"""Durable manual Git sequences. The browser submits once and only observes.

Each effect has a persisted phase. Child publish jobs use deterministic identities;
the parent never holds a Git lock while waiting. Ambiguous crash evidence parks the
run for recovery rather than replaying a commit or update against new user edits.
"""
from __future__ import annotations

import json

from modules.flow_gate.db import operation_job as jobs
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import git_service as gs
from . import job_store as store, job_runner as runner, instance_registry
from . import worktree_manual as manual, manual_merge, finalize, commit_suggest
from . import base_publish, remote_refresh, lock_manager, scope_base
from .credentials import GitServiceError

KIND = "git_execution"
STEPS = {"commit": ["commit"], "merge": ["commit", "update", "merge"],
         "push": ["commit", "update", "merge", "push"]}
UPDATE_SKIP = frozenset(("git_error", "base_dirty", "base_diverged", "merge_conflict_open",
    "update_unavailable", "group_work_base_unverified", "group_work_base_diverged",
    "group_work_base_source_rewritten", "group_work_base_record_failed"))
WAIT_CODES = frozenset(("git_busy", "group_lease_active", "run_already_active",
                       "branch_merge_claim_active", "graph_execution_active"))


def _json(value):
    return json.loads(value) if isinstance(value, str) and value else value or {}


def _jobs(group_id):
    return jobs.jobs_of_group(group_id, KIND, (*jobs.WAITING_STATUSES,
        *jobs.EXECUTING_STATUSES, *jobs.TERMINAL_STATUSES, "recovery_required"))


def view(job):
    if not job:
        return None
    p, ev = _json(job.get("payload")), _json(job.get("evidence"))
    completed = ev.get("completed", p.get("completed", []))
    status = job["status"]
    result = _json(job.get("result"))
    return {"jobId": job["job_id"], "scope": p["scope"], "target": p.get("target"),
        "steps": p["steps"], "completed": completed, "cursor": len(completed),
        "status": (result.get("status", "complete") if status == "succeeded" else
                   "stopped" if status in ("failed", "cancelled", "recovery_required") else "running"),
        "error": job.get("last_error_code"), "detail": ev.get("detail"),
        "skipped": ev.get("skipped", {}), "mergeId": ev.get("merge_id"),
        "jobStatus": status, "childJobId": ev.get("child_id")}


def latest(group_id):
    rows = _jobs(group_id)
    active = [j for j in rows if j["status"] not in jobs.TERMINAL_STATUSES]
    return view(max(active or rows, key=lambda j: _json(j.get("payload")).get("sequence", 0))) if rows else None


def start(group_id, *, scope, target=None, message="", provider_id=None,
          paths=None, all=False, request_id, resume_from=None, commit_remaining=False):
    if scope not in STEPS or bool(paths is not None) == bool(all) or (paths is not None and not paths):
        raise GitServiceError(422, "invalid_request", "Choose a scope and files")
    project_id = gs._project_of_group(group_id)
    install()
    outcome, lock = lock_manager.acquire_group(project_id, group_id, holder_kind="source_mutation")
    if not outcome.ok:
        raise GitServiceError(409, "git_busy", "Another Git operation is in progress")
    try:
        rows = _jobs(group_id)
        request = dict(group_id=group_id, scope=scope, target_branch=target, message=message,
                       provider_id=provider_id, paths=paths, all=all, resume_from=resume_from,
                       commit_remaining=commit_remaining, client_request_key=request_id)
        key = store.derive_request_key(KIND, project_id, request)
        existing = jobs.get_job_by_key(project_id, KIND, store._sha256_hex(key))
        if existing:
            if existing["request_fingerprint"] != store.fingerprint(KIND, project_id, request):
                raise GitServiceError(409, "request_key_conflict", "Request identity has different inputs")
            return view(existing)
        active = [j for j in rows if j["status"] not in jobs.TERMINAL_STATUSES]
        if active:
            # Duplicate clicks / another browser attach to the run that already owns this Flow.
            return view(active[-1])
        completed = []
        skipped = {}
        retry_sync = False
        if resume_from:
            old = store.get_job(resume_from)
            if not old or old.get("group_id") != group_id or old.get("kind") != KIND or old["status"] not in jobs.TERMINAL_STATUSES:
                raise GitServiceError(409, "invalid_execution", "Execution cannot be resumed")
            previous = _json(old["payload"])
            scope, target = previous["scope"], previous.get("target")
            completed = list(_json(old.get("evidence")).get("completed", []))
            skipped = _json(old.get("evidence")).get("skipped", {})
            retry_sync = old.get("last_error_code") == "push_rejected"
            if commit_remaining:
                completed = []
        state = manual.worktree_status(group_id)
        if not state["worktree_available"]:
            raise GitServiceError(409, "git_unavailable", "Worktree is unavailable")
        if scope != "commit" and not target:
            target = manual_merge.merge_view(group_id)["default_target"]
        payload = dict(scope=scope, target=target, steps=STEPS[scope], message=message,
            provider_id=provider_id, selection={"paths": paths} if paths is not None else {"all": True},
            worktree_token=state["worktree_token"], completed=completed, skipped=skipped, retry_sync=retry_sync,
            sequence=max((_json(j.get("payload")).get("sequence", 0) for j in rows), default=0)+1)
        cr = store.create_or_get_job(KIND, project_id, request, group_id=group_id,
                                    payload=payload, wake=False)
        if not cr.ok:
            raise GitServiceError(503, "job_store_unavailable", "Could not register execution")
    finally:
        lock_manager.release(lock, outcome.lock_key)
    store.notify_created(cr.job)
    return view(cr.job)


def _save(ctx, phase, ev):
    store.ensure_lease(ctx)
    store.job_write(ctx, phase=phase, evidence=ev, last_error_code=None)


def _wait(ctx, ev, code=None):
    # Polling children and human conflict review is not a failed attempt, nor a worker wait.
    store.job_write(ctx, evidence=ev, status="retry_wait", last_error_code=code,
        available_at=instance_registry.add_seconds(now_iso(), 3),
        lease_owner=None, lease_token=None, lease_until=None)


def _fail(code):
    raise GitServiceError(409, code, code)


def _advance(ctx, ev, step):
    ev["completed"] = [*ev.get("completed", []), step]
    for key in ("child_id", "child_payload", "merge_id", "commit", "update", "detail"):
        ev.pop(key, None)
    _save(ctx, "next", ev)


def _commit(ctx, p, ev):
    gid = ctx.job["group_id"]
    if ctx.job["phase"] != "commit_apply":
        state = manual.worktree_status(gid)
        if not state["worktree_available"]:
            _fail("git_unavailable")
        if not state["counts"]["changed"]:
            return
        if state["worktree_token"] != p["worktree_token"]:
            _fail("worktree_changed")
        selected = p["selection"]
        if "paths" in selected:
            excluded = [f["path"] for f in state["files"] if f["staged"] and f["path"] not in selected["paths"]]
            if excluded:
                manual.unstage(gid, paths=excluded, worktree_token=state["worktree_token"])
                state = manual.worktree_status(gid)
        manual.stage(gid, **selected, worktree_token=state["worktree_token"])
        state = manual.worktree_status(gid)
        if not state["counts"]["staged"]:
            _fail("nothing_staged")
        message = p["message"].strip()
        if not message:
            if not p.get("provider_id"):
                _fail("commit_message_required")
            suggestion = commit_suggest.suggest(gid, provider_id=p["provider_id"])
            if suggestion["index_token"] != state["index_token"]:
                _fail("worktree_changed")
            message = suggestion["suggested"].strip()
        ev["commit_message"] = message
        ev["commit_index"] = state["index_token"]
    def before_commit(root, snapshot):
        ev["commit"] = {"head": snapshot.head, "tree": manual._git(root, ["write-tree"]).strip()}
        _save(ctx, "commit_apply", ev)
    manual.commit(gid, message=ev["commit_message"], index_token=ev["commit_index"],
                  before_commit=before_commit)


def _update(ctx, ev):
    if ctx.job["phase"] == "update_record":
        proof = ev["update"]
        with manual.mutation_context(ctx.job["group_id"]) as (pid, cfg, _, root):
            store.ensure_lease(ctx)
            scope_base.record_update(pid, ctx.job["group_id"], root,
                source_ref=proof["source_ref"], source_sha=proof["source_sha"],
                floor_before=proof["floor_before"], path="clean", config=cfg)
        return {"status": "updated"}
    def before_update(evidence):
        ev["update"] = evidence
        _save(ctx, "update_apply", ev)
    return finalize.update_from_base(ctx.job["group_id"], require_clean=True,
                                     before_update=before_update)["result"]


def _sync(ctx, p, ev):
    from flowgate.gate_decisions import manual_sync
    def started(proof):
        ev["sync"] = proof
        _save(ctx, "sync_apply", ev)
    result = manual_sync.sync(ctx.job["group_id"], p["target"], on_started=started)
    if result["status"] == "conflict":
        ev.update(merge_id=result["merge_id"], sync_conflict=True)
        _save(ctx, "conflict", ev)
        _wait(ctx, ev, "merge_conflict")
        return False
    ev["sync_done"] = True
    _save(ctx, "push", ev)
    return True


def _merge_result(group_id, target):
    """The target's latest completed manual merge row, or None when the Flow never merged into it."""
    return next((r for r in manual_merge.merge_results_view(group_id)["results"]
                 if r["target"]["target_branch"] == target), None)


def _child(ctx, p, ev, step):
    gid = ctx.job["group_id"]
    if "child_payload" not in ev:
        if step == "merge":
            state = manual_merge.merge_view(gid, p["target"])
            if state["ahead_of_target"] == 0:
                return {}
            if not state["can_merge"]:
                _fail((state["blocked"] or [{"code": "merge_blocked"}])[0]["code"])
            child = dict(action=manual_merge.MANUAL_MERGE, git_action="merge_only", target_branch=p["target"])
        else:
            remote_refresh.fetch_refs(ctx.job["project_id"], gid)
            row = _merge_result(gid, p["target"])
            if row is None:
                # Never merged into the target: nothing ahead means nothing to push, not an unreadable status.
                if manual_merge.merge_view(gid, p["target"])["ahead_of_target"] == 0:
                    return {}
                _fail("merge_result_missing")
            if not row["remote"]["measured"]:
                _fail("status_unavailable")
            if row["remote"]["contains_merge_commit"] and row["remote"]["local_ahead_count"] == 0:
                return {}
            if not row["push_available"]:
                _fail(row["push_blocked_reason"] or "push_blocked")
            child = dict(action=manual_merge.MERGE_RESULT_PUSH, group_id=gid,
                merge_id=row["merge_id"], merge_commit=row["merge_commit"], target_branch=p["target"],
                is_project_base=row["target"]["is_project_base"])
        ev["child_payload"] = child
        _save(ctx, "child", ev)
    child = ev["child_payload"]
    cr = store.create_or_get_job(base_publish.KIND, ctx.job["project_id"],
        {"action": child["action"], "source_identity": f"execution:{ctx.job_id}:{step}"},
        group_id=gid, payload=child)
    if not cr.ok:
        _fail("job_store_unavailable")
    job = cr.job
    ev["child_id"] = job["job_id"]
    if job["status"] not in jobs.TERMINAL_STATUSES:
        _wait(ctx, ev, job.get("last_error_code"))
        return None
    if job["status"] != "succeeded":
        _fail(job.get("last_error_code") or "request_failed")
    return _json(job.get("result")).get("result", {})


def _remaining(ctx, p, ev):
    gid = ctx.job["group_id"]
    state = manual.worktree_status(gid)
    remains = any(state["counts"].get(k) != 0 for k in ("changed", "staged", "hidden", "managed_dirty"))
    if p["scope"] != "commit":
        state = manual_merge.merge_view(gid, p["target"])
        remains |= state["ahead_of_target"] != 0 or (not ev.get("skipped", {}).get("update") and state["work_base_behind"] != 0)
    if p["scope"] == "push":
        remote_refresh.fetch_refs(ctx.job["project_id"], gid)
        row = _merge_result(gid, p["target"])
        # No merge result: commits not merged yet are already counted by ahead_of_target above.
        if row is not None:
            remote = row["remote"]
            remains |= (not remote["measured"] or remote["contains_merge_commit"] is not True
                        or remote["local_ahead_count"] != 0)
    return bool(remains)


def run(ctx, entry):
    p, ev = _json(ctx.job["payload"]), _json(ctx.job.get("evidence"))
    ev.setdefault("completed", p.get("completed", []))
    ev.setdefault("skipped", p.get("skipped", {}))
    step = None
    try:
        if ctx.job["phase"] == "conflict":
            session = gs.db_git.get_session(ev["merge_id"])
            if not session or session["status"] not in ("open", "done"):
                _fail("merge_aborted")
            if session["status"] == "open":
                _wait(ctx, ev, "merge_conflict")
                return
            if ev.pop("sync_conflict", False):
                ev["sync_done"] = True
                ev.pop("merge_id", None)
                _save(ctx, "push", ev)
            else:
                _advance(ctx, ev, p["steps"][len(ev["completed"])])
        while len(ev["completed"]) < len(p["steps"]):
            store.ensure_lease(ctx)
            step = p["steps"][len(ev["completed"])]
            if ctx.job["phase"] in ("start", "next"):
                _save(ctx, step, ev)
            result = {}
            if step == "commit":
                _commit(ctx, p, ev)
            elif step == "update":
                try:
                    result = _update(ctx, ev)
                except GitServiceError as exc:
                    if exc.code not in UPDATE_SKIP:
                        raise
                    ev.setdefault("skipped", {})["update"] = exc.code
            else:
                if step == "push" and p.get("retry_sync") and not ev.get("sync_done"):
                    if not _sync(ctx, p, ev):
                        return
                result = _child(ctx, p, ev, step)
                if result is None:
                    return
            if result.get("status") == "conflict":
                ev["merge_id"] = result["merge_id"]
                _save(ctx, "conflict", ev)
                _wait(ctx, ev, "merge_conflict")
                return
            _advance(ctx, ev, step)
        _save(ctx, "verify", ev)
        result = {"status": "remaining" if _remaining(ctx, p, ev) else "complete"}
        return store.RunResult(store.SUCCESS, result=result)
    except GitServiceError as exc:
        if exc.code in WAIT_CODES:
            _wait(ctx, ev, exc.code)
            return
        store.job_write(ctx, evidence=ev)
        return store.RunResult(store.PERMANENT, code=exc.code)


def decide(job):
    phase, gid = job["phase"], job["group_id"]
    ev = _json(job.get("evidence"))
    if phase == "sync_apply":
        session = gs.db_git.get_session(ev["sync"]["merge_id"])
        if session and session["status"] == "done":
            return runner.Decision(runner.APPLIED_NEXT, next_phase="push", evidence={"sync_done": True})
        if session and session["status"] == "open":
            from . import merge_target
            if merge_target.attempt_phase(session) == merge_target.PHASE_CONFLICT:
                return runner.Decision(runner.APPLIED_NEXT, next_phase="conflict",
                    evidence={"merge_id": session["merge_id"], "sync_conflict": True})
        return runner.Decision(runner.UNDECIDABLE, code="sync_evidence_unsettled")
    if phase not in ("commit_apply", "update_apply", "update_record"):
        return runner.Decision(runner.NOT_APPLIED)
    _, _, state, root = manual._context(gid)
    head = manual._git(root, ["rev-parse", "HEAD"]).strip()
    if phase == "commit_apply":
        proof = ev["commit"]
        if head == proof["head"]:
            snapshot = manual.read_index_snapshot(gid)
            if snapshot.index_token == ev["commit_index"]:
                return runner.Decision(runner.NOT_APPLIED)
        elif (manual._git(root, ["show", "-s", "--format=%P", head]).strip() == proof["head"]
              and manual._git(root, ["rev-parse", "HEAD^{tree}"]).strip() == proof["tree"]):
            return runner.Decision(runner.APPLIED_NEXT, next_phase="next",
                evidence={"completed": [*ev.get("completed", []), "commit"]})
    else:
        proof = ev["update"]
        session = gs.db_git.get_open_session_by_group(gid)
        if session and gs.db_git.session_context(session).get("source_sha") == proof["source_sha"]:
            return runner.Decision(runner.APPLIED_NEXT, next_phase="conflict", evidence={"merge_id": session["merge_id"]})
        if (state.get("work_base_sync_sha") == proof["source_sha"]
                and scope_base.is_ancestor(root, proof["source_sha"], head)):
            return runner.Decision(runner.APPLIED_NEXT, next_phase="next",
                evidence={"completed": [*ev.get("completed", []), "update"]})
        if (not session and not gs._rev_parse(root, "MERGE_HEAD")
                and manual._git(root, ["show", "-s", "--format=%P", head]).strip().split()
                    == [proof["head"], proof["source_sha"]]):
            return runner.Decision(runner.APPLIED_NEXT, next_phase="update_record")
        if head == proof["head"] and not session and not gs._rev_parse(root, "MERGE_HEAD") and not gs._unmerged_paths(root):
            return runner.Decision(runner.NOT_APPLIED, resume_phase="update")
    return runner.Decision(runner.UNDECIDABLE, code="execution_evidence_changed")


def install():
    base_publish.install()
    runner.register_executor(KIND, run)
    for phase in ("start", "next", "commit", "commit_apply", "update", "update_apply", "merge", "push", "child", "conflict", "verify", "sync_apply", "update_record"):
        runner.register_decider(KIND, phase, decide)

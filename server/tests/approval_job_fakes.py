"""Final-approval orchestration on the real job layer (flowgate.default.0669).

The approval is a ``final_approval_publish`` job (units 6a/6b). The suites that used to mock the
project mutex around ``_orchestrate_final_approval`` now run the real route function, the real
``approval_publish.start``, the real job store / lock manager on SQLite (``group_store``).
Only the parts that need a repository are replaced, and they are named here so the limits stay
visible:

* ``approval_freeze.run_freeze``: F1~F5 touch Git (absorb commit, pin ref). The stand-in writes
  the same F5 end state to the DB (claim ``publish_wait``, sha/tree/pin recorded). The real F1~F4
  run in ``test_git_real_bodies_0669``.
* ``approval_freeze.release_pin``: the stand-in freeze made no pin ref, so the release of a failed
  approval finds nothing to delete (the real conditional pin delete runs in ``test_git_real_bodies_0669``).
* ``git_service.run_approve_git_action``: the Git body. Each test scripts its outcome; the stand-in
  records which locks the job holds at that moment.
* ``pipeline_service.commit_final_approval``: the AC/root transition. The stand-in runs the real
  ``consume_hook`` inside a real transaction, like the original does.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from group_lock_stub import held_locks

NOW = "2026-10-05T12:00:00+09:00"


def seed_group(store, project_id: str, group_id: str) -> None:
    """The rows the freeze claim and the job need (group + its Git ledger row)."""
    with store.transaction():
        store._execute("DELETE FROM group_git_state WHERE group_id = ?", [group_id])
        store._execute("DELETE FROM groups WHERE group_id = ?", [group_id])
        store._execute(
            "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, ?, 'default', ?, 'OPEN', ?, ?)", [group_id, project_id, group_id, NOW, NOW])
        store._execute(
            "INSERT INTO group_git_state (group_id, project_id, branch, worktree_registered, status, "
            "created_at, updated_at) VALUES (?, ?, ?, 1, 'awaiting_choice', ?, ?)",
            [group_id, project_id, group_id.replace(".", "_"), NOW, NOW])


def install(monkeypatch, store, *, project_id: str, group_id: str, doc: dict) -> SimpleNamespace:
    """Wire the real route to the real job layer; returns ``fx`` with ``events`` and ``script``.

    ``fx.script``: ``outcome`` (what the Git body answers), ``commit`` (None = succeed, or an
    exception to raise from the approval transaction)."""
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import approval_freeze as af
    from modules.flow_gate.services.git import approval_publish as ap
    from modules.flow_gate.services.git import job_store as jobs
    from modules.flow_gate.services.git import merge_target
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.workflow.routers import workflow

    seed_group(store, project_id, group_id)
    fx = SimpleNamespace(events=[], held_at_git=[], commits=[], script={"outcome": None, "commit": None},
                         clean_retry=None, consumed=[], git_actions=[], completed=[])

    def fake_freeze(ctx, origin="request"):
        with store.transaction():
            af.set_freeze_claim(ctx, "publish_wait")
            enc = jobs.fenced_write(ctx, dict(
                phase="publish_pending", freeze_completed=1, frozen_sha="a" * 40, frozen_tree="b" * 40,
                pin_ref=af.pin_ref_name(ctx.job_id), attempt_count=0,
                status="running" if origin == af.REQUEST else "pending"),
                expected=af._FREEZING, allow_freeze=True, update_local=False)
        ctx.job.update(enc)
        fx.events.append("freeze")
        return af.FROZEN

    def fake_git(group, action, *args, approval_context=None, **kw):
        fx.events.append("git")
        fx.git_actions.append(action)
        fx.held_at_git.append(sorted((r["domain"], r["holder_kind"]) for r in held_locks(project_id)))
        assert approval_context is not None and approval_context.frozen_sha == "a" * 40
        return fx.script["outcome"]

    def fake_commit(doc_id, actor_user_id, user_permissions, locale, consume_hook):
        fx.events.append("approval")
        failure = fx.script["commit"]
        if failure is not None:
            raise failure
        with store.transaction():
            assert consume_hook(None, None) is not False
        fx.commits.append(doc_id)
        return {"document": {**doc, "doc_review_status": "approved"},
                "root": {"doc_review_status": "wf_done"}}

    monkeypatch.setattr(af, "run_freeze", fake_freeze)
    # the stand-in freeze never made a pin ref, so releasing it has nothing to delete
    monkeypatch.setattr(af, "release_pin", lambda ctx: (af.CLEARED, None))
    monkeypatch.setattr(gs, "run_approve_git_action", fake_git)
    monkeypatch.setattr(pipeline_service, "commit_final_approval", fake_commit)
    monkeypatch.setattr(gs, "_project_of_group", lambda g: project_id)
    monkeypatch.setattr(gs.db_git, "get_config", lambda _p: {})
    def complete(group, action, outcome, *, approved):
        fx.events.append("complete")
        fx.completed.append((action, approved))

    monkeypatch.setattr(gs, "complete_approve_git_action", complete)
    monkeypatch.setattr(gs, "realize_wf_done_transition", lambda *a, **k: fx.events.append("realize"))
    monkeypatch.setattr(merge_target, "plan_finalize_target",
                        lambda *a, **k: SimpleNamespace(is_project_base=True, target_branch="main"))
    monkeypatch.setattr(ap.approval_intent, "find_clean_retry", lambda g: fx.clean_retry or (None, None))
    monkeypatch.setattr(ap.approval_intent, "consume_clean_retry",
                        lambda g, intent_id: fx.consumed.append((g, intent_id)) or True)
    # the route's own collaborators that need other tables
    monkeypatch.setattr(workflow, "_guard_group_not_disposed", lambda *a: None)
    monkeypatch.setattr(workflow, "_guard_group_not_ai_running", lambda *a: None)
    monkeypatch.setattr(workflow.db_docs, "get_by_id", lambda doc_id: dict(doc))
    monkeypatch.setattr(workflow, "precheck_document_review_transition",
                        lambda **kw: fx.events.append("precheck") or {"document": dict(doc)})
    monkeypatch.setattr(workflow.git_service, "precheck_approve_git_action", lambda d, a: group_id)
    monkeypatch.setattr(ap.db_documents, "get_by_id", lambda doc_id: dict(doc))
    ap.install()
    return fx


def approve(doc_id: str, user: dict, git_action: str = "merge"):
    """Drive the real final-approval route; (status, payload)."""
    from modules.flow_gate.workflow.routers import workflow

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=doc_id, git_action=git_action), user, None))
    return response.status_code, json.loads(response.body.decode("utf-8"))


def job_of(group_id: str) -> dict:
    """The Group's newest final_approval_publish job, whatever its status."""
    from modules.flow_gate.db import operation_job as db_jobs

    jobs = db_jobs.jobs_of_group(group_id, "final_approval_publish", (
        "freezing", "freeze_wait", "pending", "running", "blocked", "retry_wait",
        "recovery_required", "succeeded", "failed", "cancelled"))
    return jobs[-1]

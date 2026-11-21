"""Git integration 0115: independently selectable scenario module."""
from __future__ import annotations

from test_git_integration_0115 import (
    Path,
    _API_0594_OURS,
    _API_0594_PATHS,
    _API_0594_THEIRS,
    _FILE_CLONE,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _ScriptedConflictModel0608,
    _TMP_STORAGE,
    _api_conflict_run_0608,
    _checkout_0608,
    _chunk_sides_0608,
    _git,
    _git_can_clone_local_file_url,
    _history_blob_0608,
    _ident_of,
    _index_conflict_stages_0608,
    _make_untracked_collision,
    _migration_applied,
    _node_syntax_checker_available,
    _real_reads_for_conflict_0608,
    _resolve_token_over_urlopen_0608,
    _seed_wf_done_root,
    _session_json_0608,
    _unmerged_0608,
    act_origin,
    adopt_origin,
    api_conflict_origin_0608,
    author_origin,
    base64,
    base_origin,
    contextmanager,
    grpexp_origin,
    held_locks,
    hold_lock,
    lm,
    needs_git,
    needs_node_syntax,
    noop_origin,
    nowork_surface_origin,
    origin_repo,
    os,
    patch_store,
    project_scoped_admission_origin,
    pytest,
    resolve_base_dirty_origin,
    seed,
    shutil,
    sqlite3,
    subprocess,
    sys,
    tempfile,
    terminal_reopen_origin,
    tmp_db,
    untracked_conflict_origin,
    untracked_origin,
    untracked_remove_origin,
)

from test_git_integration_0115 import TestGitEndToEnd as _Scenario


class TestGitEndToEnd(_Scenario):
    def test_review_gate_write_plan_stale_run_discarded_when_approved_while_active(
        self, origin_repo, monkeypatch,
    ):
        # AI review finding: the review design requires a
        # re-instruction run's result to be discarded as `stale_run` when the
        # review it was launched against is no longer the one on screen. A human
        # can approve the CURRENTLY pending snapshot while a conversation run
        # (started against that same snapshot) is still in flight — approval
        # does not change review_fingerprint/instruction_generation at all, only
        # review_state, so a guard that only compared fingerprint/generation
        # would miss this race entirely and let the run's late write plan try to
        # apply against an already-merged session.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0140"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0140")
        (wt / "shared.py").write_text('"stale approve group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"stale approve mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "stale approve mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"stale approve group version"\n"stale approve mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 고쳐서 적용해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_stale_approve_race",
        )
        assert sent["result"]["status"] == "accepted"

        # the human approves the CURRENT (still pending) snapshot while that run
        # is still active — approval never checks pending_conversation_run_id.
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"
        merged_head = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()

        # only now does the run finish, with a write plan that (if applied)
        # would edit shared.py again on top of the already-merged commit.
        manifest_entry = next(
            e for e in db_git.session_context(db_git.get_session(merge_id))["snapshot_manifest"]
            if e["path"] == "shared.py"
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1", "kind": "edit", "path": "shared.py",
                "expected_before_blob": manifest_entry["oid"],
                "anchor": {
                    "body_base64": base64.b64encode(b'"stale approve group version"\n').decode("ascii"),
                    "expected_count": 1,
                },
                "replacement_bytes_base64": base64.b64encode(b'"this must never land"\n').decode("ascii"),
                "purpose": "must be discarded as stale_run, not applied",
            }],
            "held_test_operations": [],
        }
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "수정했습니다.", "provider_id": "prov_test",
                "write_plan": plan,
            },
        )

        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "completed"
        assert review["review_fingerprint"] == fingerprint  # untouched by the discarded run
        roles = [(t["role"], t["status"]) for t in review["conversation"]]
        assert ("ai", "stale_run") in roles
        assert not any(t["role"] == "ai" and t["status"] == "accepted" for t in review["conversation"])
        # After a rejected review: the PLAN is still discarded, but the ANSWER is
        # kept. Before this the operator got only "the approval target changed, instruct
        # again" -- twice in the rejected transcript -- and never saw what the run said.
        stale = next(t for t in review["conversation"] if t["status"] == "stale_run")
        # The answer is CONTENT and stays byte-for-byte; the three things
        # rev6 added to the sentence (which check fired, that it answers an older
        # candidate, that the plan was discarded) are now params, so they survive a locale
        # change instead of being frozen in Korean.
        assert stale["message"] == "수정했습니다."
        assert stale["message_code"] == "review_stale_run"
        assert "approval_settled" in stale["message_params"]["changed"]
        assert stale["message_params"]["plan_discarded"] is True

        # the plan never touched the tree: mainline still has exactly the commit
        # approval created, and no new candidate/generation was minted.
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() == merged_head
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context.get("pending_conversation_run_id") is None
        assert int(context.get("instruction_generation") or 0) == 0



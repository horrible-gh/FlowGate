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
    def test_review_gate_write_plan_allow_test_edits_second_authorization_applies_test_edit(self, origin_repo, monkeypatch):
        # 0009-TR rev3 (AI review finding 2): the human's second explicit action
        # (the "re-instruct including test edits" action) starts a run with
        # allow_test_edits=true, and ONLY such a run may put a test-path
        # operation directly in operations[] — where it goes through the exact
        # same anchored-apply/rollback engine as any product-code edit.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0139"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0139")
        (wt / "shared.py").write_text('"testedit group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"testedit mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "testedit mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"testedit group version"\n"testedit mainline version"\n',
        }], True)
        fingerprint = db_git.session_context(db_git.get_session(merge_id))["review_fingerprint"]
        sent = svc.send_review_message(
            group, merge_id, message="이번엔 테스트 편집도 포함해서 다시 해줘",
            provider_id="prov_test", provider_pinned=True,
            apply_requested=True, allow_test_edits=True,
            start_run=lambda: "aiv_test_edit_run",
        )
        assert sent["result"]["status"] == "accepted"
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["pending_conversation_allow_test_edits"] is True

        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "server/tests/allowed_case.py",
                "absent": True,
                "content_bytes_base64": base64.b64encode(b'"now allowed"\n').decode("ascii"),
                "mode": "100644", "purpose": "regression case for the second-authorization path",
            }],
            "held_test_operations": [],
        }
        svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_test_edit_run")
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan},
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "re_review"
        assert any(c["path"] == "server/tests/allowed_case.py" for c in review["changes"])
        assert not review["held_test_operations"]

        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=review["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"



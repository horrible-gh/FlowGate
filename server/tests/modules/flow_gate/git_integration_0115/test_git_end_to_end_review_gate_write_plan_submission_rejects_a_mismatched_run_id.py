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
    def test_review_gate_write_plan_submission_rejects_a_mismatched_run_id(self, origin_repo):
        # 0009-TR rev3 (AI review finding 1): action_scope/group_id/merge_id
        # alone bind a resolve_conflict token to the MERGE, not to the specific
        # pending write TURN — a still-valid token for a stale/different run
        # must not be able to attach a plan to the CURRENT pending write turn.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0135"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0135")
        (wt / "shared.py").write_text('"mismatch group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"mismatch mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "mismatch mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"mismatch group version"\n"mismatch mainline version"\n',
        }], True)
        svc.send_review_message(
            group, merge_id, message="고쳐줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_real_run",
        )
        context_before = db_git.session_context(db_git.get_session(merge_id))
        assert context_before["pending_conversation_run_id"] == "aiv_real_run"

        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": context_before["review_fingerprint"],
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "unrelated.py",
                "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
            }],
            "held_test_operations": [],
        }
        # a stale/different run's own token (its ai_run_id does not match the
        # session's CURRENT pending write turn) is rejected before the plan is
        # ever recorded.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_stale_run")
        assert exc.value.status == 403
        assert exc.value.code == "write_plan_run_mismatch"

        # a token with NO ai_run_id claim at all (should not happen for a real
        # resolve_conflict token, but must not be treated as a wildcard match)
        # is rejected the same way.
        with pytest.raises(svc.GitServiceError) as exc2:
            svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id=None)
        assert exc2.value.code == "write_plan_run_mismatch"

        # the session's pending write turn is untouched by either rejected attempt.
        context_after = db_git.session_context(db_git.get_session(merge_id))
        assert context_after["pending_conversation_run_id"] == "aiv_real_run"

        svc.abort_merge(group, merge_id)



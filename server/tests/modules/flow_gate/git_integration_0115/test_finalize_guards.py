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

from test_git_integration_0115 import TestFinalizeGuards as _Scenario


class TestFinalizeGuards(_Scenario):
    def test_non_integrated_group(self, seed):
        from modules.flow_gate.services import git_service as svc

        state = svc.get_finalize_state("plainprj.default.0001")
        assert state["state"]["status"] == "none"
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize("plainprj.default.0001", "merge")
        assert exc.value.status == 409


    def test_preview_flag_reflects_preview_ac(self, seed):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitprj.default.0101"
        svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "enabled": True,
        })
        db_git.register_worktree(group, "gitprj", "gitprj_default_0101")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        state = svc.get_finalize_state(group, preview_ac=True)

        assert state["state"]["status"] == "awaiting_choice"
        assert state["state"]["preview"] is True


    def test_preview_ac_read_only_with_wf_in_progress_root(self, seed):
        """Test7: preview_ac shows preliminary awaiting_choice while
        root is wf_in_progress, without DB changes (pure read).

        Setup: Root = wf_in_progress (not wf_done), Git status = none.
        Call: GET /finalize?context=approval (preview_ac=True).
        Expect: response shows awaiting_choice display-only, but DB status
                remains none. No side effects on group_git_state.
        """
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import groups as db_groups
        from modules.flow_gate.services import git_service as svc

        group = "gitprj.default.0102"
        project_id = "gitprj"

        svc.save_config(project_id, {
            "repo_url": "https://example.com/team/repo.git",
            "enabled": True,
        })

        # Create group and root document with wf_in_progress (NOT wf_done)
        if db_groups.get_by_id(group) is None:
            db_groups.create({
                "group_id": group, "project_id": project_id,
                "module": "default", "title": "test root",
            })

        doc_id = f"{group}.0001-R"
        if db_docs.get_by_id(doc_id) is None:
            db_docs.create({
                "doc_id": doc_id, "project_id": project_id, "module": "default",
                "group_id": group, "type_code": "R", "seq": 1, "title": "root",
                "file_path": f"documents/{group}/0001-R.md",
            })
        # Set root status to wf_in_progress (not wf_done)
        db_docs.update(doc_id, {"doc_review_status": "wf_in_progress"})

        # Register worktree and set git status to none
        db_git.register_worktree(group, project_id, f"{project_id}_default_0102")
        db_git.set_status(group, "none")

        # Verify _group_root_wf_done returns False (precondition)
        from modules.flow_gate.services.git_service import _group_root_wf_done
        assert not _group_root_wf_done(group), "root should not be wf_done"

        # Capture persisted status before preview call
        before_status = db_git.get_state(group)["status"]
        assert before_status == "none"

        # Call get_finalize_state with preview_ac=True
        state = svc.get_finalize_state(group, preview_ac=True)

        # Display status shows preliminary awaiting_choice (display-only)
        assert state["state"]["status"] == "awaiting_choice"
        assert state["state"]["preview"] is True

        # Verify choices are populated (actionable=True for display)
        assert len(state["state"]["choices"]) > 0

        # Verify DB status unchanged (pure read, no side effects)
        after_status = db_git.get_state(group)["status"]
        assert after_status == "none"
        assert after_status == before_status



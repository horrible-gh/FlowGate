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

from test_git_integration_0115 import TestStatus0162NoGit as _Scenario


class TestStatus0162NoGit(_Scenario):
    def test_disabled_project(self, seed):
        from modules.flow_gate.services import git_service as svc

        out = svc.project_git_status("plainprj")["status"]
        assert out["enabled"] is False
        assert out["pending_count"] == 0
        assert out["slots"] == [] and out["pending"] == []
        assert out["base_branch"] is None


    def test_unknown_project_404(self, seed):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.project_git_status("ghost")
        assert exc.value.status == 404


    def test_precheck_rejections(self, seed):
        from modules.flow_gate.services import git_service as svc

        # bad action value
        with pytest.raises(svc.GitServiceError) as e1:
            svc.precheck_approve_git_action(
                {"type_code": "AC", "group_id": "plainprj.default.0001"}, "frobnicate"
            )
        assert e1.value.status == 422
        # non-AC document
        with pytest.raises(svc.GitServiceError) as e2:
            svc.precheck_approve_git_action(
                {"type_code": "D", "group_id": "plainprj.default.0001"}, "merge"
            )
        assert e2.value.status == 422
        # AC but the group has no git config / worktree
        with pytest.raises(svc.GitServiceError) as e3:
            svc.precheck_approve_git_action(
                {"type_code": "AC", "group_id": "plainprj.default.0001"}, "merge"
            )
        assert e3.value.status == 422


    def test_run_action_never_raises(self, seed):
        from modules.flow_gate.services import git_service as svc

        # finalize on a non-integrated group raises 409 internally; the approve
        # wrapper must swallow it into {ok: false} (D §3.1 — approval stands).
        out = svc.run_approve_git_action("plainprj.default.0001", "merge")
        assert out["ok"] is False
        assert out["error"]["code"]



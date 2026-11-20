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

from test_git_integration_0115 import TestAutoCommitMessage as _Scenario


class TestAutoCommitMessage(_Scenario):
    def test_requirement_with_design_doc_is_feat(self, seed):
        from modules.flow_gate.services import git_service as svc

        group_id = "gitprj.default.0168"
        self._create_group(group_id, "깃 커밋 메세지", ["R", "D"])

        assert svc.build_auto_commit_message(group_id) == (
            "feat: 깃 커밋 메세지"
        )


    def test_bug_root_is_fix_even_with_design_doc(self, seed):
        from modules.flow_gate.services import git_service as svc

        group_id = "gitprj.default.0169"
        self._create_group(group_id, "로그인 오류 수정", ["B", "D"])

        assert svc.build_auto_commit_message(group_id) == (
            "fix: 로그인 오류 수정"
        )


    def test_requirement_without_design_doc_is_chore(self, seed):
        from modules.flow_gate.services import git_service as svc

        group_id = "gitprj.default.0170"
        self._create_group(group_id, "문서 정리", ["R", "TR"])

        assert svc.build_auto_commit_message(group_id) == (
            "chore: 문서 정리"
        )


    def test_missing_metadata_uses_conventional_fallback(self, seed):
        from modules.flow_gate.services import git_service as svc

        assert svc.build_auto_commit_message("gitprj.default.9999") == (
            "chore: finalize workflow changes"
        )


    def test_resolver_ascii_title_omits_group_scope(self, seed):
        from modules.flow_gate.services import git_service as svc

        group_id = "gitprj.default.0171"
        self._create_group(group_id, "Polish git finalize subject", ["B", "TR"])

        assert svc.resolve_commit_message(group_id) == (
            "fix: Polish git finalize subject",
            "auto_title",
        )


    def test_resolver_fallback_omits_group_scope(self, seed):
        from modules.flow_gate.services import git_service as svc

        assert svc.resolve_commit_message("gitprj.default.9998") == (
            "chore: finalize workflow changes",
            "fallback",
        )



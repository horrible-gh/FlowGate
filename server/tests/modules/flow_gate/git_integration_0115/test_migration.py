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

from test_git_integration_0115 import TestMigration as _Scenario


class TestMigration(_Scenario):
    def test_tables_exist(self, tmp_db):
        assert _migration_applied(tmp_db), "056_git_integration.sql did not apply"


    def test_grant_group_id_column(self, tmp_db):
        mock_db, _ = tmp_db
        cols = [r["name"] for r in mock_db.fetch_all("PRAGMA table_info(remote_tool_grant)")]
        assert "group_id" in cols


    def test_merge_session_finalize_action_column(self, tmp_db):
        mock_db, _ = tmp_db
        cols = [r["name"] for r in mock_db.fetch_all("PRAGMA table_info(git_merge_session)")]
        assert "finalize_action" in cols


    def test_project_git_config_author_columns(self, tmp_db):
        # 065_git_author.sql (0237) — configurable commit author
        mock_db, _ = tmp_db
        cols = [r["name"] for r in mock_db.fetch_all("PRAGMA table_info(project_git_config)")]
        assert "author_name" in cols and "author_email" in cols



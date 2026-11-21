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

from test_git_integration_0115 import TestFirstPushBootstrap0297 as _Scenario


class TestFirstPushBootstrap0297(_Scenario):
    def test_empty_remote_reports_bootstrap_fields(self, tmp_path):
        from modules.flow_gate.services import git_service as svc

        bare, base = self._bootstrap(tmp_path)
        assert not svc._ref_exists(base, "refs/remotes/origin/main")

        unpushed = svc._build_unpushed("gitprj", base, "main", None)
        assert unpushed["measured"] is False
        assert unpushed["commit_count"] == 0          # legacy field, unchanged
        assert unpushed["remote_branch_missing"] is True
        assert unpushed["local_commit_count"] == 1    # …but there IS work to push

        # The push the client used to hide is a plain success.
        _git(["push", "origin", "main"], cwd=base)
        assert "refs/heads/main" in _git(["ls-remote", "--heads", str(bare)])


    def test_measured_payload_keeps_shape(self, tmp_path):
        from modules.flow_gate.services import git_service as svc

        _bare, base = self._bootstrap(tmp_path)
        _git(["push", "origin", "main"], cwd=base)
        (base / "next.txt").write_text("more\n", encoding="utf-8")
        _git(["add", "-A"], cwd=base)
        _git(["commit", "-m", "second"], cwd=base)

        unpushed = svc._build_unpushed("gitprj", base, "main", None)
        assert unpushed["measured"] is True
        assert unpushed["commit_count"] == 1
        # Both bootstrap fields are always present so the client reads them
        # unconditionally; measured means the remote branch exists.
        assert unpushed["remote_branch_missing"] is False
        assert unpushed["local_commit_count"] is None


    def test_missing_checkout_is_not_reported_as_empty_remote(self, tmp_path):
        """Unmeasured has other causes (no checkout, git off). Those must NOT
        read as "empty remote", or the client would offer a first push with
        nothing to push."""
        from modules.flow_gate.services import git_service as svc

        unpushed = svc._build_unpushed("gitprj", tmp_path / "missing", "main", None)
        assert unpushed["measured"] is False
        assert unpushed["remote_branch_missing"] is False
        assert unpushed["local_commit_count"] is None



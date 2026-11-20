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

from test_git_integration_0115 import TestEffectiveSrcRoot as _Scenario


class TestEffectiveSrcRoot(_Scenario):
    def test_fallbacks(self, seed):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        # no group / no config
        assert svc.effective_src_root("gitprj", None) is None
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # config exists but disabled
        svc.save_config("gitprj", {"repo_url": "https://x/y.git", "enabled": False})
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # enabled but no worktree ledger entry
        svc.save_config("gitprj", {"repo_url": "https://x/y.git", "enabled": True})
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # ledger entry without a real directory → still fallback (E7/E13)
        db_git.register_worktree("gitprj.default.0001", "gitprj", "gitprj_default_0001")
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # directory appears → worktree wins
        from modules.flow_gate.storage.paths import src_root

        wt = src_root("GitProj", "gitprj_default_0001")
        wt.mkdir(parents=True, exist_ok=True)
        # 0287 NR0004: the `.git` link is what makes it a worktree rather than a
        # leftover directory from an interrupted teardown.
        (wt / ".git").write_text("gitdir: ../main/.git/worktrees/x", encoding="utf-8")
        resolved = svc.effective_src_root("gitprj", "gitprj.default.0001")
        assert resolved is not None and resolved.name == "gitprj_default_0001"
        # cleanup for later git e2e tests
        shutil.rmtree(wt)
        svc.delete_config("gitprj")


    def test_paths_resolver_group_param(self, seed):
        from modules.flow_gate.storage.paths import resolve_project_src_root

        # group-less call keeps the pre-0115 behavior (project branch folder)
        root = resolve_project_src_root("gitprj")
        assert root is not None and root.name == "main"
        # unknown group falls back identically
        with_group = resolve_project_src_root("gitprj", group_id="gitprj.default.9999")
        assert with_group == root



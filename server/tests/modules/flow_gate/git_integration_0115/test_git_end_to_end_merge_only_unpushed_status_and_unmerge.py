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
    def test_merge_only_unpushed_status_and_unmerge(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0105"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0105")
        (wt / "local-only.txt").write_text("not pushed yet\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "merge_only", commit_message="feat: local merge only")
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is False
        merge_commit = out["result"]["merge_commit"]

        assert "local-only.txt" in _git(["ls-tree", "--name-only", "main"], cwd=src_root("GitProj", "main"))
        assert "local-only.txt" not in _git(
            ["ls-tree", "--name-only", "main"], cwd=origin_repo["bare"]
        )

        status = svc.project_git_status("gitprj")["status"]
        unpushed = status["unpushed"]
        assert unpushed["count"] == 1
        assert unpushed["commit_count"] >= unpushed["count"]
        assert unpushed["merges"][0]["group_id"] == group
        assert unpushed["merges"][0]["can_unmerge"] is True

        undo = svc.unmerge(group, merge_commit)["result"]
        assert undo["unmerged"] is True
        assert undo["group_status"] == "awaiting_choice"
        assert undo["reprovisioned"] is True

        state = db_git.get_state(group)
        assert state["status"] == "awaiting_choice"
        assert state["merge_commit"] is None
        assert state["worktree_registered"] == 1
        assert (wt / "local-only.txt").read_text(encoding="utf-8") == "not pushed yet\n"
        assert "local-only.txt" not in _git(["ls-tree", "--name-only", "main"], cwd=src_root("GitProj", "main"))



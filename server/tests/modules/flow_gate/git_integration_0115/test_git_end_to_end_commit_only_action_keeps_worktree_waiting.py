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
    def test_commit_only_action_keeps_worktree_waiting(self, origin_repo):
        # `commit_only` commits dirty edits
        # locally without pushing or merging, and — unlike merge/push — must NOT
        # tear the slot down, since the group can still be merged/pushed/archived
        # later.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0108"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0108")
        (wt / "feature.txt").write_text("branch only\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "commit_only", commit_message="feat: commit only")
        assert out["result"]["status"] == "waiting"
        assert out["result"]["action"] == "commit_only"
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0108" not in heads

        state = db_git.get_state(group)
        assert state["worktree_registered"] == 1
        assert state["status"] == "waiting"
        assert _git(["status", "--porcelain"], cwd=wt).strip() == ""
        assert _git(["log", "-1", "--format=%s"], cwd=wt).strip() == "feat: commit only"

        # still finalize-able afterward (e.g. push it now) — commit_push does not
        # touch the shared base checkout, unlike merge/merge_only.
        pushed = svc.finalize(group, "commit_push")
        assert pushed["result"]["status"] == "pushed"
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0108" in heads



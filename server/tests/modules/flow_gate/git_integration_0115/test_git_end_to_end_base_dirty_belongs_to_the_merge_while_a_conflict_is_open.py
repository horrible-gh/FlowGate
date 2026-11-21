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
    def test_base_dirty_belongs_to_the_merge_while_a_conflict_is_open(self, origin_repo):
        """Why the "Hand to AI" action kept answering that it could not start.

        A stopped merge leaves its unmerged AND its cleanly-merged paths in the base
        checkout's `git status --porcelain`, so `base_dirty` fills up with the merge itself
        and the Git panel offers it as stray-edit cleanup: "Hand to AI", per-file
        "Revert", "Commit". All three are wrong there, and the AI one could never start.
        `base_dirty.merge_in_progress` is the fact the panel needs to say so instead.
        """
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0172"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "ownership.txt").write_text("base line\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add ownership base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0172")
        (wt / "ownership.txt").write_text("group change\n", encoding="utf-8")

        (seedwt / "ownership.txt").write_text("mainline change\n", encoding="utf-8")
        _git(["commit", "-am", "mainline change to ownership"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        # No merge yet: the base checkout is clean and owns nothing.
        assert svc.base_merge_in_progress("gitprj") is None

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        # The conflict really does show up as base-checkout dirt — this is the pile the
        # panel was inviting the operator to "clean up".
        base_dirty = svc.project_git_status("gitprj")["status"]["base_dirty"]
        assert "ownership.txt" in base_dirty["files"]
        assert base_dirty["merge_in_progress"] == {"merge_id": merge_id, "group_id": group}
        assert svc.base_merge_in_progress("gitprj") == {"merge_id": merge_id, "group_id": group}

        svc.abort_merge(group, merge_id)

        # Once the merge is gone the same files are ordinary base dirt again (or gone).
        assert svc.base_merge_in_progress("gitprj") is None
        assert svc.project_git_status("gitprj")["status"]["base_dirty"]["merge_in_progress"] is None



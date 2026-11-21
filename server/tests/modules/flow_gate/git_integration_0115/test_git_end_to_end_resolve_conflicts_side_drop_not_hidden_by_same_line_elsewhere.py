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
    def test_resolve_conflicts_side_drop_not_hidden_by_same_line_elsewhere(self, origin_repo):
        # The reverse defect found in the OLD validator: it
        # tested whole-file line membership, so a chunk that genuinely dropped one side
        # could slip through if that side's text happened to also appear, unrelated,
        # elsewhere in the same file. The check must be chunk-local.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0151"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "sidecheck3.txt").write_text(
            "group change\nnoise1\nplaceholder\nnoise2\n", encoding="utf-8",
        )
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add sidecheck3 base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0151")
        (wt / "sidecheck3.txt").write_text(
            "group change\nnoise1\ngroup change\nnoise2\n", encoding="utf-8",
        )

        (seedwt / "sidecheck3.txt").write_text(
            "group change\nnoise1\nmainline change\nnoise2\n", encoding="utf-8",
        )
        _git(["commit", "-am", "mainline change to sidecheck3"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        conflicts = svc.list_conflicts(group, merge_id)
        entry = next(f for f in conflicts["files"] if f["path"] == "sidecheck3.txt")
        assert "|||||||" in entry["content"], "zdiff3 base marker required for this check"

        # drops theirs ("group change") for this chunk, but that exact string also sits
        # unrelated on line 1 — a whole-file membership check would wrongly see it as
        # "still present" and let the drop through.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "sidecheck3.txt",
                "content": "group change\nnoise1\nmainline change\nnoise2\n",
            }], True)
        assert exc.value.code == "conflict_side_dropped"
        assert exc.value.status == 422

        svc.abort_merge(group, merge_id)



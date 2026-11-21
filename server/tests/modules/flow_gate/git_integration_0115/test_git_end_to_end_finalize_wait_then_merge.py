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
    def test_finalize_wait_then_merge(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        assert svc.ensure_worktree("gitprj", "default", self.GROUP) == "ok"
        wt = src_root("GitProj", "gitprj_default_0100")
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")

        _seed_wf_done_root(self.GROUP, project_id=self.GROUP.split(".", 1)[0])
        db_git.set_status(self.GROUP, "awaiting_choice")
        out = svc.finalize(self.GROUP, "wait")
        assert out["result"]["status"] == "waiting"

        state = svc.get_finalize_state(self.GROUP)["state"]
        assert state["status"] == "waiting"
        assert state["choices"] == ["merge", "merge_only", "wait"]
        assert state["aux_choices"] == ["push"]

        subject = "fix(git_service): use confirmed merge subject"
        out = svc.finalize(self.GROUP, "merge", commit_message=subject)
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is True
        assert out["result"]["merge_commit"]
        # The merge commit no longer reuses the work
        # subject — reusing it stamped two commits of identical title+diff onto
        # origin ("same code committed twice"). The top of origin main is now a
        # conventional Merge commit; the confirmed work subject rides in on its
        # second parent (the absorb commit), a normal work-commit + merge-commit
        # pair. `main^2` also proves the two-parent topology unmerge relies on.
        top_subject = _git(["log", "-1", "--format=%s", "main"], cwd=origin_repo["bare"]).strip()
        assert top_subject.startswith("Merge branch ")
        assert top_subject != subject
        assert _git(["log", "-1", "--format=%s", "main^2"], cwd=origin_repo["bare"]).strip() == subject
        # origin main actually contains the group's work
        files = _git(
            ["ls-tree", "--name-only", "main"], cwd=origin_repo["bare"]
        ).split()
        assert "work.txt" in files
        # A merge lands the work in default but
        # must NOT publish the intermediate work branch to origin.
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0100" not in heads
        # re-finalize is rejected (already finalized)
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(self.GROUP, "merge")
        assert exc.value.status == 409



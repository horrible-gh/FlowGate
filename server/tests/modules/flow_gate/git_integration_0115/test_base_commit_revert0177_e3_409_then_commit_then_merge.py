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

from test_git_integration_0115 import TestBaseCommitRevert0177 as _Scenario


class TestBaseCommitRevert0177(_Scenario):
    def test_e3_409_then_commit_then_merge(self, base_origin):
        """The confirmed flow (§2.6-c): merge → 409 base_dirty(files) →
        base-commit → retry merge succeeds, and the base-commit rides along."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "baseprj.default.0301"
        assert svc.ensure_worktree("baseprj", "default", group) == "ok"
        wt = src_root("BaseProj", "baseprj_default_0301")
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        base = base_origin["base"]
        (base / "a.txt").write_text("hotfixed alpha\n", encoding="utf-8")

        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(group, "merge")
        assert exc.value.status == 409          # was 500 before 0177 (L0002 §2.5)
        assert exc.value.code == "base_dirty"
        assert exc.value.details == {"files": ["a.txt"]}

        out = svc.base_commit("baseprj", None)["result"]
        assert out["committed"] is True and out["remaining"] == []

        # the local base commit does not block the retry (ff-only stays a no-op)
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "merged"
        # origin main now holds the group work AND the explicit base commit
        files = _git(["ls-tree", "--name-only", "main"], cwd=base_origin["bare"]).split()
        assert "work.txt" in files
        assert _git(["show", "main:a.txt"], cwd=base_origin["bare"]) == "hotfixed alpha\n"


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
    def test_dirty_listed_then_revert_then_commit(self, base_origin):
        from modules.flow_gate.services import git_service as svc

        base = base_origin["base"]
        (base / "a.txt").write_text("edited alpha\n", encoding="utf-8")
        (base / "b.txt").unlink()  # a deletion is a tracked change too
        (base / "junk.tmp").write_text("untracked\n", encoding="utf-8")

        out = svc.project_git_status("baseprj")["status"]
        assert out["base_dirty"]["dirty"] is True
        # untracked artifacts never appear (E3 scope)
        assert sorted(out["base_dirty"]["files"]) == ["a.txt", "b.txt"]

        # per-file revert restores a deletion from HEAD; a.txt stays dirty
        rev = svc.base_revert("baseprj", ["b.txt"])
        assert rev["ok"] is True
        assert rev["result"]["results"] == [{"path": "b.txt", "result": "reverted"}]
        assert rev["result"]["remaining"] == ["a.txt"]
        assert (base / "b.txt").read_text(encoding="utf-8") == "beta\n"

        # blank message → server derives the default subject (§2.2/§2.3)
        out = svc.base_commit("baseprj", "")["result"]
        assert out["committed"] is True
        assert out["subject"] == "fix: a.txt"
        assert out["files"] == ["a.txt"] and out["remaining"] == []
        assert out["commit"]
        # committed but NOT pushed: base is ahead of origin by exactly 1
        st = svc.project_git_status("baseprj")["status"]
        # rev6: ai_run rides alongside (the project AI-cleanup lease as server truth).
        assert st["base_dirty"] == {
            "dirty": False, "files": [], "merge_in_progress": None, "ai_run": None,
        }
        assert st["ahead_count"] == 1 and st["behind_count"] == 0
        log = _git(["log", "-1", "--pretty=%s"], cwd=base)
        assert log.strip() == "fix: a.txt"


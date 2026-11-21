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

from test_git_integration_0115 import TestResolveBaseDirtyRealRepo0482 as _Scenario


class TestResolveBaseDirtyRealRepo0482(_Scenario):
    def test_resolve_base_dirty_commits_selected_paths_with_the_given_message(self, resolve_base_dirty_origin, monkeypatch):
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services import remote_tool_service

        base = resolve_base_dirty_origin["base"]
        (base / "a.txt").write_text("edited alpha\n", encoding="utf-8")
        (base / "b.txt").write_text("edited beta\n", encoding="utf-8")
        status = svc.project_git_status("rbdprj")["status"]
        assert sorted(status["base_dirty"]["files"]) == ["a.txt", "b.txt"]

        monkeypatch.setattr(
            remote_tool_service, "_worker_token_for_grant",
            lambda _grant: {"action_scope": "resolve_base_dirty"},
        )
        result, _ = remote_tool_service._exec_resolve_base_dirty(
            {
                "decisions": [
                    {"path": "a.txt", "action": "commit"},
                    {"path": "b.txt", "action": "discard"},
                ],
                "complete": True,
                "commit_message": "fix: resolve base dirty a.txt",
            },
            {"project": "rbdprj"},
        )
        assert result["status"] == "resolved"
        assert result["remaining"] == []
        assert result["commit"]   # a short hash, not the commit message or a path

        # b.txt was discarded (restored to HEAD), not committed
        assert (base / "b.txt").read_text(encoding="utf-8") == "beta\n"
        subject = _git(["log", "-1", "--format=%s"], cwd=base).strip()
        assert subject == "fix: resolve base dirty a.txt"
        committed_files = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], cwd=base).split()
        assert committed_files == ["a.txt"]
        status_after = svc.project_git_status("rbdprj")["status"]
        # rev6: ai_run rides alongside — the project AI-cleanup lease as server truth,
        # so the panel stops latching "already running" in the browser (rejection 2).
        assert status_after["base_dirty"] == {
            "dirty": False, "files": [], "merge_in_progress": None, "ai_run": None,
        }


    def test_resolve_base_dirty_lock_busy_when_project_already_locked(self, resolve_base_dirty_origin, monkeypatch):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services import remote_tool_service

        base = resolve_base_dirty_origin["base"]
        (base / "a.txt").write_text("edited again\n", encoding="utf-8")
        assert svc.project_git_status("rbdprj")["status"]["base_dirty"]["files"] == ["a.txt"]

        monkeypatch.setattr(
            remote_tool_service, "_worker_token_for_grant",
            lambda _grant: {"action_scope": "resolve_base_dirty"},
        )
        with hold_lock("B", "rbdprj", holder_kind="base_mutation"):
            with pytest.raises(remote_tool_service._OpError) as exc:
                remote_tool_service._exec_resolve_base_dirty(
                    {"decisions": [{"path": "a.txt", "action": "discard"}], "complete": True},
                    {"project": "rbdprj"},
                )
            assert exc.value.status == 409
            assert exc.value.details["reason"] == "git_busy"
            assert exc.value.details["reason_code"] == "lock_busy"
        assert held_locks("rbdprj") == []
        # nothing was applied while the project lock was held elsewhere
        assert svc.project_git_status("rbdprj")["status"]["base_dirty"]["files"] == ["a.txt"]



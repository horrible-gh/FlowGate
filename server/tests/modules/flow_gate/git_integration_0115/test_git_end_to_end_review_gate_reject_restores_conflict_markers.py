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
    def test_review_gate_reject_restores_conflict_markers(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0123"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0123")
        (wt / "shared.py").write_text('"reject group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"reject mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "reject mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        original = svc.list_conflicts(group, merge_id)
        original_content = next(
            f["content"] for f in original["files"] if f["path"] == "shared.py"
        )
        assert "<<<<<<<" in original_content

        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"reject group version"\n"reject mainline version"\n',
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"

        started: dict = {}

        def _fake_start_run(first_message):
            started["msg"] = first_message
            return "aiv_fake_retry"

        rejected = svc.reject_merge_review(
            group, merge_id,
            reason="다시 확인해 주세요 — 이 사유는 충분히 깁니다.",
            provider_id="prov_test", provider_pinned=True,
            start_run=_fake_start_run,
        )
        assert rejected["result"]["status"] == "returned_to_resolver"
        assert rejected["result"]["resolver_run_id"] == "aiv_fake_retry"
        assert started["msg"]

        restored = svc.list_conflicts(group, merge_id)
        restored_content = next(
            f["content"] for f in restored["files"] if f["path"] == "shared.py"
        )
        assert restored_content == original_content
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context.get("review_state") is None
        assert "snapshot_tree" not in context
        assert context.get("auto_authority") is False

        svc.abort_merge(group, merge_id)



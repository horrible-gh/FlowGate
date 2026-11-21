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
    def test_review_gate_message_does_not_overwrite_an_approval_that_won_the_race(
        self, origin_repo,
    ):
        # AI review finding (same class): `send_review_message`
        # validated the session, then started the AI run — a slow call — and only
        # then wrote the pending bookkeeping back from the context it had read
        # BEFORE the run started. An approval completing in that window was
        # silently overwritten (review_state/merge_commit reverted to a
        # pre-approval copy). The turn must now be refused instead, and the
        # approval left exactly as it landed.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0144"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0144")
        (wt / "shared.py").write_text('"race send group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"race send mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "race send mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"race send group version"\n"race send mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        message = "실행이 시작되는 사이에 승인이 끝나는 경우"

        def start_run_while_the_human_approves():
            approved = svc.approve_merge_review(
                group, merge_id, attempt_id=str(uuid.uuid4()),
                review_fingerprint=fingerprint, authority="human",
            )
            assert approved["result"]["status"] == "merged"
            return "aiv_orphaned_by_approval"

        with pytest.raises(svc.GitServiceError) as exc:
            svc.send_review_message(
                group, merge_id, message=message,
                provider_id="prov_test", provider_pinned=True, apply_requested=True,
                start_run=start_run_while_the_human_approves,
            )
        assert exc.value.code == "review_not_ready"

        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["review_state"] == "completed"
        assert context.get("merge_commit")
        assert db_git.get_session(merge_id)["status"] == "done"
        assert context.get("pending_conversation_run_id") is None
        assert context.get("pending_conversation_write_requested") is None
        assert not any(t["message"] == message for t in context.get("conversation") or [])



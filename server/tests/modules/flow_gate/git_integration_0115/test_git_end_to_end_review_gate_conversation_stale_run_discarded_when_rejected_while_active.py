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
    def test_review_gate_conversation_stale_run_discarded_when_rejected_while_active(
        self, origin_repo, monkeypatch,
    ):
        # AI review finding: a human can reject the
        # candidate a conversation run is still answering — reject_and_return_
        # to_resolver never checks pending_conversation_run_id. Once such a run
        # later finishes, its answer must be discarded as `stale_run`, never
        # folded in as if it still answered a candidate the session has already
        # abandoned (review_state moved off resolved_pending_review/re_review).
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0141"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0141")
        (wt / "shared.py").write_text('"stale reject group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"stale reject mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "stale reject mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"stale reject group version"\n"stale reject mainline version"\n',
        }], True)

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 왜 이렇게 했어?",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_stale_reject_race",
        )
        assert sent["result"]["status"] == "accepted"

        rejected = svc.reject_merge_review(
            group, merge_id, reason="다시 확인해 주세요 — 이 사유는 충분히 깁니다.",
            provider_id="prov_test", provider_pinned=True,
            start_run=lambda first_message: "aiv_new_resolver_run",
        )
        assert rejected["result"]["status"] == "returned_to_resolver"

        # only now does the OLD (pre-reject) run finish.
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "이 답은 이미 버려진 후보에 대한 것입니다.",
                "provider_id": "prov_test",
            },
        )
        # the session is back to `open` (review_state is None), so the review
        # screen itself is correctly unavailable — but materialization still
        # runs as a side effect of the lookup and must discard the stale answer.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.get_merge_review(group, merge_id)
        assert exc.value.code == "review_not_ready"

        context = db_git.session_context(db_git.get_session(merge_id))
        roles = [(t["role"], t["status"]) for t in context["conversation"]]
        assert ("ai", "stale_run") in roles
        assert not any(t["role"] == "ai" and t["status"] == "accepted" for t in context["conversation"])
        # rev6: kept, under a line that names what moved (here: the reject ended the wait
        # and refroze nothing, so the candidate identity is gone too).
        stale = next(t for t in context["conversation"] if t["status"] == "stale_run")
        # Same two facts, read off the contract instead of the sentence.
        # [반려] leaves review_state None, so `review_state` is absent from the params and
        # the surfaces say "the approval wait ended" without naming a state.
        assert stale["message"] == "이 답은 이미 버려진 후보에 대한 것입니다."
        assert stale["message_code"] == "review_stale_run"
        assert "approval_settled" in stale["message_params"]["changed"]
        assert "review_state" not in stale["message_params"]
        assert context.get("pending_conversation_run_id") is None

        svc.abort_merge(group, merge_id)



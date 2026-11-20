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
    def test_review_gate_conversation_message_appends_ai_turn(self, origin_repo, monkeypatch):
        # D0006 §3.7/§3.9 / L0007 §2.9: a propose-only question during pending
        # review starts a bound run and, once it finishes, its last message is
        # folded into the conversation on the next read — without moving the
        # review off resolved_pending_review or touching the fingerprint.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0126"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0126")
        (wt / "shared.py").write_text('"convo group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"convo mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "convo mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"convo group version"\n"convo mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="ko.ts 쪽은 왜 이렇게 바꿨어?",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_fake_convo",
        )
        assert sent["result"]["status"] == "accepted"
        assert sent["result"]["run_id"] == "aiv_fake_convo"
        # a second message while a run is still active is refused.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.send_review_message(
                group, merge_id, message="still waiting",
                provider_id="prov_test", provider_pinned=True, apply_requested=False,
                start_run=lambda: "aiv_should_not_start",
            )
        assert exc.value.code == "re_instruction_busy"

        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "새로 쓰는 라벨이라 키를 추가했습니다.",
                "provider_id": "prov_test",
            },
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint  # unchanged: propose-only
        roles = [(t["role"], t["message"]) for t in review["conversation"]]
        assert ("human", "ko.ts 쪽은 왜 이렇게 바꿨어?") in roles
        assert ("ai", "새로 쓰는 라벨이라 키를 추가했습니다.") in roles

        # the completed run's slot is cleared — a new message can start another one.
        sent2 = svc.send_review_message(
            group, merge_id, message="고마워요",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_fake_convo_2",
        )
        assert sent2["result"]["status"] == "accepted"
        svc.abort_merge(group, merge_id)



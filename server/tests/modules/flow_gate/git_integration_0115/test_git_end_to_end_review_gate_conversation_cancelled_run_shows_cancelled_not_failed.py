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
    def test_review_gate_conversation_cancelled_run_shows_cancelled_not_failed(self, origin_repo, monkeypatch):
        # T0004 §2.2 (flowgate.default.0570): a review-conversation run the human
        # stopped with the STOP button must not fold into the conversation looking
        # like an ordinary run failure -- the server tells the two apart using the
        # underlying ai-invoke run's end_reason (§4.1 of 0187's cancel branch stamps
        # end_reason="cancelled").
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0147"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0147")
        (wt / "shared.py").write_text('"convo group version 0147"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"convo mainline version 0147"\n', encoding="utf-8")
        _git(["commit", "-am", "convo mainline change 0147"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"convo group version 0147"\n"convo mainline version 0147"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 수정 왜 이렇게 오래 걸려?",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_fake_stop",
        )
        assert sent["result"]["run_id"] == "aiv_fake_stop"

        # The run was cancelled server-side via the STOP button's
        # POST /api/v1/ai-invoke/{run_id}/cancel path.
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": False, "end_reason": "cancelled",
                "last_message": None, "provider_id": "prov_test",
            },
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint  # unchanged: nothing was applied
        cancelled_turn = next(t for t in review["conversation"] if t["role"] == "ai")
        assert cancelled_turn["status"] == "cancelled"
        # 0578 T0006 §2.2: "not the word 실패" was a proxy for "this reads as a cancellation,
        # not a failure". The stored turn now says that outright, in a code, and carries no
        # server-authored sentence at all for any surface to mistake.
        assert cancelled_turn["message_code"] == "review_cancelled"
        assert cancelled_turn["message"] == ""

        # the slot is cleared -- a new message can start another run right away
        # (T0004 §2.2's "취소 후 다음 대화/수정 적용 요청을 다시 시작할 수 있어야 한다").
        sent2 = svc.send_review_message(
            group, merge_id, message="다시 요청할게",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_fake_stop_2",
        )
        assert sent2["result"]["status"] == "accepted"
        svc.abort_merge(group, merge_id)



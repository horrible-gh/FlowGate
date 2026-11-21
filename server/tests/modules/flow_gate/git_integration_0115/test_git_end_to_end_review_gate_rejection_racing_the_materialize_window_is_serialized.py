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
    def test_review_gate_rejection_racing_the_materialize_window_is_serialized(
        self, origin_repo, monkeypatch,
    ):
        # AI review finding, propose-only half: the same
        # window let a plain answer be appended as `accepted` to a session a
        # rejection had already returned to the resolver. A [반려] fired from
        # inside the window must be serialized too — the answer lands on the
        # candidate the check saw, and the rejection is refused the lock.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0143"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0143")
        (wt / "shared.py").write_text('"race ask group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"race ask mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "race ask mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"race ask group version"\n"race ask mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 왜 이렇게 했어?",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_materialize_race_ask",
        )
        assert sent["result"]["status"] == "accepted"
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "이렇게 해결했습니다.", "provider_id": "prov_test",
            },
        )

        monkeypatch.setattr(svc, "LOCK_WAIT_SEC", 0)
        raced: list = []
        real_set_session_context = db_git.set_session_context

        def racing_set_session_context(mid, ctx):
            real_set_session_context(mid, ctx)
            if raced:
                return
            raced.append("fired")
            try:
                svc.reject_merge_review(
                    group, merge_id, reason="이 답이 오기 전에 반려합니다 — 사유는 충분히 깁니다.",
                    provider_id="prov_test", provider_pinned=True,
                    start_run=lambda first_message: "aiv_race_resolver_run",
                )
                raced.append("interleaved")
            except svc.GitServiceError as exc:
                raced.append(exc.code)

        monkeypatch.setattr(db_git, "set_session_context", racing_set_session_context)

        review = svc.get_merge_review(group, merge_id)["result"]
        assert raced == ["fired", "git_busy"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint
        statuses = [(t["role"], t["status"]) for t in review["conversation"]]
        assert statuses.count(("ai", "accepted")) == 1
        assert ("ai", "stale_run") not in statuses
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context.get("pending_conversation_run_id") is None

        # the rejection the racer could not interleave still works afterwards.
        rejected = svc.reject_merge_review(
            group, merge_id, reason="이제 반려합니다 — 이 사유는 충분히 깁니다.",
            provider_id="prov_test", provider_pinned=True,
            start_run=lambda first_message: "aiv_race_resolver_run",
        )
        assert rejected["result"]["status"] == "returned_to_resolver"
        svc.abort_merge(group, merge_id)



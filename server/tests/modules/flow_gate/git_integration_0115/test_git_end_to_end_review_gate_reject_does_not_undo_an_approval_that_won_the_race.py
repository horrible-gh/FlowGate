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
    def test_review_gate_reject_does_not_undo_an_approval_that_won_the_race(
        self, origin_repo, monkeypatch,
    ):
        # 0481 TR0009 rev5 (AI review finding, same class): `reject_merge_review`
        # checked review_state BEFORE taking the project lock and then restored
        # the conflict from that pre-lock context — so an approval that committed
        # while the rejection was waiting for the lock was both undone in the
        # checkout and overwritten in the session. The rejection must now lose
        # the race cleanly. The racer fires from inside the lock acquisition (`lock_manager.acquire`), i.e.
        # exactly in the window between the pre-check and the lock being held.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0145"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0145")
        (wt / "shared.py").write_text('"race reject group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"race reject mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "race reject mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"race reject group version"\n"race reject mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        raced: list = []
        real_acquire = lm.acquire

        def racing_acquire(domain, project_id, **kw):
            if not raced:
                raced.append(domain)
                approved = svc.approve_merge_review(
                    group, merge_id, attempt_id=str(uuid.uuid4()),
                    review_fingerprint=fingerprint, authority="human",
                )
                assert approved["result"]["status"] == "merged"
            return real_acquire(domain, project_id, **kw)

        monkeypatch.setattr(lm, "acquire", racing_acquire)

        with pytest.raises(svc.GitServiceError) as exc:
            svc.reject_merge_review(
                group, merge_id, reason="승인과 경쟁하는 반려 — 이 사유는 충분히 깁니다.",
                provider_id="prov_test", provider_pinned=True,
                start_run=lambda first_message: "aiv_race_reject_resolver",
            )
        assert exc.value.code == "review_not_ready"

        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["review_state"] == "completed"
        assert context.get("merge_commit")
        assert db_git.get_session(merge_id)["status"] == "done"
        # the approved candidate is intact: the rejection did not re-run the
        # merge and did not put conflict markers back.
        assert not any(
            t["status"] == "rejected" for t in context.get("conversation") or []
        )



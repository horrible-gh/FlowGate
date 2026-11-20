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
    def test_review_gate_conditional_push_rejects_a_moved_remote(self, origin_repo):
        # A third-party push invalidates the frozen lease. Current recovery keeps
        # the approved commit preserved and waits for reconciliation; it must
        # never overwrite the remote or silently mint a new review candidate.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0125"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0125")
        (wt / "shared.py").write_text('"cas group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"cas mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "cas mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"cas group version"\n"cas mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        # a THIRD party pushes to origin/main without ever touching this project's
        # base checkout — expected_remote_head (frozen above) is now stale.
        (seedwt / "unrelated.txt").write_text("someone else\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "unrelated concurrent push"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)
        moved_remote = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()

        result = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert result["result"]["status"] == "reconciling"
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() == moved_remote
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["review_state"] == "reconciling"
        assert context["merge_commit"]
        assert context["preserve_ref"]
        assert context["review_fingerprint"] == fingerprint
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["can_approve"] is False
        assert review["recovery"]["requires_recovery"] is True
        assert review["recovery"]["preserve_sha"] == context["merge_commit"]

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
    def test_resolve_conflicts_accepts_real_workplaneditor_disabled_merge(self, origin_repo):
        # The exact real-world regression: both sides
        # rewrote the SAME Vue `:disabled` attribute line, and a normal synthesized
        # resolution that folds both conditions into one attribute must not be
        # rejected — and must not require writing the attribute twice just to satisfy
        # the validator (explicitly forbidden). This stops at
        # resolved_pending_review + the chunk's "manual" classification: whether the
        # merge candidate ALSO clears the unrelated .vue real-syntax-check gate in
        # approve_merge_review depends on client/node_modules being installed in this
        # worktree, which is outside conflict_side_dropped's scope (this
        # task rewrites the validator, not 0599's WorkPlanEditor.vue merge itself).
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base_line = (
            ':disabled="loading || !!unreadable || dirty || downloading || '
            'hasPendingCapabilityWarning"\n'
        )
        ours_line = (
            ':disabled="loading || dirty || downloading || hasPendingCapabilityWarning '
            '|| (!plan && !unreadable?.raw)"\n'
        )
        theirs_line = (
            ':disabled="loading || (!!unreadable && unreadable.raw === null) || dirty '
            '|| downloading || hasPendingCapabilityWarning"\n'
        )
        combined_line = (
            ':disabled="loading || (!!unreadable && unreadable.raw === null) || dirty '
            '|| downloading || hasPendingCapabilityWarning || (!plan && !unreadable?.raw)"\n'
        )

        group = "gitprj.default.0150"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "WorkPlanEditor.vue").write_text(base_line, encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add WorkPlanEditor base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0150")
        (wt / "WorkPlanEditor.vue").write_text(theirs_line, encoding="utf-8")

        (seedwt / "WorkPlanEditor.vue").write_text(ours_line, encoding="utf-8")
        _git(["commit", "-am", "mainline :disabled change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "WorkPlanEditor.vue",
            "content": combined_line,
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"

        review = svc.get_merge_review(group, merge_id)["result"]
        origins = [o for o in review["conflict_origins"] if o["path"] == "WorkPlanEditor.vue"]
        assert len(origins) == 1
        assert origins[0]["selection"] == "manual"

        # release the session so it doesn't block later tests' project-level guard;
        # the point already proved is that resolve_conflicts() accepted the synthesized
        # merge instead of raising conflict_side_dropped.
        svc.abort_merge(group, merge_id)



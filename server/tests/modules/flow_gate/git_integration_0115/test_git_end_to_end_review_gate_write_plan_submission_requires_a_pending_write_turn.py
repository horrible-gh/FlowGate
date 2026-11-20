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
    def test_review_gate_write_plan_submission_requires_a_pending_write_turn(self, origin_repo):
        # 0481 TR0009 rev2: the worker-token submission endpoint's server-side
        # entry point (svc.submit_review_write_plan) must refuse a plan when no
        # write turn is waiting for one — a propose-only conversation run (or no
        # run at all) has no write intent recorded on the session.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0134"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0134")
        (wt / "shared.py").write_text('"noreq group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"noreq mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "noreq mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"noreq group version"\n"noreq mainline version"\n',
        }], True)

        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": "0" * 64,
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "unrelated.py",
                "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
            }],
        }
        with pytest.raises(svc.GitServiceError) as exc:
            svc.submit_review_write_plan(group, merge_id, plan=plan)
        assert exc.value.status == 409
        assert exc.value.code == "write_plan_not_requested"

        # a propose-only conversation turn ALSO does not open the write channel.
        svc.send_review_message(
            group, merge_id, message="그냥 질문",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_fake_question",
        )
        with pytest.raises(svc.GitServiceError) as exc2:
            svc.submit_review_write_plan(group, merge_id, plan=plan)
        assert exc2.value.code == "write_plan_not_requested"

        svc.abort_merge(group, merge_id)



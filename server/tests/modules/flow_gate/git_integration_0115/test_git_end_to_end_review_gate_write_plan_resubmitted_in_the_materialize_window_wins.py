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
    def test_review_gate_write_plan_resubmitted_in_the_materialize_window_wins(
        self, origin_repo, monkeypatch,
    ):
        # AI review finding, the plan half: the stale check is
        # not the only input that has to be atomic with what it authorizes — so is
        # the plan being applied. `_materialize_pending_conversation_run` reads the
        # run detail BEFORE taking the project lock, and `submit_review_write_plan`
        # (worker token) takes no git lock at all, so a second submission landing in
        # that window was silently dropped: the worker was told `accepted` and the
        # PREVIOUS plan got applied. The run's last submission must win — the same
        # contract `..._submission_overwrites_the_runs_own_prior_plan` states for the
        # sequential case — so the run detail is re-read under the lock too.
        import base64

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0146"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0146")
        (wt / "shared.py").write_text('"resubmit group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"resubmit mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "resubmit mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"resubmit group version"\n"resubmit mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 고쳐서 적용해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_resubmit_race",
        )
        assert sent["result"]["status"] == "accepted"

        actual = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]["content"]
        anchor_text = actual[:actual.index('"resubmit mainline')]
        manifest_entry = next(
            e for e in db_git.session_context(db_git.get_session(merge_id))["snapshot_manifest"]
            if e["path"] == "shared.py"
        )

        def _plan(tag):
            replacement = anchor_text.replace(
                "resubmit group version", "resubmit group version ({})".format(tag),
            )
            return {
                "schema_version": "flowgate.write-plan.v1",
                "base_fingerprint": fingerprint,
                "operations": [{
                    "operation_id": "op-{}".format(tag), "kind": "edit", "path": "shared.py",
                    "expected_before_blob": manifest_entry["oid"],
                    "anchor": {
                        "body_base64": base64.b64encode(anchor_text.encode("utf-8")).decode("ascii"),
                        "expected_count": 1,
                    },
                    "replacement_bytes_base64": base64.b64encode(
                        replacement.encode("utf-8")
                    ).decode("ascii"),
                    "purpose": "plan {}".format(tag),
                }],
                "held_test_operations": [],
            }

        plan_first, plan_second = _plan("first"), _plan("second")

        # stands in for the runs table: submit WRITES the plan, materialize READS it.
        recorded: dict = {}
        monkeypatch.setattr(
            ai_invoke_service, "record_run_write_plan",
            lambda run_id, plan: recorded.__setitem__(run_id, plan),
        )
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "수정했습니다.", "provider_id": "prov_test",
                "write_plan": recorded.get(run_id),
            },
        )
        assert svc.submit_review_write_plan(
            group, merge_id, plan=plan_first, ai_run_id="aiv_resubmit_race",
        )["result"]["status"] == "accepted"

        # the racer: the worker's SECOND submission lands after materialization has
        # already read the run detail, but before it takes the lock and claims the run.
        raced: list = []
        real_acquire = lm.acquire

        def racing_acquire(domain, project_id, **kw):
            if not raced:
                raced.append(domain)
                assert svc.submit_review_write_plan(
                    group, merge_id, plan=plan_second, ai_run_id="aiv_resubmit_race",
                )["result"]["status"] == "accepted"
            return real_acquire(domain, project_id, **kw)

        monkeypatch.setattr(lm, "acquire", racing_acquire)

        review = svc.get_merge_review(group, merge_id)["result"]
        assert raced, "the racing submission never fired"
        # the plan that was applied is the one submitted LAST, not the pre-lock copy.
        assert review["review_state"] == "re_review"
        assert review["review_fingerprint"] != fingerprint
        content = svc.read_merge_review_file_diff(
            group, merge_id, "shared.py",
        )["data"]["new"]["content"]
        assert content.startswith('"resubmit group version (second)"')
        assert "(first)" not in content
        statuses = [(t["role"], t["status"]) for t in review["conversation"]]
        assert statuses.count(("ai", "accepted")) == 1
        assert ("ai", "stale_run") not in statuses
        svc.abort_merge(group, merge_id)



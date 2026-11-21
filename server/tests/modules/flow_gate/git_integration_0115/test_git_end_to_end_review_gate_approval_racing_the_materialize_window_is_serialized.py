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
    def test_review_gate_approval_racing_the_materialize_window_is_serialized(
        self, origin_repo, monkeypatch,
    ):
        # AI review finding: the stale_run decision must be
        # ATOMIC with what it authorizes. rev4 read review_state/fingerprint/
        # generation with NO lock held and only afterwards applied the plan and
        # appended the turn, so an approval landing in that check-to-materialize
        # window produced exactly what L0007 §2.9 forbids — a write run's result
        # applied/reported against a target nobody re-checked (and reported as a
        # generic `apply_failed`, not `stale_run`). This test drives an approval
        # INTO that window: the racing call fires from inside materialization,
        # at the very moment the pending run is claimed, and must be serialized
        # by the project git lock instead of interleaving.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0142"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0142")
        (wt / "shared.py").write_text('"race apply group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"race apply mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "race apply mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"race apply group version"\n"race apply mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 고쳐서 적용해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_materialize_race",
        )
        assert sent["result"]["status"] == "accepted"

        actual = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]["content"]
        split_at = actual.index('"race apply mainline')
        anchor_text = actual[:split_at]
        replacement_text = anchor_text.replace("race apply group version", "race apply group version (fixed)")
        manifest_entry = next(
            e for e in db_git.session_context(db_git.get_session(merge_id))["snapshot_manifest"]
            if e["path"] == "shared.py"
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1", "kind": "edit", "path": "shared.py",
                "expected_before_blob": manifest_entry["oid"],
                "anchor": {
                    "body_base64": base64.b64encode(anchor_text.encode("utf-8")).decode("ascii"),
                    "expected_count": 1,
                },
                "replacement_bytes_base64": base64.b64encode(
                    replacement_text.encode("utf-8")
                ).decode("ascii"),
                "purpose": "must apply exactly once, against the checked target",
            }],
            "held_test_operations": [],
        }
        assert svc.submit_review_write_plan(
            group, merge_id, plan=plan, ai_run_id="aiv_materialize_race",
        )["result"]["status"] == "accepted"
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "수정했습니다.", "provider_id": "prov_test",
                "write_plan": plan,
            },
        )

        # the racer: an approval of the CURRENT (checked, still pending) snapshot
        # fired from inside the check-to-materialize window — the first session
        # write materialization makes is its claim on the pending run.
        monkeypatch.setattr(svc, "LOCK_WAIT_SEC", 0)  # the racer must not block the test
        raced: list = []
        real_set_session_context = db_git.set_session_context

        def racing_set_session_context(mid, ctx):
            real_set_session_context(mid, ctx)
            if raced:
                return
            raced.append("fired")
            try:
                svc.approve_merge_review(
                    group, merge_id, attempt_id=str(uuid.uuid4()),
                    review_fingerprint=fingerprint, authority="human",
                )
                raced.append("interleaved")
            except svc.GitServiceError as exc:
                raced.append(exc.code)

        monkeypatch.setattr(db_git, "set_session_context", racing_set_session_context)

        review = svc.get_merge_review(group, merge_id)["result"]
        # the approval could not interleave: it was refused the project lock the
        # materialization was holding across check + claim + apply + append.
        assert raced == ["fired", "git_busy"]
        # and the run's plan was applied exactly once, to the target the stale
        # check actually looked at.
        assert review["review_state"] == "re_review"
        new_fingerprint = review["review_fingerprint"]
        assert new_fingerprint != fingerprint
        statuses = [(t["role"], t["status"]) for t in review["conversation"]]
        assert statuses.count(("ai", "accepted")) == 1
        assert ("ai", "stale_run") not in statuses
        assert svc.read_merge_review_file_diff(
            group, merge_id, "shared.py",
        )["data"]["new"]["content"].startswith('"race apply group version (fixed)"')

        # the human's approval of the target they had on screen is now correctly
        # refused (it is no longer the candidate), and the new one merges.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.approve_merge_review(
                group, merge_id, attempt_id=str(uuid.uuid4()),
                review_fingerprint=fingerprint, authority="human",
            )
        assert exc.value.code == "stale_review"
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=new_fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"



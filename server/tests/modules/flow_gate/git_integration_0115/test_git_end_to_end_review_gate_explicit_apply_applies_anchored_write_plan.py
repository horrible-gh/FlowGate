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
    def test_review_gate_explicit_apply_applies_anchored_write_plan(self, origin_repo, monkeypatch):
        # 0481 TR0009 rev2 (AI review finding 1): T0008 item 1 and its completion
        # criteria require a human-requested apply to consume an anchored
        # edit/create-file plan, validate it in isolation, atomically apply it,
        # and produce a NEW frozen candidate/fingerprint — not a 409 refusal.
        # This exercises the whole path: [수정 적용] starts a run, the worker
        # submits a plan through the SAME endpoint a real resolve_conflict
        # worker token would call (svc.submit_review_write_plan — its run has no
        # write tool at all), and once the run is observed finished the plan is
        # applied and a NEW review_fingerprint/re_review candidate appears that
        # approves and commits like any other.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0129"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0129")
        (wt / "shared.py").write_text('"apply group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"apply mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "apply mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"apply group version"\n"apply mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 고쳐서 적용해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_fake_apply",
        )
        assert sent["result"]["status"] == "accepted"
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["pending_conversation_write_requested"] is True
        assert context["pending_conversation_run_id"] == "aiv_fake_apply"

        # the worker reads the candidate's manifest for shared.py's current blob
        # (the anchor/replacement bytes are sliced off the ACTUAL current text —
        # this checkout's git may store LF or CRLF line endings, and an anchor
        # must match the real bytes, never an assumed terminator), then submits
        # an anchored edit — the ONLY channel by which this run can change
        # anything.
        manifest_entry = next(e for e in context["snapshot_manifest"] if e["path"] == "shared.py")
        actual = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]["content"]
        split_at = actual.index('"apply mainline')
        anchor_text = actual[:split_at]
        replacement_text = anchor_text.replace("apply group version", "apply group version (fixed)")
        expected_new_content = replacement_text + actual[split_at:]
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1",
                "kind": "edit",
                "path": "shared.py",
                "expected_before_blob": manifest_entry["oid"],
                "anchor": {
                    "body_base64": base64.b64encode(anchor_text.encode("utf-8")).decode("ascii"),
                    "expected_count": 1,
                },
                "replacement_bytes_base64": base64.b64encode(
                    replacement_text.encode("utf-8")
                ).decode("ascii"),
                "purpose": "fix the group side wording",
            }],
            "held_test_operations": [],
        }
        # 0009-TR rev3 (AI review finding 1): the submission must carry the SAME
        # ai_run_id the worker token would have been issued for this run — a
        # wrong/stale run's token must not be able to attach a plan here (see
        # test_review_gate_write_plan_submission_rejects_a_mismatched_run_id).
        submitted = svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_fake_apply")
        assert submitted["result"]["status"] == "accepted"

        # submitting a SECOND plan for the same run overwrites the first — only
        # the run's own last submission is what the run's own finish attaches.
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "수정했습니다.", "provider_id": "prov_test",
                "write_plan": plan,
            },
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "re_review"
        new_fingerprint = review["review_fingerprint"]
        assert new_fingerprint != fingerprint
        roles = [(t["role"], t["status"]) for t in review["conversation"]]
        assert ("ai", "accepted") in roles
        # the pending-turn bookkeeping is cleared, and a NEW message can start
        # another turn afterward.
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context.get("pending_conversation_run_id") is None
        assert context.get("pending_conversation_write_requested") is None

        new_side = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]
        assert new_side["content"] == expected_new_content

        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=new_fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"



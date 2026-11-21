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
    def test_review_gate_write_plan_anchor_mismatch_is_rejected(self, origin_repo, monkeypatch):
        # An anchor that does not occur exactly
        # `expected_count` times in the CURRENT file must fail apply — this is
        # the whole point of anchoring instead of trusting a line number or
        # trusting the model's own claim about the file's content.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0133"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0133")
        (wt / "shared.py").write_text('"anchor group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"anchor mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "anchor mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"anchor group version"\n"anchor mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]
        context = db_git.session_context(db_git.get_session(merge_id))
        manifest_entry = next(e for e in context["snapshot_manifest"] if e["path"] == "shared.py")
        before_content = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]["content"]

        import base64

        svc.send_review_message(
            group, merge_id, message="고쳐줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_fake_anchor",
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1",
                "kind": "edit",
                "path": "shared.py",
                "expected_before_blob": manifest_entry["oid"],
                "anchor": {
                    "body_base64": base64.b64encode(b"this text does not appear in the file").decode("ascii"),
                    "expected_count": 1,
                },
                "replacement_bytes_base64": base64.b64encode(b"replacement").decode("ascii"),
                "purpose": "x",
            }],
            "held_test_operations": [],
        }
        svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_fake_anchor")
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan},
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint
        roles = [(t["role"], t["message"], t["status"]) for t in review["conversation"]]
        assert any(role == "ai" and status == "failed" for role, _msg, status in roles)

        # the real worktree file is untouched — validation failed before the one
        # step that would have touched it.
        new_side = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]
        assert new_side["content"] == before_content

        svc.abort_merge(group, merge_id)



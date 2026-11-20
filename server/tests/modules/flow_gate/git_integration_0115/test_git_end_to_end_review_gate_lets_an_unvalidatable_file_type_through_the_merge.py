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
    def test_review_gate_lets_an_unvalidatable_file_type_through_the_merge(self, origin_repo):
        # 0481 TR0010 rev3, human rejection 2026-09-08 10:33 ("머지는 되지도 않음").
        #
        # This test used to assert the opposite: TR0009 rev1 (AI review finding 1)
        # read L0007 §2.7 as covering every changed path in the candidate, so an
        # extension with no registered validator vetoed the approval. §2.7 scopes
        # that rule to the WRITE PLAN's own targets (`syntax_validation_scope` =
        # "변경되거나 생성된 모든 plan 대상 파일"), and applying it to a merge made any
        # merge carrying a `.md`, `.txt`, `.lock` or `.tsbuildinfo` permanently
        # unapprovable — [승인] could only ever answer pre_commit_validation_failed.
        #
        # The merge must complete. The plan-side rule is unchanged and is asserted
        # on the very same tree at the end of this test.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0127"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0127")
        # added independently on both sides — no common ancestor for this path,
        # so the side-drop check (unrelated to this test) is skipped.
        (wt / "notes.rst").write_text('"group notes"\n', encoding="utf-8")
        (seedwt / "notes.rst").write_text('"mainline notes"\n', encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline adds notes.rst independently"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        before_head = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()

        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "notes.rst",
            "content": '"group notes"\n"mainline notes"\n',
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"
        fingerprint = out["result"]["review_fingerprint"]

        # The candidate the human approves, captured before the commit exists.
        context = db_git.session_context(db_git.get_session(merge_id))
        base_root = svc._base_root_of("gitprj")

        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged", approved
        assert approved["result"]["review_state"] == "completed"
        # It really merged and really pushed — the remote head moved.
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() != before_head
        assert db_git.get_session(merge_id)["status"] != "open"

        # The write-plan rule is untouched: the SAME path, the SAME candidate tree,
        # asked with the plan's mode, is still refused whole.
        plan_errors = svc._validate_review_changed_paths(
            base_root, context, unregistered_extension="reject",
        )
        assert any(
            e["path"] == "notes.rst" and e["validator"] == "unsupported"
            for e in plan_errors
        ), plan_errors



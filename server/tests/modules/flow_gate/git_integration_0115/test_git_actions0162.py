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

from test_git_integration_0115 import TestGitActions0162 as _Scenario


class TestGitActions0162(_Scenario):
    def test_status_aggregation_and_pending(self, act_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        g_wait = "gitactprj.default.0201"
        g_conf = "gitactprj.default.0202"
        g_done = "gitactprj.default.0203"
        for g in (g_wait, g_conf, g_done):
            assert svc.ensure_worktree("gitactprj", "default", g) == "ok"

        _seed_wf_done_root(g_wait, project_id=g_wait.split(".", 1)[0])
        db_git.set_status(g_wait, "waiting")
        db_git.set_status(g_conf, "conflict", merge_id=None)
        db_git.set_status(g_done, "merged", merge_commit="deadbee")

        out = svc.project_git_status("gitactprj")["status"]
        assert out["enabled"] is True
        assert out["base_branch"] == "main"
        assert out["base_path_state"] == "checkout"
        # fresh clone: base == origin/main
        assert out["ahead_count"] == 0 and out["behind_count"] == 0

        slot_ids = {s["group_id"] for s in out["slots"]}
        pend_ids = {p["group_id"] for p in out["pending"]}
        assert {g_wait, g_conf} <= slot_ids
        assert g_done not in slot_ids            # terminal excluded from slots
        # Every slot reports whether its worktree is really there,
        # so the file explorer can offer create/upload on a working group instead of
        # treating every selected group as read-only. These were provisioned above.
        assert all(s["writable"] is True for s in out["slots"])
        assert pend_ids == {g_wait, g_conf} & pend_ids
        assert g_wait in pend_ids and g_conf in pend_ids
        assert g_done not in pend_ids
        assert out["pending_count"] == len(out["pending"])
        # every pending item carries the project's default action
        assert all(p["default_action"] == "merge" for p in out["pending"])


    def test_manual_fetch_fast_forwards_clean_base(self, act_origin):
        # manual_fetch used to only move the tracking ref and
        # *report* behind — the clean base checkout never advanced (a user asked whether it
        # would ever fetch). It now fast-forwards a clean base to origin/{base}, so a
        # single Fetch catches the base up (behind -> 0, advanced=True).
        from modules.flow_gate.services import git_service as svc

        # origin main advances via the seed worktree
        seedwt = act_origin["seedwt"]
        (seedwt / "moved.txt").write_text("x\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "advance"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        out = svc.manual_fetch("gitactprj")["result"]
        assert out["fetched"] is True
        assert out["advanced"] is True
        assert out["base_branch"] == "main"
        assert out["behind_count"] == 0
        assert out["ahead_count"] == 0


    def test_manual_push_allowed_and_rejected(self, act_origin):
        from modules.flow_gate.services import git_service as svc

        # A registered slot branch is an allowed push target (recovery re-push).
        # (Pushing base 'main' here would be a legitimate non-fast-forward after
        # the fetch test advanced origin — slot push avoids that coupling.)
        slot = "gitactprj_default_0201"
        out = svc.manual_push("gitactprj", slot)["result"]
        assert out["pushed"] is True and out["branch"] == slot
        heads = _git(["ls-remote", "--heads", str(act_origin["bare"])])
        assert slot in heads

        # A branch that is neither base nor a slot is rejected before any git.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.manual_push("gitactprj", "release-nope")
        assert exc.value.status == 422
        assert exc.value.code == "invalid_request"


    def test_approve_action_wait_then_merge(self, act_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitactprj.default.0204"
        assert svc.ensure_worktree("gitactprj", "default", group) == "ok"
        from modules.flow_gate.storage.paths import src_root
        wt = src_root("GitActProj", "gitactprj_default_0204")
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        # Final approval rejects wait, while the already-approved manual finalize
        # surface retains its historical wait -> merge workflow.
        with pytest.raises(svc.GitServiceError) as denied:
            svc.precheck_approve_git_action(
                {"type_code": "AC", "group_id": group}, "wait"
            )
        assert (denied.value.status, denied.value.code) == (422, "invalid_request")

        waited = svc.finalize(group, "wait")
        assert waited["ok"] is True and waited["result"]["status"] == "waiting"

        merged = svc.finalize(group, "merge")
        assert merged["ok"] is True and merged["result"]["status"] == "merged"
        files = _git(["ls-tree", "--name-only", "main"], cwd=act_origin["bare"]).split()
        assert "work.txt" in files


